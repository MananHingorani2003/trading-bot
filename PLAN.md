# AI Trading Terminal — Build Plan

**Owner:** Manan · **Capital target:** ₹50,000 · **Style:** Swing (days–weeks) · **Broker:** Zerodha (Kite Connect) · **Risk:** Moderate

---

## 0. Reality check (read first)

This is not "AI prints money." Be clear-eyed:

- Most retail algo traders lose money. AI reasoning doesn't fix a bad strategy — it just dresses it up.
- ₹50k is small. Brokerage + STT + GST + slippage = **~0.3–0.6% per round trip** in cash equity. You'll do ~15–25 round trips/month on swing → ~5–10% annual drag before the strategy even fires.
- Fixed monthly costs (~₹3.5–6k) on ₹50k = **~8–14% of capital/yr** burnt on infra alone. You need to clear that *before* you've made a rupee.
- Backtests almost always look better than live (overfitting + optimistic fills).
- Indian short-term capital gains tax is now **20%** — high turnover crushes after-tax returns. Swing > intraday for this reason alone.

**Implication:** the system is worth building because it's interesting, automated, and you'll learn a ton. Treat the first 6 months as tuition, not income. If after paper-trading 4 weeks the simulated returns don't beat a plain Nifty Bees buy-and-hold by a clear margin, **the right answer is to keep the money in an index fund and run this for fun**.

---

## 1. Architecture

```
trading_terminal/
├── core/
│   ├── broker/          # Kite Connect adapter + paper broker (same interface)
│   ├── data/            # OHLC, fundamentals, corporate actions
│   ├── strategy/        # Signal generators — technical + fundamental
│   ├── risk/            # Position sizing, stops, exposure caps, kill switch
│   ├── ai/              # Tiered Claude layer (Haiku → Sonnet → Opus)
│   └── execution/       # Order router, retries, idempotency, reconciliation
├── intelligence/
│   ├── news/            # MoneyControl RSS, ET Markets, company-specific feeds
│   ├── sentiment/       # SGX Nifty, Dow, crude, USD/INR, India VIX, FII/DII
│   └── events/          # Earnings calendar, dividends, splits, board meetings
├── knowledge/
│   ├── vector_store/    # ChromaDB — semantic recall of past reasoning
│   ├── ledger.db        # SQLite — positions, trades, decisions, theses
│   └── journals/        # Daily pre-market briefs + EOD postmortems
├── scheduler/           # APScheduler jobs on IST market hours
├── dashboard/           # FastAPI + HTMX — read-only monitoring UI
├── alerts/              # Telegram bot — push + kill switch commands
└── config/
    ├── .env             # API keys (gitignored)
    ├── watchlist.yaml   # Universe (Nifty 500 minus blacklist)
    └── strategy.yaml    # Tunables — versioned with git SHA
```

**Key design rules:**
- `Broker` is an interface. `PaperBroker` and `KiteBroker` are interchangeable. You can flip between paper and live with one env var.
- Every decision (buy/sell/hold/skip) is persisted with its full reasoning, the market snapshot it saw, and the strategy version. Replay mode can re-run any day from logs.
- AI is a *layer*, not the strategy. Rules-based signals propose; AI vets and contextualizes.

---

## 2. Daily decision loop (IST)

| Time | Job | Cost tier |
|---|---|---|
| 08:00 | Fetch overnight: global cues (Dow close, SGX Nifty, crude, USD/INR), India VIX, FII/DII flows, overnight news on holdings | Haiku |
| 08:30 | **Pre-market briefing** — Claude (Sonnet) writes a 1-page "market mood" doc + flags positions at risk + flags watchlist names with news. Saved to KB, sent to Telegram. | Sonnet |
| 09:15 | Market opens — bot waits, does not trade in first 15 min (volatility) |  |
| 09:30 | Universe scan: technical filter (200 → 30 candidates) | Rules + Haiku |
| 09:45 | Per-candidate analysis: technical setup + fundamentals + news scan (30 → 5 shortlist) | Sonnet |
| 10:00 | **Final buy decisions** — Opus reasons on shortlist + portfolio fit + risk budget. Writes full thesis to KB. | Opus |
| 11:30, 13:30 | Position monitor — check stops/targets, re-evaluate if news breaks | Haiku, escalate if needed |
| 14:45 | **Sell decisions** — Opus reviews each holding: original thesis + intervening notes + current setup. Decide hold/trim/exit. | Opus |
| 15:25 | Final 10-min window — execute pending exits |  |
| 15:35 | **EOD journal** — Sonnet writes day summary, P&L attribution, lessons. Saved to KB. | Sonnet |
| 21:00 | Post-market news sweep — flag anything for tomorrow's brief | Haiku |

**Estimated Claude calls/day:** ~3 Opus, ~5 Sonnet, ~30 Haiku → ~$0.50–1.50/day = **~$15–40/month**.

---

## 3. Knowledge base (the "why did we buy this?" system)

Three layers:

**a) SQLite ledger (`ledger.db`)** — the source of truth
```
positions(id, symbol, qty, avg_price, opened_at, status, thesis_id)
trades(id, symbol, side, qty, price, ts, order_id, decision_id)
decisions(id, ts, kind, symbol, action, strategy_version, reasoning_md, market_snapshot_json)
theses(id, position_id, summary_md, target, stop, time_horizon, catalysts_json)
journals(date, premarket_md, eod_md, pnl, notes)
```

**b) ChromaDB vector store** — semantic recall
- Every `decision.reasoning_md`, `thesis.summary_md`, and `journal` chunk is embedded.
- Before any buy/sell, AI retrieves: prior decisions on the same symbol, prior decisions on similar setups (sector, technical pattern), recent journal entries that mentioned the symbol.
- This is exactly your "bought day 1, sell day 30" use case: the sell prompt is *always* preceded by the original buy thesis + every interim note about the position.

**c) Journals folder** — human-readable markdown
- `journals/2026-05-27-premarket.md`, `journals/2026-05-27-eod.md`
- You can read these on your phone while commuting. This is how you stay in the loop without micromanaging.

---

## 4. Risk controls (non-negotiable)

Hard-coded, can't be overridden by AI:

- **Max 5–7 concurrent positions** (focus > diversification at this size)
- **Max 18% capital per position** at entry
- **Hard stop-loss as a broker SL-M order** the moment a buy fills — not just an "intent" the AI tracks
- **Default stop:** 5–7% below entry (ATR-adjusted) · **Default target:** 2× the risk (R:R ≥ 2)
- **Daily loss circuit-breaker:** if portfolio drops 3% in a day → halt all new entries until next day
- **Weekly loss circuit-breaker:** -7% in a week → halt for 3 trading days, force a postmortem
- **No averaging down ever** (the #1 retail account-killer)
- **No F&O, no penny stocks, no stocks <₹50 price, no stocks <₹500cr market cap** for v1
- **Stock blacklist:** anything in F&O ban list, anything with recent corp action surprises, anything circuit-locked in last 5 sessions
- **Telegram kill switch:** `/halt` command squares off everything and disables the bot
- **Reconciliation:** every 30 min, fetch positions from Kite and diff against local ledger. Mismatch → alert + halt.

---

## 5. Cost model (realistic, monthly)

| Item | Cost (INR) | Cost (USD) | Notes |
|---|---|---|---|
| Kite Connect API | ₹2,000 | ~$24 | Required for any auto-trading |
| Claude API (tiered, swing) | ₹1,200–3,300 | $15–40 | Scales with activity |
| VPS — Hetzner CX22 or AWS t4g.small Mumbai | ₹400–800 | $5–10 | Mumbai region for latency to Zerodha |
| News data (free tier) | ₹0 | $0 | RSS + scraping covers most |
| Telegram bot | ₹0 | $0 | |
| Domain (optional) | ₹40 | $0.50 | If you want a dashboard URL |
| **Total fixed** | **₹3,640–6,140** | **~$45–75** | |

**Important:** Claude Pro ($23) is for the *chat product* — it does **not** give you API credits. For the bot, you pay the API separately (per-token). You can keep Pro for yourself if you use Claude Code, but it's not part of the bot's running cost.

**Annual fixed cost:** ~₹45k–75k. **On ₹50k capital this is ~10% drag.** You need >10% annual returns *gross* just to break even on infrastructure. Reasonable for a year-1 swing strategy but not trivial.

**One-time:**
- VPS setup time: 1 evening
- Kite Connect app registration: 1 day approval

---

## 6. Phased build & rollout

### Phase 0 — Scaffolding (Week 1, ~10 hrs)
- Repo structure, Python 3.12, `uv` for deps, `ruff` + `pytest`
- SQLite schema + migrations
- `Broker` interface + `PaperBroker` mock with realistic slippage
- Backtest harness — replay 2 years of Nifty 500 daily data
- **Deliverable:** can backtest a dumb SMA crossover strategy end-to-end

### Phase 1 — Strategy + AI layer (Week 2–3, ~20 hrs) — **DONE 2026-05-27**
- ✅ Trend strategy (EMA20+EMA50+ADX14+RSI14+ATR14 scoring); mean-revert deferred to Phase 2
- ✅ Fundamentals via yfinance .info (P/E, ROE, debt/equity, mcap, growth) — cached weekly
- ✅ Universe screener: liquidity (avg daily value cr), price floor, mcap floor, blacklist
- ✅ Tiered Claude layer: Haiku rank → Sonnet analyse → Opus decide
- ✅ Graceful degradation when no `ANTHROPIC_API_KEY` (rules-only fallback throughout)
- ✅ KB via SQLite FTS5 (structured retrieval + keyword search); ChromaDB deferred — not needed yet
- ✅ Decision logging end-to-end with token+cost tracking
- ✅ `tt run-day` orchestrator
- **Delivered:** `uv run tt run-day --universe nifty500` runs the full pipeline.

### Phase 2 — Intelligence layer (Week 4, ~10 hrs) — **DONE 2026-05-27**
- ✅ News fetchers (MoneyControl + ET Markets RSS via feedparser, hourly cache)
- ✅ Global cues (Dow, Nasdaq, Nifty 50, India VIX, crude, gold, USD/INR via yfinance)
- ✅ Earnings blackout (yfinance calendar; 2-day window blocks new buys; flags holdings)
- ✅ Pre-market briefing (Sonnet) — writes journal row + KB entry + decision row
- ✅ News + mood injected into Sonnet analyse and Opus decide
- ✅ Opus thesis-aware sell reasoner — invoked only when rules flag a position (saves $)
- ✅ Dashboard: /briefings + /thesis/{symbol} routes
- ✅ CLI: `tt brief`, `tt cues`, `tt ai-cost`
- ⏸  FII/DII (NSE) — deferred, cookie handshake annoyance

### Phase 2.5 — Prod paper hosting + Telegram (DONE 2026-05-27)
- ✅ APScheduler IST cron: 08:30 brief, 09:30 run-day, Mon–Fri only
- ✅ NSE holiday calendar (`config/holidays.yaml` 2025-26) — scheduler + run_day skip on holidays
- ✅ Telegram alerts: briefing, fills, EOD summary, errors
- ✅ HALT kill switch (file flag checked at run_day start)
- ✅ `tt schedule | tg-test | halt | unhalt | news | news-refresh`
- ✅ `deploy/trading-bot.service` systemd unit
- ✅ `deploy/README.md` — full VPS setup, costs, kill switch, backups
- ✅ TA additions: 52w high proximity + volume surge → trend score
- ✅ Fundamentals additions: PEG ratio + 52w-low proximity
- ✅ Schema v3: persistent headlines table + FTS5; 7-day retention; per-symbol news via ticker + company name
- ✅ Dashboard `/news` route with search form

### Phase 3 — Paper trade live (Week 5–8, 4 weeks)
- Run on real market data, simulated execution
- **Measure:** win rate, avg win/loss, max drawdown, Sharpe, vs Nifty 50 buy-hold
- Daily review of decisions — is the AI reasoning sane? Is it overtrading?
- Tune. Iterate. Don't ship yet.
- **Graduation criteria to go live:**
  - ≥50% win rate over ≥30 trades
  - Max drawdown ≤8%
  - Net (after simulated costs) beats Nifty 50 buy-hold for the period
  - You've read every EOD journal and feel you understand what it's doing

### Phase 4 — Live, small (Week 9–10, ₹10k)
- Subscribe to Kite Connect
- Deploy to VPS (Hetzner Mumbai recommended — ~₹500/mo, ~5ms to Zerodha)
- Start with **₹10k**, not ₹50k. Two weeks live.
- Keep paper bot running in parallel — compare fills/decisions
- Reconciliation alerts on, kill switch tested

### Phase 5 — Scale (Week 11+)
- If P&L tracks paper within reasonable margin → scale to ₹25k, then ₹50k over 2–3 weeks
- If it diverges badly → back to paper, find the gap (slippage? data lag? AI confidence drift?)

---

## 7. Things I'd add that you didn't ask for

- **Replay mode** — re-run any past day from stored logs/snapshots. Essential for debugging "why did it buy that?"
- **Strategy version pinning** — every decision tagged with git SHA + strategy.yaml hash. When you change params, old decisions stay attributable.
- **"Why am I in this trade?" Telegram command** — `/why TCS` → bot replies with the original thesis + every note since.
- **Drift detector** — if Claude's reasoning style/confidence changes notably week-over-week (e.g. new model release), flag for review.
- **Earnings blackout** — auto-exit or no-new-entry 2 days before any holding's earnings (earnings moves are not what the strategy is trained on).
- **Liquidity check at order time** — abort if avg 20-day volume × price < 50× our order size.
- **Order idempotency** — every order has a client-side ID; if the bot crashes mid-place, restart doesn't double-fire.
- **Read-only dashboard** — FastAPI + HTMX page on `localhost:8000` (then VPS) showing positions, P&L, last 50 decisions, KB search. No write actions from the UI — safer.

---

## 8. What I am NOT going to do (and you shouldn't ask for)

- **Intraday/scalping** — wrong tool, wrong capital size, wrong cost structure.
- **F&O / options** — the loss tail is unbounded; AI has no business there at this stage.
- **Sentiment-only trades** — "Twitter says rocket" is not a signal. News is a *filter*, not a *trigger*.
- **Letting AI override hard risk limits** — even if Opus is 99% sure, the 18%-per-position cap holds.
- **Pump-and-dump / SME / illiquid stocks** — blacklisted.
- **Hiding losses** — every loss is journaled. Painful, necessary.

---

## 9. Decisions locked in (2026-05-27)

1. **Universe:** Nifty 500 (will require tighter filtering — see liquidity/blacklist rules in §4)
2. **News:** Free RSS / scraping for v1 (MoneyControl, ET Markets, NSE announcements)
3. **Dashboard:** Built in Phase 0 alongside the ledger. Must show post-cost P&L per trade and live tax sheet.
4. **Tax tracking:** FIFO tax-lot tracker built in from day one. STCG 20%, LTCG 12.5% (post-Jul 2024), ₹1.25L LTCG exemption.

**P&L definition:** All "profit" numbers in the system are **net of brokerage + STT + exchange + SEBI + stamp duty + GST + DP charges**. Tax is shown separately in the tax sheet. No "gross P&L" surfaces in the UI — too easy to fool yourself.

---

## 10. Next step

If this plan looks right, say go and I'll start Phase 0:
1. Set up the repo structure
2. Add `pyproject.toml` with deps (kiteconnect, pandas, chromadb, fastapi, apscheduler, anthropic, python-telegram-bot)
3. Define the SQLite schema
4. Build the `Broker` interface + `PaperBroker`
5. Wire up a trivial SMA backtest so you can see the loop working end-to-end

That should fit in one or two sessions. Then we hit Phase 1.
