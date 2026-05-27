-- Trading terminal ledger schema. All amounts in INR (rupees).
-- Costs and tax tracked in paise-precision via REAL; UI rounds to ₹.

CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT    NOT NULL,
    qty          INTEGER NOT NULL,
    avg_price    REAL    NOT NULL,
    opened_at    TEXT    NOT NULL,
    closed_at    TEXT,
    status       TEXT    NOT NULL CHECK (status IN ('open','closed')),
    thesis_id    INTEGER REFERENCES theses(id),
    strategy_ver TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);

CREATE TABLE IF NOT EXISTS trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT    NOT NULL,
    symbol         TEXT    NOT NULL,
    side           TEXT    NOT NULL CHECK (side IN ('BUY','SELL')),
    qty            INTEGER NOT NULL,
    price          REAL    NOT NULL,
    gross_value    REAL    NOT NULL,
    brokerage      REAL    NOT NULL DEFAULT 0,
    stt            REAL    NOT NULL DEFAULT 0,
    exchange_txn   REAL    NOT NULL DEFAULT 0,
    sebi_charges   REAL    NOT NULL DEFAULT 0,
    stamp_duty     REAL    NOT NULL DEFAULT 0,
    gst            REAL    NOT NULL DEFAULT 0,
    dp_charges     REAL    NOT NULL DEFAULT 0,
    total_costs    REAL    NOT NULL DEFAULT 0,
    net_value      REAL    NOT NULL,
    order_id       TEXT,
    decision_id    INTEGER REFERENCES decisions(id),
    position_id    INTEGER REFERENCES positions(id)
);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_ts ON trades(symbol, ts);

CREATE TABLE IF NOT EXISTS decisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT    NOT NULL,
    kind             TEXT    NOT NULL,    -- 'premarket','scan','buy','sell','hold','skip','journal'
    symbol           TEXT,                 -- nullable for portfolio-level
    action           TEXT,                 -- 'BUY','SELL','HOLD','SKIP'
    strategy_version TEXT    NOT NULL,
    git_sha          TEXT,
    reasoning_md     TEXT    NOT NULL,
    market_snapshot  TEXT,                 -- JSON
    ai_tier          TEXT,                 -- 'rules','haiku','sonnet','opus'
    tokens_in        INTEGER DEFAULT 0,
    tokens_out       INTEGER DEFAULT 0,
    cost_usd         REAL    DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol_ts ON decisions(symbol, ts);

CREATE TABLE IF NOT EXISTS theses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id   INTEGER REFERENCES positions(id),
    summary_md    TEXT    NOT NULL,
    target_price  REAL,
    stop_price    REAL,
    time_horizon  TEXT,                    -- e.g. '2w','3m'
    catalysts     TEXT,                    -- JSON list
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS journals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL UNIQUE,
    premarket   TEXT,
    eod         TEXT,
    realised_pnl REAL DEFAULT 0,
    notes       TEXT
);

-- FIFO tax lots. Every BUY creates a lot; SELL consumes lots in date order
-- and writes one row to tax_realisations per consumed lot.
CREATE TABLE IF NOT EXISTS tax_lots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    buy_trade_id    INTEGER NOT NULL REFERENCES trades(id),
    buy_date        TEXT    NOT NULL,
    qty_remaining   INTEGER NOT NULL,
    qty_original    INTEGER NOT NULL,
    buy_price       REAL    NOT NULL,
    buy_costs_per_share REAL NOT NULL      -- amortised buy-side costs
);
CREATE INDEX IF NOT EXISTS idx_lots_symbol ON tax_lots(symbol, buy_date);

CREATE TABLE IF NOT EXISTS tax_realisations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sell_trade_id   INTEGER NOT NULL REFERENCES trades(id),
    buy_trade_id    INTEGER NOT NULL REFERENCES trades(id),
    symbol          TEXT    NOT NULL,
    qty             INTEGER NOT NULL,
    buy_date        TEXT    NOT NULL,
    sell_date       TEXT    NOT NULL,
    holding_days    INTEGER NOT NULL,
    buy_cost_total  REAL    NOT NULL,      -- price*qty + amortised buy costs
    sell_proceeds   REAL    NOT NULL,      -- price*qty - amortised sell costs
    realised_pnl    REAL    NOT NULL,      -- proceeds - cost (POST all charges)
    gain_type       TEXT    NOT NULL CHECK (gain_type IN ('STCG','LTCG')),
    fy              TEXT    NOT NULL       -- e.g. 'FY2026-27'
);
CREATE INDEX IF NOT EXISTS idx_realisations_fy ON tax_realisations(fy, gain_type);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('version', '1');
