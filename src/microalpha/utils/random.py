"""Randomness control."""

from __future__ import annotations

import os
import random


def set_random_seed(seed: int) -> None:
    """Set deterministic seeds for standard libraries available in Phase 0."""

    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    try:
        import numpy as np
    except ImportError:
        return
    np.random.seed(seed)
