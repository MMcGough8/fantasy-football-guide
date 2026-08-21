import importlib

import news
from news import build_analyst_prompt
from scoring import scoring_summary


def test_models_default_and_override(monkeypatch):
    monkeypatch.delenv("CLAUDE_FAST_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_ANALYST_MODEL", raising=False)
    importlib.reload(news)
    assert news.FAST_MODEL == "claude-haiku-4-5"
    assert news.ANALYST_MODEL == "claude-sonnet-4-6"
    monkeypatch.setenv("CLAUDE_FAST_MODEL", "claude-opus-5")
    monkeypatch.setenv("CLAUDE_ANALYST_MODEL", "claude-haiku-4-5")
    importlib.reload(news)
    assert news.FAST_MODEL == "claude-opus-5"
    assert news.ANALYST_MODEL == "claude-haiku-4-5"


def test_analyst_prompt_includes_board_needs_and_scoring():
    available = [
        {"name": "Puka Nacua", "position": "WR", "team": "LAR", "points": 277.4, "vor": 119.6,
         "bye": 11, "points_by_source": {"sleeper": 260.0, "fp": 269.6}, "tier": 1},
        {"name": "Josh Allen", "position": "QB", "team": "BUF", "points": 422.9, "vor": 71.4,
         "bye": 7, "points_by_source": {"sleeper": 413.9, "fp": 401.9, "espn": 395.0}, "tier": 1},
    ]
    prompt = build_analyst_prompt(
        "Who should I take?",
        league_size=12,
        scoring="Don Rugh FFL league scoring",
        my_roster=[{"name": "Jahmyr Gibbs", "position": "RB"}],
        taken_count=14,
        round_num=2,
        pick_in_round=3,
        available=available,
        needs={"QB": 1, "WR": 2, "FLEX": 1},
        scoring_notes="0.4 per reception, 6 per passing TD",
    )
    assert "Puka Nacua" in prompt and "277.4" in prompt and "119.6" in prompt
    assert "Sleeper 413.9 / FP 401.9 / ESPN 395.0" in prompt
    assert "QB x1, WR x2, FLEX x1" in prompt
    assert "0.4 per reception" in prompt
    assert "Round 2, Pick 3" in prompt
    assert "14 players have been drafted" in prompt
    assert "Jahmyr Gibbs (RB)" in prompt


def test_analyst_prompt_without_board_still_works():
    prompt = build_analyst_prompt("Zero RB?", league_size=10, scoring="PPR")
    assert "nobody yet" in prompt
    assert "TOP AVAILABLE" not in prompt


def test_scoring_summary_reads_like_english(league):
    text = scoring_summary(league["scoring_settings"])
    assert "0.4 per reception" in text
    assert "6 per passing TD" in text
    assert "1 per 30 passing yds" in text
    assert "1 per 10 rushing/receiving yds" in text
    assert "100-yd game bonuses" in text


def test_scoring_summary_for_plain_ppr():
    text = scoring_summary({"rec": 1.0, "pass_td": 4.0, "pass_yd": 0.04, "rush_yd": 0.1, "rec_yd": 0.1})
    assert text.startswith("1 per reception")
    assert "4 per passing TD" in text
    assert "bonus" not in text
