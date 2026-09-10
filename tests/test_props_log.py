import pytest

from projection_log import read_records
from props_log import (
    bet_summary, calibration_report, fit_market_weight, grade_bet, grade_bets, grade_leg, line_records, log_lines,
    log_stats, record_bet,
)


def _priced(player, market, side, p, line=52.5, price=-110, player_id="1", p_model=0.6, p_market=0.5):
    return {"player": player, "player_id": player_id, "position": "WR", "market": market, "side": side, "line": line, "price": price,
            "book": "draftkings", "p": p, "p_model": p_model, "p_market": p_market, "p_book": p_market, "ev": 0.02, "consensus_line": line,
            "event_id": "evt1", "home": "IND", "away": "ATL"}


def test_log_lines_is_idempotent_per_week_and_keeps_every_field(tmp_path):
    path = tmp_path / "props.jsonl"
    priced = [_priced("Michael Pittman", "player_reception_yds", "over", 0.56)]
    assert log_lines(path, "2026", 1, priced, pulled_at="2026-09-11T12:00:00Z") == 1
    assert log_lines(path, "2026", 1, priced, pulled_at="2026-09-11T12:00:00Z") == 0  # the same pull is not logged twice
    assert log_lines(path, "2026", 1, priced, pulled_at="2026-09-13T14:00:00Z") == 1  # a Sunday refresh is a new pull
    records = read_records(path)
    line = next(r for r in records if r["kind"] == "line")
    assert line["player_id"] == "1" and line["p"] == 0.56 and line["pulled_at"] == "2026-09-11T12:00:00Z" and line["week"] == 1
    assert [r["what"] for r in records if r["kind"] == "logged"] == ["lines", "lines"]


def test_record_bet_assigns_an_id_and_round_trips(tmp_path):
    path = tmp_path / "props.jsonl"
    bet_id = record_bet(path, {"season": "2026", "week": 1, "book": "fanduel", "stake": 20.0, "price": -110, "quoted_payout": None,
                               "legs": [{"player_id": "1", "player": "Michael Pittman", "market": "player_reception_yds", "side": "over", "line": 52.5, "price": -110, "p": 0.56}]},
                        now="2026-09-12T10:00:00Z")
    bet = next(r for r in read_records(path) if r["kind"] == "bet")
    assert bet["id"] == bet_id and bet["placed_at"] == "2026-09-12T10:00:00Z" and bet["legs"][0]["line"] == 52.5


def test_grade_leg_covers_win_loss_push_void_and_touchdowns():
    over = {"player_id": "1", "market": "player_reception_yds", "side": "over", "line": 52.5}
    assert grade_leg(over, {"rec_yd": 60, "gp": 1}) == "win" and grade_leg(over, {"rec_yd": 40, "gp": 1}) == "loss"
    assert grade_leg({**over, "line": 60}, {"rec_yd": 60, "gp": 1}) == "push"
    assert grade_leg({**over, "side": "under"}, {"rec_yd": 40, "gp": 1}) == "win"
    assert grade_leg(over, None) == "void" and grade_leg(over, {"rec_yd": 0, "gp": 0}) == "void"
    td = {"player_id": "1", "market": "player_anytime_td", "side": "yes", "line": None}
    assert grade_leg(td, {"rec_td": 1, "gp": 1}) == "win" and grade_leg(td, {"pr_td": 1, "gp": 1}) == "win"
    assert grade_leg(td, {"rec_td": 0, "rush_td": 0, "gp": 1}) == "loss"
    assert grade_leg({**over, "market": "player_receptions", "line": 4}, {"rec": 4, "gp": 1}) == "push"


def test_grade_bet_settles_singles_and_parlays_with_voids():
    single = {"id": "b1", "stake": 10.0, "price": -110, "quoted_payout": None,
              "legs": [{"player_id": "1", "market": "player_reception_yds", "side": "over", "line": 52.5, "price": -110}]}
    stats = {"1": {"rec_yd": 60, "gp": 1}, "2": {"rec": 6, "gp": 1}, "3": {"gp": 0}}
    won = grade_bet(single, stats)
    assert won["result"] == "win" and won["profit"] == pytest.approx(10.0 * 100 / 110, abs=0.01) and won["bet_id"] == "b1"
    lost = grade_bet({**single, "legs": [{**single["legs"][0], "line": 70.5}]}, stats)
    assert lost["result"] == "loss" and lost["profit"] == -10.0
    boosted = grade_bet({**single, "price": 120}, stats)  # a single pays the bet's own price (an odds boost), not the leg's
    assert boosted["profit"] == pytest.approx(12.0)
    parlay = {"id": "b2", "stake": 10.0, "price": None, "quoted_payout": 3.6,
              "legs": [{"player_id": "1", "market": "player_reception_yds", "side": "over", "line": 52.5, "price": -110},
                       {"player_id": "2", "market": "player_receptions", "side": "over", "line": 4.5, "price": -120}]}
    hit = grade_bet(parlay, stats)
    assert hit["result"] == "win" and hit["profit"] == pytest.approx(26.0) and [l["result"] for l in hit["legs"]] == ["win", "win"]
    voided = grade_bet({**parlay, "legs": parlay["legs"] + [{"player_id": "3", "market": "player_rush_yds", "side": "over", "line": 50.5, "price": -110}]}, stats)
    assert voided["result"] == "win" and voided["profit"] == pytest.approx(10.0 * (1.9091 * 1.8333 - 1), abs=0.05)  # the void leg drops out, the payout is re-multiplied from the leg prices
    assert grade_bet({**single, "legs": [{**single["legs"][0], "player_id": "3"}]}, stats)["result"] == "void"


def test_grade_bets_only_grades_weeks_with_stats_and_never_twice(tmp_path):
    path = tmp_path / "props.jsonl"
    record_bet(path, {"season": "2026", "week": 1, "book": "draftkings", "stake": 10.0, "price": -110, "quoted_payout": None,
                      "legs": [{"player_id": "1", "market": "player_reception_yds", "side": "over", "line": 52.5, "price": -110}]})
    record_bet(path, {"season": "2026", "week": 2, "book": "draftkings", "stake": 10.0, "price": -110, "quoted_payout": None,
                      "legs": [{"player_id": "1", "market": "player_reception_yds", "side": "over", "line": 52.5, "price": -110}]})
    log_stats(path, "2026", 1, {"1": {"rec_yd": 60, "gp": 1}})
    assert grade_bets(path) == 1
    assert grade_bets(path) == 0
    summary = bet_summary(read_records(path))
    assert summary["bets"] == 2 and summary["graded"] == 1 and summary["profit"] == pytest.approx(9.09, abs=0.01) and summary["roi"] == pytest.approx(0.909, abs=0.01)


def test_calibration_report_buckets_lines_by_probability_and_fits_the_market_weight(tmp_path):
    path = tmp_path / "props.jsonl"
    priced = []
    stats = {}
    for i in range(200):
        pid = str(i)
        hit = i % 10 < 6  # 60% of these legs hit
        priced.append(_priced(f"P{i}", "player_reception_yds", "over", 0.6, player_id=pid, p_model=0.8 if hit else 0.4, p_market=0.6))
        stats[pid] = {"rec_yd": 60 if hit else 40, "gp": 1}
    log_lines(path, "2026", 1, priced)
    log_stats(path, "2026", 1, stats)
    report = calibration_report(read_records(path))
    bucket = next(b for b in report["buckets"] if b["p"] == [0.6, 0.65])  # p = 0.6 sits in the 60-65% bucket
    assert bucket["n"] == 200 and bucket["observed"] == pytest.approx(0.6) and bucket["predicted"] == pytest.approx(0.6)
    assert report["by_market"]["player_reception_yds"]["n"] == 200
    assert report["weight"] < 0.5  # here the model probability was the informative one
    assert fit_market_weight([]) is None
