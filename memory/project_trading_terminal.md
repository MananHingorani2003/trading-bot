---
name: project-trading-terminal
description: AI trading terminal project — decisions locked in 2026-05-27
metadata:
  type: project
---

Locked decisions (2026-05-27):
- **Broker:** Zerodha Kite Connect (user will subscribe — ₹2000/mo). Not Kite Publisher (manual click defeats the purpose).
- **Style:** Swing trading (days–weeks hold). Explicitly NOT intraday.
- **Hosting:** Cheap VPS in India for live phase (Hetzner Mumbai or AWS Mumbai t4g.small). Local-first during paper phase.
- **AI tiering:** Haiku for universe filter → Sonnet for per-stock analysis → Opus only for final buy/sell calls. Keeps Claude API ~$15–40/mo.
- **Capital ramp:** Paper → ₹10k live → ₹50k.

**Why:** Swing + tiered AI + Mumbai VPS is the only combo where the math works on ₹50k capital. Intraday brokerage drag + Opus-on-everything would eat any edge. User confirmed all four choices via AskUserQuestion before plan was written.

**How to apply:** When user asks for features that contradict these (e.g. "let's add intraday scalping" or "use Opus for every decision"), push back and point at the cost math in PLAN.md §5 before agreeing. Recheck before scope changes.

**GitHub remote:** `git@github.com:MananHingorani2003/trading-bot.git` (private). User pushes from Mac; VPS clones from this URL.

Master plan: [[reference-plan-doc]]
