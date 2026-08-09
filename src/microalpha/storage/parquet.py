"""Parquet storage utilities for large derived artifacts.

Non-timestamp values are stored as strings so feature/label values round-trip
without decimal or float formatting drift. Timestamp columns are stored as UTC
timestamp columns.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from microalpha.research.dataset import dataset_hash

TIMESTAMP_COLUMN_SUFFIXES = ("_time",)
EXPLICIT_TIMESTAMP_COLUMNS = {
    "observation_time",
    "feature_cutoff_time",
    "event_time",
    "receive_time",
}


def is_timestamp_column(column: str) -> bool:
    return column in EXPLICIT_TIMESTAMP_COLUMNS or column.endswith(TIMESTAMP_COLUMN_SUFFIXES)


def csv_to_parquet(
    *,
    csv_path: str | Path,
    parquet_path: str | Path,
    compression: str = "zstd",
) -> str:
    source = Path(csv_path)
    output = Path(parquet_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.read_csv(source, dtype="string", keep_default_na=False)
    for column in frame.columns:
        if is_timestamp_column(column):
            non_empty = frame[column] != ""
            parsed = pd.to_datetime(frame.loc[non_empty, column], utc=True)
            frame[column] = frame[column].astype("object")
            frame.loc[non_empty, column] = parsed
            frame.loc[~non_empty, column] = pd.NaT
            frame[column] = pd.to_datetime(frame[column], utc=True)
    frame.to_parquet(output, engine="pyarrow", index=False, compression=compression)
    return dataset_hash(output)


def parquet_to_comparable_rows(parquet_path: str | Path) -> list[dict[str, str]]:
    frame = pd.read_parquet(parquet_path, engine="pyarrow")
    result: list[dict[str, str]] = []
    for _, row in frame.iterrows():
        item: dict[str, str] = {}
        for column, value in row.items():
            if pd.isna(value):
                item[column] = ""
            elif is_timestamp_column(column):
                item[column] = pd.Timestamp(value).tz_convert("UTC").isoformat()
            else:
                item[column] = str(value)
        result.append(item)
    return result


def csv_comparable_rows(csv_path: str | Path) -> list[dict[str, str]]:
    frame = pd.read_csv(csv_path, dtype="string", keep_default_na=False)
    result: list[dict[str, str]] = []
    for _, row in frame.iterrows():
        item = {}
        for column, value in row.items():
            if is_timestamp_column(column) and value != "":
                item[column] = pd.Timestamp(value).tz_convert("UTC").isoformat()
            else:
                item[column] = "" if pd.isna(value) else str(value)
        result.append(item)
    return result


def parquet_round_trip_matches(csv_path: str | Path, parquet_path: str | Path) -> bool:
    return csv_comparable_rows(csv_path) == parquet_to_comparable_rows(parquet_path)
