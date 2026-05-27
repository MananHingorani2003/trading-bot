---
name: reference-plan-doc
description: Master build plan for the trading terminal — lives at PLAN.md in repo root
metadata:
  type: reference
---

Single source of truth for architecture, phases, costs, and risk rules:
**`/Users/mananhingorani/ClaudeCode_AI_Trading/PLAN.md`**

Sections:
- §0 Reality check (cost drag, expected loss probability)
- §1 Architecture (folder layout, design rules)
- §2 Daily decision loop (IST schedule, tier per job)
- §3 Knowledge base (SQLite + ChromaDB + journals)
- §4 Risk controls (hard limits, kill switch)
- §5 Cost model (~₹3.6–6.1k/mo fixed)
- §6 Phased rollout (Phase 0 scaffolding → Phase 5 ₹50k live)
- §7 Extras (replay mode, dashboard, earnings blackout)
- §8 Explicit non-goals (intraday, F&O, sentiment-only)
- §9 Open questions (universe size, news API, dashboard timing, tax tracker)

Always check PLAN.md before suggesting architecture changes — don't reinvent decisions already made.
