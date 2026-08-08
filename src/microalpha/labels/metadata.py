"""Label metadata for microstructure_labels_v1."""

from __future__ import annotations

from dataclasses import asdict, dataclass

LABEL_VERSION = "microstructure_labels_v1"


@dataclass(frozen=True)
class LabelDefinition:
    label_name: str
    definition: str
    horizon: str
    source_price: str
    target_time_rule: str
    future_lookup_rule: str
    max_lookup_delay_ms: int
    missing_value_rule: str
    units: str
    classification_threshold_bps: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def horizon_name(horizon_ms: int) -> str:
    if horizon_ms % 1000 == 0:
        return f"{horizon_ms // 1000}s"
    return f"{horizon_ms}ms"


def label_definitions(
    *,
    horizons_ms: tuple[int, ...],
    future_lookup_rule: str,
    max_label_delay_ms: int,
    classification_threshold_bps: str,
) -> list[LabelDefinition]:
    definitions: list[LabelDefinition] = []
    for horizon_ms in horizons_ms:
        suffix = horizon_name(horizon_ms)
        common = {
            "horizon": suffix,
            "source_price": "fixed-clock research-table mid",
            "target_time_rule": "target_time = feature_cutoff_time + horizon",
            "future_lookup_rule": future_lookup_rule,
            "max_lookup_delay_ms": max_label_delay_ms,
            "missing_value_rule": (
                "missing when current/future book state is invalid or stale, no future "
                "state exists, the future state crosses the session boundary, or accepted "
                "delay exceeds max_label_delay_ms"
            ),
        }
        definitions.append(
            LabelDefinition(
                label_name=f"ret_fwd_{suffix}",
                definition="log(mid_future / mid_T)",
                units="log return",
                **common,
            )
        )
        definitions.append(
            LabelDefinition(
                label_name=f"direction_{suffix}",
                definition="UP if return > threshold, DOWN if return < -threshold, else FLAT",
                units="class",
                classification_threshold_bps=classification_threshold_bps,
                **common,
            )
        )
        definitions.append(
            LabelDefinition(
                label_name=f"future_mid_move_bps_{suffix}",
                definition="10000 * (mid_future - mid_T) / mid_T",
                units="basis points",
                **common,
            )
        )
        definitions.append(
            LabelDefinition(
                label_name=f"future_move_in_spreads_{suffix}",
                definition="(mid_future - mid_T) / spread_T",
                units="current spreads",
                **common,
            )
        )
    definitions.append(
        LabelDefinition(
            label_name="next_mid_change_available",
            definition=(
                "true when a valid future completed state with changed mid was observed "
                "within the configured search horizon"
            ),
            horizon="configured search horizon",
            source_price="fixed-clock research-table mid",
            target_time_rule="search starts strictly after feature_cutoff_time",
            future_lookup_rule="first future valid observation with changed mid",
            max_lookup_delay_ms=0,
            missing_value_rule="false when unavailable or no future mid change is observed",
            units="boolean",
        )
    )
    definitions.append(
        LabelDefinition(
            label_name="next_mid_change_direction",
            definition=(
                "direction of first future valid completed state whose mid differs from mid_T; "
                "-1 down, +1 up, blank when unavailable"
            ),
            horizon="configured search horizon",
            source_price="fixed-clock research-table mid",
            target_time_rule="search starts strictly after feature_cutoff_time",
            future_lookup_rule="first future valid observation with changed mid",
            max_lookup_delay_ms=0,
            missing_value_rule=(
                "blank when unavailable or no future mid change within search horizon"
            ),
            units="direction",
        )
    )
    definitions.append(
        LabelDefinition(
            label_name="time_to_next_mid_change_ms",
            definition="milliseconds from feature_cutoff_time to first future valid changed mid",
            horizon="configured search horizon",
            source_price="fixed-clock research-table mid",
            target_time_rule="search starts strictly after feature_cutoff_time",
            future_lookup_rule="first future valid observation with changed mid",
            max_lookup_delay_ms=0,
            missing_value_rule=(
                "blank when unavailable or no future mid change within search horizon"
            ),
            units="milliseconds",
        )
    )
    return definitions
