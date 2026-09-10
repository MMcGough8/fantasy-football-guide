"""Matchup context for one NFL week from nflverse's free schedule, and the
conservative adjustment it drives on top of the blended weekly projection.

Vegas implied team totals, home/away/neutral and the roof come from
nflverse's `games.csv`; defense-vs-position factors come from `dvp.py`. The
feeds already price most of this, so the coefficients below are deliberately
small and every term is capped; they are meant to be re-tuned from the
projection log once enough actuals exist (see `projection_log.accuracy_report`).
"""
import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

SCHEDULE_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
TIMEOUT_SECONDS = 30
# nflverse team codes that differ from Sleeper's
TEAM_CODES = {"LA": "LAR"}
DEFAULT_IMPLIED = 22.5  # league-average implied team total when no lines are posted
ET = ZoneInfo("America/New_York")  # nflverse publishes gameday/gametime in US/Eastern

# adjusted = points x clamp(implied_term x dvp_term x site_term, 1 - CAP, 1 + CAP)
A_IMPLIED = 0.35  # elasticity to the team's implied total vs the league average
IMPLIED_TERM_CAP = 0.10  # the implied term alone never moves more than 10%
B_DVP = 0.25  # elasticity to the opponent's points-allowed factor (0.8-1.2 => +/-5%)
SITE_TERMS = {"home": 1.01, "away": 0.99, "neutral": 1.0}
CAP = 0.12  # the whole adjustment never moves more than 12%
DVP_POSITIONS = ("QB", "RB", "WR", "TE")


class MatchupError(Exception):
    """Raised for any failure fetching or parsing nflverse data, with a user-facing message."""


def to_sleeper_team(code):
    return TEAM_CODES.get(code, code)


def fetch_csv(url):
    """Rows of a CSV at `url` as dicts."""
    try:
        resp = requests.get(url, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise MatchupError(f"Couldn't fetch {url.rsplit('/', 1)[-1]} ({e})") from e
    return list(csv.DictReader(io.StringIO(resp.text)))


def _number(value):
    return float(value) if value not in (None, "") else None


def parse_schedule(rows, season):
    """Regular-season games of `season`: {week, home, away, neutral, total, spread_line,
    roof, gameday, gametime}, with Sleeper team codes. `spread_line` is nflverse's
    (positive = home favored)."""
    games = []
    for row in rows:
        if row.get("game_type") != "REG" or str(row.get("season")) != str(season):
            continue
        games.append({
            "week": int(row["week"]),
            "home": to_sleeper_team(row["home_team"]),
            "away": to_sleeper_team(row["away_team"]),
            "neutral": row.get("location") == "Neutral",
            "total": _number(row.get("total_line")),
            "spread_line": _number(row.get("spread_line")),
            "roof": row.get("roof") or None,
            "gameday": row.get("gameday"),
            "gametime": row.get("gametime"),
        })
    return games


def fetch_schedule(season):
    return parse_schedule(fetch_csv(SCHEDULE_URL), season)


def _implied(total, spread_line, home):
    if total is None or spread_line is None:
        return None
    return round(total / 2 + (spread_line / 2 if home else -spread_line / 2), 2)


def team_context(games, week):
    """{team: {opponent, site, implied, opponent_implied, total, spread, roof, gameday,
    gametime}} for every team playing in `week`. `spread` is the team's own view
    (negative = favored). Bye teams are absent."""
    context = {}
    for g in games:
        if g["week"] != week:
            continue
        common = {"total": g["total"], "roof": g["roof"], "gameday": g["gameday"], "gametime": g["gametime"]}
        home_implied = _implied(g["total"], g["spread_line"], home=True)
        away_implied = _implied(g["total"], g["spread_line"], home=False)
        spread = g["spread_line"]
        context[g["home"]] = {
            **common, "opponent": g["away"], "site": "neutral" if g["neutral"] else "home",
            "implied": home_implied, "opponent_implied": away_implied,
            "spread": -spread if spread is not None else None,
        }
        context[g["away"]] = {
            **common, "opponent": g["home"], "site": "neutral" if g["neutral"] else "away",
            "implied": away_implied, "opponent_implied": home_implied, "spread": spread,
        }
    return context


def league_avg_implied(context):
    known = [c["implied"] for c in context.values() if c.get("implied") is not None]
    return sum(known) / len(known) if known else DEFAULT_IMPLIED


def _clamp(value, low, high):
    return max(low, min(high, value))


def matchup_factor(position, ctx, dvp_entry, avg_implied):
    """(factor, parts) for one player. A defense scores against the opposing
    offense, so its implied term runs the other way; kickers and defenses skip DvP."""
    if position == "DEF":
        basis, sign = ctx.get("opponent_implied"), -1
    else:
        basis, sign = ctx.get("implied"), 1
    implied_term = 1.0
    if basis is not None and avg_implied:
        implied_term = _clamp(1 + sign * A_IMPLIED * (basis / avg_implied - 1), 1 - IMPLIED_TERM_CAP, 1 + IMPLIED_TERM_CAP)
    dvp_term = 1.0
    if position in DVP_POSITIONS and dvp_entry and dvp_entry.get("factor") is not None:
        dvp_term = 1 + B_DVP * (dvp_entry["factor"] - 1)
    site_term = SITE_TERMS.get(ctx.get("site"), 1.0)
    factor = _clamp(implied_term * dvp_term * site_term, 1 - CAP, 1 + CAP)
    return factor, {"implied": implied_term, "dvp": dvp_term, "site": site_term}


def attach_matchup(pool, context, dvp):
    """A new pool with `matchup` (context, DvP, factor, parts) and `adjusted_points`.
    Players without a game this week keep their points and get `matchup` None."""
    avg_implied = league_avg_implied(context)
    adjusted = {}
    for player_id, row in pool.items():
        ctx = context.get(row.get("team")) if row.get("team") else None
        if ctx is None:
            adjusted[player_id] = {**row, "matchup": None, "adjusted_points": row["points"]}
            continue
        entry = ((dvp or {}).get(ctx["opponent"]) or {}).get(row["position"])
        factor, parts = matchup_factor(row["position"], ctx, entry, avg_implied)
        adjusted[player_id] = {
            **row,
            "matchup": {
                **ctx,
                "dvp_factor": entry.get("factor") if entry else None,
                "dvp_rank": entry.get("rank") if entry else None,
                "factor": round(factor, 4),
                "parts": parts,
            },
            "adjusted_points": round(row["points"] * factor, 1),
        }
    return adjusted


def kickoff_time(ctx):
    """Kickoff as an aware Eastern datetime, from a team context or a row's `matchup`
    (both carry `gameday` and `gametime`); None when either is unknown."""
    if not ctx or not ctx.get("gameday") or not ctx.get("gametime"):
        return None
    try:
        return datetime.strptime(f"{ctx['gameday']} {ctx['gametime']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    except ValueError:
        return None


def _has_kicked_off(ctx, now):
    kickoff = kickoff_time(ctx)
    return kickoff is not None and kickoff <= now


def locked_teams(context, now):
    """Teams whose game has kicked off by `now`; Sleeper locks their players at kickoff."""
    return frozenset(team for team, ctx in context.items() if _has_kicked_off(ctx, now))


def fmt_kickoff(when):
    """'Thu 8:35 pm ET' (the %-I directive is POSIX-only, which covers macOS and Linux)."""
    return when.strftime("%a %-I:%M %p ET").replace("AM", "am").replace("PM", "pm")


def game_label(context, teams):
    """'SF at LAR', or 'LAR vs SF' when the game is on a neutral site."""
    away = [t for t in teams if (context.get(t) or {}).get("site") == "away"]
    home = [t for t in teams if (context.get(t) or {}).get("site") == "home"]
    if away and home:
        return f"{away[0]} at {home[0]}"
    return " vs ".join(sorted(teams))


def next_kickoffs(context, now):
    """[(kickoff, frozenset(teams))] for the week's games still to start, earliest first.

    Each game appears twice in the context (once per team) and a neutral-site game
    has no home side, so games are keyed by the pair of teams.
    """
    games = {}
    for team, ctx in context.items():
        kickoff = kickoff_time(ctx)
        if kickoff is not None and kickoff > now:
            games[frozenset({team, ctx.get("opponent")})] = kickoff
    return sorted(((kickoff, teams) for teams, kickoff in games.items()), key=lambda pair: (pair[0], sorted(pair[1])))
