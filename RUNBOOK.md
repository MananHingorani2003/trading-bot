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

## 12. What to expect when the bot is healthy

**Every weekday (Mon–Fri, IST), unless NSE holiday:**
- **08:30** — phone buzzes with mood card (`🟢/🟡/🔴 Pre-market YYYY-MM-DD`) + a 5–8 line briefing
- **09:30** — pipeline runs; you may get 0–5 fill alerts depending on what the AI decides
- **EOD** — phone buzzes with summary card (fills count, buys, sells, realised P&L)

**Weekends + NSE holidays:**
- Silent. One quiet `😴 Skipped YYYY-MM-DD — weekend/NSE holiday: X` message at 09:30 (silenced — won't make sound).

**Costs accumulating:**
- AWS Lightsail: ~$7/mo, billed monthly
- Claude API: ~$10–35/mo, charged to your Anthropic account
- Run `uv run tt ai-cost` weekly to monitor

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
