"""Feature metadata for microstructure_v1."""

from __future__ import annotations

from dataclasses import asdict, dataclass

FEATURE_VERSION = "microstructure_v1"


@dataclass(frozen=True)
class FeatureDefinition:
    feature_name: str
    definition: str
    source_stream: str
    lookback: str
    causal_timestamp: str
    missing_value_rule: str
    units: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def base_feature_definitions() -> list[FeatureDefinition]:
    return [
        FeatureDefinition(
            "mid",
            "(best_bid + best_ask) / 2",
            "book_state",
            "state",
            "feature_cutoff_time",
            "missing when book state unavailable",
            "price",
        ),
        FeatureDefinition(
            "relative_spread",
            "(best_ask - best_bid) / mid",
            "book_state",
            "state",
            "feature_cutoff_time",
            "missing when mid is unavailable or zero",
            "ratio",
        ),
        FeatureDefinition(
            "qi_1",
            "(bid_sz_1 - ask_sz_1) / (bid_sz_1 + ask_sz_1)",
            "book_state",
            "state",
            "feature_cutoff_time",
            "NaN when denominator is zero or state unavailable",
            "ratio",
        ),
        FeatureDefinition(
            "microprice",
            "ask_px_1 * bid_sz_1 / (bid_sz_1 + ask_sz_1) + "
            "bid_px_1 * ask_sz_1 / (bid_sz_1 + ask_sz_1)",
            "book_state",
            "state",
            "feature_cutoff_time",
            "NaN when denominator is zero or state unavailable",
            "price",
        ),
        FeatureDefinition(
            "ofi_event",
            "Cont et al. BBO transition contribution from consecutive completed states",
            "book_state_events",
            "event",
            "book_observation_time",
            "first completed state has OFI 0",
            "base asset quantity",
        ),
    ]


def windowed_definition(
    feature_name: str,
    definition: str,
    source_stream: str,
    lookback_ms: int,
    missing_value_rule: str,
    units: str,
) -> FeatureDefinition:
    return FeatureDefinition(
        feature_name=feature_name,
        definition=definition,
        source_stream=source_stream,
        lookback=f"{lookback_ms}ms",
        causal_timestamp="feature_cutoff_time using (T-W, T]",
        missing_value_rule=missing_value_rule,
        units=units,
    )
