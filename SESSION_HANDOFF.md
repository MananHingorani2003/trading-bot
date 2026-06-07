# Session Handoff — AI Trading Terminal

**Snapshot date:** 2026-06-07
**Status:** Live on VPS in paper mode. Week 2 settings just shipped, scheduled to fire Mon 2026-06-08 09:30 IST.

This document is the bridge between sessions. If you're starting a new conversation and need to pick up where this one left off, read this first. For deeper docs see [PLAN.md](PLAN.md) (architecture) and [RUNBOOK.md](RUNBOOK.md) (ops).

---

## 1. Where things live

### Infrastructure
| Thing | Where |
|---|---|
| **VPS** | AWS Lightsail Mumbai 2GB · `65.0.55.227` · Ubuntu 24.04 |
| AWS account | mahijee1603@gmail.com · Paid plan (~$200 credits, ~28 months runway) |
| SSH key (Mac) | `~/.ssh/lightsail-mumbai.pem` |
| GitHub repo | `git@github.com:MananHingorani2003/trading-bot.git` (private) |
| GitHub user | MananHingorani2003 |
| Mac SSH key for GitHub | `~/.ssh/id_ed25519` |
| VPS Deploy key on GitHub | `vps-mumbai`, read-only |

### On the VPS
| Thing | Path |
|---|---|
| Repo root | `/opt/trading-terminal` |
| Owner | `trader` user (no sudo); system tasks as `ubuntu` |
| Production ledger | `/opt/trading-terminal/data/ledger.db` ← **source of truth** |
| Systemd unit | `/etc/systemd/system/trading-bot.service` |
| Logs | `journalctl -u trading-bot.service` |
| Secrets | `/opt/trading-terminal/.env` (perms 600) |

### On the Mac
| Thing | Path |
|---|---|
| Local repo | `~/ClaudeCode_AI_Trading` |
| Local db (sandbox / snapshot from VPS) | `~/ClaudeCode_AI_Trading/data/ledger.db` |
| Backups dir | `~/Backups/ledger-prod-YYYYMMDD.db` |

### External services
| Service | Account / key location |
|---|---|
| Anthropic API | console.anthropic.com (mahijee1603@gmail.com); key in VPS `.env` |
| Telegram bot | created via @BotFather; token + chat_id in VPS `.env` |

---

## 2. What's deployed and running

### Systemd
- `trading-bot.service` `Active: active (running)` as of restart **2026-06-07 12:07:52 UTC**
- Main PID at last check: 64646 (process is `tt`)
- Auto-restarts on crash, auto-starts on VPS reboot
- Logs to journald (not the legacy `/var/log/trading-bot/*.log` files)

### Scheduled jobs (IST timezone)
| When | Job | What |
|---|---|---|
| 08:30 Mon–Fri | `job_briefing()` | Sonnet writes mood briefing → journal + Telegram |
| 09:30 Mon–Fri | `job_run_day()` | Full pipeline: universe → screen → AI → broker |
| Sat/Sun + NSE holidays | (both jobs) | Send `😴 markets closed` Telegram message and exit |

### Telegram alerts
- Pre-market briefing card with mood + body
- Per-fill alerts: `🟢⬆ BUY[cor/agg] SYMBOL N @ ₹X — net ₹Y`
- EOD summary: fills count, buys, sells, realised P&L
- Closed-day note at 08:30 on weekends and holidays
- Error alerts for scheduler failures

---

## 3. Week 1 retrospective (just completed)

### Performance (2026-06-01 → 2026-06-05)
- **1 trade:** BUY FINCABLES ₹1136.57 → SELL ₹1019.79 after 4 days
- **Realised P&L: -₹849.64** (STCG, classified)
- Brokerage paid: ₹32 total
- **AI cost for the week: $0.99 (~₹85)**

### Decision flow audit
| Day | Sonnet CONSIDER | Sonnet WATCH | Opus action |
|---|---|---|---|
| Mon Jun 1 | 1 (FINCABLES) | 7 | BUY FINCABLES |
| Tue Jun 2 | 0 | 8 | SKIP all |
| Wed Jun 3 | 0 | 7 (+1 SKIP) | SKIP all |
| Thu Jun 4 | 0 | 7 (+1 SKIP) | SKIP all |
| Fri Jun 5 | 0 | 7 (+1 SKIP) | EXIT FINCABLES (stop hit) |

### What went right
- Stop-loss capped FINCABLES loss at -10.3% (would've been worse without)
- Opus exit reasoning was textbook: "low-volume caveat flagged at entry has played out badly"
- Audit trail captured every reasoning step
- Sleeve discipline limited single-stock exposure to 16% of capital
- Tax FIFO classification worked (STCG correctly tagged)

### What went wrong
- Opus identified the exact risk (volume at 13% of avg) but bought anyway
- Sonnet's `watch` recommendations were not reaching Opus (fallback-only path)
- 1 trade/week is too few to evaluate strategy
- No volume floor on core sleeve — only aggressive sleeve had it

### Week 2 tune (shipped commit `dddd471`)
Changes for Monday 2026-06-08 onwards:

| Setting | Before | After | Reasoning |
|---|---|---|---|
| `max_positions` | 7 | 8 | Room for 6 core + 2 aggressive |
| `max_pct_per_position` | 18% | 12% | Smaller bets, less per-trade pain |
| `default_stop_loss_pct` | 6% | 5% | Pair with smaller positions |
| `sleeves.core.min_volume_surge` | none | 0.7 | Would have blocked FINCABLES (0.13×) |
| `TopN_AFTER_HAIKU` | 8 | 12 | More candidates reach Sonnet |
| `TopN_AFTER_SONNET` | 5 | 6 | Larger Opus shortlist |
| Watch → Opus | fallback-only | always | Opus is the gatekeeper, let it compare |

Expected: 3-5 trades/week instead of 1; max per-trade loss ~₹300 vs ₹850.

---

## 4. Codebase architecture

### Folder layout
```
src/trading/
├── ai/
│   ├── client.py        # Anthropic wrapper, parse_json_object, cost tracking
│   ├── prompts.py       # System + user prompts per tier
│   └── tiers.py         # haiku_rank, sonnet_analyse, opus_decide, opus_sell_review
├── alerts/
│   └── telegram.py      # Bot API wrapper, halt switch, notify_briefing/fill/eod
├── broker/
│   ├── base.py          # Broker interface (Order, Fill, Position dataclasses)
│   └── paper.py         # PaperBroker — Zerodha cost math + tax lot writes
├── costs/
│   └── zerodha.py       # STT, exchange, SEBI, stamp duty, GST, DP — per-trade
├── data/
│   ├── fundamentals.py  # yfinance .info wrapper, JSON cache + PEG + 52w low
│   ├── prices.py        # yfinance daily OHLC, parquet cache + NaN drop
│   └── universe.py      # NSE Nifty 500 CSV loader (504 symbols)
├── dashboard/
│   ├── app.py           # FastAPI routes (10 pages)
│   └── templates/       # Jinja, dark theme, mobile-friendly
├── intelligence/
│   ├── briefing.py      # Sonnet pre-market mood doc
│   ├── events.py        # yfinance earnings calendar + blackout
│   ├── news.py          # MoneyControl/ET RSS, SQLite-backed 7d retention + FTS5
│   ├── sentiment.py     # Dow/Nasdaq/Nifty/VIX/crude/gold/INR via yfinance
│   └── watchlist.py     # NEW (week 1) — re-evaluate WATCH'd symbols daily
├── knowledge/
│   ├── kb.py            # FTS5 search + structured history retrieval
│   ├── ledger.py        # SQLite access layer (typed inserts)
│   ├── migrations.py    # Forward-only schema migrations (v1→v4)
│   └── schema.sql       # Base tables
├── risk/
│   └── gates.py         # Sleeve-aware pre-trade gates + stop_and_target sizing
├── scheduler/
│   └── runner.py        # APScheduler IST cron + holiday/HALT awareness
├── strategy/
│   ├── indicators.py    # EMA, RSI, ADX, ATR, Bollinger, 52w high, volume surge
│   ├── momentum.py      # Aggressive sleeve — breakout + news + volume
│   ├── screen.py        # Universe filter (price, liquidity, mcap, blacklist)
│   └── trend.py         # Core sleeve — EMA cross + ADX + RSI + 52w high score
├── tax/
│   └── fifo.py          # FIFO lot matching, STCG/LTCG, ₹1.25L exemption
├── backtest/
│   └── runner.py        # Legacy SMA backtest (Phase 0 — not the real pipeline)
├── calendar.py          # NSE holiday + weekend checks (config/holidays.yaml)
├── cli.py               # `tt` CLI — 18 commands
├── orchestrator.py      # run_day() — the whole pipeline in one function
└── settings.py          # Pydantic config + .env loading

config/
├── strategy.yaml        # Sleeve config, risk caps, signal thresholds
├── watchlist.yaml       # Small seed list for fast tests
└── holidays.yaml        # NSE 2025-2026 trading holidays

deploy/
├── README.md            # VPS provisioning + systemd install
└── trading-bot.service  # systemd unit — ExecStart=.venv/bin/tt + PYTHONUNBUFFERED

tests/                   # 13 tests covering costs + tax + indicators
PLAN.md                  # Full architecture + phased rollout plan
RUNBOOK.md               # Daily ops, troubleshooting, fresh-session re-orient
README.md                # Project intro
SESSION_HANDOFF.md       # THIS FILE
```

### CLI commands (all under `tt`)

**Daily ops (used most):**
- `tt sync-prod -d` — pull VPS db → local + open dashboard in browser
- `tt config-check` — verify all secrets are wired
- `tt ai-cost` — Claude spend by tier
- `tt sleeves` — core vs aggressive allocation + usage
- `tt watchlist` — currently active WATCH'd symbols
- `tt tax` — current FY STCG/LTCG sheet
- `tt halt "reason"` / `tt unhalt` — pause/resume

**Manual triggers:**
- `tt brief` — generate today's pre-market brief (writes to db + phone)
- `tt run-day --universe nifty500 --day YYYY-MM-DD` — replay a date
- `tt news-refresh` / `tt news [SYMBOL]` — refresh RSS / per-symbol search
- `tt cues` — global indicators snapshot

**Infra:**
- `tt init-db` — create/migrate ledger
- `tt universe-refresh` — re-fetch Nifty 500 list
- `tt dashboard` — launch UI on `localhost:8000`
- `tt schedule` — run the IST cron forever (used by systemd)
- `tt tg-test "msg"` — send test Telegram

### Schema versions (forward-only migrations)
| Version | Adds |
|---|---|
| v1 | Base: positions, trades, decisions, theses, journals, tax_lots, tax_realisations |
| v2 | `kb_fts` virtual table for decision/thesis/journal full-text search |
| v3 | `headlines` + `headlines_fts` for persistent news store |
| v4 | `positions.sleeve` column ('core' / 'aggressive') |

---

## 5. Configuration locked in this session

### `config/strategy.yaml` (current, after week 1 tune)
```yaml
portfolio:
  capital: 50000          # INR — paper capital
  max_positions: 8        # 6 core + 2 aggressive
  max_pct_per_position: 0.14
  reserve_cash_pct: 0.10

risk:
  default_stop_loss_pct: 0.05
  default_target_r_multiple: 2.0
  daily_loss_halt_pct: 0.03      # NOT WIRED YET — TODO
  weekly_loss_halt_pct: 0.07     # NOT WIRED YET — TODO
  no_average_down: true

sleeves:
  core:
    capital_pct: 0.80
    max_positions: 6
    max_pct_per_position: 0.12
    stop_loss_pct: 0.05
    target_r_multiple: 2.0
    min_volume_surge: 0.7        # NEW: blocks low-participation trades
  aggressive:
    capital_pct: 0.20
    max_positions: 2
    max_pct_per_position: 0.10
    stop_loss_pct: 0.04
    target_r_multiple: 3.0
    min_market_cap_cr: 200
    min_avg_daily_value_cr: 2
    require_news_hours: 48
    breakout_lookback_days: 20
    min_volume_surge: 1.8
    rsi_max: 80
```

### Orchestrator constants (`src/trading/orchestrator.py`)
```python
TopN_AFTER_RULES = 30           # cap on candidates before Haiku
TopN_AFTER_HAIKU = 12           # was 8 — picks fed to Sonnet
TopN_AFTER_SONNET = 6           # was 5 — passed to Opus
MAX_AGGRESSIVE_FROM_RULES = 8
```

### AI token budgets (`src/trading/ai/tiers.py`, `intelligence/briefing.py`)
| Tier | max_tokens | Avg actual use |
|---|---|---|
| Briefing (Sonnet) | 2000 | ~600 |
| Analyse (Sonnet) | 1500 | ~300 |
| Decide (Opus) | 2500 | ~400 |
| Sell review (Opus) | 1500 | ~250 |
| Haiku rank | 300 | ~70 |

---

## 6. Known TODOs / not yet implemented

| Priority | Item | Notes |
|---|---|---|
| Medium | Daily/weekly loss circuit breakers | Designed in `strategy.yaml` but not wired into orchestrator yet. Would cap weekly drawdown at 7%. ~80 lines. |
| Medium | Promoter pledging fetcher | Critical India red flag. Needs screener.in scraping. ~80 lines. |
| Medium | Hard SL-M broker order on Kite | Only relevant once live trading begins. Part of `KiteBroker` implementation. |
| Low | FII/DII feed | NSE blocks scrape with cookie wall. Skip until needed. |
| Low | Replay mode | Already works via `tt run-day --day YYYY-MM-DD`. Could add a cleaner UI. |
| Low | Live `KiteBroker` | Phase 4 entirely. Wait for paper-trading proof first. |

---

## 7. Open architectural decisions

| Decision | Default | Status |
|---|---|---|
| Vector DB for KB | ChromaDB deferred; using SQLite FTS5 | Working fine, not yet needed |
| Markdown rendering in dashboard | Plain `<pre>` text | Could add markdown→HTML later |
| Run-day idempotency | Re-runs are safe (PaperBroker handles duplicates) | Confirmed working |
| Weekend Telegram silence | Currently sends "markets closed" daily | User wants to see it — keep until 3 weeks of trust |

---

## 8. Last gotcha to know

**Token deprecations on Opus 4.x**: `temperature` parameter is rejected by Opus models. We omit it conditionally in `client.py`. If Anthropic deprecates more params, look here first.

**yfinance DUMMY tickers**: NSE Nifty 500 list contains placeholder tickers like `DUMMYVEDL1.NS` for unresolved corporate actions. We blacklist via prefix match in `prices.py:fetch_many()`.

**Sonnet JSON parsing**: Sonnet occasionally wraps responses in arrays, occasionally truncates mid-array. `parse_json_object()` in `client.py` handles both — preferring objects that contain required keys.

**Schema migrations**: Always forward-only. Never destroy or rename. If you need a column rename, add new column + backfill + drop old in two separate releases.

---

## 9. Picking up next session

### Immediate next step (Friday 2026-06-12 evening IST)

Once week 2 has run, sync the prod db and review:

```bash
cd ~/ClaudeCode_AI_Trading
uv run tt sync-prod -d
```

Then look at:
1. `/decisions` — count buy_execs for the week (target: 3-5)
2. `/positions` — what's open
3. `/realised` — sum weekly P&L
4. `/watchlist` — what's actively being monitored

Compare to week 1 (1 trade, -₹850). Decide:
- ✅ 3+ trades with controlled loss → settings work, leave alone, run week 3
- ⚠️ 0-2 trades → consider lifting `min_volume_surge` 0.7 → 0.5, or `max_pct_per_position` 0.12 → 0.15
- 🛑 >3% capital lost in the week → something's broken, audit decisions, possibly halt

### Carry-over context

- The user has explicitly said: keep risk management strict, prefer transparency over results, paper-trade indefinitely until edge is statistically proven (≥30 trades, ≥55% win rate, beats Nifty Bees)
- AWS Free credits expire ~late Oct 2026 — set calendar reminder before then
- The user is on Paid AWS plan, so no 6-month cliff, but credits run out around month 28

### What NOT to do

- Don't keep tuning every session before letting data accumulate. Pattern: **change one thing → wait one full week → evaluate → decide**.
- Don't disable risk gates "to see what happens." The whole point is they're hard caps AI can't override.
- Don't run `tt run-day` locally after `sync-prod` — diverges prod ledger from local.
- Don't commit `.env`. It's gitignored; don't override.

---

## 10. Recent commits (this session)

| SHA | What |
|---|---|
| `dddd471` | Week 1 retrospective tune (latest) |
| `ebe2fd8` | Watchlist dashboard: full Sonnet reasoning, not 200-char excerpt |
| `cd78ae8` | `tt sync-prod --dashboard` flag |
| `829544b` | Watchlist sweep + `tt sync-prod` |
| `2032ace` | `parse_json_object` with required_keys; sonnet/opus token bumps |
| Earlier | NaN drops, NSE holiday calendar, Telegram, scheduler, sleeves, news store, etc. |

To pull all of this on the VPS at any time:
```bash
ssh -i ~/.ssh/lightsail-mumbai.pem ubuntu@65.0.55.227 \
  "sudo -iu trader bash -lc 'cd /opt/trading-terminal && git pull' && \
   sudo systemctl restart trading-bot.service"
```

---

**Last verified working:** 2026-06-07 12:07 UTC. Bot is running. Next scheduled fire: Monday 2026-06-08 08:30 IST.
