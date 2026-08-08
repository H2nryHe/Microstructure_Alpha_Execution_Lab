"""Ordered limit-order-book state for Phase 3 replay."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


class BookStateError(ValueError):
    """Raised when an order-book invariant is violated."""


@dataclass(frozen=True)
class BookMetrics:
    best_bid: Decimal
    best_ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    mid: Decimal
    spread: Decimal
    bid_depth: Decimal
    ask_depth: Decimal


class OrderedBookSide:
    """Price-level container using a sorted price list plus quantity map.

    Insert/delete is O(n) due list shifts, but each update uses binary search
    and does not sort the full book. This is the Phase 3 correctness baseline.
    """

    def __init__(self, side: str) -> None:
        if side not in {"bid", "ask"}:
            raise ValueError(f"Unsupported side: {side}")
        self.side = side
        self._quantities: dict[Decimal, Decimal] = {}
        self._prices_ascending: list[Decimal] = []

    def __len__(self) -> int:
        return len(self._prices_ascending)

    def clear(self) -> None:
        self._quantities.clear()
        self._prices_ascending.clear()

    def set_level(self, price: Decimal, quantity: Decimal) -> str:
        if quantity < 0:
            raise BookStateError("Book quantity cannot be negative")
        exists = price in self._quantities
        if quantity == 0:
            if exists:
                del self._quantities[price]
                index = bisect_left(self._prices_ascending, price)
                if index < len(self._prices_ascending) and self._prices_ascending[index] == price:
                    self._prices_ascending.pop(index)
                return "delete"
            return "noop"
        if not exists:
            self._prices_ascending.insert(bisect_left(self._prices_ascending, price), price)
            self._quantities[price] = quantity
            return "insert"
        self._quantities[price] = quantity
        return "update"

    def best_price(self) -> Optional[Decimal]:
        if not self._prices_ascending:
            return None
        if self.side == "bid":
            return self._prices_ascending[-1]
        return self._prices_ascending[0]

    def best_size(self) -> Optional[Decimal]:
        best = self.best_price()
        if best is None:
            return None
        return self._quantities[best]

    def top_n(self, depth: int) -> list[tuple[Decimal, Decimal]]:
        if self.side == "bid":
            prices = reversed(self._prices_ascending[-depth:])
        else:
            prices = self._prices_ascending[:depth]
        return [(price, self._quantities[price]) for price in prices]

    def depth(self, depth: int) -> Decimal:
        total = Decimal("0")
        for _, quantity in self.top_n(depth):
            total += quantity
        return total


class OrderBook:
    def __init__(self) -> None:
        self.bids = OrderedBookSide("bid")
        self.asks = OrderedBookSide("ask")

    def clear(self) -> None:
        self.bids.clear()
        self.asks.clear()

    def apply_level(self, side: str, price: Decimal, quantity: Decimal) -> str:
        if side == "bid":
            return self.bids.set_level(price, quantity)
        if side == "ask":
            return self.asks.set_level(price, quantity)
        raise BookStateError(f"Unsupported book side: {side}")

    def metrics(self, depth: int = 5) -> BookMetrics:
        best_bid = self.bids.best_price()
        best_ask = self.asks.best_price()
        bid_size = self.bids.best_size()
        ask_size = self.asks.best_size()
        if best_bid is None or best_ask is None or bid_size is None or ask_size is None:
            raise BookStateError("Book must have both bid and ask sides")
        if best_bid >= best_ask:
            raise BookStateError(f"Crossed or locked book: bid={best_bid}, ask={best_ask}")
        return BookMetrics(
            best_bid=best_bid,
            best_ask=best_ask,
            bid_size=bid_size,
            ask_size=ask_size,
            mid=(best_bid + best_ask) / Decimal("2"),
            spread=best_ask - best_bid,
            bid_depth=self.bids.depth(depth),
            ask_depth=self.asks.depth(depth),
        )

    def snapshot(self, depth: int = 5) -> dict[str, str]:
        metrics = self.metrics(depth=depth)
        result = {
            "best_bid": str(metrics.best_bid),
            "best_ask": str(metrics.best_ask),
            "bid_size": str(metrics.bid_size),
            "ask_size": str(metrics.ask_size),
            "mid": str(metrics.mid),
            "spread": str(metrics.spread),
            "bid_depth": str(metrics.bid_depth),
            "ask_depth": str(metrics.ask_depth),
        }
        for index, (price, quantity) in enumerate(self.bids.top_n(depth), start=1):
            result[f"bid_px_{index}"] = str(price)
            result[f"bid_sz_{index}"] = str(quantity)
        for index, (price, quantity) in enumerate(self.asks.top_n(depth), start=1):
            result[f"ask_px_{index}"] = str(price)
            result[f"ask_sz_{index}"] = str(quantity)
        return result
