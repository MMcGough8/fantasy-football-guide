"""FantasyPros consensus projections (public API v2, needs FANTASYPROS_API_KEY)."""
import time

import requests

from espn_ranks import match_key

BASE_URL = "https://api.fantasypros.com/public/v2/json"
TIMEOUT_SECONDS = 20
RETRY_DELAYS = (0.5, 1.0, 2.0)  # backoff on 429 / 5xx; the public API is rate limited
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
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


def _get_with_retry(url, api_key, params):
    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(attempts):
        try:
            resp = requests.get(
                url, headers={"x-api-key": api_key}, params=params, timeout=TIMEOUT_SECONDS
            )
            if resp.status_code in RETRYABLE_STATUSES and attempt < attempts - 1:
                time.sleep(RETRY_DELAYS[attempt])
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt < attempts - 1:
                time.sleep(RETRY_DELAYS[attempt])
                continue
            raise FantasyProsError(f"Couldn't reach FantasyPros ({e})") from e
        except ValueError as e:
            raise FantasyProsError(f"FantasyPros returned an unexpected response ({e})") from e
    raise FantasyProsError("FantasyPros did not answer")


def to_sleeper_stats(fp_stats):
    return {STAT_MAP[k]: v for k, v in fp_stats.items() if k in STAT_MAP}


def fetch_projections(position, season, api_key):
    """Return {normalized_name: stats_in_sleeper_keys} for one board position."""
    if not api_key:
        raise FantasyProsError("FANTASYPROS_API_KEY is not set")
    params = {"position": FP_POSITIONS[position], "week": PRESEASON_WEEK}
    payload = _get_with_retry(f"{BASE_URL}/nfl/{season}/projections", api_key, params)

    players = payload.get("players") if isinstance(payload, dict) else None
    if players is None:
        raise FantasyProsError("FantasyPros response had no players")
    return {
        match_key(p["name"], position): to_sleeper_stats(p.get("stats") or {})
        for p in players
        if p.get("name")
    }


def scoring_code_for(scoring_settings):
    """FantasyPros scoring code closest to the league's reception value."""
    rec = (scoring_settings or {}).get("rec", 1.0) or 0
    if rec >= 0.75:
        return "PPR"
    return "HALF" if rec >= 0.25 else "STD"


def fetch_consensus_rankings(season, api_key, scoring="HALF"):
    """Expert-consensus draft rankings: {match_key: {rank, std, tier, min, max}}.

    rank_std is the spread of the experts' ranks for the player, a measured
    stand-in for how contested his draft slot is.
    """
    if not api_key:
        raise FantasyProsError("FANTASYPROS_API_KEY is not set")
    params = {"type": "draft", "scoring": scoring, "position": "ALL", "week": PRESEASON_WEEK, "experts": "available"}
    payload = _get_with_retry(f"{BASE_URL}/nfl/{season}/consensus-rankings", api_key, params)
    players = payload.get("players") if isinstance(payload, dict) else None
    if players is None:
        raise FantasyProsError("FantasyPros rankings response had no players")
    ranks = {}
    for p in players:
        name, pos = p.get("player_name"), p.get("player_position_id")
        if not name or pos is None:
            continue
        try:
            ranks[match_key(name, pos)] = {
                "rank": int(p["rank_ecr"]),
                "std": float(p.get("rank_std") or 0),
                "tier": int(p["tier"]) if p.get("tier") is not None else None,
                "min": int(p["rank_min"]) if p.get("rank_min") is not None else None,
                "max": int(p["rank_max"]) if p.get("rank_max") is not None else None,
            }
        except (TypeError, ValueError):
            continue
    return ranks


def fetch_all(season, api_key):
    """Return {"projections": {position: {match_key: stats}}, "missing": [positions]}.

    A position that fails is skipped so one bad response does not drop the feed.
    """
    projections, missing = {}, []
    for pos in FP_POSITIONS:
        try:
            projections[pos] = fetch_projections(pos, season, api_key)
        except FantasyProsError:
            missing.append(pos)
    if not projections:
        raise FantasyProsError("FantasyPros returned nothing for any position")
    return {"projections": projections, "missing": missing}
