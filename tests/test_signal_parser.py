from trading.intelligence.signals.parser import parse


def test_buy_signal_happy_path():
    text = """📈 STOCKS | 📦 DELIVERY

🟢 BUY WOCKPHARMA

💰 Entry Range: ₹1880.00 - ₹1888.00
🎯 Target: ₹2100.00
🛑 Stop Loss: ₹1690.00
⏳ Valid for: 30 days
⏰ 09 Jun 2026, 01:46 PM"""
    p = parse(text)
    assert p.kind == "buy", p.reason
    assert p.buy is not None
    assert p.buy.symbol == "WOCKPHARMA"
    assert p.buy.entry_low == 1880.0
    assert p.buy.entry_high == 1888.0
    assert p.buy.target == 2100.0
    assert p.buy.stop_loss == 1690.0
    assert p.buy.valid_days == 30


def test_fno_message_rejected():
    text = """📊 F&O | 📦 DELIVERY
🟢 BUY M&M PE 3020
💰 Entry Range: ₹82.00 - ₹84.00"""
    p = parse(text)
    assert p.kind == "rejected"
    assert "F&O" in p.reason


def test_update_book_profit():
    text = """Update on FEDERALBNK
Book Profit at 315.00 (5.70% profit)"""
    p = parse(text)
    assert p.kind == "update"
    assert p.symbol == "FEDERALBNK"
    assert p.update.side == "book_profit"
    assert p.update.exit_price == 315.0


def test_update_book_loss():
    text = """Update on RELIANCE
Book Loss at 1280.00 (-2.1%)"""
    p = parse(text)
    assert p.kind == "update"
    assert p.update.side == "book_loss"
    assert p.update.exit_price == 1280.0


def test_missing_header_rejected():
    text = """🟢 BUY WOCKPHARMA
Entry Range: ₹1880-1888
Target: 2100  Stop Loss: 1690"""
    p = parse(text)
    assert p.kind == "rejected"


def test_buy_without_targets_unknown():
    text = """📈 STOCKS | 📦 DELIVERY
🟢 BUY HDFCBANK"""
    p = parse(text)
    assert p.kind == "unknown"
    assert p.symbol == "HDFCBANK"


def test_garbage_does_not_execute():
    """Any non-actionable text returns either 'rejected' or 'unknown' —
    both result in 'do not place an order' which is the safe behavior."""
    assert parse("hi how are you").kind in ("rejected", "unknown")
    assert parse("").kind == "unknown"
    assert parse("   ").kind == "unknown"


def test_valid_for_weeks():
    text = """📈 STOCKS | 📦 DELIVERY
🟢 BUY INFY
💰 Entry Range: ₹1500 - ₹1510
🎯 Target: ₹1650
🛑 Stop Loss: ₹1450
⏳ Valid for: 2 weeks"""
    p = parse(text)
    assert p.kind == "buy"
    assert p.buy.valid_days == 14
