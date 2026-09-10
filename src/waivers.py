"""Waiver targets for a Sleeper league: who on the wire improves my lineup this week
or over the season, and who on my bench is expendable.

Pure functions over weekly rows (the weekly pool, `weekly_board.roster_rows`) and
season rows (the draft board scored with this league's rules). The app and
`waiver_report.py` do the fetching.
"""
from dataclasses import dataclass

from lineup import lineup_points, optimal_lineup, reachable_lineup
from recommend import is_unavailable
from roster_slots import allocate_slots

WEEKLY_ONLY_POSITIONS = ("K", "DEF")  # streamed, never a season-long add
WAIVER_TYPES = {0: "rolling", 1: "reverse standings", 2: "FAAB"}


def rostered_ids(rosters):
    """Every player id somebody in the league holds, including reserve and taxi."""
    ids = set()
    for roster in rosters:
        for field in ("players", "reserve", "taxi"):
            ids.update(str(pid) for pid in (roster.get(field) or []))
    return ids


def free_agents(pool, rosters):
    taken = rostered_ids(rosters)
    return [row for pid, row in pool.items() if str(pid) not in taken]


def best_points(rows, starters, key="points", respect_injury=True, current=None):
    """Points of the best legal lineup. With `respect_injury` off, Out and IR players
    count: the season view treats an injured stud as a keeper, not a hole. With
    `current` (this week's lineup), kickoff locks apply: locked starters stay put and
    a locked bench player or free agent cannot start (`lineup.reachable_lineup`)."""
    if current is not None:
        return lineup_points(reachable_lineup(rows, current, starters, key=key), key=key)
    if respect_injury:
        return lineup_points(optimal_lineup(rows, starters, key=key), key=key)
    return lineup_points(allocate_slots(rows, starters, key=key).slots, key=key)


def lineup_gain(candidate, my_rows, starters, key="points", respect_injury=True, current=None):
    """How much the best lineup improves with `candidate` on the roster."""
    without = best_points(my_rows, starters, key, respect_injury, current)
    return _gain_over(candidate, my_rows, without, starters, key, respect_injury, current)


def _gain_over(candidate, my_rows, baseline, starters, key, respect_injury, current=None):
    with_him = best_points(list(my_rows) + [candidate], starters, key, respect_injury, current)
    return round(with_him - baseline, 1)


@dataclass(frozen=True)
class _Baselines:
    """My roster and its best-lineup points, computed once for every candidate."""
    week_rows: list
    week_base: float
    season_rows: list
    season_base: float
    season_index: dict
    starters: dict
    key: str
    trending: dict
    current: dict | None


def _target(row, b):
    season_row = b.season_index.get(str(row["player_id"]))
    season_gain = 0.0
    if season_row is not None and not is_unavailable(season_row) and not is_unavailable(row):
        season_gain = _gain_over(season_row, b.season_rows, b.season_base, b.starters, "points", False)
    return {
        "row": row,
        "week_gain": _gain_over(row, b.week_rows, b.week_base, b.starters, b.key, True, b.current),
        "season_gain": season_gain,
        "has_season": season_row is not None,
        "adds": b.trending.get(str(row["player_id"]), 0),
    }


def season_value(season_row):
    """A player's season worth for cross-position comparison: the board's value over
    replacement when it has one (raw points would rank a QB3 above a TE2 in a one-QB
    league), else his season points; None without a season row."""
    if season_row is None:
        return None
    return season_row["vor"] if season_row.get("vor") is not None else season_row.get("points")


def _depth_upgrades(entries, season_index, expendable, listed_ids):
    """Free agents worth more over the season than my least valuable expendable player.
    Long-term stashes (IR, PUP, ...) are not upgrades to anything."""
    floor = next((d for d in expendable if d["season_value"] is not None), None)
    if floor is None:
        return []
    upgrades = []
    for entry in entries:
        row = entry["row"]
        season_row = season_index.get(str(row["player_id"]))
        if season_row is None or row["position"] in WEEKLY_ONLY_POSITIONS or row["player_id"] in listed_ids:
            continue
        if is_unavailable(row) or is_unavailable(season_row):
            continue
        value = season_value(season_row)
        if value is not None and value > floor["season_value"]:
            upgrades.append({**entry, "season_points": season_row.get("points"), "season_value": value, "over": floor["row"]})
    upgrades.sort(key=lambda e: -e["season_value"])
    return upgrades


def waiver_targets(free, my_week_rows, my_season_rows, season_index, starters, key, trending, limit=8, current=None):
    """{"season": adds worth holding, "week": streamers, "depth": bench upgrades}.

    A season add improves the season lineup (K and DEF never qualify, nor does a
    player on IR/PUP); a streamer improves this week's lineup without improving the
    season one, which is what a bye or injury filler looks like; a depth upgrade
    improves neither lineup but is worth more over the season than my least valuable
    droppable player. With `current`, this week's gains respect kickoff locks (a
    locked free agent cannot start). Every gain assumes a free roster spot.
    """
    b = _Baselines(
        week_rows=list(my_week_rows),
        week_base=best_points(my_week_rows, starters, key, current=current),
        season_rows=list(my_season_rows),
        season_base=best_points(my_season_rows, starters, "points", respect_injury=False),
        season_index=season_index, starters=starters, key=key, trending=trending, current=current,
    )
    entries = [_target(row, b) for row in free]

    def weekly_only(entry):
        return entry["row"]["position"] in WEEKLY_ONLY_POSITIONS

    season = [e for e in entries if e["season_gain"] > 0 and not weekly_only(e)]
    week = [e for e in entries if e["week_gain"] > 0 and (e["season_gain"] <= 0 or weekly_only(e))]
    season.sort(key=lambda e: (-e["season_gain"], -e["week_gain"], e["row"]["name"]))
    week.sort(key=lambda e: (-e["week_gain"], -e["season_gain"], e["row"]["name"]))
    listed = {e["row"]["player_id"] for e in season[:limit] + week[:limit]}
    expendable = _expendable(my_week_rows, my_season_rows, starters, key, current)
    depth = _depth_upgrades(entries, season_index, expendable, listed)
    return {"season": season[:limit], "week": week[:limit], "depth": depth[:limit]}


def _expendable(my_week_rows, my_season_rows, starters, key, current=None):
    """[{"row", "season_points", "season_value"}] for my players outside both this week's
    lineup and the season lineup, least valuable (lowest season VOR) first; players without
    a season projection come last, long-term stashes (IR, PUP, ...) never appear."""
    week_lineup = reachable_lineup(my_week_rows, current, starters, key=key) if current is not None else optimal_lineup(my_week_rows, starters, key=key)
    week_ids = {r["player_id"] for rows in week_lineup.values() for r in rows if r}
    season_slots = allocate_slots(my_season_rows, starters, key="points").slots
    season_ids = {r["player_id"] for rows in season_slots.values() for r in rows}
    by_id = {r["player_id"]: r for r in my_season_rows}
    expendable = [
        r for r in my_week_rows
        if r["player_id"] not in week_ids and r["player_id"] not in season_ids and not is_unavailable(r)
    ]

    def value(row):
        return season_value(by_id.get(row["player_id"]))

    expendable.sort(key=lambda r: (value(r) is None, value(r) or 0))
    return [
        {"row": r, "season_points": (by_id.get(r["player_id"]) or {}).get("points"), "season_value": value(r)}
        for r in expendable
    ]


def drop_candidates(my_week_rows, my_season_rows, starters, key="points", limit=3, current=None):
    """The `limit` least valuable expendable players (see `_expendable`)."""
    return _expendable(my_week_rows, my_season_rows, starters, key, current)[:limit]


def waiver_settings(league):
    """How the league runs waivers, from Sleeper's league settings."""
    settings = league.get("settings") or {}
    return {
        "type": WAIVER_TYPES.get(settings.get("waiver_type"), "unknown"),
        "budget": settings.get("waiver_budget"),
        "clear_days": settings.get("waiver_clear_days"),
    }


def faab_left(roster, budget):
    if budget is None:
        return None
    used = (roster.get("settings") or {}).get("waiver_budget_used") or 0
    return budget - used


def parse_trending(payload):
    """{player_id: adds in the lookback window} from Sleeper's trending-adds payload."""
    return {str(entry["player_id"]): entry.get("count", 0) for entry in (payload or []) if entry.get("player_id") is not None}
