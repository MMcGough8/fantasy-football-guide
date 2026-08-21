from roster_slots import allocate_slots

VALUE_WEIGHT = 40
COMPLETENESS_WEIGHT = 35
BALANCE_WEIGHT = 25
FULL_MARKS_AVG_VOR = 3.0  # average VOR per pick that earns the full value score

LETTER_CUTOFFS = [(90, "A"), (80, "B"), (70, "C"), (60, "D")]


def _letter(score):
    for cutoff, letter in LETTER_CUTOFFS:
        if score >= cutoff:
            return letter
    return "F"


def grade_draft(my_roster, starters):
    """Grade a drafted roster on value, completeness, and balance."""
    if not my_roster:
        return None

    allocation = allocate_slots(my_roster, starters)
    total_vor = sum(p.get("vor", 0) for p in my_roster)
    avg_vor = total_vor / len(my_roster)

    completeness = allocation.filled / allocation.total if allocation.total else 1.0
    balance = 1.0 - (len(allocation.missing) / len(starters)) if starters else 1.0

    value_score = min(avg_vor / FULL_MARKS_AVG_VOR, 1.0) * VALUE_WEIGHT
    total_score = value_score + completeness * COMPLETENESS_WEIGHT + balance * BALANCE_WEIGHT

    return {
        "score": round(total_score),
        "letter": _letter(total_score),
        "total_vor": round(total_vor, 1),
        "avg_vor": round(avg_vor, 1),
        "slots_filled": allocation.filled,
        "slots_total": allocation.total,
        "missing": allocation.missing,
    }
