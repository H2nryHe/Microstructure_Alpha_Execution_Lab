from microalpha.manifest import build_run_manifest


def test_manifest_contains_required_phase0_fields() -> None:
    config = {
        "experiment": {
            "experiment_id": "unit",
            "data_snapshot": "synthetic-none",
            "instrument": "BTC-USDT",
            "date_range": {"start": None, "end": None},
            "feature_version": "v0",
            "label_version": "v0",
            "model_version": "v0",
            "execution_version": "v0",
            "random_seed": 42,
        }
    }

    manifest = build_run_manifest(
        config=config,
        repo_dir=".",
        timestamp="2026-08-08T00:00:00+00:00",
    ).to_dict()

    assert set(manifest) == {
        "run_id",
        "timestamp",
        "git_commit",
        "config_hash",
        "data_snapshot",
        "instrument",
        "date_range",
        "feature_version",
        "label_version",
        "model_version",
        "execution_version",
        "random_seed",
    }
    assert manifest["random_seed"] == 42
