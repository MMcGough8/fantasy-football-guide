"""Pick recommendation: best value over replacement, nudged toward open slots."""
from roster_slots import FLEX_ELIGIBILITY

NEED_BONUS = 20.0  # added to VOR when the player fills an open starting slot
LATE_NEED_BONUS = 100.0  # K/DEF still open inside the final picks: take one
LATE_ROUND_WINDOW = 3  # K and DEF are only considered within this many picks of the end
LATE_ONLY_POSITIONS = ("K", "DEF")


def fills_need(position, needs):
    if needs.get(position, 0) > 0:
        return True
    return any(
        needs.get(slot, 0) > 0 and position in eligible
        for slot, eligible in FLEX_ELIGIBILITY.items()
    )


def recommend_pick(available, needs, roster_size, total_picks):
    """Return (player, reason). `roster_size` is how many picks you have made."""
    if not available:
        return None, ""
    picks_left = total_picks - roster_size
    in_late_window = picks_left <= LATE_ROUND_WINDOW

    def is_candidate(p):
        if p["position"] not in LATE_ONLY_POSITIONS:
            return True
        # A kicker or defense is only ever a pick when a slot is open and the draft is ending
        return in_late_window and fills_need(p["position"], needs)

    candidates = [p for p in available if is_candidate(p)] or available

    def adjusted(p):
        if not fills_need(p["position"], needs):
            return p["vor"]
        if p["position"] in LATE_ONLY_POSITIONS:
            return p["vor"] + LATE_NEED_BONUS
        return p["vor"] + NEED_BONUS

    pick = max(candidates, key=adjusted)
    reason = (
        f"fills a need at {pick['position']}" if fills_need(pick["position"], needs) else "best value on the board"
    )
    return pick, reason
