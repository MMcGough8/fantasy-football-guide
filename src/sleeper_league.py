"""Read-only client for the public Sleeper API (no auth required)."""
import re

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


LAST_WEEK = 18


def get_nfl_state():
    """Sleeper's current NFL season and week ({"season", "week", "season_type", ...})."""
    state = _get("state/nfl")
    if not state or state.get("week") is None:
        raise SleeperError("Sleeper did not report the current NFL week")
    return state


def lineup_week(state):
    """The week a lineup page opens on: 1 in the preseason, the live week in season, 18 after."""
    kind = state.get("season_type")
    if kind == "pre":
        return 1
    if kind == "regular":
        return int(state.get("week") or 1)
    return LAST_WEEK


def find_my_roster(rosters, user_id):
    """The roster this user owns or co-owns, or None (unclaimed rosters have no owner)."""
    if not user_id:
        return None
    for roster in rosters or []:
        if roster.get("owner_id") == user_id or user_id in (roster.get("co_owners") or []):
            return roster
    return None


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


def get_trending_adds(lookback_hours=24, limit=50):
    """Sleeper's most-added players over the window: [{"player_id", "count"}]."""
    return _get(f"players/nfl/trending/add?lookback_hours={lookback_hours}&limit={limit}")


def get_draft(draft_id):
    draft = _get(f"draft/{draft_id}")
    if not draft:
        raise SleeperError(f"Draft {draft_id} not found")
    return draft


def get_draft_picks(draft_id):
    return _get(f"draft/{draft_id}/picks") or []


DRAFT_ID_PATTERN = re.compile(r"(\d{15,})")


def parse_draft_id(text):
    """A bare draft id or any Sleeper draft URL -> the id, else None."""
    match = DRAFT_ID_PATTERN.search(text or "")
    return match.group(1) if match else None


def my_roster_id_from_draft(draft, user_id):
    """roster_id for `user_id` from the draft's published order (works for mock drafts)."""
    order = draft.get("draft_order") or {}
    slot = order.get(user_id)
    if slot is None:
        return None
    return (draft.get("slot_to_roster_id") or {}).get(str(slot))


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
