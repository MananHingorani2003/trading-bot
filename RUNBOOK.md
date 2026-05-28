# Trading Bot — Operations Runbook

Single page for everything you need when you come back after a few days, want to verify the bot is alive, edit code, or debug. Skim the **Inventory** + **Daily verify** sections first; the rest is reference.

---

## 1. Inventory — everything you set up

### Accounts
| Service | Login | Purpose |
|---|---|---|
| **GitHub** | MananHingorani2003 | code repo `trading-bot` (private) |
| **AWS** | mahijee1603@gmail.com | Lightsail VPS in Mumbai (`trading-bot` instance) |
| **Anthropic** | console.anthropic.com | Claude API key for AI tiers |
| **Telegram** | @BotFather | bot for push alerts + your personal chat ID |

### Keys & files (NEVER commit these)
| What | Where it lives |
|---|---|
| GitHub SSH key (Mac) | `~/.ssh/id_ed25519` (Mac) |
| AWS Lightsail SSH key | `~/.ssh/lightsail-mumbai.pem` (Mac) |
| GitHub Deploy key for VPS | `~/.ssh/id_ed25519` on VPS (added to repo Settings → Deploy keys as `vps-mumbai`) |
| Secrets (Claude key, Telegram token+chat id) | `/opt/trading-terminal/.env` on VPS, `~/ClaudeCode_AI_Trading/.env` on Mac |

### Hosts & paths
| Thing | Value |
|---|---|
| VPS public IP | _(grab from Lightsail console, top of instance page)_ |
| VPS user for system tasks | `ubuntu` (uses `sudo`) |
| VPS user for the bot | `trader` (no sudo, owns the code+db) |
| Repo on VPS | `/opt/trading-terminal` |
| **The ledger database** | `/opt/trading-terminal/data/ledger.db` ← source of truth |
| Systemd service name | `trading-bot.service` |
| Logs | `journalctl -u trading-bot.service` |
| Local dev repo | `~/ClaudeCode_AI_Trading` on your Mac |
| Local dev db (sandbox) | `~/ClaudeCode_AI_Trading/data/ledger.db` ← throwaway |

### Repo URL
```
git@github.com:MananHingorani2003/trading-bot.git
```

---

## 1a. Capital sleeves — core vs aggressive

Your capital is split into two books with **different risk profiles**:

| | Core (80%) | Aggressive (20%) |
|---|---|---|
| Capital | ₹40,000 | ₹10,000 |
| Strategy | Trend-following (EMA20>EMA50 + ADX + RSI) | News-catalyst breakout + volume surge |
| Max positions | 5 | 2 |
| Per-position cap | 18% of total | 10% of total |
| Stop loss | 6% | 4% (tighter — risky setups die fast) |
| Target R-multiple | 2× | 3× (asymmetric reward chase) |
| Min market cap | ₹500cr | ₹200cr (smaller names OK) |
| Min liquidity | ₹5cr avg daily value | ₹2cr |
| News requirement | None | Must have headline in last 48h |
| Holding period | Days–weeks | Days–weeks (typically shorter) |

**Worst-case math** if every aggressive position blows through its stop:
2 positions × 10% capital × -4% = **-0.8% of total capital**. Acceptable damage to chase upside.

Inspect current sleeve usage anytime:
```bash
uv run tt sleeves
```

Positions table in the dashboard now has a `sleeve` column showing which book each holding belongs to.

---

## 1b. Components — what each piece does

The system is **eight cooperating pieces**, all running inside one Python process under systemd.

```
              ┌─────────────────────────────────────────────────────┐
              │                  systemd (Linux)                    │
              │   keeps the process alive, restarts on crash,       │
              │   starts on boot, captures logs to journald         │
              └──────────────────────────┬──────────────────────────┘
                                         │ runs
                                         ▼
              ┌──────────────────────────────────────────────────────┐
              │     Scheduler (APScheduler, IST timezone)            │
              │     Fires two jobs Mon–Fri (skips holidays):         │
              │       08:30 → job_briefing()                         │
              │       09:30 → job_run_day()                          │
              └────────┬─────────────────────────────┬───────────────┘
                       │                             │
            ┌──────────▼──────────┐       ┌─────────▼─────────────────┐
            │  Pre-market         │       │  Orchestrator (run_day)   │
            │  Briefing (Sonnet)  │       │  ─────────────────────    │
            │  writes mood + body │       │  Universe → Screen →      │
            │  to journal+KB,     │       │  Trend signals → AI       │
            │  pushes Telegram    │       │  tiers → Risk gate →      │
            └──────────┬──────────┘       │  PaperBroker → KB write   │
                       │                  └──┬────────────────────┬───┘
                       │ uses                │                    │
                       ▼                     ▼                    ▼
              ┌──────────────┐      ┌────────────────┐   ┌────────────────┐
              │ Intelligence │      │   AI tiers     │   │  Risk gates    │
              │ ──────────   │      │  ───────       │   │  ─────────     │
              │ • Cues       │      │ Rules → Haiku  │   │ max positions, │
              │   (yfinance) │      │ → Sonnet →     │   │ % cap, no-avg- │
              │ • News (RSS) │      │ Opus           │   │ down, earnings │
              │ • Events     │      │ Falls back to  │   │ blackout       │
              │   (earnings) │      │ rules if no    │   │ AI can't       │
              │              │      │ Claude key     │   │ override these │
              └──────┬───────┘      └────────┬───────┘   └────────┬───────┘
                     │                       │                    │
                     └───────────┬───────────┴────────────────────┘
                                 │ all writes through
                                 ▼
                  ┌─────────────────────────────────────┐
                  │    PaperBroker (in-process)         │
                  │  Simulates fills, applies real      │
                  │  Zerodha costs (STT, GST, DP, etc.) │
                  └──────────────────┬──────────────────┘
                                     │
                                     ▼
                  ┌─────────────────────────────────────┐
                  │    Ledger (SQLite, one file)        │
                  │    /opt/trading-terminal/           │
                  │           data/ledger.db            │
                  │                                     │
                  │  Tables:                            │
                  │  • positions, trades                │
                  │  • decisions (every reasoning event)│
                  │  • theses (buy thesis per position) │
                  │  • tax_lots, tax_realisations       │
                  │  • journals (daily briefings)       │
                  │  • headlines + headlines_fts (news) │
                  │  • kb_fts (decision/thesis search)  │
                  └──────────────────┬──────────────────┘
                                     │ read by
                       ┌─────────────┼─────────────────┐
                       ▼             ▼                 ▼
              ┌──────────────┐ ┌──────────┐  ┌──────────────────┐
              │   Dashboard  │ │   CLI    │  │   Telegram       │
              │   (FastAPI)  │ │  (`tt`)  │  │   (push only)    │
              │  Read-only   │ │ commands │  │ briefings, fills,│
              │ port 8000    │ │ in shell │  │ EOD, errors      │
              └──────────────┘ └──────────┘  └──────────────────┘
```

### Component cheat sheet

| Component | Source files | What it owns | Failure mode |
|---|---|---|---|
| **systemd unit** | `deploy/trading-bot.service` | process lifetime, auto-restart, boot start | service shows `failed` → check journalctl |
| **Scheduler** | `src/trading/scheduler/runner.py` | when jobs fire (IST cron) | wrong timezone → bot fires at wrong hours |
| **Orchestrator** | `src/trading/orchestrator.py` | end-to-end pipeline for one trading day | crashes mid-run → next run-day resumes from current state |
| **Briefing** | `src/trading/intelligence/briefing.py` | morning mood doc (Sonnet) | Sonnet parse fails → falls back to rules-only briefing |
| **AI tiers** | `src/trading/ai/{tiers,client,prompts}.py` | Haiku rank → Sonnet analyse → Opus decide/sell | no API key → rules-only path everywhere |
| **News + cues + events** | `src/trading/intelligence/{news,sentiment,events}.py` | RSS pull, global cues, earnings | Yahoo blocks → cues show `nan`, briefing still runs |
| **Risk gates** | `src/trading/risk/gates.py` | hard caps AI cannot override | only blocks bad trades, never crashes |
| **PaperBroker** | `src/trading/broker/paper.py` | simulated fills + Zerodha cost math + tax lots | insufficient cash → ValueError logged as SKIP |
| **Ledger DB** | SQLite at `data/ledger.db` | THE source of truth | corruption → restore from weekly `scp` backup |
| **Dashboard** | `src/trading/dashboard/` | read-only HTTP UI | port collision → use `--port 8001` |
| **Telegram** | `src/trading/alerts/telegram.py` | push notifications, HALT switch | env vars unset → silent no-op; bot runs anyway |

---

## 2. Architecture — what runs where

```
   Your Mac                  GitHub                     Lightsail VPS (Mumbai)
 ──────────────             ─────────                ──────────────────────────
                                                    ┌───────────────────────┐
   edit code      ─push─►   main branch  ─pull──►   │ /opt/trading-terminal │
   `tt` commands             (private)              │  (owned by `trader`)  │
   (dev sandbox)                                    │                       │
                                                    │  systemd runs:        │
                                                    │   tt schedule         │
                                                    │      ↓                │
                                                    │   08:30 IST briefing  │
                                                    │   09:30 IST run-day   │
                                                    │      ↓                │
                                                    │   data/ledger.db ◄────┼── source of truth
                                                    │   data/cache/...      │
                                                    │      ↓                │
                                                    │   Telegram push       │
                                                    │      ↓                │
                                                    └───────┼───────────────┘
                                                            ↓
                                                       your phone
```

**Key invariant:** the VPS's `ledger.db` is the ONLY db that matters once deployed. Your Mac's db is a sandbox you can wipe anytime.

---

## 3. Daily verify — "is the bot alive?" (60 seconds)

From your Mac:

```bash
# 1. SSH in
ssh -i ~/.ssh/lightsail-mumbai.pem ubuntu@<vps-ip>

# 2. Status check
sudo systemctl status trading-bot.service
```

**Healthy output:**
```
● trading-bot.service - AI Trading Terminal (paper mode scheduler)
     Active: active (running) since Wed 2026-05-28 ...
   Main PID: 12345 (uv)
     Memory: 180M
```

**Unhealthy:** if you see `failed` or `inactive`, go to [Troubleshooting §10](#10-troubleshooting).

```bash
# 3. See the next scheduled fire times
sudo journalctl -u trading-bot.service --since "10 minutes ago" | grep "next runs" -A 3
```

**Expected:**
```
scheduler started; next runs:
  briefing: 2026-05-29 08:30:00+05:30
  run_day:  2026-05-29 09:30:00+05:30
```

Times should be tomorrow's date if today is past 09:30, else today's.

```bash
# 4. Tail live logs (Ctrl+C to detach — won't stop the bot)
sudo journalctl -u trading-bot.service -f
```

**Anytime you can close the Terminal app — the bot keeps running.**

---

## 4. Test a full run RIGHT NOW (don't wait until tomorrow)

```bash
sudo -iu trader bash
cd /opt/trading-terminal
uv run tt run-day --universe watchlist --day $(date +%Y-%m-%d)
```

**Expected console output:**
```
[day 2026-05-28] universe size: 15
[day 2026-05-28] mood: risk_on (Dow +0.5%, VIX 14)
[day 2026-05-28] screen passed: 15/15
[day 2026-05-28] trend candidates: 2
                              Run-day 2026-05-28
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ metric            ┃ value                                                   ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Universe size     │ 15                                                      │
│ After screen      │ 15                                                      │
│ Trend candidates  │ 2                                                       │
│ Haiku top picks   │ ASIANPAINT, SUNPHARMA                                   │
│ Sonnet recommends │ ASIANPAINT                                              │
│ Opus buys         │ ASIANPAINT                                              │
│ Sells             │ —                                                       │
│ Fills             │ 1                                                       │
└───────────────────┴─────────────────────────────────────────────────────────┘
```

**Expected on your phone (Telegram):**
1. Pre-market briefing card
2. `🟢⬆ BUY ASIANPAINT  N @ ₹...`  (or skip notifications if Opus passed)
3. EOD summary

This is what tomorrow morning 09:30 IST will look like — except automatic.

---

## 5. Verify Telegram + secrets are wired

```bash
# As trader
uv run tt config-check
```

**All green expected:**
```
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ item               ┃ status     ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ ANTHROPIC_API_KEY  │ set        │
│ AIClient available │ yes        │
│ TELEGRAM_BOT_TOKEN │ set        │
│ TELEGRAM_CHAT_ID   │ set        │
│ Telegram push      │ configured │
│ Mode               │ paper      │
│ Ledger DB          │ /opt/trading-terminal/data/ledger.db │
└────────────────────┴────────────┘
```

Quick standalone Telegram smoke:
```bash
uv run tt tg-test "ping"
# Phone should buzz within 2 sec
```

---

## 6. Browse the dashboard

```bash
# Terminal 1 — from Mac, open the tunnel
ssh -i ~/.ssh/lightsail-mumbai.pem -L 8000:127.0.0.1:8000 ubuntu@<vps-ip>

# Terminal 2 — from Mac, ssh in again
ssh -i ~/.ssh/lightsail-mumbai.pem ubuntu@<vps-ip>
sudo -iu trader bash -lc 'cd /opt/trading-terminal && uv run tt dashboard'

# Browser on Mac: http://localhost:8000
# Pages: /overview /positions /trades /realised /tax /decisions /briefings /news /thesis/SYMBOL

# When done, Ctrl+C in both terminals — dashboard process dies, scheduler keeps running
```

---

## 7. Coming back after a few days — full re-orientation

You closed your laptop a week ago. You want to log in, see what happened, maybe tweak something. Step by step:

### 7a. Find the VPS IP
- Browser → Lightsail console → instance `trading-bot` → copy public IP

### 7b. SSH in (Mac)
```bash
ssh -i ~/.ssh/lightsail-mumbai.pem ubuntu@<vps-ip>
```

### 7c. Quick health check
```bash
sudo systemctl status trading-bot.service   # should say "active (running)"
sudo journalctl -u trading-bot.service --since "yesterday" | tail -50
```

### 7d. See what the bot did this week
```bash
sudo -iu trader
cd /opt/trading-terminal
uv run tt ai-cost                          # Claude spend
sqlite3 data/ledger.db "SELECT date, mood FROM journals ORDER BY date DESC LIMIT 7;"
sqlite3 data/ledger.db "SELECT ts, symbol, side, qty, price FROM trades ORDER BY ts DESC LIMIT 10;"
sqlite3 data/ledger.db "SELECT symbol, qty, avg_price, opened_at FROM positions WHERE status='open';"
```

### 7e. OR browse the dashboard (see §6)

### 7f. Back up the db before doing anything (Mac)
```bash
scp -i ~/.ssh/lightsail-mumbai.pem \
    ubuntu@<vps-ip>:/opt/trading-terminal/data/ledger.db \
    ~/Backups/ledger-$(date +%Y%m%d).db
```

---

## 8. Edit & deploy code changes — the loop

You want to change something — say, the trend strategy thresholds in `config/strategy.yaml`.

### 8a. On your Mac
```bash
cd ~/ClaudeCode_AI_Trading

# Pull anything new (in case you committed from another machine)
git pull

# Edit files
nano config/strategy.yaml          # or use your editor of choice

# Test locally if you want
uv run pytest
uv run tt run-day --universe watchlist     # exercises the new config

# Commit + push
git add -A
git commit -m "tweak: lower ADX threshold to 18"
git push
```

### 8b. On the VPS
```bash
# SSH in
ssh -i ~/.ssh/lightsail-mumbai.pem ubuntu@<vps-ip>

# Pull (as trader, since trader owns the repo)
sudo -iu trader bash -lc 'cd /opt/trading-terminal && git pull && uv sync'
# `uv sync` is only needed if you changed pyproject.toml — harmless otherwise

# Restart so the new code is picked up
exit                                       # back to ubuntu
sudo systemctl restart trading-bot.service
sudo systemctl status trading-bot.service
```

That's the entire edit loop. **The ledger db is never touched.**

---

## 9. Common commands cheat sheet

All run on the VPS as `trader` (after `sudo -iu trader` and `cd /opt/trading-terminal`).

| Command | What it does |
|---|---|
| `uv run tt config-check` | Show whether secrets/services are configured |
| `uv run tt tg-test "ping"` | Send a test Telegram message |
| `uv run tt brief` | Generate today's pre-market briefing (writes to db + phone) |
| `uv run tt cues` | Print global cues (Dow, Nifty, VIX, crude, INR) |
| `uv run tt news` | Last 7 days of macro news |
| `uv run tt news RELIANCE` | Last 7 days of news mentioning RELIANCE |
| `uv run tt news-refresh` | Pull latest RSS into the db |
| `uv run tt run-day --universe watchlist` | Run pipeline on small universe (fast, cheap) |
| `uv run tt run-day --universe nifty500` | Run pipeline on full Nifty 500 |
| `uv run tt run-day --day 2026-05-26` | Replay a past date |
| `uv run tt kb-search "trend"` | FTS5 search through decisions/theses/journals |
| `uv run tt ai-cost` | Claude API spend by tier |
| `uv run tt tax` | Current-FY tax sheet |
| `uv run tt halt "reason"` | Pause all trading (next run_day will abort) |
| `uv run tt unhalt` | Resume |
| `uv run tt universe-refresh` | Re-fetch Nifty 500 list |
| `uv run tt dashboard` | Launch read-only web UI on port 8000 |

System commands (as `ubuntu`, with `sudo`):

| Command | What it does |
|---|---|
| `sudo systemctl status trading-bot.service` | Bot health |
| `sudo systemctl restart trading-bot.service` | Restart (after code update) |
| `sudo systemctl stop trading-bot.service` | Stop (won't auto-restart) |
| `sudo systemctl start trading-bot.service` | Start back up |
| `sudo journalctl -u trading-bot.service -f` | Live log tail |
| `sudo journalctl -u trading-bot.service --since "1 hour ago"` | Recent logs |

---

## 10. Troubleshooting

### Bot is not running (systemctl says `failed`)
```bash
sudo journalctl -u trading-bot.service --since "10 minutes ago" | tail -40
```
Common causes:
- **`.env` missing or unreadable** → `ls -la /opt/trading-terminal/.env` → should be `-rw------- trader trader`
- **Schema mismatch after pulling new code** → `sudo -iu trader bash -lc 'cd /opt/trading-terminal && uv run tt init-db'` to apply pending migrations, then restart
- **Disk full** → `df -h` → if `/` is >90%, prune old snapshots/logs

### Telegram messages stop arriving
```bash
sudo -iu trader bash -lc 'cd /opt/trading-terminal && uv run tt config-check'
sudo -iu trader bash -lc 'cd /opt/trading-terminal && uv run tt tg-test "alive?"'
```
If the second fails, your bot token may have been revoked (check @BotFather), or the chat id changed (e.g. you removed and re-added the bot).

### No fills happening, every run shows 0 buys
Normal on most days if Opus is being conservative. Check:
```bash
sudo -iu trader bash -lc 'cd /opt/trading-terminal && \
  sqlite3 data/ledger.db "SELECT ts, action, ai_tier, substr(reasoning_md,1,120) \
  FROM decisions WHERE ts > date(\"now\", \"-2 days\") ORDER BY ts DESC LIMIT 20;"'
```
You'll see if it's being rejected at screen, Sonnet, Opus, or the risk gate.

### `git pull` fails on VPS
```bash
sudo -iu trader bash -lc 'cd /opt/trading-terminal && ssh -T git@github.com'
# Should say: "Hi MananHingorani2003/trading-bot! You've successfully authenticated"
```
If permission denied: the VPS's deploy key may have been removed from the repo settings. Re-add `~/.ssh/id_ed25519.pub` (on VPS) as a deploy key at https://github.com/MananHingorani2003/trading-bot/settings/keys.

### Bot kept running but missed a day (no briefing/run-day)
```bash
cat /opt/trading-terminal/data/HALT   # if file exists, HALT was set
```
Resume with `uv run tt unhalt` if so. Otherwise check whether it was an NSE holiday (`config/holidays.yaml`).

### Run an old day to debug ("what would have happened on May 26?")
```bash
sudo -iu trader bash -lc 'cd /opt/trading-terminal && uv run tt run-day --universe watchlist --day 2026-05-26'
```

### Restore from backup
```bash
# stop the bot
sudo systemctl stop trading-bot.service

# replace the db
sudo -u trader cp /opt/trading-terminal/data/ledger.db /opt/trading-terminal/data/ledger.broken.db
sudo -u trader scp ~/Backups/ledger-20260524.db trader@vps:/opt/trading-terminal/data/ledger.db   # from Mac
# or if you scp'd it up first: sudo -u trader cp /tmp/ledger-20260524.db /opt/trading-terminal/data/ledger.db

# start back
sudo systemctl start trading-bot.service
```

---

## 11. Recurring habits (set calendar reminders NOW if you haven't)

| When | Action |
|---|---|
| **Every Sunday 9 PM** | Backup db: `scp ubuntu@vps:/opt/trading-terminal/data/ledger.db ~/Backups/ledger-$(date +%Y%m%d).db` |
| **30 days from deploy** | Review: `uv run tt ai-cost`, browse `/realised`, decide if strategy works |
| **Annually each March** | Refresh `config/holidays.yaml` with next FY's NSE dates |

---

## 12. Realtime scenarios — what an actual day looks like

### Scenario A — Trading day (Mon–Fri, NSE open)

**Example: Thursday, 29 May 2026 (regular trading day)**

| IST time | What happens | What you see on phone |
|---|---|---|
| **00:00–08:29** | Bot idle, scheduler waiting | Nothing |
| **08:30:00** | `job_briefing()` fires. Fetches cues (Yahoo), pulls RSS news into DB, builds positions-news map, calls Sonnet for mood. Writes journal+KB. | `🟢 Pre-market 2026-05-29`<br>`Mood: risk_on — Dow +0.6%, VIX 14`<br>+ 5-8 line markdown body with watch items |
| **08:30–09:29** | Idle | Nothing |
| **09:15** | NSE opens. Bot deliberately waits 15 min for opening volatility. | Nothing |
| **09:30:00** | `job_run_day()` fires. Walks the full pipeline. | Nothing yet — pipeline takes ~2-5 min on Nifty 500 |
| **09:31** | Halt-check passes → universe loaded → fundamentals fetched → 500 screened down to ~280 → trend signals → 20 candidates | Nothing |
| **09:32** | Haiku ranks top 8. Sonnet analyses each (~3s/stock). Opus picks final buys (~6s). | Nothing |
| **09:33** | Each BUY/SELL the broker fills pushes a per-fill alert | `🟢⬆ BUY TATAMOTORS  12 @ ₹720.36 — net ₹8,654.57`<br>(one buzz per fill, 0-3 typical) |
| **09:33** | EOD summary fires (yes, immediately — paper trading does the whole "day" in one pass) | `EOD 2026-05-29`<br>`Fills: 2  Buys: TATAMOTORS, ICICIBANK  Sells: —  Realised P&L: ₹0.00` |
| **09:33–15:25** | Bot idle | Nothing |
| **15:30** | NSE closes | Nothing |
| **15:30–23:59** | Bot idle, scheduler queued for next day | Nothing |

**Net: typically 2-5 phone buzzes on a trading day.**

### Scenario B — Weekend or NSE holiday (e.g. tomorrow, 28 May 2026 = Bakri-Id)

| IST time | What happens | What you see on phone |
|---|---|---|
| **08:30:00** | `job_briefing()` fires, checks calendar, sees holiday/weekend → sends one polite message → returns | `😴 2026-05-28 — markets closed`<br>`Bakri-Id / Eid-ul-Adha (tentative)`<br>`Bot is resting today. No briefing, no trades. Next run-day will be the next trading session.` |
| **09:30:00** | `job_run_day()` fires, sees same flag, silently returns (no duplicate alert) | Nothing |
| **rest of day** | Bot idle | Nothing |

**Net: one Telegram message in the morning confirming the bot is alive and aware that markets are closed.** No surprises, no silent absences.

### Scenario C — Something genuinely broken

Most common: yfinance gives a 429 (rate-limited), Anthropic API key expires, network blip.

| What happened | You see |
|---|---|
| Network failed during briefing | `⚠️ Error in scheduled briefing` + Python traceback (truncated) |
| Network failed during run-day | `⚠️ Error in scheduled run-day` + traceback |
| Bot process crashed | systemd auto-restarts within 15s — *you don't get a Telegram*, but bot resumes |
| Bot can't restart 5x in a row | systemd gives up — bot stays dead until you SSH in (no Telegram). **This is why §11 weekly health check exists.** |

### Scenario D — You hit HALT

You sent `tt halt "lunch break"` from your phone or laptop.

| When | What happens |
|---|---|
| Immediately | `⛔ Trading HALTED — lunch break` arrives |
| Next 08:30 brief | Briefing job runs normally (briefing always allowed; it doesn't trade) |
| Next 09:30 run-day | Aborts at the start: `⛔ Skipped run-day 2026-05-29 — HALT flag set` |
| Until `tt unhalt` | All run-days skip; briefings continue |
| When you `tt unhalt` | `✅ Trading RESUMED` arrives; next run-day fires normally |

### Costs accruing in the background

- **AWS Lightsail**: $7/mo, billed on the 1st (covered by $200 credits for ~28 months)
- **Claude API**: scales with activity. Holiday-heavy weeks ≈ $5/mo. Active 5-day weeks ≈ $8-15/mo. Run `uv run tt ai-cost` weekly to see exact spend.
- **Snapshots** (if you enabled them in Lightsail): $0.50-1/mo

---

## 13. Going live with Kite (much later — only after 4+ weeks of healthy paper trading)

When you decide to go live:
1. Subscribe to Kite Connect at developers.kite.trade (₹2k/mo, 1-day approval)
2. Add to `/opt/trading-terminal/.env`:
   ```
   MODE=live
   KITE_API_KEY=...
   KITE_API_SECRET=...
   KITE_ACCESS_TOKEN=...
   ```
3. Write `src/trading/broker/kite.py` implementing the same `Broker` interface (~200 lines)
4. Edit `src/trading/orchestrator.py` to instantiate `KiteBroker` when `MODE=live`
5. `git push` → `git pull` on VPS → `systemctl restart trading-bot.service`

Nothing else changes. The screener, AI tiers, risk gates, dashboard, Telegram, tax accounting, KB — all broker-agnostic.

**Graduation criteria before flipping to live (revisit):**
- ≥30 paper trades logged
- Hit rate ≥50%
- Max drawdown ≤8%
- Net (post-cost) paper P&L beats Nifty 50 buy-and-hold for the period

If those aren't met → keep paper-trading, or accept the strategy isn't there yet.

---

## 14. Tear-down (if you ever want to stop everything)

```bash
# Stop bot
sudo systemctl stop trading-bot.service
sudo systemctl disable trading-bot.service

# Back up db one last time (from Mac)
scp -i ~/.ssh/lightsail-mumbai.pem \
    ubuntu@<vps-ip>:/opt/trading-terminal/data/ledger.db \
    ~/Backups/ledger-final-$(date +%Y%m%d).db

# Delete the Lightsail instance from the AWS web console
# (this stops $7/mo billing immediately)
```

The GitHub repo, Anthropic API key, and Telegram bot stay until you delete them manually.
