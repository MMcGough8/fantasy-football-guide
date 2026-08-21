"""Read-only client for the public Sleeper API (no auth required)."""
import requests

from roster_slots import starters_from_roster_positions

BASE_URL = "https://api.sleeper.app/v1"
TIMEOUT_SECONDS = 8  # draft polling must fail fast; the next poll retries
DYNASTY_LEAGUE_TYPE = 2


class SleeperError(Exception):
    """Raised for any failure talking to Sleeper, with a user-facing message."""


def _get(path):
    try:
        resp = requests.get(f"{BASE_URL}/{path}", timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        raise SleeperError(f"Couldn't reach Sleeper ({e})") from e
    except ValueError as e:
        raise SleeperError(f"Sleeper returned an unexpected response ({e})") from e


def get_user(username):
    user = _get(f"user/{username}")
    if not user:
        raise SleeperError(f"Sleeper user '{username}' not found")
    return user


def get_leagues(user_id, season):
    return _get(f"user/{user_id}/leagues/nfl/{season}") or []


def get_league(league_id):
    league = _get(f"league/{league_id}")
    if not league:
        raise SleeperError(f"League {league_id} not found")
    return league


def get_rosters(league_id):
    return _get(f"league/{league_id}/rosters") or []


def get_draft(draft_id):
    draft = _get(f"draft/{draft_id}")
    if not draft:
        raise SleeperError(f"Draft {draft_id} not found")
    return draft


def get_draft_picks(draft_id):
    return _get(f"draft/{draft_id}/picks") or []


def my_roster_id(rosters, user_id):
    for r in rosters:
        if r.get("owner_id") == user_id or user_id in (r.get("co_owners") or []):
            return r.get("roster_id")
    return None


def league_config(league, user_id):
    """Normalize a Sleeper league payload into what the app needs."""
    starters, bench = starters_from_roster_positions(league.get("roster_positions") or [])
    settings = league.get("settings") or {}
    return {
        "league_id": str(league["league_id"]),
        "name": league.get("name", "Sleeper league"),
        "num_teams": league.get("total_rosters") or len(league.get("roster_positions") or []) or 12,
        "starters": starters,
        "bench": bench,
        "scoring_settings": league.get("scoring_settings") or {},
        "draft_id": str(league["draft_id"]) if league.get("draft_id") else None,
        "is_dynasty": settings.get("type") == DYNASTY_LEAGUE_TYPE,
        "user_id": user_id,
    }
