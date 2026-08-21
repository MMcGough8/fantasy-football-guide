import os
from anthropic import Anthropic
from dotenv import load_dotenv

from recommend import is_unavailable

load_dotenv()

# Override in .env. FAST handles news lookups (short, frequent, low effort);
# ANALYST answers draft questions with the board in context (high effort).
FAST_MODEL = os.getenv("CLAUDE_FAST_MODEL", "claude-opus-5")
ANALYST_MODEL = os.getenv("CLAUDE_ANALYST_MODEL", "claude-opus-5")
FAST_EFFORT = os.getenv("CLAUDE_FAST_EFFORT", "low")
ANALYST_EFFORT = os.getenv("CLAUDE_ANALYST_EFFORT", "high")
# max_tokens covers adaptive thinking plus the answer; short answers still stay short
NEWS_MAX_TOKENS = 2000
ANALYST_MAX_TOKENS = 4000
WEB_SEARCH_MAX_USES = 3
# Web searches dominate analyst latency (40-120s with search vs ~18s without).
# The board already carries injury status and news flags, so the default is to
# answer from the board alone; set CLAUDE_ANALYST_SEARCH_USES=2 for news-aware
# answers when you are not on the clock.
ANALYST_SEARCH_USES = int(os.getenv("CLAUDE_ANALYST_SEARCH_USES", "0"))
NO_PREAMBLE = "Reply with the answer only; do not narrate what you are searching for."
BOARD_ROWS_FOR_ANALYST = 25

# Models from Opus/Sonnet 4.6 onward take the dynamic-filtering search tool and the
# effort knob; older models (Haiku 4.5) take the basic tool and no output_config.
CURRENT_GEN_PREFIXES = (
    "claude-opus-5", "claude-sonnet-5", "claude-fable-5",
    "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8", "claude-sonnet-4-6",
)
# Server-side refusal fallback: if a frontier model declines, the API reruns on
# the substitute inside the same call.
FALLBACK_MODELS = {"claude-opus-5": "claude-opus-4-8", "claude-fable-5": "claude-opus-4-8"}
FALLBACK_BETA = "server-side-fallback-2026-06-01"
REFUSED_TEXT = "Claude declined to answer that one."

_client = None


def client():
    """Anthropic client, created on first use so a missing key fails at the call site."""
    global _client
    if _client is None:
        _client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    return _client


def is_current_gen(model):
    return model.startswith(CURRENT_GEN_PREFIXES)


def web_search_tool(model, max_uses=WEB_SEARCH_MAX_USES):
    tool_type = "web_search_20260209" if is_current_gen(model) else "web_search_20250305"
    return {"type": tool_type, "name": "web_search", "max_uses": max_uses}


def create_message(model, effort, max_tokens, prompt, search_uses=WEB_SEARCH_MAX_USES):
    """One request, shaped for whichever model is configured; search_uses=0 means no tool."""
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if search_uses:
        kwargs["tools"] = [web_search_tool(model, search_uses)]
    if is_current_gen(model):
        kwargs["output_config"] = {"effort": effort}
    fallback = FALLBACK_MODELS.get(model)
    if fallback:
        return client().beta.messages.create(
            betas=[FALLBACK_BETA], fallbacks=[{"model": fallback}], **kwargs
        )
    return client().messages.create(**kwargs)


def response_text(response):
    if getattr(response, "stop_reason", None) == "refusal":
        return REFUSED_TEXT
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


def collect_sources(response):
    """{url: title} from text citations and from the search-result blocks themselves."""
    sources = {}
    for block in response.content:
        if block.type == "text":
            for cite in getattr(block, "citations", None) or []:
                url = getattr(cite, "url", None)
                if url:
                    sources[url] = getattr(cite, "title", None) or url
        elif block.type == "web_search_tool_result" and isinstance(block.content, list):
            for result in block.content:
                url = getattr(result, "url", None)
                if url:
                    sources.setdefault(url, getattr(result, "title", None) or url)
    return sources


def get_player_news(name, team, position):
    """Search fantasy outlets and return a summary plus its source links."""
    prompt = (
        f"Search for the latest fantasy football news on {name}, "
        f"{position} for {team}. In 2-3 sentences, summarize the most important "
        f"recent updates that matter for fantasy: injuries, depth-chart or role "
        f"changes, or usage trends. If there's no notable recent news, say so "
        f"briefly. Finish with a one-line bold fantasy takeaway. {NO_PREAMBLE}"
    )

    response = create_message(FAST_MODEL, FAST_EFFORT, NEWS_MAX_TOKENS, prompt)
    return {"summary": response_text(response), "sources": collect_sources(response)}


SOURCE_LABELS = {"sleeper": "Sleeper", "fp": "FP", "espn": "ESPN"}


def _board_row(p):
    by_source = p.get("points_by_source") or {}
    sources = ""
    if len(by_source) > 1:
        sources = " (" + " / ".join(f"{SOURCE_LABELS.get(s, s)} {v}" for s, v in by_source.items()) + ")"
    bye = f" bye {p['bye']}" if p.get("bye") else ""
    injury = ""
    if p.get("injury_status"):
        part = f" ({p['injury_body_part']})" if p.get("injury_body_part") else ""
        injury = f" [{p['injury_status']}{part}]"
    return (
        f"{p['name']} {p['position']} {p['team']}{bye}{injury}: proj {p['points']}{sources}, "
        f"VOR {p['vor']}, tier {p.get('tier', '?')}"
    )


def build_analyst_prompt(
    question,
    league_size=10,
    scoring="PPR",
    my_roster=None,
    taken_count=0,
    round_num=None,
    pick_in_round=None,
    available=None,
    needs=None,
    scoring_notes=None,
):
    """Prompt for the draft analyst, with the live board when the app supplies it."""
    from collections import Counter

    roster_text = "nobody yet"
    pos_counts = {}
    if my_roster:
        pos_counts = dict(Counter(p["position"] for p in my_roster))
        roster_text = ", ".join(f"{p['name']} ({p['position']})" for p in my_roster)
    construction = ", ".join(f"{n} {pos}" for pos, n in pos_counts.items()) or "empty"

    where = f"You are at Round {round_num}, Pick {pick_in_round}. " if round_num and pick_in_round else ""
    picks_gone = f"{taken_count} players have been drafted overall. " if taken_count else ""
    scoring_line = f"Scoring: {scoring_notes}.\n" if scoring_notes else ""
    needs_line = (
        "Starting slots still open: " + ", ".join(f"{s} x{n}" for s, n in needs.items()) + "\n"
        if needs
        else "All starting slots are filled; remaining picks are bench depth.\n"
    )

    board_block = ""
    if available:
        draftable = [p for p in available if not is_unavailable(p)]
        rows = "\n".join(_board_row(p) for p in draftable[:BOARD_ROWS_FOR_ANALYST])
        board_block = (
            f"\nTOP AVAILABLE PLAYERS (already scored with this league's rules; "
            f"VOR = points over the replacement starter at that position; the bracket "
            f"shows each projection source's own number):\n{rows}\n\n"
            f"Treat these numbers as the ground truth for value. Use web search only for "
            f"breaking news (injury, suspension, trade) that the projections might not reflect.\n"
        )

    return (
        f"You're a sharp, energetic fantasy draft analyst, confident and opinionated "
        f"but grounded in real strategy. Setting: a {league_size}-team draft, {scoring}.\n"
        f"{scoring_line}\n"
        f"DRAFT SITUATION:\n"
        f"- {where}{picks_gone}\n"
        f"- The person's roster so far: {roster_text}\n"
        f"- Positional construction: {construction}\n"
        f"- {needs_line}"
        f"{board_block}\n"
        f"Use real draft-strategy frameworks in your reasoning and name them when "
        f"relevant: Zero RB, Hero RB, Robust RB, late-round QB, and streaming/early TE. "
        f"Consider positional scarcity (tiers left), roster balance, what's likely to "
        f"fall to their next pick, and value under this scoring. Name specific players "
        f"from the list above. Tell them which position to prioritize NOW and why, with "
        f"a clear, decisive take. If they should wait on a position, say so and explain. "
        f"Answer in 4-6 sentences. {NO_PREAMBLE}\n\n"
        f"Question: {question}"
    )


def ask_question(
    question,
    league_size=10,
    scoring="PPR",
    my_roster=None,
    taken=None,
    round_num=None,
    pick_in_round=None,
    available=None,
    needs=None,
    scoring_notes=None,
):
    """Answer a fantasy question with the live board in context."""
    taken_count = taken if isinstance(taken, int) else len(taken or ())
    prompt = build_analyst_prompt(
        question,
        league_size=league_size,
        scoring=scoring,
        my_roster=my_roster,
        taken_count=taken_count,
        round_num=round_num,
        pick_in_round=pick_in_round,
        available=available,
        needs=needs,
        scoring_notes=scoring_notes,
    )
    response = create_message(
        ANALYST_MODEL, ANALYST_EFFORT, ANALYST_MAX_TOKENS, prompt, search_uses=ANALYST_SEARCH_USES
    )
    return response_text(response)


def get_top_stories(my_players=None):
    """Top stories — general if no roster, personalized to your players if you have one."""
    if my_players:
        # Personalized: news about the user's rostered players
        names = ", ".join(my_players[:15])  # cap the list length
        prompt = (
            f"Search for the latest fantasy football news specifically about these "
            f"players on my roster: {names}. Give me the 5 most important updates "
            f"among THESE players — injuries, role or usage changes, notable news. "
            f"For each, respond with ONLY:\nPLAYER | headline\n"
            f"(headline under 10 words). One per line, no numbering, no other text. "
            f"If a player has no notable recent news, skip them. {NO_PREAMBLE}"
        )
    else:
        # General: top NFL/fantasy stories
        prompt = (
            "Search for the most important fantasy football news right now. "
            "Give me the top 5 stories that matter most for fantasy managers — "
            "injuries, role changes, big news. For each, respond with ONLY:\n"
            "PLAYER | headline\n"
            f"(headline under 10 words). One per line, no numbering, no other text. {NO_PREAMBLE}"
        )

    response = create_message(FAST_MODEL, FAST_EFFORT, NEWS_MAX_TOKENS, prompt)
    text = response_text(response)

    stories = []
    for line in text.split("\n"):
        if "|" in line:
            name, headline = line.split("|", 1)
            stories.append({"player": name.strip(), "headline": headline.strip()})
    return stories


if __name__ == "__main__":
    print(get_player_news("Christian McCaffrey", "SF", "RB"))
