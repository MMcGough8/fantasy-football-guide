"""Knockout pools: the owner's picks per pool and week, kept in `.survivor.json` at the repo root
(`SURVIVOR_FILE` overrides the path), graded from final scores. CBS has no usable API, so the
owner records here what they entered there. Weeks are strings on disk and ints in memory; every
function returns a new dict; alive or out is derived, never stored.
"""
import json
import os

DEFAULT_POOLS = ("CBS pool 1", "CBS pool 2")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SURVIVOR_FILE = os.getenv("SURVIVOR_FILE") or os.path.join(REPO_ROOT, ".survivor.json")


def default_pools():
    return {name: {"picks": {}} for name in DEFAULT_POOLS}


def load_pools(path, season):
    """The season's pools, or the defaults on a missing, unreadable, malformed or other-season file
    (the file loads at startup in every mode, so it must never raise)."""
    try:
        with open(path) as f:
            data = json.load(f)
        if str(data.get("season")) != str(season):
            return default_pools()
        return {str(name): {"picks": {int(w): str(t) for w, t in (pool.get("picks") or {}).items()}} for name, pool in data["pools"].items()}
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return default_pools()


def save_pools(path, season, pools):
    """Writes the file; an OSError is the caller's to show."""
    with open(path, "w") as f:
        json.dump({"season": str(season), "pools": {name: {"picks": {str(w): t for w, t in pool["picks"].items()}} for name, pool in pools.items()}}, f, indent=1)


def add_pool(pools, name):
    name = (name or "").strip()
    if not name:
        raise ValueError("a pool needs a name")
    if name in pools:
        raise ValueError(f"there is already a pool called {name}")
    return {**pools, name: {"picks": {}}}


def rename_pool(pools, old, new):
    new = (new or "").strip()
    if not new:
        raise ValueError("a pool needs a name")
    if new != old and new in pools:
        raise ValueError(f"there is already a pool called {new}")
    return {(new if name == old else name): pool for name, pool in pools.items()}


def remove_pool(pools, name):
    return {n: pool for n, pool in pools.items() if n != name}


def used_teams(picks, except_week=None):
    return frozenset(team for week, team in picks.items() if week != except_week)


def record_pick(pools, pool, week, team):
    """The pool with `team` as its week-`week` pick; a team already used in another week is refused."""
    picks = pools[pool]["picks"]
    if team in used_teams(picks, except_week=week):
        raise ValueError(f"{team} is already used in week {next(w for w, t in picks.items() if t == team)}")
    return {**pools, pool: {**pools[pool], "picks": {**picks, week: team}}}


def remove_pick(pools, pool, week):
    return {**pools, pool: {**pools[pool], "picks": {w: t for w, t in pools[pool]["picks"].items() if w != week}}}


def _result(game, team):
    home, away = game.get("home_score"), game.get("away_score")
    if home is None or away is None:
        return "pending", None
    mine, theirs = (home, away) if game["home"] == team else (away, home)
    return ("won" if mine > theirs else "lost"), f"{mine:g}-{theirs:g}"  # a tie is not a win in a straight-up pool


def grade_picks(picks, games):
    """{week: {team, opponent, site, result: won|lost|pending|no game, score, week_over}} for every pick."""
    graded = {}
    for week, team in sorted(picks.items()):
        week_games = [g for g in games if g["week"] == week]
        game = next((g for g in week_games if team in (g["home"], g["away"])), None)
        week_over = bool(week_games) and all(g.get("home_score") is not None for g in week_games)
        if game is None:
            graded[week] = {"team": team, "opponent": None, "site": None, "result": "no game", "score": None, "week_over": week_over}
            continue
        result, score = _result(game, team)
        site = "neutral" if game.get("neutral") else "home" if game["home"] == team else "away"
        opponent = game["away"] if game["home"] == team else game["home"]
        graded[week] = {"team": team, "opponent": opponent, "site": site, "result": result, "score": score, "week_over": week_over}
    return graded


def pool_status(graded):
    """Alive, or out at the first completed week that was lost (or had no game)."""
    for week in sorted(graded):
        entry = graded[week]
        if entry["result"] == "lost" or (entry["result"] == "no game" and entry["week_over"]):
            return {"alive": False, "week": week, "team": entry["team"]}
    return {"alive": True, "week": None, "team": None}
