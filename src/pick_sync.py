"""Map Sleeper draft picks onto the board and compute snake-draft turn order."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PickSync:
    taken: list
    mine: list
    unmatched: list


def index_board_by_player_id(board):
    return {p["player_id"]: p for p in board if p.get("player_id")}


def apply_picks(picks, board_index, my_user_id, my_roster_id):
    """Split Sleeper picks into board players taken by anyone and by me."""
    taken, mine, unmatched = [], [], []
    for pick in picks:
        player = board_index.get(str(pick.get("player_id")))
        if player is None:
            unmatched.append(pick)
            continue
        taken.append(player)
        # roster_id says whose team owns the player; picked_by only says who
        # clicked (a commissioner can pick for an absent manager).
        if my_roster_id is not None and pick.get("roster_id") is not None:
            is_mine = pick.get("roster_id") == my_roster_id
        else:
            is_mine = pick.get("picked_by") == my_user_id
        if is_mine:
            mine.append(player)
    return PickSync(taken=taken, mine=mine, unmatched=unmatched)


SUPPORTED_DRAFT_TYPES = ("snake", "linear")


def _slot_for_pick(pick_no, num_teams, draft_type):
    """1-based slot on the clock for a 1-based overall pick number."""
    round_num = (pick_no - 1) // num_teams + 1
    idx = (pick_no - 1) % num_teams
    if draft_type == "linear" or round_num % 2 == 1:
        return round_num, idx + 1
    return round_num, num_teams - idx


def next_pick_info(draft, picks_made, my_user_id):
    """Who is on the clock and how many picks until mine; None if order unknown."""
    order = draft.get("draft_order")
    if not order or my_user_id not in order:
        return None
    settings = draft.get("settings") or {}
    draft_type = draft.get("type")
    if draft_type not in SUPPORTED_DRAFT_TYPES or settings.get("reversal_round"):
        return None  # third-round-reversal and auction orders are not modelled
    num_teams = settings.get("teams") or len(order)
    total_picks = num_teams * settings.get("rounds", 0)
    pick_no = picks_made + 1
    if pick_no > total_picks:
        return None

    slot_to_user = {slot: uid for uid, slot in order.items()}
    my_slot = order[my_user_id]
    round_num, on_clock_slot = _slot_for_pick(pick_no, num_teams, draft_type)

    my_turns = [
        n for n in range(pick_no, total_picks + 1)
        if _slot_for_pick(n, num_teams, draft_type)[1] == my_slot
    ]
    picks_until_mine = (my_turns[0] - pick_no) if my_turns else total_picks - pick_no + 1
    # Picks by other teams between my upcoming turn and the one after it
    picks_until_following = (my_turns[1] - my_turns[0] - 1) if len(my_turns) > 1 else 0

    return {
        "my_slot": my_slot,
        "on_clock_slot": on_clock_slot,
        "on_clock_user_id": slot_to_user.get(on_clock_slot),
        "picks_until_mine": picks_until_mine,
        "picks_until_following": picks_until_following,
        "round": round_num,
    }
