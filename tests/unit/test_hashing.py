import pytest

from microalpha.utils.hashing import hash_config


def test_identical_config_dicts_produce_identical_hashes() -> None:
    left = {"model": {"seed": 42, "features": ["ofi", "queue_imbalance"]}}
    right = {"model": {"features": ["ofi", "queue_imbalance"], "seed": 42}}

    assert hash_config(left) == hash_config(right)


def test_different_config_values_produce_different_hashes() -> None:
    baseline = {"model": {"seed": 42}}
    changed = {"model": {"seed": 43}}

    assert hash_config(baseline) != hash_config(changed)


def test_non_string_mapping_key_fails_before_json_sorting() -> None:
    with pytest.raises(
        ValueError,
        match=(
            r"Non-string YAML mapping key detected at config\.model\.models: None\. "
            r"Quote or rename reserved YAML keys\."
        ),
    ):
        hash_config({"model": {"models": {None: {"enabled": True}}}})
