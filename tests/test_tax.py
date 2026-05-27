from datetime import date

from trading.knowledge.ledger import connect, init_db
from trading.tax.fifo import fy_for, fy_summary, realise_sell_fifo, record_buy_lot


def _setup(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    return db


def _fake_buy_trade(conn, symbol, qty, price, ts):
    cur = conn.execute(
        """INSERT INTO trades (ts, symbol, side, qty, price, gross_value, total_costs, net_value)
           VALUES (?,?, 'BUY', ?, ?, ?, 0, ?)""",
        (ts, symbol, qty, price, qty * price, qty * price),
    )
    return int(cur.lastrowid)


def _fake_sell_trade(conn, symbol, qty, price, ts):
    cur = conn.execute(
        """INSERT INTO trades (ts, symbol, side, qty, price, gross_value, total_costs, net_value)
           VALUES (?,?, 'SELL', ?, ?, ?, 0, ?)""",
        (ts, symbol, qty, price, qty * price, qty * price),
    )
    return int(cur.lastrowid)


def test_fy_for():
    assert fy_for(date(2026, 3, 31)) == "FY2025-26"
    assert fy_for(date(2026, 4, 1))  == "FY2026-27"
    assert fy_for(date(2026, 5, 27)) == "FY2026-27"


def test_fifo_stcg(tmp_path):
    db = _setup(tmp_path)
    with connect(db) as conn:
        b = _fake_buy_trade(conn, "TCS", 10, 1000.0, "2026-05-01T10:00:00")
        record_buy_lot(conn, symbol="TCS", buy_trade_id=b, buy_date="2026-05-01",
                       qty=10, price=1000.0, buy_costs_total=50.0)
        s = _fake_sell_trade(conn, "TCS", 10, 1100.0, "2026-08-01T10:00:00")
        rs = realise_sell_fifo(conn, symbol="TCS", sell_trade_id=s,
                               sell_date="2026-08-01", qty=10, price=1100.0,
                               sell_costs_total=40.0)
    assert len(rs) == 1
    r = rs[0]
    assert r.gain_type == "STCG"
    # buy cost = 10*1000 + 50 = 10050 ; proceeds = 10*1100 - 40 = 10960 ; pnl = 910
    assert r.realised_pnl == 910.0


def test_fifo_split_lot_and_ltcg(tmp_path):
    db = _setup(tmp_path)
    with connect(db) as conn:
        b1 = _fake_buy_trade(conn, "INFY", 5, 1000.0, "2025-01-01T10:00:00")
        record_buy_lot(conn, symbol="INFY", buy_trade_id=b1, buy_date="2025-01-01",
                       qty=5, price=1000.0, buy_costs_total=10.0)
        b2 = _fake_buy_trade(conn, "INFY", 10, 1200.0, "2026-03-01T10:00:00")
        record_buy_lot(conn, symbol="INFY", buy_trade_id=b2, buy_date="2026-03-01",
                       qty=10, price=1200.0, buy_costs_total=20.0)

        s = _fake_sell_trade(conn, "INFY", 8, 1500.0, "2026-05-01T10:00:00")
        rs = realise_sell_fifo(conn, symbol="INFY", sell_trade_id=s,
                               sell_date="2026-05-01", qty=8, price=1500.0,
                               sell_costs_total=24.0)

    assert len(rs) == 2
    # First 5 from older lot (held >1y) -> LTCG
    assert rs[0].qty == 5 and rs[0].gain_type == "LTCG"
    # Next 3 from newer lot -> STCG
    assert rs[1].qty == 3 and rs[1].gain_type == "STCG"


def test_fy_summary_applies_ltcg_exemption(tmp_path):
    db = _setup(tmp_path)
    with connect(db) as conn:
        b1 = _fake_buy_trade(conn, "X", 1, 100.0, "2024-01-01T10:00:00")
        s1 = _fake_sell_trade(conn, "X", 1, 100.0, "2026-05-01T10:00:00")
        b2 = _fake_buy_trade(conn, "Y", 1, 100.0, "2026-01-01T10:00:00")
        s2 = _fake_sell_trade(conn, "Y", 1, 100.0, "2026-05-01T10:00:00")
        conn.execute(
            """INSERT INTO tax_realisations
               (sell_trade_id, buy_trade_id, symbol, qty, buy_date, sell_date,
                holding_days, buy_cost_total, sell_proceeds, realised_pnl, gain_type, fy)
               VALUES (?, ?, 'X', 1, '2024-01-01', '2026-05-01', 851, 0, 0, 200000, 'LTCG', 'FY2026-27')""",
            (s1, b1),
        )
        conn.execute(
            """INSERT INTO tax_realisations
               (sell_trade_id, buy_trade_id, symbol, qty, buy_date, sell_date,
                holding_days, buy_cost_total, sell_proceeds, realised_pnl, gain_type, fy)
               VALUES (?, ?, 'Y', 1, '2026-01-01', '2026-05-01', 120, 0, 0, 50000, 'STCG', 'FY2026-27')""",
            (s2, b2),
        )
        s = fy_summary(conn, "FY2026-27")

    assert s.stcg_gain == 50000
    assert s.ltcg_gain == 200000
    assert s.ltcg_exempt == 125000
    assert s.ltcg_taxable == 75000
    assert s.stcg_tax == 10000.0           # 50k * 20%
    assert s.ltcg_tax == 9375.0            # 75k * 12.5%
    assert s.total_tax == 19375.0
