from statistics import median

import requests

from espn_ranks import attach_espn_ranks, match_key
from scoring import score_stats

_bye_cache = None

SEASON = "2026"
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]
MIN_POINTS = 1.0  # drop inactive/depth players with negligible projections
TIER_GAP = 15.0  # points drop-off that starts a new within-position tier
GLOBAL_TIER_GAP = 12.0  # VOR drop-off that starts a new cross-position tier

# How a flex slot's demand is split across the positions it accepts.
FLEX_SHARES = {
    "FLEX": {"RB": 0.5, "WR": 0.5},
    "SUPER_FLEX": {"QB": 0.8, "RB": 0.1, "WR": 0.1},
    "REC_FLEX": {"WR": 0.8, "TE": 0.2},
    "WRRB_FLEX": {"RB": 0.5, "WR": 0.5},
}


def replacement_ranks(num_teams, starters=None):
    """How many of each position get started league-wide: the replacement level."""
    if not starters:
        return {
            "QB": num_teams,
            "RB": num_teams * 2 + 6,
            "WR": num_teams * 2 + 6,
            "TE": num_teams,
            "K": num_teams,
            "DEF": num_teams,
        }
    demand = {pos: float(starters.get(pos, 0)) for pos in POSITIONS}
    for slot, shares in FLEX_SHARES.items():
        for pos, share in shares.items():
            demand[pos] += starters.get(slot, 0) * share
    return {pos: max(1, round(num_teams * demand[pos])) for pos in POSITIONS}


def blend_stats(lines):
    """Combine stat lines key by key: median of 3+, mean of 2, pass-through of 1."""
    blended = {}
    for key in {k for line in lines for k in line}:
        values = [line[key] for line in lines if line.get(key) is not None]
        if len(values) >= 3:
            blended[key] = median(values)
        elif len(values) == 2:
            blended[key] = (values[0] + values[1]) / 2
        elif values:
            blended[key] = values[0]
    return blended


def _points(stats, scoring, scoring_settings, position):
    if scoring_settings:
        return score_stats(stats, scoring_settings, position)
    return stats.get(scoring)


def adp_key_for(scoring, scoring_settings):
    """Which Sleeper ADP column best matches the league's reception scoring."""
    if scoring_settings:
        rec = scoring_settings.get("rec") or 0
        if rec >= 0.75:
            return "adp_ppr"
        return "adp_half_ppr" if rec >= 0.25 else "adp_std"
    return {"pts_ppr": "adp_ppr", "pts_half_ppr": "adp_half_ppr"}.get(scoring, "adp_std")


def fetch_position(position, scoring="pts_ppr", scoring_settings=None, extra_sources=None):
    """Get all projected players for one position from Sleeper.

    With `scoring_settings` (a Sleeper league's scoring map) points are recomputed
    from raw stat projections; otherwise Sleeper's preset total `scoring` is used.
    `extra_sources` is {source_name: {match_key: stats}} for additional projection
    feeds (FantasyPros, ESPN); the stat lines are combined by `blend_stats`.
    """
    adp_key = adp_key_for(scoring, scoring_settings)
    url = f"https://api.sleeper.com/projections/nfl/{SEASON}"
    params = {"season_type": "regular", "position[]": position, "order_by": scoring}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()

    players = []
    for rec in resp.json():
        sleeper_stats = rec.get("stats") or {}
        info = rec.get("player") or {}
        name = f"{info.get('first_name', '')} {info.get('last_name', '')}".strip()
        if not name:
            name = f"{info.get('team') or 'FA'} DST"

        key = match_key(name, position)
        lines = {"sleeper": sleeper_stats}
        for source, table in (extra_sources or {}).items():
            line = table.get(key)
            if line is None:
                continue
            if "gp" not in line and sleeper_stats.get("gp"):
                # games played drives the per-game bonus estimate; not every feed has it
                line = {**line, "gp": sleeper_stats["gp"]}
            lines[source] = line
        # A feed that scores a player at zero (Sleeper publishes empty stat lines
        # for IR players) is not a source; drop it so `sources` stays honest.
        points_by_source = {
            source: _points(line, scoring, scoring_settings, position) for source, line in lines.items()
        }
        lines = {s: line for s, line in lines.items() if (points_by_source.get(s) or 0) > 0}
        if not lines:
            continue
        stats = blend_stats(list(lines.values())) if len(lines) > 1 else next(iter(lines.values()))
        points = _points(stats, scoring, scoring_settings, position)
        if points is None or points < MIN_POINTS:
            continue
        points_by_source = {s: round(points_by_source[s], 1) for s in lines}

        players.append(
            {
                "name": name,
                "position": position,
                "player_id": rec.get("player_id"),
                "team": info.get("team") or "FA",
                "points": round(points, 1),
                "points_by_source": points_by_source,
                "sources": len(lines),
                "adp": sleeper_stats.get(adp_key),
                "years_exp": info.get("years_exp"),
                "touches": (stats.get("rush_att") or 0) + (stats.get("rec") or 0),
                "tds": (stats.get("rush_td") or 0) + (stats.get("rec_td") or 0),
                "big_plays": stats.get("rec_40p") or 0,
                "receptions": stats.get("rec") or 0,
            }
        )

    players.sort(key=lambda p: p["points"], reverse=True)
    return players


def add_value_over_replacement(players, position, replacement_rank):
    rank = replacement_rank[position]
    # If fewer players than the rank, use the last one as replacement
    idx = min(rank, len(players)) - 1
    replacement_points = players[idx]["points"] if players else 0
    for p in players:
        p["vor"] = round(p["points"] - replacement_points, 1)
    return players


def assign_tiers(players, gap=TIER_GAP):
    """Within a position, a new tier starts on a big scoring drop-off."""
    tier = 1
    for i, p in enumerate(players):
        if i > 0 and (players[i - 1]["points"] - p["points"]) > gap:
            tier += 1
        p["tier"] = tier
    return players


def get_bye_weeks(season=SEASON):
    """Return {team: bye_week} by finding which week each team has no game."""
    global _bye_cache
    if _bye_cache is not None:
        return _bye_cache

    url = f"https://api.sleeper.app/schedule/nfl/regular/{season}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    games = resp.json()

    weeks_played = {}
    all_weeks = set()
    for g in games:
        wk = g.get("week")
        if wk is None or g.get("status") == "canceled":
            continue
        all_weeks.add(wk)
        for side in ("home", "away"):
            team = g.get(side)
            if team:
                weeks_played.setdefault(team, set()).add(wk)

    # A team's bye is the regular-season week they don't appear
    regular_weeks = {w for w in all_weeks if w <= 18}
    byes = {}
    for team, played in weeks_played.items():
        missing = regular_weeks - played
        if missing:
            byes[team] = min(missing)

    _bye_cache = byes
    return byes


def build_board(
    scoring="pts_ppr",
    num_teams=12,
    scoring_settings=None,
    starters=None,
    extra_projections=None,
    live_espn_ranks=None,
):
    """`extra_projections` is {source_name: {position: {match_key: stats}}}."""
    ranks = replacement_ranks(num_teams, starters)

    board = []
    for pos in POSITIONS:
        extra_sources = {
            source: tables[pos]
            for source, tables in (extra_projections or {}).items()
            if tables.get(pos)
        }
        players = fetch_position(pos, scoring, scoring_settings, extra_sources)
        players = add_value_over_replacement(players, pos, ranks)
        players = assign_tiers(players)
        board.extend(players)

    byes = get_bye_weeks()
    for p in board:
        p["bye"] = byes.get(p["team"])

    board.sort(key=lambda p: p["vor"], reverse=True)

    global_tier = 1
    for i, p in enumerate(board):
        if i > 0 and (board[i - 1]["vor"] - p["vor"]) > GLOBAL_TIER_GAP:
            global_tier += 1
        p["global_tier"] = global_tier

    return attach_espn_ranks(board, live_espn_ranks)


if __name__ == "__main__":
    board = build_board()

    print(
        f"{'#':>3}  {'PLAYER':24} {'POS':4} {'TEAM':4} {'PROJ':>6} {'VOR':>6} {'TIER':>4}"
    )
    print("-" * 60)
    for i, p in enumerate(board[:60], start=1):
        tier_label = f"{p['position']}{p['tier']}"
        print(
            f"{i:>3}  {p['name']:24} {p['position']:4} {p['team']:4} "
            f"{p['points']:>6} {p['vor']:>6} {tier_label:>4}"
        )
