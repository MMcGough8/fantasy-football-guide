"""Weekly lineup decisions: the owner's current starters against the best legal lineup.

Pure functions over weekly rows (see `weekly_board.roster_rows`): each row carries
`player_id`, `position`, `points` (this week's league-scored projection),
`injury_status` and `reason` (why he cannot score this week, or None).
"""
from recommend import EXCLUDED_STATUSES
from roster_slots import BENCH_SLOTS, FLEX_ELIGIBILITY, allocate_slots, starters_from_roster_positions

# Out and Doubtful never start; the draft's long-term statuses (IR, PUP, ...) neither.
SIT_STATUSES = {"Out", "Doubtful"} | set(EXCLUDED_STATUSES)
# A swap worth less than this is inside projection noise; the page says so.
COIN_FLIP_POINTS = 1.5
EMPTY_SLOT = "0"  # Sleeper's placeholder for an unfilled starting slot


def can_play(row):
    """Whether the row can score this week (Questionable counts as playing)."""
    return row.get("reason") is None and row.get("injury_status") not in SIT_STATUSES


def sit_reason(row):
    return row.get("reason") or row.get("injury_status")


def startable(rows):
    return [r for r in rows if can_play(r)]


def current_lineup(starter_ids, roster_positions, rows_by_id):
    """{slot: [row or None]} in the league's slot order.

    Sleeper lists `starters` in `roster_positions` order without the bench;
    "0" marks an empty slot. Slots the app does not model (IDP, IR, taxi)
    are dropped, which is what `starters_from_roster_positions` does too.
    """
    starters, _ = starters_from_roster_positions(roster_positions)
    # Sleeper lists IR/TAXI after the bench, so keeping them here cannot shift the zip
    slot_order = [slot for slot in roster_positions if slot not in BENCH_SLOTS]
    lineup = {slot: [] for slot in starters}
    for slot, pid in zip(slot_order, starter_ids):
        if slot not in lineup:
            continue
        lineup[slot].append(None if pid in (None, EMPTY_SLOT) else rows_by_id.get(str(pid)))
    return lineup


def optimal_lineup(rows, starters):
    """The best legal lineup by this week's points; slots nobody can fill hold None."""
    slots = allocate_slots(startable(rows), starters, key="points").slots
    return {slot: rows + [None] * (starters[slot] - len(rows)) for slot, rows in slots.items()}


def lineup_points(slots):
    """Projected points of the players listed, whether or not they can play."""
    return round(sum(r["points"] for rows in slots.values() for r in rows if r), 1)


def expected_points(slots):
    """Projected points from the players who can actually play this week."""
    return round(sum(r["points"] for rows in slots.values() for r in rows if r and can_play(r)), 1)


def unplayable_starters(current):
    """[(row, reason)] for current starters who cannot score this week, in slot order."""
    return [(r, sit_reason(r)) for rows in current.values() for r in rows if r and not can_play(r)]


def _player_ids(slots):
    return {r["player_id"] for rows in slots.values() for r in rows if r}


def lineup_diff(current, optimal):
    """The swaps that turn `current` into `optimal`.

    Each incoming player is paired with an outgoing one at the same position when
    there is one, otherwise with the lowest-scoring outgoing player who could have
    held the incoming player's slot; an empty slot pairs with nobody. An outgoing
    player who cannot play counts for zero, so replacing an Out starter is a gain.
    Returns [{"slot", "in", "out", "out_reason", "delta", "coin_flip"}].
    """
    current_ids, optimal_ids = _player_ids(current), _player_ids(optimal)
    incoming = sorted(
        ((slot, r) for slot, rows in optimal.items() for r in rows if r and r["player_id"] not in current_ids),
        key=lambda pair: -pair[1]["points"],
    )
    outgoing = sorted(
        (r for rows in current.values() for r in rows if r and r["player_id"] not in optimal_ids),
        key=lambda r: r["points"] if can_play(r) else 0,
    )
    swaps = []
    for slot, player in incoming:
        eligible = FLEX_ELIGIBILITY.get(slot, (slot,))
        out = next((o for o in outgoing if o["position"] == player["position"]), None)
        if out is None:
            out = next((o for o in outgoing if o["position"] in eligible), None)
        if out is not None:
            outgoing.remove(out)
        out_points = out["points"] if out and can_play(out) else 0
        delta = round(player["points"] - out_points, 1)
        swaps.append({
            "slot": slot,
            "in": player,
            "out": out,
            "out_reason": sit_reason(out) if out and not can_play(out) else None,
            "delta": delta,
            "coin_flip": 0 < delta < COIN_FLIP_POINTS,
        })
    return swaps
