"""Defense vs position: how many fantasy points each defense allows to each
position, from nflverse's free weekly player stats.

No free feed publishes this, so it is computed here: per (defense, position,
week) the PPR points of every opposing player are summed, games are averaged,
and each defense is expressed relative to the league mean. Last season is the
prior; the current season blends in as its weeks are published.
"""
from matchups import DVP_POSITIONS, MatchupError, fetch_csv, to_sleeper_team

STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
PRIOR_GAMES = 4  # the current season carries weight n / (n + PRIOR_GAMES)
DVP_MIN, DVP_MAX = 0.8, 1.2


def parse_player_weeks(rows):
    """Regular-season player-weeks with points: [{week, position, opponent, points}]."""
    weeks = []
    for row in rows:
        if row.get("season_type") != "REG" or row.get("fantasy_points_ppr") in (None, ""):
            continue
        weeks.append({
            "week": int(row["week"]),
            "position": row.get("position"),
            "opponent": to_sleeper_team(row.get("opponent_team")),
            "points": float(row["fantasy_points_ppr"]),
        })
    return weeks


def allowed_per_game(weeks):
    """{defense: {position: {"allowed": mean points per game, "games": n}}}."""
    per_game = {}
    for w in weeks:
        if w["position"] not in DVP_POSITIONS:
            continue
        key = (w["opponent"], w["position"], w["week"])
        per_game[key] = per_game.get(key, 0.0) + w["points"]
    totals = {}
    for (team, position, _week), points in per_game.items():
        entry = totals.setdefault(team, {}).setdefault(position, {"allowed": 0.0, "games": 0})
        entry["allowed"] += points
        entry["games"] += 1
    return {
        team: {pos: {"allowed": round(e["allowed"] / e["games"], 2), "games": e["games"]} for pos, e in by_pos.items()}
        for team, by_pos in totals.items()
    }


def factors(allowed):
    """Each defense relative to the league mean per position, ranked 1 = stingiest."""
    result = {team: {} for team in allowed}
    for position in DVP_POSITIONS:
        teams = [(team, by_pos[position]) for team, by_pos in allowed.items() if position in by_pos]
        if not teams:
            continue
        mean = sum(e["allowed"] for _, e in teams) / len(teams)
        ranked = sorted(teams, key=lambda pair: pair[1]["allowed"])
        for rank, (team, entry) in enumerate(ranked, start=1):
            result[team][position] = {
                "factor": entry["allowed"] / mean if mean else 1.0,
                "rank": rank,
                "allowed": entry["allowed"],
                "games": entry["games"],
            }
    return result


def blend_seasons(prior, current):
    """Prior-season factors nudged toward the current season by games played, clamped,
    then re-ranked per position on the blended factor."""
    blended = {}
    for team in set(prior) | set(current):
        blended[team] = {}
        for position in set(prior.get(team, {})) | set(current.get(team, {})):
            p = prior.get(team, {}).get(position)
            c = current.get(team, {}).get(position)
            base = p["factor"] if p else 1.0
            if c:
                w = c["games"] / (c["games"] + PRIOR_GAMES)
                factor = (1 - w) * base + w * c["factor"]
            else:
                factor = base
            source = c or p
            blended[team][position] = {
                "factor": round(max(DVP_MIN, min(DVP_MAX, factor)), 4),
                "allowed": source["allowed"],
                "games": source["games"],
            }
    for position in DVP_POSITIONS:
        ranked = sorted((team for team in blended if position in blended[team]), key=lambda t: blended[t][position]["factor"])
        for rank, team in enumerate(ranked, start=1):
            blended[team][position]["rank"] = rank
    return blended


def fetch_player_weeks(season):
    try:
        rows = fetch_csv(STATS_URL.format(season=season))
    except MatchupError as e:
        raise MatchupError(f"nflverse weekly stats for {season} are not published yet ({e})") from e
    return parse_player_weeks(rows)


def dvp_entry(dvp, team, position):
    return ((dvp or {}).get(team) or {}).get(position)
