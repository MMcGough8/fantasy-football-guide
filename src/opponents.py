"""What the other teams need, and how that changes who reaches my pick.

The base survival model is market-wide (ADP and expert spread). The teams that
actually pick between my turns have rosters we can see, so their appetite for
a position scales the hazard: three RB-full teams ahead of me make an RB more
likely to reach me; a TE run in progress makes a TE less likely.
"""
from collections import Counter

from pick_sync import SUPPORTED_DRAFT_TYPES, _slot_for_pick
from recommend import LATE_ONLY_POSITIONS, LATE_ROUND_WINDOW, POSITION_CAPS
from roster_slots import FLEX_ELIGIBILITY

OPEN_SLOT = 1.0  # a dedicated starting slot is open
FLEX_ONLY = 0.6  # only a flex slot is open for this position
BENCH_ONLY = 0.3  # starters filled, still under the cap
NEGLIGIBLE = 0.05  # at the cap, or K/DEF before the final rounds
MULTIPLIER_RANGE = (0.3, 2.5)


def team_counts(picks):
    """{roster_id: {position: n}} from Sleeper picks."""
    counts = {}
    for pick in picks:
        roster_id = pick.get("roster_id")
        pos = (pick.get("metadata") or {}).get("position")
        if roster_id is None or not pos:
            continue
        counts.setdefault(roster_id, Counter())[pos] += 1
    return {rid: dict(c) for rid, c in counts.items()}


def upcoming_rosters(draft, picks_made, n):
    """roster_ids on the clock for the next n picks, in order; [] if the order is unknown."""
    order = draft.get("draft_order")
    if not order or draft.get("type") not in SUPPORTED_DRAFT_TYPES:
        return []
    settings = draft.get("settings") or {}
    if settings.get("reversal_round"):
        return []
    num_teams = settings.get("teams") or len(order)
    total = num_teams * settings.get("rounds", 0)
    slot_to_roster = draft.get("slot_to_roster_id") or {}
    out = []
    for pick_no in range(picks_made + 1, min(picks_made + n, total) + 1):
        _, slot = _slot_for_pick(pick_no, num_teams, draft["type"])
        out.append(slot_to_roster.get(str(slot), slot))
    return out


def demand_factor(counts, pos, starters, current_round, total_rounds):
    """How much a team with `counts` wants `pos` right now, 1.0 = an open starting slot."""
    have = counts.get(pos, 0)
    if have >= POSITION_CAPS.get(pos, 99):
        return NEGLIGIBLE
    if pos in LATE_ONLY_POSITIONS:
        late = total_rounds - current_round + 1 <= LATE_ROUND_WINDOW
        return OPEN_SLOT if late and have < starters.get(pos, 0) else NEGLIGIBLE
    if have < starters.get(pos, 0):
        return OPEN_SLOT
    flex_open = any(
        pos in eligible and _flex_slot_open(counts, slot, starters)
        for slot, eligible in FLEX_ELIGIBILITY.items()
        if starters.get(slot)
    )
    return FLEX_ONLY if flex_open else BENCH_ONLY


def _flex_slot_open(counts, slot, starters):
    """A flex slot is open if the eligible positions' surplus over their own slots
    is smaller than the number of such flex slots."""
    surplus = sum(max(0, counts.get(p, 0) - starters.get(p, 0)) for p in FLEX_ELIGIBILITY[slot])
    return surplus < starters.get(slot, 0)


def demand_multipliers(draft, picks, starters, picks_made, gap):
    """{position: hazard multiplier} for the next `gap` picks, relative to the league
    average appetite. Empty when the draft order is unknown or nothing is ahead."""
    pickers = upcoming_rosters(draft, picks_made, gap)
    if not pickers:
        return {}
    settings = draft.get("settings") or {}
    num_teams = settings.get("teams") or len(draft.get("draft_order") or {})
    total_rounds = settings.get("rounds") or 14
    current_round = picks_made // num_teams + 1
    counts = team_counts(picks)
    all_rosters = list((draft.get("slot_to_roster_id") or {}).values()) or list(range(1, num_teams + 1))

    lo, hi = MULTIPLIER_RANGE
    multipliers = {}
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        ahead = [demand_factor(counts.get(r, {}), pos, starters, current_round, total_rounds) for r in pickers]
        league = [demand_factor(counts.get(r, {}), pos, starters, current_round, total_rounds) for r in all_rosters]
        avg_league = sum(league) / len(league) if league else 1.0
        avg_ahead = sum(ahead) / len(ahead)
        m = avg_ahead / avg_league if avg_league > 0 else 1.0
        multipliers[pos] = round(max(lo, min(hi, m)), 2)
    return multipliers
