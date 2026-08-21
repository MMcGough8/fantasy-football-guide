import importlib

import news
from news import build_analyst_prompt
from scoring import scoring_summary


def test_models_override_from_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_FAST_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("CLAUDE_ANALYST_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("CLAUDE_ANALYST_EFFORT", "xhigh")
    importlib.reload(news)
    assert news.FAST_MODEL == "claude-haiku-4-5"
    assert news.ANALYST_MODEL == "claude-sonnet-5"
    assert news.ANALYST_EFFORT == "xhigh"


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


def test_analyst_prompt_flags_injuries_and_hides_ir_players():
    available = [
        {"name": "Healthy Guy", "position": "WR", "team": "LAR", "points": 200, "vor": 50, "tier": 2},
        {"name": "Banged Up", "position": "RB", "team": "SF", "points": 190, "vor": 45, "tier": 2,
         "injury_status": "Questionable", "injury_body_part": "Knee"},
        {"name": "Out For Year", "position": "WR", "team": "SF", "points": 180, "vor": 40, "tier": 2,
         "injury_status": "IR"},
    ]
    prompt = build_analyst_prompt("Who?", available=available, needs={"WR": 1})
    assert "Banged Up" in prompt and "Questionable (Knee)" in prompt
    assert "Out For Year" not in prompt


class _Captured:
    def __init__(self):
        self.calls = []


class _FakeMessages:
    def __init__(self, captured, via):
        self.captured, self.via = captured, via

    def create(self, **kwargs):
        self.captured.calls.append((self.via, kwargs))
        return type("R", (), {"content": [], "stop_reason": "end_turn"})()


class _FakeClient:
    def __init__(self, captured):
        self.messages = _FakeMessages(captured, "standard")
        self.beta = type("B", (), {"messages": _FakeMessages(captured, "beta")})()


def test_current_gen_model_uses_new_search_tool_effort_and_fallbacks(monkeypatch):
    captured = _Captured()
    monkeypatch.setattr(news, "client", lambda: _FakeClient(captured))
    news.create_message("claude-opus-5", "high", 4000, "hello")
    via, kwargs = captured.calls[0]
    assert via == "beta"
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["tools"][0]["type"] == "web_search_20260209"
    assert kwargs["output_config"] == {"effort": "high"}
    assert kwargs["fallbacks"] == [{"model": "claude-opus-4-8"}]
    assert news.FALLBACK_BETA in kwargs["betas"]


def test_haiku_override_uses_legacy_search_tool_and_no_effort(monkeypatch):
    captured = _Captured()
    monkeypatch.setattr(news, "client", lambda: _FakeClient(captured))
    news.create_message("claude-haiku-4-5", "low", 2000, "hello")
    via, kwargs = captured.calls[0]
    assert via == "standard"
    assert kwargs["tools"][0]["type"] == "web_search_20250305"
    assert "output_config" not in kwargs
    assert "fallbacks" not in kwargs


def test_defaults_are_opus_5_with_role_specific_effort(monkeypatch):
    for var in ("CLAUDE_FAST_MODEL", "CLAUDE_ANALYST_MODEL", "CLAUDE_FAST_EFFORT", "CLAUDE_ANALYST_EFFORT"):
        monkeypatch.delenv(var, raising=False)
    importlib.reload(news)
    assert news.FAST_MODEL == "claude-opus-5" and news.FAST_EFFORT == "low"
    assert news.ANALYST_MODEL == "claude-opus-5" and news.ANALYST_EFFORT == "high"


def test_refusal_returns_a_message_not_a_crash(monkeypatch):
    class Refused:
        content, stop_reason = [], "refusal"

    monkeypatch.setattr(news, "create_message", lambda *a, **k: Refused())
    assert "declined" in news.ask_question("anything").lower()


def test_sources_are_collected_from_search_result_blocks_and_citations():
    class Cite:
        url, title = "https://a.example/x", "Story A"

    class TextBlock:
        type, text = "text", "summary"
        citations = [Cite()]

    class Result:
        url, title = "https://b.example/y", "Story B"

    class SearchBlock:
        type, content = "web_search_tool_result", [Result()]

    class Resp:
        content, stop_reason = [SearchBlock(), TextBlock()], "end_turn"

    assert news.collect_sources(Resp()) == {
        "https://a.example/x": "Story A",
        "https://b.example/y": "Story B",
    }


def test_analyst_search_can_be_disabled(monkeypatch):
    captured = _Captured()
    monkeypatch.setattr(news, "client", lambda: _FakeClient(captured))
    monkeypatch.setattr(news, "ANALYST_SEARCH_USES", 0)
    news.ask_question("Who?")
    _, kwargs = captured.calls[0]
    assert "tools" not in kwargs
    monkeypatch.setattr(news, "ANALYST_SEARCH_USES", 2)
    news.ask_question("Who?")
    _, kwargs = captured.calls[1]
    assert kwargs["tools"][0]["max_uses"] == 2
