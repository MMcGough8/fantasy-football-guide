import os
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

# Override in .env. FAST handles news lookups (cheap, frequent); ANALYST answers
# draft questions with the board in context.
FAST_MODEL = os.getenv("CLAUDE_FAST_MODEL", "claude-haiku-4-5")
ANALYST_MODEL = os.getenv("CLAUDE_ANALYST_MODEL", "claude-sonnet-4-6")
WEB_SEARCH = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
BOARD_ROWS_FOR_ANALYST = 25

_client = None


def client():
    """Anthropic client, created on first use so a missing key fails at the call site."""
    global _client
    if _client is None:
        _client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    return _client


def get_player_news(name, team, position):
    """Search fantasy outlets and return a summary plus its source links."""
    prompt = (
        f"Search for the latest fantasy football news on {name}, "
        f"{position} for {team}. In 2-3 sentences, summarize the most important "
        f"recent updates that matter for fantasy: injuries, depth-chart or role "
        f"changes, or usage trends. If there's no notable recent news, say so "
        f"briefly. Finish with a one-line bold fantasy takeaway."
    )

    response = client().messages.create(
        model=FAST_MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
        tools=[WEB_SEARCH],
    )

    # Pull the summary text
    text_parts = [block.text for block in response.content if block.type == "text"]
    summary = "\n".join(text_parts).strip()

    # Pull the source links from citations on the text blocks
    sources = {}
    for block in response.content:
        if block.type == "text" and getattr(block, "citations", None):
            for cite in block.citations:
                url = getattr(cite, "url", None)
                title = getattr(cite, "title", None) or url
                if url:
                    sources[url] = title  # dict dedupes repeated URLs

    return {"summary": summary, "sources": sources}


SOURCE_LABELS = {"sleeper": "Sleeper", "fp": "FP", "espn": "ESPN"}


def _board_row(p):
    by_source = p.get("points_by_source") or {}
    sources = ""
    if len(by_source) > 1:
        sources = " (" + " / ".join(f"{SOURCE_LABELS.get(s, s)} {v}" for s, v in by_source.items()) + ")"
    bye = f" bye {p['bye']}" if p.get("bye") else ""
    return (
        f"{p['name']} {p['position']} {p['team']}{bye}: proj {p['points']}{sources}, "
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
        rows = "\n".join(_board_row(p) for p in available[:BOARD_ROWS_FOR_ANALYST])
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
        f"Answer in 4-6 sentences.\n\n"
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
    response = client().messages.create(
        model=ANALYST_MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
        tools=[WEB_SEARCH],
    )
    text_parts = [block.text for block in response.content if block.type == "text"]
    return "\n".join(text_parts).strip()


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
            f"If a player has no notable recent news, skip them."
        )
    else:
        # General: top NFL/fantasy stories
        prompt = (
            "Search for the most important fantasy football news right now. "
            "Give me the top 5 stories that matter most for fantasy managers — "
            "injuries, role changes, big news. For each, respond with ONLY:\n"
            "PLAYER | headline\n"
            "(headline under 10 words). One per line, no numbering, no other text."
        )

    response = client().messages.create(
        model=FAST_MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
        tools=[WEB_SEARCH],
    )
    text = "\n".join(b.text for b in response.content if b.type == "text").strip()

    stories = []
    for line in text.split("\n"):
        if "|" in line:
            name, headline = line.split("|", 1)
            stories.append({"player": name.strip(), "headline": headline.strip()})
    return stories


if __name__ == "__main__":
    print(get_player_news("Christian McCaffrey", "SF", "RB"))
