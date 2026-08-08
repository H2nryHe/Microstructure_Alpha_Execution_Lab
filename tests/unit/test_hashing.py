from microalpha.utils.hashing import hash_config


def test_identical_config_dicts_produce_identical_hashes() -> None:
    left = {"model": {"seed": 42, "features": ["ofi", "queue_imbalance"]}}
    right = {"model": {"features": ["ofi", "queue_imbalance"], "seed": 42}}

    assert hash_config(left) == hash_config(right)


def test_different_config_values_produce_different_hashes() -> None:
    baseline = {"model": {"seed": 42}}
    changed = {"model": {"seed": 43}}

    assert hash_config(baseline) != hash_config(changed)
