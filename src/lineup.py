"""Weekly lineup decisions: the owner's current starters against the best legal lineup.

Pure functions over weekly rows (see `weekly_board.roster_rows`): each row carries
`player_id`, `position`, `points` (this week's league-scored projection),
`injury_status` and `reason` (why he cannot score this week, or None).
"""
from matchups import fmt_kickoff, game_label, kickoff_time, locked_teams, next_kickoffs
from recommend import EXCLUDED_STATUSES
from roster_slots import BENCH_SLOTS, FLEX_ELIGIBILITY, allocate_slots, starters_from_roster_positions

# Out and Doubtful never start; the draft's long-term statuses (IR, PUP, ...) neither.
SIT_STATUSES = {"Out", "Doubtful"} | set(EXCLUDED_STATUSES)
# A swap worth less than this is inside projection noise; the page says so.
COIN_FLIP_POINTS = 1.5
KICKOFF_SOON_HOURS = 3  # a swap whose deadline is this close is flagged in red
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


def optimal_lineup(rows, starters, key="points"):
    """The best legal lineup by `key` (this week's points, or the matchup-adjusted
    points); slots nobody can fill hold None."""
    slots = allocate_slots(startable(rows), starters, key=key).slots
    return {slot: rows + [None] * (starters[slot] - len(rows)) for slot, rows in slots.items()}


def lineup_points(slots, key="points"):
    """Points of the players listed, whether or not they can play."""
    return round(sum(r.get(key, 0) for rows in slots.values() for r in rows if r), 1)


def expected_points(slots, key="points"):
    """Points from the players who can actually play this week."""
    return round(sum(r.get(key, 0) for rows in slots.values() for r in rows if r and can_play(r)), 1)


def unplayable_starters(current):
    """[(row, reason)] for current starters who cannot score this week, in slot order."""
    return [(r, sit_reason(r)) for rows in current.values() for r in rows if r and not can_play(r)]


def _player_ids(slots):
    return {r["player_id"] for rows in slots.values() for r in rows if r}


def lineup_diff(current, optimal, key="points"):
    """The swaps that turn `current` into `optimal`, valued by `key`.

    Each incoming player is paired with an outgoing one at the same position when
    there is one, otherwise with the lowest-scoring outgoing player who could have
    held the incoming player's slot; an empty slot pairs with nobody. An outgoing
    player who cannot play counts for zero, so replacing an Out starter is a gain.
    Returns [{"slot", "in", "out", "out_reason", "delta", "coin_flip"}].
    """
    current_ids, optimal_ids = _player_ids(current), _player_ids(optimal)
    incoming = sorted(
        ((slot, r) for slot, rows in optimal.items() for r in rows if r and r["player_id"] not in current_ids),
        key=lambda pair: -pair[1].get(key, 0),
    )
    outgoing = sorted(
        (r for rows in current.values() for r in rows if r and r["player_id"] not in optimal_ids),
        key=lambda r: r.get(key, 0) if can_play(r) else 0,
    )
    swaps = []
    for slot, player in incoming:
        eligible = FLEX_ELIGIBILITY.get(slot, (slot,))
        out = next((o for o in outgoing if o["position"] == player["position"]), None)
        if out is None:
            out = next((o for o in outgoing if o["position"] in eligible), None)
        if out is not None:
            outgoing.remove(out)
        out_points = out.get(key, 0) if out and can_play(out) else 0
        delta = round(player.get(key, 0) - out_points, 1)
        swaps.append({
            "slot": slot,
            "in": player,
            "out": out,
            "out_reason": sit_reason(out) if out and not can_play(out) else None,
            "delta": delta,
            "coin_flip": 0 < delta < COIN_FLIP_POINTS,
        })
    return swaps


def mark_locked(rows, locked):
    """New rows stamped with `locked`: the player's game has kicked off, so Sleeper
    will not move him in or out of the lineup this week."""
    return [{**r, "locked": r.get("team") in locked} for r in rows]


def is_locked(row):
    return bool(row and row.get("locked"))


def locked_starters(current):
    return [r for rows in current.values() for r in rows if is_locked(r)]


def reachable_lineup(rows, current, starters, key="points"):
    """The best lineup the owner can still set.

    Locked starters stay in the slot they occupy (a locked RB in FLEX stays in FLEX;
    a locked Out starter stays too, there is nothing to do about him), locked bench
    players never enter, and the slots left open are filled by `optimal_lineup`
    over the unlocked rows. Same shape as `optimal_lineup`.
    """
    frozen = {slot: [r for r in current.get(slot, []) if is_locked(r)] for slot in starters}
    open_slots = {slot: n - len(frozen[slot]) for slot, n in starters.items() if n > len(frozen[slot])}
    incumbents = [r for slot_rows in current.values() for r in slot_rows if r and not is_locked(r)]
    incumbent_ids = {r["player_id"] for r in incumbents}
    bench = [r for r in rows if not is_locked(r) and r["player_id"] not in incumbent_ids]
    # incumbents first: the allocator's sort is stable, so a tie never recommends churn
    best = optimal_lineup(incumbents + bench, open_slots, key=key) if open_slots else {}
    return {slot: frozen[slot] + best.get(slot, []) for slot in starters}


def swap_deadline(swap):
    """When the swap must be made: the earlier kickoff of the two players involved,
    since Sleeper freezes both sides once either game starts. None without a schedule."""
    kickoffs = [kickoff_time(r.get("matchup")) for r in (swap.get("in"), swap.get("out")) if r]
    known = [k for k in kickoffs if k is not None]
    return min(known) if known else None


def deadline_status(deadline, now):
    """("past" | "soon" | "later", text) for a swap's deadline; None without a schedule.
    Hours are rounded down so the warning never overstates the time left."""
    if deadline is None:
        return None
    hours = (deadline - now).total_seconds() / 3600
    if hours <= 0:
        return ("past", "kicked off")
    if hours <= KICKOFF_SOON_HOURS:
        left = f"{int(hours * 60)} min" if hours < 1 else f"{int(hours)}h"
        return ("soon", f"kicks off in {left} ({fmt_kickoff(deadline)})")
    return ("later", f"by {fmt_kickoff(deadline)}")


def missed_players(optimal, reachable):
    """Players the unconstrained optimal wanted that the locks keep out: the ones locked
    on the bench themselves, and the free ones blocked by a locked starter holding the slot."""
    reachable_ids = _player_ids(reachable)
    kept_out = [r for rows in optimal.values() for r in rows if r and r["player_id"] not in reachable_ids]
    return {"locked": [r for r in kept_out if is_locked(r)], "blocked": [r for r in kept_out if not is_locked(r)]}


def lock_status(context, rows, current, now):
    """One line: which of my players' teams have kicked off, and the next kickoff with any
    of my starters in it. Only my teams are listed, so a Sunday afternoon does not print
    half the league. None when there is nothing to say (no schedule, or a finished week)."""
    mine = {r.get("team") for r in rows if r.get("team")}
    locked_all = locked_teams(context, now)
    locked = locked_all & mine
    bits = []
    if locked:
        latest = max(kickoff_time(context[t]) for t in locked)
        bits.append(f"Locked: {', '.join(sorted(locked))} (kicked off {fmt_kickoff(latest)})")
    elif locked_all:
        bits.append("None of your players locked yet")
    upcoming = next_kickoffs(context, now)
    if upcoming:
        kickoff, teams = upcoming[0]
        starting = [r["name"] for slot_rows in current.values() for r in slot_rows if r and r.get("team") in teams]
        who = f" ({', '.join(starting)} starting)" if starting else ""
        bits.append(f"{'Next' if locked_all else 'First'} kickoff: {fmt_kickoff(kickoff)}, {game_label(context, teams)}{who}")
    return " · ".join(bits) if bits else None
