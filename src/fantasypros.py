"""FantasyPros consensus projections (public API v2, needs FANTASYPROS_API_KEY)."""
import requests

from espn_ranks import match_key

BASE_URL = "https://api.fantasypros.com/public/v2/json"
TIMEOUT_SECONDS = 20
PRESEASON_WEEK = 0

# Board position -> FantasyPros position id
FP_POSITIONS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K", "DEF": "DST"}

# FantasyPros stat names -> Sleeper stat names, so one scorer handles both feeds.
# Keys FantasyPros returns but never populates (rush_yds_100, ret_tds, 2pt_tds)
# are deliberately left out.
STAT_MAP = {
    "pass_att": "pass_att",
    "pass_cmp": "pass_cmp",
    "pass_yds": "pass_yd",
    "pass_tds": "pass_td",
    "pass_ints": "pass_int",
    "rush_att": "rush_att",
    "rush_yds": "rush_yd",
    "rush_tds": "rush_td",
    "rec_rec": "rec",
    "rec_yds": "rec_yd",
    "rec_tds": "rec_td",
    "fumbles": "fum_lost",
    "points": "pts_std",
    "points_ppr": "pts_ppr",
    "points_half": "pts_half_ppr",
}


class FantasyProsError(Exception):
    """Raised for any failure talking to FantasyPros, with a user-facing message."""


def to_sleeper_stats(fp_stats):
    return {STAT_MAP[k]: v for k, v in fp_stats.items() if k in STAT_MAP}


def fetch_projections(position, season, api_key):
    """Return {normalized_name: stats_in_sleeper_keys} for one board position."""
    if not api_key:
        raise FantasyProsError("FANTASYPROS_API_KEY is not set")
    params = {"position": FP_POSITIONS[position], "week": PRESEASON_WEEK}
    try:
        resp = requests.get(
            f"{BASE_URL}/nfl/{season}/projections",
            headers={"x-api-key": api_key},
            params=params,
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as e:
        raise FantasyProsError(f"Couldn't reach FantasyPros ({e})") from e
    except ValueError as e:
        raise FantasyProsError(f"FantasyPros returned an unexpected response ({e})") from e

    players = payload.get("players") if isinstance(payload, dict) else None
    if players is None:
        raise FantasyProsError("FantasyPros response had no players")
    return {
        match_key(p["name"], position): to_sleeper_stats(p.get("stats") or {})
        for p in players
        if p.get("name")
    }


def fetch_all(season, api_key):
    """Return {board_position: {normalized_name: stats}} for every position."""
    return {pos: fetch_projections(pos, season, api_key) for pos in FP_POSITIONS}
