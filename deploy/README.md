# Deploying for unattended paper trading

Goal: run the bot on a cheap VPS in Mumbai region so it executes the daily pipeline at IST market hours without you typing anything. **Paper mode only — no Kite, no real money.**

## Costs

| Item | Monthly | Notes |
|---|---|---|
| AWS Lightsail Mumbai 2GB | $5 | recommended; ap-south-1 = low latency to NSE data |
| Claude API (tiered swing) | $15–35 | tracked in `tt ai-cost` |
| Telegram bot | $0 | |
| News (RSS) + yfinance + NSE CSV | $0 | |
| **Total** | **~$20–40/mo** | (~₹1,700–3,400) |

No Kite Connect needed — `PaperBroker` simulates fills with real Zerodha costs and writes everything to the ledger exactly as a live broker would.

## One-time setup on the VPS

```bash
# 1. Boot Ubuntu 24.04 LTS (or any modern Linux). ssh in.

# 2. Create a dedicated user
sudo adduser --disabled-password --gecos "" trader
sudo mkdir -p /opt/trading-terminal /var/log/trading-bot
sudo chown -R trader:trader /opt/trading-terminal /var/log/trading-bot

# 3. As that user, install uv + clone the repo
sudo -iu trader bash -lc '
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  git clone <YOUR-REPO-URL> /opt/trading-terminal
  cd /opt/trading-terminal
  uv sync                                    # base deps (no [dev])
  uv run tt init-db
  uv run tt universe-refresh
'

# 4. Create /opt/trading-terminal/.env (as the trader user)
sudo -u trader vi /opt/trading-terminal/.env
# Paste:
#   MODE=paper
#   ANTHROPIC_API_KEY=sk-ant-...
#   TELEGRAM_BOT_TOKEN=...
#   TELEGRAM_CHAT_ID=...

# 5. Smoke-test
sudo -iu trader bash -lc 'cd /opt/trading-terminal && uv run tt tg-test "VPS up"'
sudo -iu trader bash -lc 'cd /opt/trading-terminal && uv run tt brief'

# 6. Install the systemd unit
sudo cp /opt/trading-terminal/deploy/trading-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable trading-bot.service
sudo systemctl start trading-bot.service

# 7. Confirm
systemctl status trading-bot.service
journalctl -u trading-bot.service -f
```

## What it does once running

| IST time | Job | What you'll see on Telegram |
|---|---|---|
| 08:30 Mon-Fri | Pre-market briefing | 🟢/🟡/🔴 mood + 5-10 line body |
| 09:30 Mon-Fri | Full run-day | Per-fill alerts + EOD summary |

The scheduler stays running 24/7, but jobs fire only on weekdays at IST market times. NSE holidays are best-effort: if there's no price data for that date, run-day logs and exits clean.

## Kill switch

From your phone, SSH or any shell:

```bash
ssh trader@vps 'cd /opt/trading-terminal && /home/trader/.local/bin/uv run tt halt "lunch break"'
# resumes:
ssh trader@vps 'cd /opt/trading-terminal && /home/trader/.local/bin/uv run tt unhalt'
```

The orchestrator checks `data/HALT` at the very start of `run_day` and aborts cleanly if present. You'll get a Telegram confirmation either way.

## Browsing the dashboard

The dashboard binds to `127.0.0.1:8000` by default — don't expose it. SSH-tunnel from your laptop:

```bash
ssh -L 8000:127.0.0.1:8000 trader@vps
# in another shell on vps:
cd /opt/trading-terminal && uv run tt dashboard
# then browse http://localhost:8000 on your laptop
```

For permanent access, put it behind Caddy + basic auth.

## Backups

The whole state is `data/ledger.db`. Nightly backup via cron:

```bash
crontab -e
# add:
30 23 * * * cp /opt/trading-terminal/data/ledger.db /opt/trading-terminal/data/ledger.$(date +\%Y\%m\%d).db
0 0 * * 0 find /opt/trading-terminal/data -name "ledger.*.db" -mtime +30 -delete
```

If you want offsite backup, `rclone copy` to S3/Backblaze B2 weekly costs pennies.

## Updating the bot

```bash
sudo -iu trader bash -lc '
  cd /opt/trading-terminal
  git pull
  uv sync
'
sudo systemctl restart trading-bot.service
```

The schema migrates forward on init. The ledger never gets recreated.
