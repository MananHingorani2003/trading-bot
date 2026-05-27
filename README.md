# Trading Terminal

AI-driven swing-trading terminal for Indian equities (Zerodha). See [PLAN.md](PLAN.md) for the full architecture and rollout plan.

**Status:** Phase 2 complete — Nifty 500 universe, technical screen, fundamentals, tiered Claude reasoning (Haiku → Sonnet → Opus), thesis-aware Opus sell reviewer, KB with FTS5 retrieval, risk gates, earnings blackout, **pre-market briefing with global cues + RSS news**, daily orchestrator. Still paper. No live broker yet.

## What works today

- **SQLite ledger** (positions, trades, decisions, theses, journals, FIFO tax lots, realisations). Schema v2 adds FTS5 over the KB content.
- **Zerodha cost calculator** for cash equity delivery — every trade is recorded post-cost.
- **FIFO tax-lot tracker** with STCG (20%) / LTCG (12.5%, ₹1.25L exempt).
- **Paper broker** that applies real costs + tax accounting on every fill.
- **Nifty 500 universe loader** (NSE CSV, cached weekly).
- **Fundamentals fetcher** (yfinance .info; cached weekly; best-effort).
- **Universe screener** — price floor, liquidity floor (₹ avg daily value), market cap floor, blacklist patterns, min bar count.
- **Trend strategy** — EMA(20)/EMA(50) + ADX(14) + RSI(14) + ATR(14)-based scoring.
- **Tiered AI** (`src/trading/ai/`):
  - **Haiku** ranks 30 candidates → top 8
  - **Sonnet** does per-stock analysis with structured JSON output
  - **Opus** makes the final buy call and writes the thesis
  - Every tier logs tokens + cost to the `decisions` table
  - **Graceful degradation**: with no `ANTHROPIC_API_KEY`, falls back to rules-only and continues running
- **Knowledge base** (`kb.py`) — structured retrieval (history per symbol, open position thesis lookup) + FTS5 keyword search across all decisions/theses/journals.
- **Risk gates** — max positions, max % per position, cash reserve floor, no-average-down. AI cannot override.
- **Daily orchestrator** (`orchestrator.run_day`) — single function walks universe → screen → trend → AI → risk → broker → KB writes.
- **FastAPI dashboard** (read-only): overview, positions, trades (with full cost breakdown), realised P&L (post-cost), tax sheet, decisions.

Live trading is **not** built yet. There is no Kite Connect adapter. `MODE=paper` is the only mode.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env          # add ANTHROPIC_API_KEY here if you have one
uv run tt init-db
uv run tt universe-refresh    # fetch latest Nifty 500
```

## Common commands

```bash
uv run tt run-day --universe nifty500           # full daily loop on Nifty 500
uv run tt run-day --universe watchlist          # faster, 15-symbol seed list
uv run tt run-day --day 2026-05-26              # replay a specific date
uv run tt run-day --universe nifty500 --universe-limit 50   # cap for speed

uv run tt brief                                 # standalone pre-market briefing
uv run tt cues                                  # current Dow/Nifty/VIX/crude/INR
uv run tt ai-cost                               # Claude spend per tier
uv run tt news-refresh                          # pull latest RSS into 7d store
uv run tt news RELIANCE                         # per-symbol news (last 7d)
uv run tt news                                  # macro news
uv run tt tg-test "hi"                          # test Telegram
uv run tt halt "lunch break"                    # pause bot; tt unhalt resumes
uv run tt schedule --universe nifty500          # forever; 08:30 brief, 09:30 trade IST
                                                #   skips weekends + NSE holidays
uv run tt kb-search "EMA crossover"             # FTS5 across decisions/theses/journals
uv run tt tax                                   # current-FY tax sheet
uv run tt dashboard                             # http://127.0.0.1:8000
uv run tt backtest --days 365                   # Phase 0 legacy backtest

uv run pytest                                   # 13 tests
```

## Tiered AI in practice

| Tier | Model | Job | When it runs |
|---|---|---|---|
| L0 | rules | Trend candidate scoring | Always |
| L1 | Haiku | Rank 30 → top 8 | If `ANTHROPIC_API_KEY` set |
| L2 | Sonnet | Per-stock analysis (JSON) | On Haiku's top 8 |
| L3 | Opus | Final buy decision + thesis | On Sonnet's recommend list |
| Sell | Opus | Per-position hold/trim/exit | On each open position |

**Without an API key**, the orchestrator runs the same pipeline using rules-only fallbacks — useful for development and for the paper-trading dry-run phase.

**Typical Claude bill per day** (with key): ~3 Opus calls + ~5 Sonnet + ~30 Haiku → roughly $0.30–1.00/day = $10–30/month on swing horizon.

## Layout

```
src/trading/
  ai/
    client.py        # Anthropic wrapper, cost tracking, JSON parser
    prompts.py       # tier templates
    tiers.py         # haiku_rank, sonnet_analyse, opus_decide
  broker/            # Broker interface + PaperBroker
  costs/zerodha.py   # cash-equity charge schedule
  data/
    universe.py      # Nifty 500 loader
    prices.py        # yfinance OHLC (parquet-cached)
    fundamentals.py  # yfinance .info (JSON-cached)
  knowledge/
    schema.sql       # base schema
    migrations.py    # forward-only migrations (FTS5 etc.)
    ledger.py        # typed CRUD
    kb.py            # structured + FTS5 retrieval
  risk/gates.py      # pre-trade gates + stop/target sizing
  strategy/
    indicators.py    # EMA, RSI, ADX, ATR, Bollinger, ADV
    screen.py        # universe screener
    trend.py         # EMA+ADX trend candidates + exit signal
  tax/fifo.py        # FIFO lots + STCG/LTCG
  backtest/runner.py # Phase 0 legacy backtest
  orchestrator.py    # daily pipeline (run_day)
  dashboard/         # FastAPI + Jinja read-only UI
  cli.py             # `tt` CLI

config/
  strategy.yaml      # versioned tunables
  watchlist.yaml     # 15-symbol seed list (tests / quick runs)
tests/               # 13 tests (costs, tax, indicators)
```

## Important notes

- **All P&L surfaces are post-cost.** No gross-profit view, on purpose.
- **Tax sheet is an estimate.** Share trade exports with your CA at FY end.
- **AI cannot override risk gates.** Even Opus at 99% confidence cannot exceed the 18%-per-position cap or bypass no-average-down.
- **Live trading is not built yet.** A `KiteBroker` implementing the same `Broker` interface is Phase 4. Until then, `MODE=paper`.
- See [PLAN.md §0](PLAN.md) before getting excited about any result. The numbers will lie to you.

## Production paper-trading (no Kite needed)

See [deploy/README.md](deploy/README.md) for the full VPS setup. tl;dr:

```bash
uv run tt schedule --universe nifty500    # foreground; 08:30 brief, 09:30 trade IST
uv run tt tg-test "bot up"                # confirm Telegram
uv run tt halt "lunch"                    # kill switch (orchestrator aborts run_day)
uv run tt unhalt
```

`deploy/trading-bot.service` is a hardened systemd unit ready to drop on a VPS. Costs ~$20–40/mo running on AWS Lightsail Mumbai with Claude API on.

## What's next (Phase 3)

1. **EOD journal** — Sonnet writes daily P&L attribution + lessons (mirror of pre-market briefing).
2. **Proper backtest of the Phase-1+2 pipeline** (not just SMA) over 1-2 years to get a real edge estimate before going live.
3. **Portfolio circuit breakers** — wire the `daily_loss_halt_pct` / `weekly_loss_halt_pct` from `strategy.yaml`.
4. **Promoter pledging fetcher** — critical Indian-market red flag; needs scraping screener.in.
5. **Multi-week paper trading** — actually accumulate enough decisions for the system to be evaluable.
