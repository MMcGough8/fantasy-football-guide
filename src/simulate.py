"""Draft simulator: 11 opponents with assigned drafting styles, the owner on the
app's recommender. Use it to see what the app would tell you under different
leagues without a Sleeper mock draft.

    .venv/bin/python src/simulate.py --slot 6 --scenario zero_rb_league
    .venv/bin/python src/simulate.py --slot 6 --scenario rb_run --compare
    .venv/bin/python src/simulate.py --list
"""
import argparse
import json
import os
import random
import sys
from collections import Counter

from opponents import demand_multipliers
from pick_sync import _slot_for_pick
from recommend import LATE_ONLY_POSITIONS, LATE_ROUND_WINDOW, POSITION_CAPS, is_unavailable, rank_candidates
from roster_slots import allocate_slots

ADP_NOISE_SD = 6.0  # how far a bot's pick wanders from ADP order

# Opponent styles: how each bot chooses when it is on the clock.
STRATEGIES = {
    "adp": "Takes the best available by ADP with a little noise (the market).",
    "zero_rb": "WR/TE/QB for the first four rounds, then running backs.",
    "rb_heavy": "Running backs with the first three picks, then best available.",
    "qb_early": "Takes a QB by round 2, otherwise the market.",
    "te_early": "Takes a TE by round 2, otherwise the market.",
    "homer": "Market, but reaches a round early on high-projection players.",
    "board": "Uses this app's own VOR board (a sharp, informed rival).",
}

# Named leagues: slot -> strategy (owner's slot is replaced by the app).
SCENARIOS = {
    "balanced": {s: ["adp", "zero_rb", "rb_heavy", "qb_early", "te_early", "homer", "board"][s % 7] for s in range(1, 13)},
    "market": {s: "adp" for s in range(1, 13)},
    "zero_rb_league": {s: ("zero_rb" if s % 2 else "adp") for s in range(1, 13)},
    "rb_run": {s: ("rb_heavy" if s <= 8 else "adp") for s in range(1, 13)},
    "qb_early_league": {s: ("qb_early" if s % 3 else "adp") for s in range(1, 13)},
    "sharks": {s: "board" for s in range(1, 13)},
}


class Team:
    def __init__(self, roster_id, strategy):
        self.roster_id = roster_id
        self.strategy = strategy
        self.roster = []

    @property
    def counts(self):
        return Counter(p["position"] for p in self.roster)


def _legal(team, p, starters, current_round, total_rounds):
    pos = p["position"]
    if is_unavailable(p):
        return False
    if team.counts.get(pos, 0) >= POSITION_CAPS.get(pos, 99):
        return False
    if pos in LATE_ONLY_POSITIONS:
        needs_it = team.counts.get(pos, 0) < starters.get(pos, 0)
        return needs_it and total_rounds - current_round + 1 <= LATE_ROUND_WINDOW
    return True


def _must_fill(team, available, starters, current_round, total_rounds):
    """Late in the draft, force open starting slots (K/DEF in the last rounds, etc.)."""
    picks_left = total_rounds - current_round + 1
    needs = allocate_slots(team.roster, starters).needs
    open_slots = sum(needs.values())
    if open_slots >= picks_left:
        for slot in needs:
            eligible = {"FLEX": ("RB", "WR", "TE")}.get(slot, (slot,))
            pool = [p for p in available if p["position"] in eligible and _legal(team, p, starters, current_round, total_rounds)]
            if pool:
                return max(pool, key=lambda p: p["vor"])
    return None


def strategy_pick(team, available, starters, current_round, total_rounds, rng):
    forced = _must_fill(team, available, starters, current_round, total_rounds)
    if forced:
        return forced
    legal = [p for p in available if _legal(team, p, starters, current_round, total_rounds)]
    if not legal:
        return available[0]
    s = team.strategy

    def by_adp(pool):
        return min(pool, key=lambda p: (p.get("adp") or 400) + rng.gauss(0, ADP_NOISE_SD))

    def by_vor(pool):
        return max(pool, key=lambda p: p["vor"])

    if s == "zero_rb" and current_round <= 4:
        pool = [p for p in legal if p["position"] != "RB"] or legal
        return by_adp(pool)
    if s == "rb_heavy" and current_round <= 3:
        pool = [p for p in legal if p["position"] == "RB"] or legal
        return by_adp(pool)
    if s == "qb_early" and current_round <= 2 and team.counts.get("QB", 0) == 0:
        pool = [p for p in legal if p["position"] == "QB"] or legal
        return by_adp(pool)
    if s == "te_early" and current_round <= 2 and team.counts.get("TE", 0) == 0:
        pool = [p for p in legal if p["position"] == "TE"] or legal
        return by_adp(pool)
    if s == "homer":
        return min(legal, key=lambda p: (p.get("adp") or 400) - 12 + rng.gauss(0, ADP_NOISE_SD))
    if s == "board":
        return by_vor(legal)
    return by_adp(legal)


def _draft_meta(num_teams, rounds):
    return {
        "type": "snake",
        "draft_order": {f"team{s}": s for s in range(1, num_teams + 1)},
        "slot_to_roster_id": {str(s): s for s in range(1, num_teams + 1)},
        "settings": {"teams": num_teams, "rounds": rounds, "reversal_round": 0},
    }


def owner_pick(owner, available, starters, picks_made, pick_no, num_teams, rounds, meta, pick_log, strategy, rng):
    """The owner's pick under the chosen strategy; "app" is the real recommender."""
    current_round = (pick_no - 1) // num_teams + 1
    if strategy in ("adp", "vor"):
        owner.strategy = "adp" if strategy == "adp" else "board"
        return strategy_pick(owner, available, starters, current_round, rounds, rng), ("market pick" if strategy == "adp" else "pure VOR")
    # the app: correct snake gaps, the opponent-needs model, the real shortlist
    my_slot = owner.roster_id
    later = [n for n in range(pick_no + 1, num_teams * rounds + 1) if _slot_for_pick(n, num_teams, "snake")[1] == my_slot]
    horizon = (later[0] - pick_no - 1) if later else 0
    picks_slim = [{"roster_id": e["roster_id"], "metadata": {"position": e["position"]}} for e in pick_log]
    cache = {}

    def demand(gap):
        if gap not in cache:
            cache[gap] = demand_multipliers(meta, picks_slim, starters, picks_made, gap)
        return cache[gap]

    needs = allocate_slots(owner.roster, starters).needs
    ranked = rank_candidates(
        available, needs, len(owner.roster), rounds, owner.counts,
        picks_made=picks_made, gap=horizon, reach_gap=0, demand=demand, limit=1,
    )
    if not ranked:
        return available[0], "nothing left"
    top = ranked[0]
    why = "fills a need" if top["fills_need"] else "best value"
    if top["mode"] == "now" and top["later"] is not None:
        why += f" · {top['back']:.0%} to still be there next turn · best {top['player']['position']} then ≈ {top['later']:.0f}"
        mult = demand(horizon)
        if mult:
            why += f" · ahead: " + " ".join(f"{k}{v:.1f}×" for k, v in mult.items() if k in ("QB", "RB", "WR", "TE"))
    return top["player"], why


def run_draft(board, starters, owner_slot, scenario, seed=0, owner_strategy="app", num_teams=12, rounds=14, custom=None):
    """Simulate one full draft. Returns the owner's log, roster, counts and scores."""
    rng = random.Random(seed)
    styles = dict(SCENARIOS[scenario]) if scenario in SCENARIOS else {}
    styles.update(custom or {})
    teams = {s: Team(s, styles.get(s, "adp")) for s in range(1, num_teams + 1)}
    owner = teams[owner_slot]
    available = sorted(board, key=lambda p: p["vor"], reverse=True)
    meta = _draft_meta(num_teams, rounds)
    pick_log, owner_log = [], []
    for pick_no in range(1, num_teams * rounds + 1):
        current_round, slot = _slot_for_pick(pick_no, num_teams, "snake")
        team = teams[slot]
        if slot == owner_slot:
            p, why = owner_pick(owner, available, starters, pick_no - 1, pick_no, num_teams, rounds, meta, pick_log, owner_strategy, rng)
            owner_log.append({"round": current_round, "pick": pick_no, "name": p["name"], "position": p["position"], "vor": p["vor"], "why": why})
        else:
            p = strategy_pick(team, available, starters, current_round, rounds, rng)
        team.roster.append(p)
        pick_log.append({"roster_id": slot, "position": p["position"], "name": p["name"]})
        available = [x for x in available if x is not p]
    return {
        "log": owner_log,
        "owner_roster": owner.roster,
        "owner_counts": dict(owner.counts),
        "lineup_points": score_roster(owner.roster, starters),
        "total_vor": round(sum(p["vor"] for p in owner.roster), 1),
        "league_mix": {s: dict(t.counts) for s, t in teams.items()},
        "styles": styles,
        "_sequence": pick_log,
    }


def score_roster(roster, starters):
    """Season points of the best legal starting lineup."""
    alloc = allocate_slots(sorted(roster, key=lambda p: p["points"], reverse=True), starters)
    # allocate_slots fills by VOR; re-fill by points for scoring
    by_pos = {}
    for p in sorted(roster, key=lambda p: p["points"], reverse=True):
        by_pos.setdefault(p["position"], []).append(p)
    total, used = 0, set()
    for slot, n in starters.items():
        if slot == "FLEX":
            continue
        for p in by_pos.get(slot, [])[:n]:
            total += p["points"]; used.add(id(p))
    flex_pool = [p for pos in ("RB", "WR", "TE") for p in by_pos.get(pos, []) if id(p) not in used]
    for p in sorted(flex_pool, key=lambda p: p["points"], reverse=True)[: starters.get("FLEX", 0)]:
        total += p["points"]
    return round(total, 1)


def load_live_board(cache_path):
    """Build the real board (cached to a file so repeated runs are instant)."""
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    import espn_projections
    import fantasypros
    from draft_board import build_board

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, ".sleeper_league.json")) as f:
        cfg = json.load(f)
    key = os.getenv("FANTASYPROS_API_KEY", "").strip()
    fp = fantasypros.fetch_all("2026", key)
    fp_ranks = fantasypros.fetch_consensus_rankings("2026", key, fantasypros.scoring_code_for(cfg["scoring_settings"]))
    espn = espn_projections.fetch_all("2026")
    board = build_board("pts_ppr", cfg["num_teams"], cfg["scoring_settings"], cfg["starters"],
                        {"fp": fp["projections"], "espn": espn["projections"]}, espn["ranks"], fp_ranks)
    with open(cache_path, "w") as f:
        json.dump({"board": board, "starters": cfg["starters"]}, f)
    return {"board": board, "starters": cfg["starters"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slot", type=int, default=6, help="your draft slot (1-12)")
    ap.add_argument("--scenario", default="balanced", help="one of: " + ", ".join(SCENARIOS))
    ap.add_argument("--team", action="append", default=[], metavar="SLOT=STYLE", help="override one team, e.g. --team 3=zero_rb")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--compare", action="store_true", help="also draft as pure VOR and as the market from the same slot")
    ap.add_argument("--list", action="store_true", help="list strategies and scenarios")
    ap.add_argument("--board-cache", default="/tmp/sim_board.json")
    ap.add_argument("--emit", metavar="DIR", help="write Sleeper-shaped draft.json/picks.json for the app to rehearse from")
    ap.add_argument("--stream", type=float, default=0.0, metavar="SECONDS", help="with --emit: seconds between picks (plays the draft live)")
    ap.add_argument("--clock", type=float, default=0.0, metavar="SECONDS", help="with --stream: extra pause when it is your pick")
    args = ap.parse_args()
    if args.list:
        print("Strategies:"); [print(f"  {k:<10} {v}") for k, v in STRATEGIES.items()]
        print("Scenarios:"); [print(f"  {k:<16} " + " ".join(f"{s}:{v}" for s, v in sc.items())) for k, sc in SCENARIOS.items()]
        return
    custom = {}
    for spec in args.team:
        s, style = spec.split("=")
        custom[int(s)] = style
    data = load_live_board(args.board_cache)
    board, starters = data["board"], data["starters"]
    if args.emit:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".sleeper_league.json")) as f:
            owner_user_id = json.load(f)["user_id"]
        print(f"Writing replay to {args.emit} (paste  file:{os.path.abspath(args.emit)}  into the app's mock-draft box)")
        write_replay(args.emit, board, starters, args.slot, owner_user_id, args.scenario, args.seed, custom,
                     stream=args.stream, clock=args.clock)
        print("done")
        return
    result = run_draft(board, starters, args.slot, args.scenario, args.seed, "app", custom=custom)
    print(f"Scenario {args.scenario}, you at slot {args.slot}, seed {args.seed}")
    print("Opponents: " + ", ".join(f"{s}={v}" for s, v in sorted(result["styles"].items()) if s != args.slot))
    print()
    for e in result["log"]:
        print(f"R{e['round']:>2} (pick {e['pick']:>3})  {e['name']:<22} {e['position']:<3} VOR {e['vor']:>6.1f}   {e['why']}")
    print(f"\nRoster: {result['owner_counts']}  ·  lineup points {result['lineup_points']}  ·  total VOR {result['total_vor']}")
    if args.compare:
        print("\nSame slot, same opponents, same seed:")
        for strat, label in (("app", "the app"), ("vor", "pure VOR"), ("adp", "market/ADP")):
            r = run_draft(board, starters, args.slot, args.scenario, args.seed, strat, custom=custom)
            print(f"  {label:<12} lineup {r['lineup_points']:>7}   VOR {r['total_vor']:>7}   mix {r['owner_counts']}")



# ---- Replay into the app -------------------------------------------------------
# Writes Sleeper-shaped draft.json / picks.json that the app can rehearse from
# ("file:/path" in the sidebar's mock-draft box). With --stream it plays the
# draft out in real time: order published, picks landing, a pause on your turn.

def _sleeper_pick(pick_no, num_teams, p, roster_id, user_ids):
    round_num, slot = _slot_for_pick(pick_no, num_teams, "snake")
    first, _, last = p["name"].partition(" ")
    return {
        "round": round_num, "pick_no": pick_no, "draft_slot": slot, "roster_id": roster_id,
        "picked_by": user_ids.get(slot, ""), "player_id": p.get("player_id"),
        "metadata": {"first_name": first, "last_name": last, "position": p["position"], "team": p.get("team", "FA")},
    }


def write_replay(out_dir, board, starters, owner_slot, owner_user_id, scenario, seed=0, custom=None,
                 num_teams=12, rounds=14, stream=0.0, clock=0.0, status="complete"):
    """Write draft.json and picks.json. stream>0 drips the picks in real time."""
    import time

    os.makedirs(out_dir, exist_ok=True)
    user_ids = {s: f"sim-team-{s}" for s in range(1, num_teams + 1)}
    user_ids[owner_slot] = owner_user_id
    draft = {
        "draft_id": "sim", "type": "snake", "status": "pre_draft", "draft_order": None,
        "slot_to_roster_id": {str(s): s for s in range(1, num_teams + 1)},
        "settings": {"teams": num_teams, "rounds": rounds, "reversal_round": 0, "pick_timer": 120},
    }

    def dump(picks):
        with open(os.path.join(out_dir, "draft.json"), "w") as f:
            json.dump(draft, f)
        with open(os.path.join(out_dir, "picks.json"), "w") as f:
            json.dump(picks, f)

    if stream:
        dump([]); print(f"pre-draft for {2 * stream:.0f}s ..."); time.sleep(2 * stream)
    draft["status"] = "drafting"
    draft["draft_order"] = {uid: slot for slot, uid in user_ids.items()}
    picks = []
    dump(picks)
    result = run_draft(board, starters, owner_slot, scenario, seed, "app", num_teams, rounds, custom)
    # Replay the recorded draft pick by pick (run_draft already decided every pick)
    by_name = {p["name"]: p for p in board}
    sequence = result["_sequence"]
    for pick_no, entry in enumerate(sequence, start=1):
        p = by_name[entry["name"]]
        if stream:
            on_owner = entry["roster_id"] == owner_slot
            wait = clock if on_owner and clock else stream
            print(f"pick {pick_no:>3} {'YOU ' if on_owner else '    '}{p['name']:<22} in {wait:.0f}s")
            time.sleep(wait)
        picks.append(_sleeper_pick(pick_no, num_teams, p, entry["roster_id"], user_ids))
        dump(picks)
    draft["status"] = status
    dump(picks)
    return result


if __name__ == "__main__":
    main()
