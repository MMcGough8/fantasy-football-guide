---
name: simulate-drafts
description: Use when asked to simulate, rehearse, replay or benchmark a draft with src/simulate.py, to validate a recommender change with the paired 36-draft sweep, or to stream a mock draft into the app's "Rehearse with a mock draft" box.
---

# Simulating drafts

`src/simulate.py` drafts a full 14-round snake with 11 bots on assigned styles (`STRATEGIES`: adp, zero_rb, rb_heavy, qb_early, te_early, homer, board) and the owner on the real recommender with the real opponent-needs model; `SCENARIOS` are named leagues (balanced, market, zero_rb_league, rb_run, qb_early_league, sharks). It builds the live board once and caches it to `/tmp/sim_board.json` (other leagues' boards were cached as `/tmp/sim_board_girls.json`, `/tmp/sim_board_deuces.json`, `/tmp/sim_board_yahoo.json`).

Run from the repo root:

```bash
.venv/bin/python src/simulate.py --list
.venv/bin/python src/simulate.py --slot 6 --scenario rb_run --compare          # app vs pure VOR vs market, same seed
.venv/bin/python src/simulate.py --slot 6 --scenario balanced --team 3=zero_rb  # override one team
.venv/bin/python src/simulate.py --slot 6 --scenario balanced --emit /tmp/simlive --stream 8 --clock 30
```

`--emit DIR` writes Sleeper-shaped `draft.json`/`picks.json`; with `--stream` it plays the draft out in real time (pre-draft, then the order is published, then a pick every N seconds with an extra `--clock` pause on the owner's turn). Paste `file:DIR` into the sidebar's "Rehearse with a mock draft" box and the app syncs from the files exactly as it would from Sleeper (`load_replay` in `draft_app.py`). This is how the live transition (order appearing, TAKE NOW, countdown) was verified.

## Validating a scoring change

Any change to the recommender's score (need bonuses, urgency, tie-breaks, K/DEF timing) must be sim-validated before it ships: two plausible bonus changes lost lineup points (see the simulation findings in CLAUDE.md). The gate is a paired sweep, same seeds on both branches:

```
--slot {3,8,10} x --scenario {balanced,rb_run,market,sharks} x --seed {1,2,3}   # 36 drafts
```

Compare mean lineup points and the count of drafts improved. The 2026-08-23 baseline on the draft-day code was a mean of 1903.9; the shipped tie-break plus run-aware K/DEF window gained +1.9 with 25 of 36 drafts improved, and the rejected position-level urgency lost 4.6.
