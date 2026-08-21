"""Pick recommendation: best value over replacement, nudged toward open slots,
and aware of who will still be there at your next turn."""
import math

from roster_slots import FLEX_ELIGIBILITY

# Spread of a player's actual draft slot around his ADP: tight early, loose late.
# sd = max(FLOOR, SCALE * sqrt(adp)) matched cross-site ADP disagreement best
# (sd ~2 at ADP 10, ~8 at 40, ~22 at 100); a linear ratio was 10-25 points
# too optimistic in the middle rounds.
ADP_SD_SCALE = 2.2
ADP_SD_FLOOR = 4.0
LIKELY_GONE_THRESHOLD = 0.5
MARKET_CANDIDATES = 40
MIN_REACH_PROBABILITY = 0.25  # between turns, only players this likely to reach me count

NEED_BONUS = 30.0  # added to VOR when the player fills an open starting slot (swept: 25-35 best)
LATE_NEED_BONUS = 100.0  # K/DEF still open inside the final picks: take one
LATE_ROUND_WINDOW = 3  # K and DEF are only considered within this many picks of the end
LATE_ONLY_POSITIONS = ("K", "DEF")
# Most of a position you would ever roster in a 14-round draft; stops pure VOR
# from stacking backup QBs and TEs once the starters are filled.
POSITION_CAPS = {"QB": 2, "RB": 6, "WR": 6, "TE": 2, "K": 1, "DEF": 1}
# A backup QB/TE never enters the lineup, so raw VOR overrates him against RB/WR
# depth once the starters are filled. Simulated drafts gained ~6 injury-adjusted
# points by holding the second QB/TE until this round.
SECOND_QB_TE_ROUND = 11
# Sleeper injury statuses that mean the player is not playing soon. Questionable,
# Doubtful and Out are preseason noise and stay eligible.
EXCLUDED_STATUSES = {"IR", "PUP", "Sus", "NA", "DNR", "COV"}


def is_unavailable(p):
    return p.get("injury_status") in EXCLUDED_STATUSES


def _normal_tail(z):
    return 0.5 * math.erfc(z / math.sqrt(2))


def survival_probability(adp, picks_made, gap):
    """P(player is still available after `gap` more picks | available now).

    Draft slot is modelled as normal around ADP. No ADP means the market does
    not expect him to be drafted, so he is treated as safe.
    """
    if not adp or gap <= 0:
        return 1.0
    sd = max(ADP_SD_FLOOR, ADP_SD_SCALE * math.sqrt(adp))
    now, later = picks_made, picks_made + gap
    alive_now = max(_normal_tail((now - adp) / sd), 1e-6)
    alive_later = _normal_tail((later - adp) / sd)
    return max(0.0, min(1.0, alive_later / alive_now))


def expected_best_vor(pool, position, picks_made, gap, exclude=None):
    """Expected VOR of the best player still there after `gap` picks.

    `position` restricts the pool; None means any non-K/DEF position, i.e. the
    best pick you could expect to make at your next turn.
    """
    players = sorted(
        (
            p for p in pool
            if p is not exclude
            and not is_unavailable(p)
            and (p["position"] == position if position else p["position"] not in LATE_ONLY_POSITIONS)
        ),
        key=lambda p: p["vor"],
        reverse=True,
    )
    expected, all_better_gone = 0.0, 1.0
    for p in players:
        if not p.get("adp"):
            continue  # no market read; do not let him pin the expectation as a sure thing
        alive = survival_probability(p.get("adp"), picks_made, gap)
        expected += p["vor"] * alive * all_better_gone
        all_better_gone *= 1.0 - alive
        if all_better_gone < 1e-4:
            break
    return expected


def likely_gone(available, picks_made, gap, limit=6):
    """Players the market expects to be taken before your next turn, best first."""
    gone = [
        p for p in available
        if p.get("adp") and not is_unavailable(p)
        and survival_probability(p["adp"], picks_made, gap) < LIKELY_GONE_THRESHOLD
    ]
    gone.sort(key=lambda p: p["adp"])
    return gone[:limit]


def reaches_me(p, picks_made, reach_gap):
    """Could this player still be there when I am next on the clock?"""
    if not reach_gap:
        return True
    return survival_probability(p.get("adp"), picks_made, reach_gap) >= MIN_REACH_PROBABILITY


def cost_of_waiting(available, positions, picks_made, gap, reach_gap=0):
    """For each position: best I can expect at my next turn vs the turn after.

    `reach_gap` is other teams' picks before my next turn (0 when on the clock);
    `gap` is the picks between that turn and the following one.
    """
    at_my_pick = picks_made + reach_gap
    table = {}
    for pos in positions:
        candidates = [
            p for p in available
            if p["position"] == pos and not is_unavailable(p) and reaches_me(p, picks_made, reach_gap)
        ]
        if not candidates:
            continue
        best = max(candidates, key=lambda p: p["vor"])
        if gap <= 0:
            # Back-to-back picks: nobody can take him before you pick again
            later = best["vor"]
        else:
            later = expected_best_vor(available, pos, at_my_pick, gap, exclude=best)
        table[pos] = {"now": best, "later": round(later, 1), "cost": round(best["vor"] - later, 1)}
    return table


def fills_need(position, needs):
    if needs.get(position, 0) > 0:
        return True
    return any(
        needs.get(slot, 0) > 0 and position in eligible
        for slot, eligible in FLEX_ELIGIBILITY.items()
    )


def recommend_pick(
    available, needs, roster_size, total_picks, roster_counts=None,
    picks_made=None, gap=None, reach_gap=0,
):
    """Return (player, reason). `roster_size` is how many picks you have made;
    `roster_counts` is {position: count} for what you already hold.

    Market-aware mode needs `picks_made`, `gap` (other teams' picks between my
    next turn and the one after) and `reach_gap` (picks before my next turn; 0
    when I am on the clock). Candidates are then limited to players likely to
    reach me, and each is scored as his VOR plus the best pick I can expect the
    turn after, so a player who will not be there outranks one who will."""
    if not available:
        return None, ""
    picks_left = total_picks - roster_size
    in_late_window = picks_left <= LATE_ROUND_WINDOW
    counts = roster_counts or {}
    use_market = picks_made is not None and gap is not None and gap > 0
    at_my_pick = (picks_made or 0) + (reach_gap or 0)

    current_round = roster_size + 1

    def allowed_now(p):
        """Eligibility that must hold even in the fallback: health, K/DEF timing, QB2/TE2 timing."""
        pos = p["position"]
        if is_unavailable(p):
            return False
        if pos in LATE_ONLY_POSITIONS:
            # A kicker or defense is only ever a pick when a slot is open and the draft is ending
            return in_late_window and fills_need(pos, needs)
        if pos in ("QB", "TE") and counts.get(pos, 0) >= 1 and current_round < SECOND_QB_TE_ROUND:
            return False
        return True

    def is_candidate(p):
        pos = p["position"]
        if not allowed_now(p):
            return False
        if counts.get(pos, 0) >= POSITION_CAPS.get(pos, 99):
            return False
        return not use_market or reaches_me(p, picks_made, reach_gap)

    candidates = [p for p in available if is_candidate(p)]
    if not candidates:
        # Last resort: relax caps, then timing rules, but never hand the pick to a
        # kicker or defense before the final rounds or to an injured player.
        candidates = (
            [p for p in available if allowed_now(p)]
            or [p for p in available if not is_unavailable(p) and p["position"] not in LATE_ONLY_POSITIONS]
            or available
        )
    if use_market:
        # The lookahead is O(pool) per candidate; only the top of the board can win
        candidates = sorted(candidates, key=lambda p: p["vor"], reverse=True)[:MARKET_CANDIDATES]

    def base_value(p):
        # One-step lookahead: this pick plus the best pick I can expect next turn
        # if this player is gone from the pool.
        if not use_market or p["position"] in LATE_ONLY_POSITIONS:
            return p["vor"]
        return p["vor"] + expected_best_vor(available, None, at_my_pick, gap, exclude=p)

    def adjusted(p):
        value = base_value(p)
        if not fills_need(p["position"], needs):
            return value
        if p["position"] in LATE_ONLY_POSITIONS:
            return value + LATE_NEED_BONUS
        return value + NEED_BONUS

    pick = max(candidates, key=adjusted)
    reason = (
        f"fills a need at {pick['position']}" if fills_need(pick["position"], needs) else "best value on the board"
    )
    if use_market and pick["position"] not in LATE_ONLY_POSITIONS:
        later = expected_best_vor(available, pick["position"], at_my_pick, gap, exclude=pick)
        if reach_gap:
            reach = survival_probability(pick.get("adp"), picks_made, reach_gap)
            reason += f" · {reach:.0%} to reach your pick"
        else:
            back = survival_probability(pick.get("adp"), picks_made, gap)
            reason += f" · {back:.0%} to still be there next turn"
        reason += f"; best {pick['position']} the turn after is worth about {later:.0f} VOR"
    return pick, reason
