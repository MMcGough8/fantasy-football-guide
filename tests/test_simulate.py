import random

from simulate import SCENARIOS, Team, run_draft, score_roster, strategy_pick

STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}


def _board():
    board, adp = [], 1
    for pos, n, top in (("QB", 30, 400), ("RB", 70, 330), ("WR", 80, 290), ("TE", 30, 190), ("K", 15, 150), ("DEF", 15, 120)):
        for i in range(n):
            board.append({"name": f"{pos}{i+1}", "team": "T", "position": pos, "points": top - i * 4,
                          "vor": top - i * 4 - (top - 12 * 4), "adp": None, "tier": 1 + i // 5, "player_id": f"{pos}{i}"})
    # ADP: interleave by points with a little positional market bias
    for i, p in enumerate(sorted(board, key=lambda p: -p["points"] * {"QB": 0.75, "TE": 0.9, "K": 0.4, "DEF": 0.45}.get(p["position"], 1.0)), 1):
        p["adp"] = i
    return board


def test_every_scenario_runs_and_produces_legal_rosters():
    board = _board()
    for name in SCENARIOS:
        result = run_draft(board, STARTERS, owner_slot=6, scenario=name, seed=1, owner_strategy="app")
        roster = result["owner_roster"]
        assert len(roster) == 14
        counts = result["owner_counts"]
        assert counts.get("QB", 0) >= 1 and counts.get("K", 0) == 1 and counts.get("DEF", 0) == 1
        assert counts.get("RB", 0) >= 2 and counts.get("WR", 0) >= 2
        assert result["lineup_points"] > 0
        assert len(result["log"]) == 14


def test_same_seed_is_deterministic():
    board = _board()
    a = run_draft(board, STARTERS, 3, "balanced", seed=7)
    b = run_draft(board, STARTERS, 3, "balanced", seed=7)
    assert [e["name"] for e in a["log"]] == [e["name"] for e in b["log"]]


def test_zero_rb_teams_avoid_running_backs_early():
    board = _board()
    rng = random.Random(1)
    team = Team(roster_id=2, strategy="zero_rb")
    picks = [strategy_pick(team, list(board), STARTERS, current_round=r, total_rounds=14, rng=rng) for r in (1, 2, 3)]
    assert all(p["position"] != "RB" for p in picks)


def test_score_roster_uses_best_legal_lineup():
    roster = [{"position": "QB", "points": 100}, {"position": "RB", "points": 50}, {"position": "RB", "points": 40},
              {"position": "RB", "points": 30}, {"position": "WR", "points": 45}, {"position": "WR", "points": 35},
              {"position": "WR", "points": 20}, {"position": "TE", "points": 25}, {"position": "K", "points": 10},
              {"position": "DEF", "points": 12}]
    # QB100 + RB50+40 + WR45+35 + TE25 + FLEX max(RB30, WR20)=30 + K10 + DEF12 = 347
    assert score_roster(roster, STARTERS) == 347
