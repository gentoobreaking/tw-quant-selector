from tw_quant_selector.strategies.combiner import DEFAULT_WEIGHTS


def test_default_weights():
    assert abs(DEFAULT_WEIGHTS["momentum"] - 0.30) < 0.001
    assert abs(DEFAULT_WEIGHTS["value"] - 0.25) < 0.001
    assert abs(DEFAULT_WEIGHTS["quality"] - 0.25) < 0.001
    assert abs(DEFAULT_WEIGHTS["growth"] - 0.20) < 0.001


def test_weights_sum_to_one():
    total = sum(DEFAULT_WEIGHTS.values())
    assert abs(total - 1.0) < 0.001
