"""Tuesday waiver report for every Sleeper league of the given users; the cloud
routine's entry point.

    .venv/bin/python src/waiver_report.py --user magoo82 --user amcgough13 [--week N] [--limit 8] [--lineup-only]

Plain text on stdout, one block per league: waiver settings and FAAB, the
kickoff-aware lineup swaps, adds for the season, streamers for the week, drop
candidates. `--lineup-only` (the pre-game routine) prints just the lock line and the
swaps. A league that fails prints its error and the report goes on. Feeds:
Sleeper (always), FantasyPros (FANTASYPROS_API_KEY), ESPN, nflverse lines and DvP,
The Odds API (ODDS_API_KEY); a missing feed is noted, never fatal.
"""
import argparse
import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

import espn_projections
import fantasypros
import sleeper_league
from draft_board import SEASON, build_board
from dvp import allowed_per_game, blend_seasons, factors, fetch_player_weeks
from espn_projections import EspnError
from fantasypros import FantasyProsError
from lineup import current_lineup, deadline_status, lineup_diff, lock_status, mark_locked, reachable_lineup, swap_deadline
from matchups import ET, MatchupError, fetch_schedule, locked_teams, team_context
from odds import OddsError, fetch_odds, merge_lines, parse_events
from sleeper_league import SleeperError
from waivers import drop_candidates, faab_left, free_agents, parse_trending, waiver_settings, waiver_targets
from weekly_board import assemble_pool, roster_rows

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "adjusted_points"  # the page's default: matchup-adjusted blend


# ---- formatting (pure) ----

def waiver_line(settings, faab):
    line = f"Waivers: {settings['type']}"
    if settings["budget"] is not None:
        line += f" · ${faab} of {settings['budget']} left"
    if settings["clear_days"]:
        line += f" · claims clear after {settings['clear_days']} day(s)"
    return line


def format_swap(swap, key, now):
    player, out = swap["in"], swap["out"]
    if out:
        reason = f", {swap['out_reason']}" if swap["out_reason"] else ""
        out_txt = f" for {out['name']} ({out.get(key, 0):.1f}{reason})"
    else:
        out_txt = " into an empty slot"
    flip = " (coin flip)" if swap["coin_flip"] else ""
    status = deadline_status(swap_deadline(swap), now)
    due = f", {status[1]}" if status else ""
    return f"{swap['slot']}: start {player['name']} ({player.get(key, 0):.1f}){out_txt}, {swap['delta']:+.1f}{flip}{due}"


def format_target(entry, key):
    row = entry["row"]
    bits = [f"{row['position']} {row['name']} {row.get('team') or 'FA'} {row.get(key, 0):.1f}", f"{entry['week_gain']:+.1f} this week"]
    if entry["season_gain"] > 0:
        bits.append(f"{entry['season_gain']:+.0f} season")
    if not entry.get("has_season", True):
        bits.append("no season projection")
    if entry["adds"]:
        bits.append(f"{entry['adds']:,} adds/24h")
    return " · ".join(bits)


def format_depth(entry):
    row, over = entry["row"], entry["over"]
    adds = f" · {entry['adds']:,} adds/24h" if entry["adds"] else ""
    return f"{row['position']} {row['name']} {row.get('team') or 'FA'} · {season_txt(entry)} over {over['name']}{adds}"


def season_txt(entry):
    """'season 153, VOR +5' (VOR only when the board computed one), or 'no season projection'."""
    if entry.get("season_points") is None:
        return "no season projection"
    text = f"season {entry['season_points']:.0f}"
    value = entry.get("season_value")
    if value is not None and value != entry["season_points"]:
        text += f", VOR {value:+.0f}"
    return text


def format_drop(drop):
    row = drop["row"]
    return f"{row['position']} {row['name']} {row.get('team') or 'FA'} · {season_txt(drop)}"


def _section(title, items, empty):
    if not items:
        return [f"{title}: {empty}"]
    return [f"{title}:"] + [f"  {item}" for item in items]


def format_league_report(name, username, week, settings, faab, swaps, targets, drops, key, notes, now=None,
                         lineup_only=False, lock_line=None):
    now = now or datetime.now(ET)
    lines = [f"== {name} ({username}) · week {week} =="]
    if lock_line:
        lines.append(lock_line)
    if not lineup_only:
        lines.append(waiver_line(settings, faab))
    lines += _section("Lineup", [format_swap(s, key, now) for s in swaps], "already optimal")
    if lineup_only:
        if notes:
            lines.append("Notes: " + "; ".join(notes))
        return "\n".join(lines)
    lines += _section("Add for the season", [format_target(e, key) for e in targets["season"]], "nobody on the wire improves this lineup")
    lines += _section(f"Streamers for week {week}", [format_target(e, key) for e in targets["week"]], "none worth a claim")
    lines += _section("Depth upgrades", [format_depth(e) for e in targets.get("depth", [])], "nobody beats your worst bench player over the season")
    lines += _section("Drop candidates", [format_drop(d) for d in drops], "everyone starts somewhere")
    if notes:
        lines.append("Notes: " + "; ".join(notes))
    return "\n".join(lines)


# ---- feeds (network; every failure becomes a note) ----

def _try(notes, label, call):
    try:
        return call()
    except (EspnError, FantasyProsError, MatchupError, OddsError, SleeperError) as e:
        notes.append(f"{label}: {e}")
        return None


def week_games(week, notes):
    schedule = _try(notes, "schedule", lambda: fetch_schedule(SEASON))
    if schedule is None:
        return []
    key = os.getenv("ODDS_API_KEY", "").strip()
    if not key:
        return merge_lines(schedule, {}, week)
    live = _try(notes, "The Odds API", lambda: parse_events(fetch_odds(key)["events"])["lines"])
    return merge_lines(schedule, live or {}, week)


def defense_factors(notes):
    prior = _try(notes, "DvP", lambda: factors(allowed_per_game(fetch_player_weeks(str(int(SEASON) - 1)))))
    if prior is None:
        return {}
    try:
        current = factors(allowed_per_game(fetch_player_weeks(SEASON)))
    except MatchupError:
        current = {}
    return blend_seasons(prior, current)


class SharedFeeds:
    """Everything that does not depend on the league, fetched once and reused."""

    def __init__(self, week):
        self.week = week
        self.notes = []
        self.fp_key = os.getenv("FANTASYPROS_API_KEY", "").strip()
        self.weekly = {}
        self.season = {}
        espn_week = _try(self.notes, "ESPN weekly", lambda: espn_projections.fetch_all(SEASON, week=week))
        espn_season = _try(self.notes, "ESPN season", lambda: espn_projections.fetch_all(SEASON))
        if espn_week:
            self.weekly["espn"] = espn_week["projections"]
        if espn_season:
            self.season["espn"] = espn_season["projections"]
        if self.fp_key:
            fp_week = _try(self.notes, "FantasyPros weekly", lambda: fantasypros.fetch_all(SEASON, self.fp_key, week=week))
            fp_season = _try(self.notes, "FantasyPros season", lambda: fantasypros.fetch_all(SEASON, self.fp_key))
            if fp_week:
                self.weekly["fp"] = fp_week["projections"]
            if fp_season:
                self.season["fp"] = fp_season["projections"]
        else:
            self.notes.append("FantasyPros: no FANTASYPROS_API_KEY, Sleeper + ESPN only")
        self.context = team_context(week_games(week, self.notes), week)
        self.dvp = defense_factors(self.notes)
        self.trending = _try(self.notes, "trending adds", lambda: parse_trending(sleeper_league.get_trending_adds())) or {}
        self._fp_ranks, self._pools, self._boards = {}, {}, {}

    def fp_week_ranks(self, code):
        if not self.fp_key:
            return None
        if code not in self._fp_ranks:
            ranks = _try(self.notes, f"FantasyPros {code} ranks", lambda: fantasypros.fetch_weekly_rankings(SEASON, self.week, self.fp_key, code))
            self._fp_ranks[code] = ranks["ranks"] if ranks else None
        return self._fp_ranks[code]

    def pool(self, scoring):
        signature = json.dumps(sorted(scoring.items()))
        if signature not in self._pools:
            ranks = self.fp_week_ranks(fantasypros.scoring_code_for(scoring))
            self._pools[signature] = assemble_pool(self.week, scoring, self.weekly, ranks, self.context, self.dvp)
        return self._pools[signature]

    def board(self, cfg):
        signature = json.dumps([cfg["num_teams"], sorted(cfg["scoring_settings"].items()), sorted(cfg["starters"].items())])
        if signature not in self._boards:
            self._boards[signature] = build_board("pts_ppr", cfg["num_teams"], cfg["scoring_settings"], cfg["starters"], self.season or None)
        return self._boards[signature]


# ---- one league ----

def league_report(username, user_id, league, shared, limit, lineup_only=False):
    cfg = sleeper_league.league_config(league, user_id)
    rosters = sleeper_league.get_rosters(cfg["league_id"])
    mine = sleeper_league.find_my_roster(rosters, user_id)
    if mine is None:
        return f"== {cfg['name']} ({username}) ==\n{username} has no roster in this league."
    scoring, starters, week = cfg["scoring_settings"], cfg["starters"], shared.week
    pool = shared.pool(scoring)
    by_id = {p["player_id"]: p for p in shared.board(cfg) if p.get("player_id")}
    now = datetime.now(ET)
    locked = locked_teams(shared.context, now)
    rows = mark_locked(roster_rows(mine.get("players") or [], pool, by_id, {}, week), locked)
    current = current_lineup(mine.get("starters") or [], league.get("roster_positions") or [], {r["player_id"]: r for r in rows})
    swaps = lineup_diff(current, reachable_lineup(rows, current, starters, key=KEY), key=KEY)
    lock_line = lock_status(shared.context, rows, current, now)
    if lineup_only:
        return format_league_report(cfg["name"], username, week, {}, None, swaps, {}, [], KEY, shared.notes, now,
                                    lineup_only=True, lock_line=lock_line)
    my_season = [by_id[r["player_id"]] for r in rows if r["player_id"] in by_id]
    free = mark_locked(free_agents(pool, rosters), locked)
    targets = waiver_targets(free, rows, my_season, by_id, starters, KEY, shared.trending, limit, current=current)
    drops = drop_candidates(rows, my_season, starters, key=KEY, current=current)
    settings = waiver_settings(league)
    return format_league_report(cfg["name"], username, week, settings, faab_left(mine, settings["budget"]),
                                swaps, targets, drops, KEY, shared.notes, now, lock_line=lock_line)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Waiver and lineup report for every Sleeper league of the given users.")
    parser.add_argument("--user", action="append", required=True, help="Sleeper username (repeatable)")
    parser.add_argument("--week", type=int, help="NFL week (default: the current lineup week)")
    parser.add_argument("--limit", type=int, default=8, help="players per list")
    parser.add_argument("--lineup-only", action="store_true", help="just the lock line and the swaps (the pre-game check)")
    args = parser.parse_args(argv)
    load_dotenv(os.path.join(REPO_ROOT, ".env"))
    week = args.week or sleeper_league.lineup_week(sleeper_league.get_nfl_state())
    shared = SharedFeeds(week)
    failures = 0
    for username in args.user:
        try:
            user = sleeper_league.get_user(username)
            leagues = sleeper_league.get_leagues(user["user_id"], SEASON)
        except SleeperError as e:
            print(f"== {username} ==\nCouldn't list leagues: {e}\n")
            failures += 1
            continue
        for league in leagues:
            try:
                print(league_report(username, user["user_id"], league, shared, args.limit, args.lineup_only))
            except Exception as e:  # one league must never sink the report
                print(f"== {league.get('name', '?')} ({username}) ==\nReport failed: {type(e).__name__}: {e}")
                failures += 1
            print()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
