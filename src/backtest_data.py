"""Pull and cache past seasons of the weekly feeds, and turn a cached season into rows.

Cache layout: `<cache>/<season>/{proj,stats,fp,fpranks,espn,actual}_<week>.json` plus
`<cache>/games.csv` (nflverse). A transient feed failure is retried with a long backoff and
never cached, so a rerun fills the hole; only a definitive "nothing stored for that week"
answer is cached as missing. Network lives here and only here.
"""
import csv
import io
import json
import os
import time
from datetime import date
from statistics import median

import requests

import espn_projections
import fantasypros
from espn_ranks import match_key
from matchups import SCHEDULE_URL, parse_schedule, team_context
from projection_log import ActualsError, fetch_actual_points

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(REPO_ROOT, ".backtest_cache")
BACKOFF_SECONDS = (15, 30, 60, 120)
WEEKS = range(1, 19)
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
STATS = ("pass_yd", "pass_td", "rush_yd", "rec", "rec_yd", "rec_td", "rush_td")
PPR = {"pass_yd": 0.04, "pass_td": 4, "pass_int": -1, "rush_yd": 0.1, "rush_td": 6, "rec": 1, "rec_yd": 0.1, "rec_td": 6,
       "fum_lost": -2, "pass_2pt": 2, "rush_2pt": 2, "rec_2pt": 2}
SLEEPER_QUERY = "&".join(f"position[]={p}" for p in POSITIONS)


def today():
    return date.today().isoformat()


def _get_json(url):
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


TRANSIENT_MARKS = ("429", "500", "502", "503", "504", "timed out", "connection", "did not answer")
DEFINITIVE_MARKS = ("404", "no players", "nothing for any position", "no weekly rankings", "no projections")


def _definitive(error):
    """A feed error that means 'nothing stored for that week', not 'try again later'."""
    text = str(error).lower()
    if any(mark in text for mark in TRANSIENT_MARKS):
        return False
    return any(mark in text for mark in DEFINITIVE_MARKS)


def _fetch_with_backoff(label, fetch, log):
    for attempt, delay in enumerate(BACKOFF_SECONDS + (None,)):
        try:
            return fetch(), None
        except Exception as e:  # the feed modules raise their own error types; any of them is retryable here
            if _definitive(e) or delay is None:
                return None, e
            log(f"  {label}: {type(e).__name__}, retrying in {delay}s")
            time.sleep(delay)
    return None, None


def cached(path, label, fetch, empty, log):
    """Load `path` or fetch and store it. A transient failure is not stored (a rerun retries);
    a definitive empty answer is stored as `empty` with the error text."""
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    data, error = _fetch_with_backoff(label, fetch, log)
    if data is None:
        if error is not None and _definitive(error) and isinstance(empty, dict):
            data = {**empty, "missing": [str(error)], "definitive": True}
        else:
            log(f"  {label}: giving up for this run ({error}); not cached")
            return empty
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def _drop_undefinitive_empties(folder):
    """An earlier puller cached empty FantasyPros weeks on rate limits; refetch those."""
    for name in os.listdir(folder):
        if not name.startswith("fp_"):
            continue
        path = os.path.join(folder, name)
        with open(path) as f:
            data = json.load(f)
        if not data.get("projections") and not data.get("definitive"):
            os.remove(path)


def pull_season(season, cache_dir=CACHE_DIR, log=print):
    folder = os.path.join(cache_dir, season)
    os.makedirs(folder, exist_ok=True)
    _drop_undefinitive_empties(folder)
    games_path = os.path.join(cache_dir, "games.csv")
    if not os.path.exists(games_path):
        resp = requests.get(SCHEDULE_URL, timeout=120)
        resp.raise_for_status()
        with open(games_path, "w") as f:
            f.write(resp.text)
    key = os.getenv("FANTASYPROS_API_KEY", "").strip()
    for w in WEEKS:
        p = lambda name: os.path.join(folder, f"{name}_{w}.json")
        cached(p("proj"), f"{season} w{w} sleeper projections", lambda: _get_json(f"https://api.sleeper.com/projections/nfl/{season}/{w}?season_type=regular&{SLEEPER_QUERY}"), [], log)
        cached(p("stats"), f"{season} w{w} sleeper stats", lambda: _get_json(f"https://api.sleeper.com/stats/nfl/{season}/{w}?season_type=regular&{SLEEPER_QUERY}"), [], log)
        if key:
            cached(p("fp"), f"{season} w{w} fantasypros", lambda: fantasypros.fetch_all(season, key, week=w), {"projections": {}}, log)
        cached(p("espn"), f"{season} w{w} espn", lambda: espn_projections.fetch_all(season, week=w), {"projections": {}}, log)
        cached(p("actual"), f"{season} w{w} actuals", lambda: _actuals(season, w, folder), {}, log)
        log(f"{season} week {w} cached")


def _actuals(season, week, folder):
    try:
        return fetch_actual_points(season, week, PPR)
    except ActualsError:  # a position without stored stats: fall back to Sleeper's own PPR totals
        with open(os.path.join(folder, f"stats_{week}.json")) as f:
            stats = json.load(f)
        return {str(s["player_id"]): (s.get("stats") or {}).get("pts_ppr", 0.0) for s in stats if (s.get("stats") or {}).get("gp")}


def _load(folder, name, week, default):
    path = os.path.join(folder, f"{name}_{week}.json")
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def _row(p, actual_stats, actual_pts, fp, es, ctx, weather, season, week):
    pl = p.get("player") or {}
    pos = pl.get("position")
    st = p.get("stats") or {}
    pid = str(p["player_id"])
    a = actual_stats.get(pid)
    if pos not in POSITIONS or not st.get("pts_ppr") or not a or not (a.get("gp") or 0):
        return None
    name = f"{pl.get('first_name', '')} {pl.get('last_name', '')}".strip()
    mk = match_key(name, pos)
    feeds = [f for f in (st, (fp.get(pos) or {}).get(mk), (es.get(pos) or {}).get(mk)) if f]
    team = p.get("team")
    c = ctx.get(team) or {}
    home = team if c.get("site") == "home" else c.get("opponent")
    wind, temp = weather.get((str(week), home), (None, None))
    return {
        "season": season, "week": week, "pid": pid, "name": name, "pos": pos, "team": team, "opp": c.get("opponent"),
        "feeds": len(feeds), "proj": {s: median([f.get(s, 0) or 0 for f in feeds]) for s in STATS},
        "ppr_proj": st["pts_ppr"], "ppr_actual": actual_pts.get(pid), "act": {s: a.get(s, 0) or 0 for s in STATS},
        "implied": c.get("implied"), "site": c.get("site"), "roof": c.get("roof"),
        "wind": float(wind) if wind not in (None, "", "NA") else None, "temp": float(temp) if temp not in (None, "", "NA") else None,
        "game": tuple(sorted([team or "", c.get("opponent") or ""])),
    }


def load_season(season, cache_dir=CACHE_DIR):
    """One row per player-week that has a projection and a game played (see `_row`)."""
    folder = os.path.join(cache_dir, season)
    with open(os.path.join(cache_dir, "games.csv")) as f:
        games_all = list(csv.DictReader(io.StringIO(f.read())))
    games = parse_schedule(games_all, season)
    weather = {(g["week"], g["home_team"]): (g.get("wind"), g.get("temp")) for g in games_all if g["season"] == season and g["game_type"] == "REG"}
    rows = []
    for w in WEEKS:
        proj = _load(folder, "proj", w, None)
        if proj is None:
            break
        stats = {str(s["player_id"]): (s.get("stats") or {}) for s in _load(folder, "stats", w, [])}
        fp = _load(folder, "fp", w, {}).get("projections") or {}
        es = _load(folder, "espn", w, {}).get("projections") or {}
        actual_pts = _load(folder, "actual", w, {})
        ctx = team_context(games, w)
        rows += [r for r in (_row(p, stats, actual_pts, fp, es, ctx, weather, season, w) for p in proj) if r]
    return rows
