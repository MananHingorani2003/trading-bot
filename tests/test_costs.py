from trading.costs.zerodha import compute_costs, net_cash_flow


def test_buy_costs_smoke():
    cb = compute_costs("BUY", 10, 1000.0)
    # turnover 10,000
    assert cb.brokerage == 0.0
    assert cb.stt == 10.0
    assert cb.dp_charges == 0.0
    assert cb.stamp_duty == 1.5
    assert cb.total > 0


def test_sell_costs_include_dp():
    cb = compute_costs("SELL", 10, 1000.0)
    assert cb.stamp_duty == 0.0
    assert cb.dp_charges == 15.34


def test_net_cash_signs():
    g, n, _ = net_cash_flow("BUY", 10, 1000.0)
    assert n > g       # buy: pay more than gross
    g, n, _ = net_cash_flow("SELL", 10, 1000.0)
    assert n < g       # sell: receive less than gross
