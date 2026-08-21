# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal Streamlit app ("Fantasy Football Command Center") for the owner's Sleeper fantasy football leagues. Draft mode builds a Value-Over-Replacement (VOR) draft board from three projection feeds (Sleeper/Rotowire, FantasyPros expert consensus, ESPN) combined per stat by median, rescored with the connected league's own scoring settings, attaches consensus rankings (live ESPN ranks, a committed Matthew Berry snapshot, and the board's own order), syncs picks live from the Sleeper draft room, and grades your picks. In-Season mode is a legacy ESPN-only feature (waiver targets via `espn-api`). Player news comes from Claude with the web-search server tool (no direct scraping).

## Commands

Run everything from the repo root.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # pdfplumber is not pinned; install separately for extract_espn.py
.venv/bin/streamlit run src/draft_app.py                             # the app
.venv/bin/python -m pytest tests -q                                  # unit tests (no network; fixtures in tests/fixtures/)
.venv/bin/python -m pytest tests/test_roster_slots.py -q -k flex     # a single test file / keyword
python src/connect.py                # ESPN connection smoke test: prints teams, your roster, top free-agent RBs
python src/waivers.py                # prints top 15 waiver targets
python src/test_cookies.py           # checks whether ESPN_S2 / SWID cookies still work (they expire)
python src/news.py                   # Claude news pull for a hardcoded player
python src/extract_espn.py [file.pdf]  # regenerates src/espn_rankings.json from an ESPN cheat-sheet PDF
```

Tests live in `tests/` and never hit the network: Sleeper payloads are monkeypatched or loaded from `tests/fixtures/` (real league/draft payloads from the owner's 12-team league, a trimmed projections sample, and a synthetic picks list). `src/test_cookies.py` is a manual ESPN cookie diagnostic, not a pytest test; `tests/conftest.py` only collects from `tests/`. There is no linter or CI.

For UI-level checks use Streamlit's headless harness: `AppTest.from_file("draft_app.py")` run from `src/`, then drive widgets by key (`quick_entry`, `quick_mine`, `quick_taken`, `sleeper_username`) or label (`Find my leagues`, `Connect`, `Sync picks`). This is how the draft flow has been verified; there are no committed AppTest tests yet.

### Secrets

A `.env` at the repo root (see `.env.example`; the real file is gitignored and chmod 600), loaded explicitly by `draft_app.py` via `python-dotenv`. `FANTASYPROS_API_KEY` enables the second projection source (without it the board is Sleeper-only and says so); `ANTHROPIC_API_KEY` powers news and the analyst (`CLAUDE_FAST_MODEL` for news/top stories, `CLAUDE_ANALYST_MODEL` for Ask the Analyst, defaults in `news.py`); the ESPN values (`LEAGUE_ID`, `YEAR`, `ESPN_S2`, `SWID`, `MY_TEAM_NAME`) only matter for the legacy in-season tools. Never read these anywhere but `os.getenv` at the boundary, and never pass the key itself as a `st.cache_data` argument (`load_fantasypros` reads it inside).

Sleeper needs no credentials: its API is public and read-only. The connected league is persisted to `.sleeper_league.json` at the repo root (gitignored) and auto-loaded on startup.

Nothing reads `st.secrets`. The draft board works with no `.env` at all because `news` and `waivers` are imported lazily inside cached functions in `draft_app.py`. `news.py` constructs `Anthropic()` at import time, so the news and analyst buttons catch and display the failure rather than crash.

## Architecture

`src/draft_app.py` is the entry point and holds all UI, CSS, and Streamlit session state. Modules import each other by bare name (`from draft_board import ...`), which only works because Streamlit puts `src/` on `sys.path`.

League config resolution happens at the top of `draft_app.py`, before the board loads: if `st.session_state.league` is set (a dict from `sleeper_league.league_config`), `starters`, `bench_spots`, `num_teams`, and `scoring_settings` come from it and the manual scoring/league-size radios are hidden; otherwise `DEFAULT_STARTERS` and the radios apply. Everything downstream reads those resolved locals, never the constants.

Draft board pipeline, in order:

1. `draft_board.fetch_position` pulls Sleeper projections per position. `extra_sources` is `{source_name: {match_key: stats}}` for the other feeds: `fantasypros.fetch_all` (stat names mapped to Sleeper's via `STAT_MAP`) and `espn_projections.fetch_all` (ESPN's numeric stat ids mapped via `STAT_IDS`; also returns live ESPN ranks and ADP). `blend_stats` combines the lines key by key: median of 3+, mean of 2, pass-through of 1 (Sleeper's `gp` is copied onto lines that lack it because the bonus estimate needs it). Players are joined across feeds by `espn_ranks.match_key`, which is the normalized name except for defenses, which match on nickname ("Texans D/ST" = "Houston Texans"). With `scoring_settings` the blended line is rescored via `scoring.score_stats`, which also adds `estimate_threshold_bonuses` (expected 100/200-yard-game bonuses from season totals, a normal approximation; the 40+/50+ yard TD bonuses are the one thing not covered, see `unprojected_bonus_keys`). Without settings it uses the preset total (`pts_ppr` etc). K and DEF always keep each site's preset total because their stat projections are too sparse (`scoring.PRESET_FALLBACK_POSITIONS`), so never compare K/DEF numbers across sources (`sources_disagree` in the app skips them). Each player carries `points` (the blend), `points_by_source` (`{"sleeper": x, "fp": y, "espn": z}`, only the feeds that had the player), and `sources` (count). Players under `MIN_POINTS` are dropped. `adp` comes from the Sleeper ADP column matching the league's reception scoring (`adp_key_for`). In Aug 2026, 149 of the top 150 players had all three feeds.
2. `add_value_over_replacement` subtracts the replacement-level player's points. `replacement_ranks(num_teams, starters)` derives the level from the actual slots, splitting flex demand across positions via `FLEX_SHARES` (one FLEX in a 12-team league gives RB/WR = 30, the same as the old `num_teams * 2 + 6` heuristic, which remains the fallback when no starters are passed).
3. `assign_tiers` sets a within-position `tier` (`TIER_GAP`), then `build_board` sets a cross-position `global_tier` (`GLOBAL_TIER_GAP`) and attaches bye weeks from the Sleeper schedule API.
4. `espn_ranks.attach_ranks(board, live_espn_ranks)` joins ESPN and Berry ranks onto the board and computes `sleeper_rank`, `consensus`, `rank_spread`, and `disagreement` (spread >= 15). Live ESPN ranks from the API win over the committed `espn_rankings.json` snapshot; `berry_rankings.json` is still a hand-maintained snapshot. Both are PPR-based, so they disagree with a league-scored board more often; that is expected.
5. `draft_app.load_board` (cached, no TTL, keyed on scoring key, team count, the scoring/starters dicts passed as sorted tuples, and a use-FantasyPros flag) returns `(board, projection_note)`; it calls the 6-hour-cached `load_fantasypros` and `load_espn`, and each feed degrades independently (the failure is appended to the note) so the board always renders with whatever sources answered. ESPN needs no key; FantasyPros needs `FANTASYPROS_API_KEY`. The app dedupes by `player_key = "{name}|{team}|{position}"`. A failure of the board load itself renders an error with Retry/Disconnect buttons and `st.stop()`s.

Roster logic goes through `roster_slots.allocate_slots(my_roster, starters)`, which fills dedicated slots with the best VOR first and spills leftovers into flex slots per `FLEX_ELIGIBILITY`. Its `SlotAllocation` (`slots`, `bench`, `needs`, `filled`, `total`, `missing`) drives the sidebar roster, Still Need, Strengths, the bench count, the recommendation, and `grader.grade_draft(my_roster, starters)`.

The recommended pick is `recommend.recommend_pick(available, needs, roster_size, total_picks)`: highest VOR plus a flat `NEED_BONUS` when the player fills an open slot (an open FLEX counts for RB/WR/TE). K and DEF are excluded entirely until the final `LATE_ROUND_WINDOW` picks, and then only when their slot is open, where they get `LATE_NEED_BONUS` so the draft never ends without one. It does not look at ADP, tiers, byes, or who will be available at the next pick; the analyst is where that reasoning lives.

Live pick sync: `sync_picks_from_sleeper` in `draft_app.py` fetches the draft and its picks through `sleeper_league` (league rosters once, cached in `st.session_state.my_roster_id`), then `pick_sync.apply_picks` maps Sleeper `player_id`s onto the board and splits taken vs mine. `roster_id` is authoritative when present (a commissioner can click for an absent manager), `picked_by` is the fallback. The synced snapshot lives in `st.session_state.synced_taken` / `synced_mine` and is *replaced* on every sync so reversed picks disappear; manual marks live in `drafted` / `my_roster` and are layered on top (a manual "Mine" on a player Sleeper says someone else took is dropped). Everything downstream reads the effective locals `drafted_keys` and `my_roster`, not the raw session keys. `pick_sync.next_pick_info` handles snake and linear orders (returns None for third-round-reversal or auction, or until Sleeper publishes `draft_order`). Auto-sync is an `st.fragment(run_every=SYNC_INTERVAL)` that calls `st.rerun(scope="app")` only when the snapshot changed.

`player_key` is the universal player identity across session state. Players are plain dicts enriched in place at each stage; `categories.py` functions also mutate board dicts in place, and since those dicts live inside `st.cache_data`, the mutations persist across reruns.

`news.py` holds the three Claude calls. The analyst prompt is built by the pure `build_analyst_prompt` (tested) and receives the top `BOARD_ROWS_FOR_ANALYST` available players with blended/Sleeper/FP points, VOR, tier and bye, the open starting slots, and `scoring.scoring_summary` of the league rules; it is told to treat those numbers as ground truth and use web search only for breaking news. The Anthropic client is created lazily in `client()` so importing `news` never needs a key.

Other modules: `grader.py` scores the roster (40% avg VOR capped at `FULL_MARKS_AVG_VOR`, 35% slots filled, 25% positional balance). `waivers.py` ranks ESPN free agents by projection plus a need bonus and has its own ESPN-only `STARTERS` copy. `categories.py` provides the sleeper/rookie/boom/floor queries for the Insights tabs. `connect.py` is standalone and unused by the app.

### Rankings JSON files

Both live in `src/` and are read only by `espn_ranks.py`, which caches them in module globals, so edits require a process restart.

- `espn_rankings.json`: 300 records `{espn_rank, position, pos_rank, name, team}`, generated by `extract_espn.py` from a PDF.
- `berry_rankings.json`: 240 records `{berry_rank, tier, name, position, team}`, hand-maintained, no generator script.

Both JSONs use `"DST"` for defenses; the board uses `"DEF"`. Matching is by name so this does not break the join directly.

## Things that will trip you up

- **Season year is hardcoded** as `SEASON` in both `draft_board.py` (projections, byes) and `draft_app.py` (which Sleeper leagues to list). ESPN year comes from `.env` `YEAR`. Both rankings JSONs are preseason snapshots that must be regenerated each year.
- **Streamlit widget keys**: Streamlit drops a keyed widget's session-state entry as soon as that widget stops rendering, so the manual scoring/league-size radios keep their value in non-widget keys (`scoring_pref`, `league_size_pref`) and bind via `index=` + `on_change`. Follow that pattern for any widget that can be hidden (by mode, by league connection).
- **`waivers.py` has its own ESPN-only `STARTERS`** with no FLEX. It is not wired to the Sleeper league config.
- **Defenses never match the rankings files.** Sleeper names them like "Los Angeles Rams" while the JSONs use "Rams D/ST"-style names, so DEF rows have no ESPN/Berry/consensus rank. (`fetch_position` also computes an unused `"{team} DST"` fallback name; it is dead code, not a bug in practice.)
- **The theme file is inert.** It lives at `streamlit/config.toml`; Streamlit only reads `.streamlit/config.toml`. All visible styling comes from the inline CSS block in `draft_app.py`.
- The board is fetched from Sleeper in both modes (the sidebar player search needs it), so In-Season mode still hits Sleeper.
- Round/pick math assumes every pick in the league is marked as drafted, not just yours.
- The README's structure block is stale (claims `.streamlit/`, omits `berry_rankings.json`, references files that were never committed).
