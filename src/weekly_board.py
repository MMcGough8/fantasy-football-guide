"""One NFL week's projections, blended across feeds and scored with the league's rules.

Reuses the draft board's fetch path (`draft_board.fetch_position` with `week`), so
the per-stat median blend, the league rescoring and the K/DEF preset totals are the
same code the draft used. Rows are keyed by Sleeper `player_id`, which is also what
Sleeper rosters list (defenses are team codes like "HOU").
"""
from draft_board import POSITIONS, fetch_position
from espn_ranks import match_key

# weekly row field -> FantasyPros weekly rank entry field
RANK_FIELDS = {
    "fp_week_rank": "rank",
    "fp_week_pos_rank": "pos_rank",
    "fp_week_rank_std": "std",
    "fp_grade": "grade",
    "fp_opponent": "opponent",
}
NO_PROJECTION = "no projection"
BYE = "bye"


def build_weekly_pool(week, scoring_settings, extra_projections=None):
    """{player_id: row} for every position, scored with the league's rules for `week`.

    `extra_projections` is {source: {position: {match_key: stats}}} from the other feeds.
    """
    pool = {}
    for position in POSITIONS:
        extra = {
            source: tables[position]
            for source, tables in (extra_projections or {}).items()
            if tables.get(position)
        }
        for row in fetch_position(position, "pts_ppr", scoring_settings, extra, week=week):
            if row.get("player_id"):
                pool[str(row["player_id"])] = row
    return pool


def attach_weekly_ranks(pool, fp_ranks):
    """A new pool with the FantasyPros weekly consensus joined on by name (None when absent)."""
    ranked = {}
    for player_id, row in pool.items():
        entry = (fp_ranks or {}).get(match_key(row["name"], row["position"])) or {}
        ranked[player_id] = {**row, **{field: entry.get(source) for field, source in RANK_FIELDS.items()}}
    return ranked


def roster_rows(player_ids, pool, season_index, byes, week):
    """One row per rostered player, in roster order.

    Players missing from the weekly pool fall back to the season board for their
    identity and score 0 with a `reason`: "bye" when their team is off, else
    "no projection".
    """
    rows, seen = [], set()
    for raw_id in player_ids:
        player_id = str(raw_id)
        if player_id in seen:
            continue
        seen.add(player_id)
        if player_id in pool:
            rows.append({**pool[player_id], "reason": None})
            continue
        base = season_index.get(player_id) or {
            "name": f"Unknown {player_id}", "position": "?", "team": None, "player_id": player_id,
        }
        on_bye = (byes or {}).get(base.get("team")) == week or base.get("bye") == week
        rows.append({
            **base,
            "player_id": player_id,
            "points": 0.0,
            "points_by_source": {},
            "sources": 0,
            "opponent": None,
            "adjusted_points": 0.0,
            "matchup": None,
            "reason": BYE if on_bye else NO_PROJECTION,
        })
    return rows
