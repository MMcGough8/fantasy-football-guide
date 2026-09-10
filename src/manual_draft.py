"""A draft the app cannot sync (Yahoo, ESPN, a paper draft) run from a hand-kept pick log.

The owner marks every pick in order (Quick Entry), the log length is the pick
count, and the snake order plus the owner's slot(s) give "your pick in N" for
each team the household controls. The result is a Sleeper-shaped draft dict and
a `draft_info` payload, so the rest of the app (status strip, lookahead, the
opponent-needs model, the K/DEF run rule) works exactly as it does when synced.
"""
from pick_sync import _slot_for_pick, next_pick_info

DRAFT_TYPE = "snake"


def build_draft(teams, rounds, slots):
    """Sleeper-shaped draft dict. `slots` is {team_name: 1-based draft slot}."""
    if teams < 2 or rounds < 1:
        raise ValueError("a manual draft needs at least 2 teams and 1 round")
    for name, slot in slots.items():
        if not 1 <= slot <= teams:
            raise ValueError(f"{name}: slot {slot} is outside 1..{teams}")
    return {
        "type": DRAFT_TYPE,
        "draft_order": dict(slots),
        "slot_to_roster_id": {str(s): s for s in range(1, teams + 1)},
        "settings": {"teams": teams, "rounds": rounds},
    }


def pick_positions(pick_log, teams, position_of):
    """The per-pick payload the opponent-needs model reads: which slot picked, what position.

    Marks are assumed to be entered in draft order, so pick i belongs to the slot
    the snake puts on the clock for pick i.
    """
    return [
        {
            "roster_id": _slot_for_pick(i, teams, DRAFT_TYPE)[1],
            "metadata": {"position": position_of(entry["key"])},
        }
        for i, entry in enumerate(pick_log, start=1)
    ]


def draft_info(draft, pick_log, active_team, position_of, complete=False):
    """What `sync_picks_from_sleeper` would have stored, built from the pick log. A hand-kept
    draft never fills every pick, so the owner ends it (`complete`) rather than the log."""
    picks = len(pick_log)
    settings = draft["settings"]
    return {
        "status": "complete" if complete else "drafting" if picks else "pre_draft",
        "has_order": True,
        "picks": picks,
        "next": next_pick_info(draft, picks, active_team),
        "rounds": settings["rounds"],
        "draft_meta": draft,
        "pick_positions": pick_positions(pick_log, settings["teams"], position_of),
        "unmatched": 0,
        "unmatched_names": [],
        "manual": True,
    }
