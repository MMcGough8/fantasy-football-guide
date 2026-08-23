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
# When FantasyPros' expert rank spread is known it replaces the formula: measured
# ADP dispersion ran about 1.0x the expert spread through round 4 and ~1.4x later.
FP_STD_SCALE = 1.2
FP_STD_FLOOR = 2.0
LIKELY_GONE_THRESHOLD = 0.5
MARKET_CANDIDATES = 40
MIN_REACH_PROBABILITY = 0.25  # between turns, only players this likely to reach me count
# The headline plan between turns uses a stricter gate: in a rehearsal the 25%
# plan reached the owner 6 of 14 times, the 65% plan 9 of 14.
HEADLINE_REACH = 0.65

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


def survival_probability(adp, picks_made, gap, sd=None, hazard=1.0):
    """P(player is still available after `gap` more picks | available now).

    Draft slot is modelled as normal around ADP with spread `sd` (default from
    ADP). `hazard` scales the chance of being taken over the window (1.0 =
    market average; 2.0 = the teams ahead want this position twice as much).
    No ADP means the market does not expect him to be drafted, so he is
    treated as safe.
    """
    if not adp or gap <= 0:
        return 1.0
    if sd is None:
        sd = max(ADP_SD_FLOOR, ADP_SD_SCALE * math.sqrt(adp))
    now, later = picks_made, picks_made + gap
    alive_now = max(_normal_tail((now - adp) / sd), 1e-6)
    alive_later = _normal_tail((later - adp) / sd)
    survival = max(0.0, min(1.0, alive_later / alive_now))
    return survival ** hazard if hazard != 1.0 else survival


def player_sd(p):
    """Spread of a player's draft slot: the experts' rank spread if known, else from ADP."""
    std = p.get("fp_rank_std")
    if std:
        return max(FP_STD_FLOOR, FP_STD_SCALE * std)
    return None


def survival_for(p, picks_made, gap, demand=None):
    """Survival odds for a player dict. `demand(gap) -> {position: hazard multiplier}`
    folds in what the teams picking in that window actually need."""
    hazard = 1.0
    if demand is not None and p.get("position"):
        hazard = (demand(gap) or {}).get(p["position"], 1.0)
    return survival_probability(p.get("adp"), picks_made, gap, sd=player_sd(p), hazard=hazard)


def expected_best_vor(pool, position, picks_made, gap, exclude=None, demand=None):
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
        alive = survival_for(p, picks_made, gap, demand)
        expected += p["vor"] * alive * all_better_gone
        all_better_gone *= 1.0 - alive
        if all_better_gone < 1e-4:
            break
    return expected


def likely_gone(available, picks_made, gap, limit=6, demand=None):
    """Players the market expects to be taken before your next turn, best first."""
    gone = [
        p for p in available
        if p.get("adp") and not is_unavailable(p)
        and survival_for(p, picks_made, gap, demand) < LIKELY_GONE_THRESHOLD
    ]
    gone.sort(key=lambda p: p["adp"])
    return gone[:limit]


def reaches_me(p, picks_made, reach_gap, demand=None, min_reach=None):
    """Could this player still be there when I am next on the clock?"""
    if not reach_gap:
        return True
    threshold = MIN_REACH_PROBABILITY if min_reach is None else min_reach
    return survival_for(p, picks_made, reach_gap, demand) >= threshold


def held_reason(p, roster_counts, roster_size, total_picks, needs):
    """Why the recommender will not take `p` right now, or None if it would consider him."""
    pos = p["position"]
    counts = roster_counts or {}
    if is_unavailable(p):
        return f"{p.get('injury_status')}: not playing soon"
    if counts.get(pos, 0) >= POSITION_CAPS.get(pos, 99):
        return f"at the {pos} cap ({POSITION_CAPS[pos]})"
    picks_left = total_picks - roster_size
    if pos in LATE_ONLY_POSITIONS:
        if picks_left > LATE_ROUND_WINDOW:
            return f"{pos} waits for the last {LATE_ROUND_WINDOW} picks"
        if not fills_need(pos, needs):
            return f"{pos} slot already filled"
    if pos in ("QB", "TE") and counts.get(pos, 0) >= 1 and roster_size + 1 < SECOND_QB_TE_ROUND:
        return f"a backup {pos} waits until round {SECOND_QB_TE_ROUND}"
    return None


def cost_of_waiting(available, positions, picks_made, gap, reach_gap=0, demand=None):
    """For each position: best I can expect at my next turn vs the turn after.

    `reach_gap` is other teams' picks before my next turn (0 when on the clock);
    `gap` is the picks between that turn and the following one.
    """
    at_my_pick = picks_made + reach_gap
    table = {}
    for pos in positions:
        candidates = [
            p for p in available
            if p["position"] == pos and not is_unavailable(p) and reaches_me(p, picks_made, reach_gap, demand)
        ]
        if not candidates:
            continue
        best = max(candidates, key=lambda p: p["vor"])
        if gap <= 0:
            # Back-to-back picks: nobody can take him before you pick again
            later = best["vor"]
        else:
            later = expected_best_vor(available, pos, at_my_pick, gap, exclude=best, demand=demand)
        table[pos] = {"now": best, "later": round(later, 1), "cost": round(best["vor"] - later, 1)}
    return table


def fills_need(position, needs):
    if needs.get(position, 0) > 0:
        return True
    return any(
        needs.get(slot, 0) > 0 and position in eligible
        for slot, eligible in FLEX_ELIGIBILITY.items()
    )


SHORTLIST = 4


def rank_candidates(
    available, needs, roster_size, total_picks, roster_counts=None,
    picks_made=None, gap=None, reach_gap=0, limit=SHORTLIST, demand=None, min_reach=None,
):
    """Scored shortlist, best first. Each entry: player, adjusted, vor, lookahead,
    fills_need, reach (odds he reaches my pick), back (odds he is still there the
    turn after), later (expected best at his position the turn after), mode
    ("value" without market info, "now" on the clock, "reach" between turns).

    `roster_size` is how many picks you have made; `roster_counts` is
    {position: count} for what you already hold. Market-aware mode needs
    `picks_made`, `gap` (other teams' picks between my next turn and the one
    after) and `reach_gap` (picks before my next turn; 0 when I am on the clock).
    Candidates are then limited to players likely to reach me, and each is
    scored as his VOR plus the best pick I can expect the turn after, so a player
    who will not be there outranks one who will."""
    if not available:
        return []
    picks_left = total_picks - roster_size
    in_late_window = picks_left <= LATE_ROUND_WINDOW
    counts = roster_counts or {}
    use_market = picks_made is not None and gap is not None and gap > 0
    at_my_pick = (picks_made or 0) + (reach_gap or 0)
    mode = "value" if not use_market else ("reach" if reach_gap else "now")

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
        return not use_market or reaches_me(p, picks_made, reach_gap, demand, min_reach)

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

    def score(p):
        market = use_market and p["position"] not in LATE_ONLY_POSITIONS
        lookahead = expected_best_vor(available, None, at_my_pick, gap, exclude=p, demand=demand) if market else 0.0
        need = fills_need(p["position"], needs)
        bonus = 0.0
        if need:
            bonus = LATE_NEED_BONUS if p["position"] in LATE_ONLY_POSITIONS else NEED_BONUS
        return {
            "player": p,
            "vor": p["vor"],
            "lookahead": round(lookahead, 1),
            "fills_need": need,
            "adjusted": round(p["vor"] + lookahead + bonus, 1),
            "reach": survival_for(p, picks_made or 0, reach_gap, demand) if market else 1.0,
            "back": survival_for(p, at_my_pick, gap, demand) if market else 1.0,
            "later": round(expected_best_vor(available, p["position"], at_my_pick, gap, exclude=p, demand=demand), 1)
            if market else None,
            "mode": mode,
        }

    ranked = sorted((score(p) for p in candidates), key=lambda c: c["adjusted"], reverse=True)
    return ranked[:limit]


def recommend_pick(
    available, needs, roster_size, total_picks, roster_counts=None,
    picks_made=None, gap=None, reach_gap=0, demand=None,
):
    """Return (player, reason): the top of `rank_candidates` with a one-line why."""
    ranked = rank_candidates(
        available, needs, roster_size, total_picks, roster_counts, picks_made, gap, reach_gap,
        limit=1, demand=demand,
    )
    if not ranked:
        return None, ""
    top = ranked[0]
    pick = top["player"]
    reason = f"fills a need at {pick['position']}" if top["fills_need"] else "best value on the board"
    if top["mode"] == "reach":
        reason += f" · {top['reach']:.0%} to reach your pick"
    elif top["mode"] == "now":
        reason += f" · {top['back']:.0%} to still be there next turn"
    if top["later"] is not None:
        reason += f"; best {pick['position']} the turn after is worth about {top['later']:.0f} VOR"
    return pick, reason
