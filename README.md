# 🏈 Fantasy Football Command Center

A full-stack Python web application for fantasy football, combining a **value-based draft assistant** with a **weekly start/sit page** for your Sleeper leagues. It blends projections from multiple sources, live league data, and AI-generated analysis into one dark-themed "command center" you can run on draft day and throughout the season.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![Streamlit](https://img.shields.io/badge/Streamlit-app-red)
![Status](https://img.shields.io/badge/status-active-brightgreen)

<!-- Add a screenshot named screenshot.png to your repo root and it will render here -->
![Command Center](screenshot.png)

---

## Overview

Most fantasy tools just rank players by projected points — which is misleading, because it ignores positional scarcity. This app is built around **Value Over Replacement (VOR)**: how much better a player is than a freely available replacement at the same position. That correctly values an elite running back above a higher-scoring quarterback when startable quarterbacks are plentiful.

The app runs in two modes via a sidebar toggle:

- **Draft mode** — a live, value-based draft board with recommendations, tiers, scarcity tracking, consensus rankings, and a draft grader.
- **Start/Sit mode** — pick any of your Sleeper leagues and week; the app blends the three feeds' weekly projections, scores them with the league's rules, and shows your current lineup against the best legal one with the point delta per swap, FantasyPros' weekly expert consensus and start/sit grades, injury flags, and coin-flip labels for swaps inside projection noise.

An AI analyst and player-news feature run in both modes.

## Features

### Draft mode
- **Value-based rankings (VOR)** with automatic tiering by scoring drop-off, and both within-position and global tiers.
- **Consensus rankings** — blends the app's Sleeper-based VOR with ESPN's rankings, and flags **disagreements** where the two sources diverge, highlighting players who warrant your own judgment.
- **Interactive draft board** — mark players "Mine" or "Taken"; the board updates live and surfaces the best available pick.
- **Quick-entry box** — type a partial name and mark a pick instantly, to keep pace with a fast draft clock.
- **Roster-aware recommendations** weighted by the starting positions you still need.
- **Configurable scoring and league size** — PPR / Half-PPR / Standard and 8/10/12/14 teams; the whole board re-ranks to match.
- **Position filtering and scarcity panel** — see how many players remain in each tier per position, so you can spot positional runs before they happen.
- **QB–WR stack highlighting** — draft a QB (or pass-catcher) and their available teammates are flagged on the board.
- **Positional strength flags** and **bye-week collision warnings** in the sidebar.
- **Draft insight tabs** — Sleepers, Top Rookies, Boom/Ceiling, and High Floor.
- **Draft grader** — grades your roster on value, completeness, and balance.
- **Sort toggle** — order the board by VOR, Consensus, or ESPN rank.

### Start/Sit mode
- **Current vs optimal lineup** — one row per starting slot (FLEX, SUPER_FLEX and the rest), swaps listed with their projected gain, bench with the reason a player cannot score this week (bye, out, no projection).
- **Kickoff locks** — players whose game has started are frozen where they are (Sleeper will not move them), swaps show the kickoff they must beat, and the page says what a missed swap would have been worth.
- **Game-day lines** — with a free The Odds API key, the current week's spreads and totals are refreshed from the sportsbooks a few times a day (median across books), so Sunday-morning moves reach the matchup adjustment; without a key the weekly nflverse lines are used.
- **Matchup adjustment** — each projection is scaled (capped at 12%) by the team's Vegas implied total, home/away, and the opponent's points allowed to the position, all from nflverse's free schedule and weekly stats; the chip on every row shows the line, the implied total, the DvP rank and the factor, and a sidebar toggle turns it off.
- **Projection accuracy log** — every week's projections are logged locally; one click records the real scores from Sleeper afterward, and the page reports each feed's error so weights and the matchup coefficients can be tuned from evidence.
- **Trade Evaluator** — on the roadmap.

### Both modes
- **AI player news** — look up any player for a current, fantasy-focused summary sourced across the web (Anthropic Claude API with web search), **with source links** for verification.
- **AI "Ask the Analyst"** — ask open-ended questions and get answers aware of your league size, scoring, and current roster, using real draft-strategy frameworks (Zero RB, Hero RB, etc.).
- **Player headshots** on the recommendation card and news panel.

## How It Works

The core methodology is **Value Over Replacement (VOR)**:

1. Pull season-long projections for every position from the Sleeper API.
2. For each position, establish a *replacement level* — the projected points of the last player who would realistically start across the league (scaled to league size).
3. Compute each player's VOR as `projected_points − replacement_level`.
4. Rank all players across positions by VOR, so positional scarcity is baked in.
5. Group players into tiers where scoring drops off sharply.

For **consensus rankings**, ESPN's published ranks are matched to the board by player and blended with the Sleeper VOR rank; large gaps between the two are flagged as disagreements.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python |
| Web UI | Streamlit |
| Projections | Sleeper API (projections, ADP, trending, schedule/byes) |
| League data | Sleeper public API — leagues, rosters, drafts, weekly projections |
| AI features | Anthropic Claude API (Opus 5 by default, configurable) with the web search tool |
| Data parsing | pdfplumber (ESPN cheat-sheet extraction) |
| HTTP / config | `requests`, `python-dotenv` |
| Tooling | Git/GitHub, virtual environments, `.env`-based secrets |

## Getting Started

### Prerequisites
- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com) *(for AI news and the analyst; the draft board runs without it)*
- FantasyPros API key *(optional — adds the second projection feed and the weekly expert consensus)*

### Installation

```bash
git clone https://github.com/<your-username>/fantasy-football-guide.git
cd fantasy-football-guide

python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root:

```env
# For AI news and the analyst
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Optional: second projection feed and weekly expert consensus
FANTASYPROS_API_KEY=
```

> The core draft board and the start/sit page run entirely on the free Sleeper API — **no keys needed** to get started. The Anthropic key powers the AI features; the FantasyPros key adds the second feed and weekly consensus.

### Run

```bash
streamlit run src/draft_app.py
```

Opens at `http://localhost:8501`.

## Project Structure

```
fantasy-football-guide/
├── .claude/skills/          # on-demand Claude Code skills (draft simulation, headless UI checks)
├── .streamlit/config.toml   # dark theme, minimal toolbar
├── src/
│   ├── draft_app.py         # Streamlit entry point: UI, both modes, sidebar, cached loaders
│   ├── draft_board.py       # Sleeper projections, three-feed blend, VOR, tiers, byes
│   ├── fantasypros.py       # FantasyPros projections and expert consensus (season and weekly)
│   ├── espn_projections.py  # ESPN projections, live ranks and ADP
│   ├── scoring.py           # rescoring with a league's settings, threshold-bonus estimate
│   ├── tiers.py             # Jenks natural-breaks tiering
│   ├── espn_ranks.py        # consensus rankings, disagreement flags, name matching
│   ├── espn_rankings.json   # ESPN cheat-sheet snapshot (regenerate with extract_espn.py)
│   ├── berry_rankings.json  # hand-maintained Matthew Berry snapshot
│   ├── extract_espn.py      # PDF -> espn_rankings.json
│   ├── roster_slots.py      # slot allocation and flex eligibility
│   ├── recommend.py         # pick recommendation, lookahead, survival odds, K/DEF window
│   ├── opponents.py         # opponent-needs model (position demand by draft window)
│   ├── pick_sync.py         # maps Sleeper picks onto the board, next-pick math
│   ├── sleeper_league.py    # Sleeper API: users, leagues, rosters, drafts, NFL state
│   ├── manual_draft.py      # hand-logged drafts (Yahoo, ESPN) for one or two household teams
│   ├── draft_state.py       # persisted Mine/Taken marks and pick log
│   ├── grader.py            # draft grader
│   ├── categories.py        # draft-insight queries (sleepers, rookies, boom, floor)
│   ├── news.py              # Claude news, top stories, Ask the Analyst
│   ├── weekly_board.py      # one week's projections, blended and league-scored
│   ├── lineup.py            # current vs optimal lineup, swap list
│   ├── matchups.py          # nflverse schedule, Vegas lines, matchup factor
│   ├── dvp.py               # defense vs position from nflverse weekly stats
│   ├── projection_log.py    # projection and actuals log, accuracy report
│   └── simulate.py          # draft simulator (bots vs the recommender)
├── tests/                   # pytest, no network; fixtures in tests/fixtures/
├── CLAUDE.md                # working notes for Claude Code
├── requirements.txt
├── .env.example
└── README.md
```

## Roadmap

Next up, in dependency order:

- **Roster sync** — done: the start/sit page reads your roster and starters straight from Sleeper.
- **Trade evaluator** — weigh value on each side of a proposed trade.
- **Rest-of-season rankings**, **player trends**, **matchup analysis**, and **playoff planning** — as live game data accrues through the season.

Some features (strength of schedule, richer start/sit stats) depend on data sources still being sourced, and are noted honestly in the roadmap rather than faked.

## Notes

- Projections, ADP, schedule, league and roster data come from the Sleeper public API; FantasyPros and ESPN add projection feeds and rankings.
- ESPN consensus rankings are extracted from ESPN's published cheat sheet — a periodic manual refresh via `extract_espn.py`.
- AI features use the Anthropic Claude API with web search; news results are cached to minimize cost.
- Secrets (API keys) live in a gitignored `.env` file and are never committed.

---

*Built as a portfolio project and a practical draft-day and in-season tool.*
