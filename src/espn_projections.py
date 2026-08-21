"""ESPN's public fantasy API: season projections, ESPN ranks, and ADP. No auth."""
import json

import requests

from espn_ranks import match_key

BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons"
TIMEOUT_SECONDS = 30
PLAYERS_PER_POSITION = 300
PROJECTION_SOURCE_ID = 1  # statSourceId 0 = actual, 1 = projected
SEASON_SPLIT_ID = 0  # statSplitTypeId 0 = full season

# Board position -> ESPN lineup slot id used to filter the request
SLOT_IDS = {"QB": 0, "RB": 2, "WR": 4, "TE": 6, "K": 17, "DEF": 16}

# ESPN numeric stat ids -> Sleeper stat names. Verified against ESPN's own
# appliedTotal under their default PPR scoring.
STAT_IDS = {
    "0": "pass_att",
    "1": "pass_cmp",
    "3": "pass_yd",
    "4": "pass_td",
    "19": "pass_2pt",
    "20": "pass_int",
    "23": "rush_att",
    "24": "rush_yd",
    "25": "rush_td",
    "26": "rush_2pt",
    "42": "rec_yd",
    "43": "rec_td",
    "44": "rec_2pt",
    "53": "rec",
    "72": "fum_lost",
    "210": "gp",
}
# ESPN's default league scoring is PPR for skill players; K/DEF use their
# standard scales, which the board treats as preset totals anyway.
APPLIED_TOTAL_KEY = {"K": "pts_std", "DEF": "pts_std"}
DEFAULT_APPLIED_TOTAL_KEY = "pts_ppr"
RANK_TYPE = "PPR"


class EspnError(Exception):
    """Raised for any failure talking to ESPN, with a user-facing message."""


def _season_projection(player, season):
    """The full-season projection block for `season`. ESPN also ships last
    season's block first, so never take the first match."""
    for s in player.get("stats") or []:
        if (
            s.get("statSourceId") == PROJECTION_SOURCE_ID
            and s.get("statSplitTypeId") == SEASON_SPLIT_ID
            and str(s.get("seasonId")) == str(season)
        ):
            return s
    return None


def parse_players(entries, position, season):
    """Return {match_key: {"stats", "espn_rank", "adp"}} for one position."""
    rows = {}
    for entry in entries:
        player = entry.get("player") or {}
        name = player.get("fullName")
        proj = _season_projection(player, season)
        if not name or proj is None:
            continue
        stats = {
            STAT_IDS[sid]: value
            for sid, value in (proj.get("stats") or {}).items()
            if sid in STAT_IDS
        }
        total = proj.get("appliedTotal")
        if total is not None:
            stats[APPLIED_TOTAL_KEY.get(position, DEFAULT_APPLIED_TOTAL_KEY)] = round(total, 1)
        rank = ((player.get("draftRanksByRankType") or {}).get(RANK_TYPE) or {}).get("rank")
        adp = (player.get("ownership") or {}).get("averageDraftPosition")
        rows[match_key(name, position)] = {"stats": stats, "espn_rank": rank, "adp": adp}
    return rows


def fetch_position(position, season):
    flt = {
        "players": {
            "filterSlotIds": {"value": [SLOT_IDS[position]]},
            "limit": PLAYERS_PER_POSITION,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
            "filterStatsForSourceIds": {"value": [PROJECTION_SOURCE_ID]},
            "filterStatsForSplitTypeIds": {"value": [SEASON_SPLIT_ID]},
        }
    }
    try:
        resp = requests.get(
            f"{BASE_URL}/{season}/segments/0/leaguedefaults/3",
            params={"view": "kona_player_info"},
            headers={"X-Fantasy-Filter": json.dumps(flt)},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as e:
        raise EspnError(f"Couldn't reach ESPN ({e})") from e
    except ValueError as e:
        raise EspnError(f"ESPN returned an unexpected response ({e})") from e
    entries = payload.get("players") if isinstance(payload, dict) else None
    if entries is None:
        raise EspnError("ESPN response had no players")
    return parse_players(entries, position, season)


def fetch_all(season):
    """Return {"projections": {position: {key: stats}}, "ranks": {key: espn_rank},
    "missing": [positions that failed]}."""
    projections, ranks, missing = {}, {}, []
    for position in SLOT_IDS:
        try:
            rows = fetch_position(position, season)
        except EspnError:
            missing.append(position)
            continue
        projections[position] = {k: r["stats"] for k, r in rows.items()}
        ranks.update({k: r["espn_rank"] for k, r in rows.items() if r["espn_rank"]})
    if not projections:
        raise EspnError("ESPN returned nothing for any position")
    return {"projections": projections, "ranks": ranks, "missing": missing}
