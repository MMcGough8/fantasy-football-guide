"""Map a drafted roster onto starting slots, including FLEX-style slots.

Sleeper roster slots are either a single position ("RB") or a flex slot that
accepts several positions. Dedicated slots are filled first with the best
players at that position; whatever is left over spills into flex slots.
"""
from dataclasses import dataclass, field

FLEX_ELIGIBILITY = {
    "FLEX": ("RB", "WR", "TE"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
    "REC_FLEX": ("WR", "TE"),
    "WRRB_FLEX": ("RB", "WR"),
}
BENCH_SLOTS = {"BN"}
IGNORED_SLOTS = {"IR", "TAXI", "DL", "LB", "DB", "IDP_FLEX", "DE", "DT", "CB", "S"}


def starters_from_roster_positions(roster_positions):
    """Collapse Sleeper's slot list into ({slot: count}, bench_count)."""
    starters = {}
    bench = 0
    for slot in roster_positions:
        if slot in BENCH_SLOTS:
            bench += 1
        elif slot in IGNORED_SLOTS:
            continue
        else:
            starters[slot] = starters.get(slot, 0) + 1
    return starters, bench


@dataclass(frozen=True)
class SlotAllocation:
    slots: dict
    bench: list
    needs: dict
    filled: int
    total: int
    missing: list = field(default_factory=list)


def allocate_slots(roster, starters):
    """Assign roster players to starting slots; best VOR fills dedicated slots first."""
    remaining = sorted(roster, key=lambda p: p.get("vor", 0), reverse=True)
    slots = {}

    def take(slot, eligible):
        taken = eligible[: starters[slot]]
        taken_ids = {id(p) for p in taken}
        slots[slot] = taken
        return [p for p in remaining if id(p) not in taken_ids]

    dedicated = [s for s in starters if s not in FLEX_ELIGIBILITY]
    for slot in dedicated:
        remaining = take(slot, [p for p in remaining if p["position"] == slot])

    # Narrow flex slots (REC_FLEX) before broad ones (SUPER_FLEX) so a broad slot
    # does not grab the only player a narrow one could have used.
    flex = sorted(
        (s for s in starters if s in FLEX_ELIGIBILITY),
        key=lambda s: len(FLEX_ELIGIBILITY[s]),
    )
    for slot in flex:
        eligible = FLEX_ELIGIBILITY[slot]
        remaining = take(slot, [p for p in remaining if p["position"] in eligible])

    slots = {s: slots[s] for s in starters}
    needs = {s: starters[s] - len(slots[s]) for s in starters if len(slots[s]) < starters[s]}
    filled = sum(len(v) for v in slots.values())
    return SlotAllocation(
        slots=slots,
        bench=remaining,
        needs=needs,
        filled=filled,
        total=sum(starters.values()),
        missing=[s for s in starters if s in needs],
    )
