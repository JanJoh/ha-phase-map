"""The phase suggestion algorithm on synthetic data."""

import random

from custom_components.phase_map.detection import analyse


def _series(n, start=0.0):
    return {start + i * 300.0: 0.0 for i in range(n)}


def test_load_on_l2_is_found():
    rng = random.Random(1)
    meter, l1, l2, l3 = _series(500), _series(500), _series(500), _series(500)
    on = False
    for i, t in enumerate(sorted(meter)):
        if i % 12 == 0:
            on = not on
        m = 9.0 if on else 0.0
        meter[t] = m
        l1[t] = 3 + rng.gauss(0, 0.8)
        l2[t] = 1 + m + rng.gauss(0, 0.8)
        l3[t] = 2 + rng.gauss(0, 0.8)
    result = analyse(meter, {"L1": l1, "L2": l2, "L3": l3})
    assert result.phase == "L2"
    assert result.confidence == "high"
    assert 0.8 < result.scores["L2"].gain < 1.2


def test_small_load_with_noise():
    rng = random.Random(2)
    meter, l1, l2, l3 = _series(2800), _series(2800), _series(2800), _series(2800)
    for i, t in enumerate(sorted(meter)):
        m = 0.2 if (i // 6) % 2 else 0.0          # a fridge-sized load cycling every 30 minutes
        meter[t] = m
        l1[t] = round(2.5 + m + rng.gauss(0, 0.15), 1)   # main meter reports in 0.1 A steps
        l2[t] = round(0.7 + rng.gauss(0, 0.15), 1)
        l3[t] = round(0.8 + rng.gauss(0, 0.15), 1)
    result = analyse(meter, {"L1": l1, "L2": l2, "L3": l3})
    assert result.phase == "L1"


def test_constant_load_cannot_be_placed():
    meter = {i * 300.0: 0.3 for i in range(300)}
    phases = {p: {i * 300.0: 1.0 for i in range(300)} for p in ("L1", "L2", "L3")}
    result = analyse(meter, phases)
    assert result.phase is None
    assert result.reason in ("too_few_steps", "no_data")


def test_unrelated_load_is_unclear():
    rng = random.Random(3)
    meter, l1, l2 = _series(400), _series(400), _series(400)
    for i, t in enumerate(sorted(meter)):
        meter[t] = 5.0 if (i // 10) % 2 else 0.0
        l1[t] = 3 + rng.gauss(0, 1)
        l2[t] = 1 + rng.gauss(0, 1)
    result = analyse(meter, {"L1": l1, "L2": l2})
    assert result.phase is None
    assert result.reason == "unclear"
