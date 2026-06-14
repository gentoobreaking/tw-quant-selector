from tw_quant_selector.strategies.base import list_strategies, get_strategy


def test_all_strategies_registered():
    names = list_strategies()
    assert "momentum" in names
    assert "value" in names
    assert "quality" in names
    assert "growth" in names


def test_each_strategy_has_name():
    for name in list_strategies():
        s = get_strategy(name)
        assert s.name == name


def test_each_strategy_required_data():
    for name in list_strategies():
        s = get_strategy(name)
        data = s.get_required_data()
        assert isinstance(data, list)
        assert len(data) > 0


def test_momentum_default_params():
    s = get_strategy("momentum")
    assert s.lookback_long == 252
    assert s.lookback_short == 22
    assert s.min_data_days == 252


def test_value_default_params():
    s = get_strategy("value")
    assert s.max_pb == 30
    assert s.max_pe == 100


def test_quality_default_params():
    s = get_strategy("quality")
    assert s.roe_weight == 0.5
    assert s.leverage_weight == 0.3
    assert s.stability_weight == 0.2


def test_growth_default_params():
    s = get_strategy("growth")
    assert s.rev_weight == 0.6
    assert s.eps_weight == 0.4
    assert s.rev_months == 3
