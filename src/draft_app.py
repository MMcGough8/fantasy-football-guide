import json
import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()  # secrets come from .env at the repo root, never from code
from collections import Counter
from draft_board import build_board
from categories import sleepers, top_rookies, boom_ceiling, high_floor
from grader import grade_draft
from roster_slots import allocate_slots
from recommend import recommend_pick
from pick_sync import apply_picks, index_board_by_player_id, next_pick_info
import sleeper_league
from sleeper_league import SleeperError
from scoring import PRESET_FALLBACK_POSITIONS, scoring_summary, unprojected_bonus_keys
import fantasypros
from fantasypros import FantasyProsError
import espn_projections
from espn_projections import EspnError

st.set_page_config(page_title="Fantasy Command Center", page_icon="🏈", layout="wide")

# ---- Config ----
DEFAULT_STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}
LEAGUE_SIZES = [8, 10, 12, 14]
REQUIRED_LEAGUE_KEYS = ("league_id", "name", "num_teams", "starters", "bench", "scoring_settings", "user_id")
DEFAULT_BENCH_SPOTS = 8
SEASON = "2026"
SYNC_INTERVAL = "15s"  # how often auto-sync polls the Sleeper draft
FP_CACHE_SECONDS = 6 * 3600
SOURCE_SPLIT_PCT = 0.15  # flag a player when Sleeper and FantasyPros differ by this much
LEAGUE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".sleeper_league.json"
)
TOP_N = 30
SCORING_LABELS = {"PPR": "pts_ppr", "Half-PPR": "pts_half_ppr", "Standard": "pts_std"}

POS_COLORS = {
    "QB": "#c084fc",
    "RB": "#34d399",
    "WR": "#38bdf8",
    "TE": "#fb923c",
    "K": "#94a3b8",
    "DEF": "#f87171",
    "FLEX": "#e879f9",
    "SUPER_FLEX": "#e879f9",
    "REC_FLEX": "#e879f9",
    "WRRB_FLEX": "#e879f9",
    "BN": "#64748b",
}

# ---- Styling ----
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;600;700&family=JetBrains+Mono:wght@400;600&display=swap');
.stApp { background: radial-gradient(1200px 600px at 20% -10%, #14203a 0%, #0b0f17 55%, #080b11 100%); }
.stApp > div, [data-testid="stMarkdownContainer"], [data-testid="stSidebar"] * { color: #ffffff; }
[data-baseweb="select"] *, [role="listbox"] *, .stTextInput input { color: #1a1a1a !important; }
header[data-testid="stHeader"] { background: transparent; }
html, body, [class*="css"] { font-family: 'Chakra Petch', sans-serif; }
.cc-title { font-weight:700; font-size:2.1rem; letter-spacing:2px; text-transform:uppercase; color:#e6edf3; margin-bottom:0; }
.cc-title .accent { color:#00e0a4; }
.cc-sub { color:#7d8590; letter-spacing:3px; font-size:0.72rem; text-transform:uppercase; }
.stat-card { background:linear-gradient(180deg,#121826,#0d1420); border:1px solid #1f2a3a; border-radius:10px; padding:12px 16px; }
.stat-val { font-family:'JetBrains Mono',monospace; font-size:1.5rem; color:#00e0a4; font-weight:600; }
.stat-lbl { color:#7d8590; font-size:0.68rem; letter-spacing:2px; text-transform:uppercase; }
.rec-panel { background:linear-gradient(90deg, rgba(0,224,164,0.12), rgba(0,224,164,0.02)); border:1px solid rgba(0,224,164,0.35); border-left:4px solid #00e0a4; border-radius:12px; padding:18px 22px; margin:14px 0 22px; box-shadow:0 0 40px rgba(0,224,164,0.08); }
.rec-label { color:#00e0a4; letter-spacing:3px; font-size:0.72rem; text-transform:uppercase; margin-bottom:4px; }
.rec-name { font-size:1.5rem; font-weight:700; color:#f0f6fc; }
.rec-meta { color:#9aa4b2; font-size:0.9rem; margin-top:4px; font-family:'JetBrains Mono',monospace; }
.sec-head { color:#e6edf3; letter-spacing:2px; text-transform:uppercase; font-weight:600; font-size:0.95rem; border-bottom:1px solid #1f2a3a; padding-bottom:8px; margin:6px 0; }
.badge { padding:2px 9px; border-radius:6px; font-weight:600; font-size:0.78rem; font-family:'JetBrains Mono',monospace; }
.mono { font-family:'JetBrains Mono',monospace; color:#c9d1d9; }
.rank-num { font-family:'JetBrains Mono',monospace; color:#4d5866; }
.stButton > button { border-radius:8px; border:1px solid #2a3a4f; font-family:'Chakra Petch',sans-serif; letter-spacing:1px; font-weight:600; transition:all .15s ease; background:#1a2230; color:#ffffff; }
.stButton > button:hover { border-color:#00e0a4; box-shadow:0 0 12px rgba(0,224,164,0.25); }
[data-testid="stSidebar"] { background:#0a0e15; border-right:1px solid #1a2230; }
</style>
""",
    unsafe_allow_html=True,
)


def badge(pos, tier=""):
    c = POS_COLORS.get(pos, "#94a3b8")
    return f"<span class='badge' style='background:{c}22;color:{c};border:1px solid {c}55;'>{pos}{tier}</span>"


def sleeper_photo(player_id):
    if not player_id:
        return None
    return f"https://sleepercdn.com/content/nfl/players/{player_id}.jpg"


def espn_photo(player_id):
    if not player_id:
        return None
    return f"https://a.espncdn.com/i/headshots/nfl/players/full/{player_id}.png"


@st.cache_data(ttl=FP_CACHE_SECONDS, show_spinner="Loading FantasyPros consensus...")
def load_fantasypros(season):
    return fantasypros.fetch_all(season, os.getenv("FANTASYPROS_API_KEY", "").strip())


@st.cache_data(ttl=FP_CACHE_SECONDS, show_spinner="Loading ESPN projections...")
def load_espn(season):
    return espn_projections.fetch_all(season)


@st.cache_data(show_spinner="Loading projections from Sleeper...")
def load_board(scoring, num_teams, scoring_items, starters_items, use_fantasypros):
    """Returns (board, projection_note). Dicts arrive as sorted tuples so the cache can hash them."""
    scoring_settings = dict(scoring_items) if scoring_items else None
    extra, live_ranks, problems = {}, None, []
    if use_fantasypros:
        try:
            extra["fp"] = load_fantasypros(SEASON)
        except FantasyProsError as e:
            problems.append(f"FantasyPros unavailable: {e}")
    try:
        espn = load_espn(SEASON)
        extra["espn"] = espn["projections"]
        live_ranks = espn["ranks"]
    except EspnError as e:
        problems.append(f"ESPN unavailable: {e}")

    names = ["Sleeper (Rotowire)"] + [
        label for key, label in (("fp", "FantasyPros consensus"), ("espn", "ESPN")) if key in extra
    ]
    how = {1: "", 2: ", averaged stat by stat", 3: ", per-stat median (one outlier ignored)"}[len(names)]
    note = " + ".join(names) + how
    if live_ranks:
        note += ". ESPN ranks are live"
    if problems:
        note += ". " + "; ".join(problems)
    board = build_board(
        scoring, num_teams, scoring_settings, dict(starters_items), extra or None, live_ranks
    )
    return board, note


@st.cache_data(ttl=3600, show_spinner=False)
def cached_news(name, team, position):
    from news import get_player_news

    return get_player_news(name, team, position)


@st.cache_data(ttl=600, show_spinner="Pulling free agents from ESPN...")
def load_waivers():
    from waivers import get_league, get_waiver_targets
    import os

    league = get_league()
    return get_waiver_targets(league, os.getenv("MY_TEAM_NAME", ""), size=40)


def player_key(p):
    return f"{p['name']}|{p['team']}|{p['position']}"


SOURCE_LABELS = {"sleeper": "Sleeper", "fp": "FP", "espn": "ESPN"}


def sources_disagree(p):
    values = list((p.get("points_by_source") or {}).values())
    if len(values) < 2 or max(values) <= 0:
        return False
    if p["position"] in PRESET_FALLBACK_POSITIONS:
        return False  # K/DEF use each site's own preset scale, not comparable
    return (max(values) - min(values)) / max(values) >= SOURCE_SPLIT_PCT


def source_txt(p):
    by_source = p.get("points_by_source") or {}
    if len(by_source) < 2:
        return ""
    return " (" + " · ".join(f"{SOURCE_LABELS.get(s, s)} {v}" for s, v in by_source.items()) + ")"


def draft_player(p, mine):
    st.session_state.drafted = st.session_state.drafted | {player_key(p)}
    if mine:
        st.session_state.my_roster = st.session_state.my_roster + [p]


def reset_draft():
    st.session_state.drafted = set()
    st.session_state.my_roster = []
    st.session_state.synced_taken = set()
    st.session_state.synced_mine = []
    st.session_state.draft_info = None


def save_league(cfg):
    try:
        with open(LEAGUE_FILE, "w") as f:
            json.dump(cfg, f)
    except OSError as e:
        st.warning(f"Connected, but couldn't save the league for next time: {e}")


def load_saved_league():
    if not os.path.exists(LEAGUE_FILE):
        return None
    try:
        with open(LEAGUE_FILE) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict) or any(k not in cfg for k in REQUIRED_LEAGUE_KEYS):
        return None  # written by an older version; reconnect instead of crashing
    return cfg


def forget_league():
    st.session_state.league = None
    st.session_state.draft_info = None
    st.session_state.synced_taken = set()
    st.session_state.synced_mine = []
    st.session_state.pop("my_roster_id", None)
    try:
        os.remove(LEAGUE_FILE)
    except FileNotFoundError:
        pass


# ---- Session defaults ----
if "drafted" not in st.session_state:
    st.session_state.drafted = set()
if "my_roster" not in st.session_state:
    st.session_state.my_roster = []
if "scoring_pref" not in st.session_state:
    st.session_state.scoring_pref = "PPR"
if "league_size_pref" not in st.session_state:
    st.session_state.league_size_pref = 10
if "synced_taken" not in st.session_state:
    st.session_state.synced_taken = set()
if "synced_mine" not in st.session_state:
    st.session_state.synced_mine = []
if "pos_filter" not in st.session_state:
    st.session_state.pos_filter = "All"
if "league" not in st.session_state:
    st.session_state.league = load_saved_league()
if "draft_info" not in st.session_state:
    st.session_state.draft_info = None

# ---- Load board (needed by player search in both modes) ----
# ---- League config: a connected Sleeper league overrides the manual radios ----
league = st.session_state.league
if league:
    starters = league["starters"]
    bench_spots = league["bench"]
    num_teams = league["num_teams"]
    scoring_items = tuple(sorted(league["scoring_settings"].items()))
    scoring_label = f"{league['name']} league scoring"
else:
    starters = DEFAULT_STARTERS
    bench_spots = DEFAULT_BENCH_SPOTS
    num_teams = st.session_state.league_size_pref
    scoring_items = None
    scoring_label = st.session_state.scoring_pref

try:
    board, projection_note = load_board(
        SCORING_LABELS[st.session_state.scoring_pref],
        num_teams,
        scoring_items,
        tuple(sorted(starters.items())),
        bool(os.getenv("FANTASYPROS_API_KEY", "").strip()),
    )
except Exception as e:
    st.error(f"Couldn't load projections from Sleeper: {e}")
    c1, c2 = st.columns(2)
    if c1.button("Retry", type="primary"):
        st.rerun()
    if league and c2.button("Disconnect league"):
        forget_league()
        st.rerun()
    st.stop()
board = list({player_key(x): x for x in board}.values())  # de-duplicate
board_by_id = index_board_by_player_id(board)

# Synced picks are the truth from Sleeper; manual Mine/Taken marks layer on top.
synced_taken = st.session_state.synced_taken
drafted_keys = st.session_state.drafted | synced_taken
my_roster = st.session_state.synced_mine + [
    p for p in st.session_state.my_roster if player_key(p) not in synced_taken
]
available = [p for p in board if player_key(p) not in drafted_keys]
available.sort(key=lambda p: p["vor"], reverse=True)

allocation = allocate_slots(my_roster, starters)
needs = allocation.needs


total_picks = sum(starters.values()) + bench_spots


def sync_picks_from_sleeper():
    """Mark every pick from the live Sleeper draft. Returns True if anything changed."""
    draft = sleeper_league.get_draft(league["draft_id"])
    picks = sleeper_league.get_draft_picks(league["draft_id"])
    if "my_roster_id" not in st.session_state:
        rosters = sleeper_league.get_rosters(league["league_id"])
        st.session_state.my_roster_id = sleeper_league.my_roster_id(rosters, league["user_id"])
    result = apply_picks(picks, board_by_id, league["user_id"], st.session_state.my_roster_id)

    new_taken = {player_key(p) for p in result.taken}
    new_mine_keys = [player_key(p) for p in result.mine]
    changed = (
        new_taken != st.session_state.synced_taken
        or new_mine_keys != [player_key(p) for p in st.session_state.synced_mine]
    )
    st.session_state.synced_taken = new_taken
    st.session_state.synced_mine = result.mine
    st.session_state.draft_info = {
        "status": draft.get("status"),
        "picks": len(picks),
        "next": next_pick_info(draft, len(picks), league["user_id"]),
        "unmatched": len(result.unmatched),
    }
    return changed


# ==================== SIDEBAR ====================
with st.sidebar:
    # ---- Mode toggle (top) ----
    st.markdown("<div class='sec-head'>Mode</div>", unsafe_allow_html=True)
    mode = st.radio(
        "App mode",
        options=["Draft", "In-Season"],
        label_visibility="collapsed",
        key="app_mode",
    )
    st.divider()

    # ---- Sleeper League ----
    st.markdown("<div class='sec-head'>Sleeper League</div>", unsafe_allow_html=True)
    if league:
        slot_text = " / ".join(
            f"{n}{slot}" if n > 1 else slot for slot, n in league["starters"].items()
        )
        st.markdown(
            f"<span style='color:#ffffff'>{league['name']}</span><br>"
            f"<span class='rank-num'>{league['num_teams']} teams · {slot_text} · "
            f"{league['bench']} BN · league scoring</span>",
            unsafe_allow_html=True,
        )
        if league.get("is_dynasty"):
            st.caption("Dynasty league: this board only values the 2026 season.")
        st.button("Disconnect", on_click=forget_league, use_container_width=True)
    else:
        username = st.text_input(
            "Sleeper username",
            placeholder="Sleeper username",
            key="sleeper_username",
            label_visibility="collapsed",
        )
        if st.button("Find my leagues", use_container_width=True) and username:
            try:
                user = sleeper_league.get_user(username.strip())
                found = sleeper_league.get_leagues(user["user_id"], SEASON)
                st.session_state.league_options = {
                    f"{lg['name']} ({lg['total_rosters']} teams)": (lg, user["user_id"])
                    for lg in found
                }
                if not found:
                    st.warning(f"No {SEASON} leagues found for {username}")
            except SleeperError as e:
                st.error(str(e))
        options = st.session_state.get("league_options") or {}
        if options:
            choice = st.selectbox(
                "League", options=list(options.keys()), label_visibility="collapsed"
            )
            if st.button("Connect", type="primary", use_container_width=True):
                picked_league, user_id = options[choice]
                cfg = sleeper_league.league_config(picked_league, user_id)
                st.session_state.league = cfg
                st.session_state.league_options = None
                save_league(cfg)
                st.rerun()
    st.divider()

    @st.cache_data(ttl=1800, show_spinner=False)
    def load_top_stories(player_names):
        from news import get_top_stories

        # player_names is a tuple (hashable for caching); empty = general news
        return get_top_stories(list(player_names) if player_names else None)

    # Build a stable, hashable key from the current roster
    roster_names = tuple(sorted(p["name"] for p in my_roster))
    label = "📰 Your Players" if roster_names else "📰 The Latest"

    with st.expander(label, expanded=False):
        try:
            for s in load_top_stories(roster_names):
                st.markdown(
                    f"<div style='margin-bottom:8px'>"
                    f"<span style='color:#00e0a4;font-size:0.72rem'>{s['player']}</span><br>"
                    f"<span style='color:#ffffff;font-size:0.85rem'>{s['headline']}</span></div>",
                    unsafe_allow_html=True,
                )
        except Exception:
            st.markdown(
                "<span class='rank-num'>News unavailable right now</span>",
                unsafe_allow_html=True,
            )

    # ---- Player Search ----
    st.markdown("<div class='sec-head'>Player Search</div>", unsafe_allow_html=True)
    news_options = {f"{p['name']} · {p['position']} {p['team']}": p for p in board}
    choice = st.selectbox(
        "Find a player", options=list(news_options.keys()), label_visibility="collapsed"
    )
    if st.button("Get news", type="primary", use_container_width=True):
        picked = news_options[choice]
        with st.spinner(f"Searching outlets for {picked['name']}..."):
            try:
                result = cached_news(picked["name"], picked["team"], picked["position"])
                st.session_state.news_summary = result["summary"]
                st.session_state.news_sources = result.get("sources", {})
                st.session_state.news_player = picked["name"]
                st.session_state.news_photo = sleeper_photo(picked.get("player_id"))
            except Exception as e:
                st.error(f"News unavailable (is ANTHROPIC_API_KEY set in .env?): {e}")

    # News result — directly under Player Search
    if st.session_state.get("news_summary"):
        with st.expander(
            f"📰 News: {st.session_state.get('news_player', '')}", expanded=True
        ):
            if st.session_state.get("news_photo"):
                st.image(st.session_state.news_photo, width=70)
            st.markdown(
                f"<div style='color:#ffffff;'>{st.session_state.news_summary}</div>",
                unsafe_allow_html=True,
            )
            sources = st.session_state.get("news_sources", {})
            if sources:
                st.markdown(
                    "<div style='color:#7d8590;font-size:0.7rem;margin-top:8px;"
                    "text-transform:uppercase;letter-spacing:1px'>Sources</div>",
                    unsafe_allow_html=True,
                )
                for url, title in sources.items():
                    short = title[:40] + "…" if len(title) > 40 else title
                    st.markdown(
                        f"<a href='{url}' target='_blank' "
                        f"style='color:#38bdf8;font-size:0.78rem'>{short}</a>",
                        unsafe_allow_html=True,
                    )

    # ---- Ask the Analyst ----
    st.markdown("<div class='sec-head'>Ask the Analyst</div>", unsafe_allow_html=True)
    user_q = st.text_input(
        "Ask a fantasy question",
        label_visibility="collapsed",
        placeholder="e.g. Should I start my WR2 this week?",
    )
    if st.button("Ask", use_container_width=True) and user_q:
        from news import ask_question

        picks_made = (st.session_state.draft_info or {}).get("picks") or len(drafted_keys)
        with st.spinner("Thinking..."):
            try:
                st.session_state.answer = ask_question(
                    user_q,
                    league_size=num_teams,
                    scoring=scoring_label,
                    my_roster=my_roster,
                    taken=picks_made,
                    round_num=picks_made // num_teams + 1,
                    pick_in_round=picks_made % num_teams + 1,
                    available=available,
                    needs=needs,
                    scoring_notes=scoring_summary(league["scoring_settings"]) if league else None,
                )
            except Exception as e:
                st.error(f"Analyst unavailable (is ANTHROPIC_API_KEY set in .env?): {e}")

    # Analyst answer — directly under Ask the Analyst
    if st.session_state.get("answer"):
        with st.expander("💬 Analyst answer", expanded=True):
            st.markdown(
                f"<div style='color:#ffffff;'>{st.session_state.answer}</div>",
                unsafe_allow_html=True,
            )
    # ---- Draft-only sidebar sections ----
    if mode == "Draft":
        st.divider()
        st.markdown("<div class='sec-head'>My Roster</div>", unsafe_allow_html=True)
        if my_roster:
            for slot, slot_players in allocation.slots.items():
                for p in slot_players:
                    st.markdown(
                        f"{badge(slot)} &nbsp; <span style='color:#ffffff;'>{p['name']}</span> "
                        f"<span class='rank-num'>({p['team']})</span>",
                        unsafe_allow_html=True,
                    )
            for p in allocation.bench:
                st.markdown(
                    f"{badge('BN')} &nbsp; <span style='color:#ffffff;'>{p['name']}</span> "
                    f"<span class='rank-num'>({p['position']} · {p['team']})</span>",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                "<span class='rank-num'>No picks yet</span>", unsafe_allow_html=True
            )

        # Bye week collision check
        bye_counts = Counter(p.get("bye") for p in my_roster if p.get("bye"))
        heavy_byes = {wk: n for wk, n in bye_counts.items() if n >= 3}
        if heavy_byes:
            st.markdown(
                "<div class='sec-head'>⚠️ Bye Stacking</div>", unsafe_allow_html=True
            )
            for wk, n in sorted(heavy_byes.items()):
                st.markdown(
                    f"<span style='color:#fb923c'>Week {wk}: {n} players on bye</span>",
                    unsafe_allow_html=True,
                )

        st.markdown("<div class='sec-head'>Still Need</div>", unsafe_allow_html=True)
        need_labels = [f"{pos} x{n}" for pos, n in needs.items() if n > 0]
        st.markdown(
            " &nbsp;".join(
                badge(l.split()[0]) + f" <span class='mono'>{l.split()[1]}</span>"
                for l in need_labels
            )
            if need_labels
            else "<span class='mono' style='color:#34d399'>Starters filled ✓</span>",
            unsafe_allow_html=True,
        )
        # ---- Positional Strengths ----
        # A position is a "strength" if you have surplus depth AND good value there
        strengths = []
        for pos in Counter(p["position"] for p in allocation.bench):
            pos_players = [p for p in my_roster if p["position"] == pos]
            pos_vor = sum(p.get("vor", 0) for p in pos_players)
            # Strong if you have depth beyond your starters AND meaningful total value
            if pos_vor > 100:
                strengths.append((pos, len(pos_players), round(pos_vor)))

        if strengths:
            st.markdown("<div class='sec-head'>Strengths</div>", unsafe_allow_html=True)
            for pos, count, vor in strengths:
                st.markdown(
                    f"{badge(pos)} <span style='color:#34d399;font-size:0.8rem'>"
                    f"{count} deep · strong ({vor} VOR)</span>",
                    unsafe_allow_html=True,
                )

        st.markdown(
            f"<div class='sec-head'>Bench</div>"
            f"<span class='mono'>{len(allocation.bench)} / {bench_spots} filled</span>",
            unsafe_allow_html=True,
        )

        st.divider()
        st.button("Reset draft", on_click=reset_draft, use_container_width=True)


# ==================== HEADER ====================
subtitle = (
    "Value-based rankings · live board"
    if mode == "Draft"
    else "In-season tools · live from your league"
)
st.markdown(
    f"<div class='cc-title'>🏈 Fantasy <span class='accent'>Command Center</span></div>"
    f"<div class='cc-sub'>{subtitle}</div>",
    unsafe_allow_html=True,
)


# ==================== DRAFT MODE ====================
if mode == "Draft":
    # ---- Scoring + league size (manual unless a Sleeper league is connected) ----
    if league:
        gaps = unprojected_bonus_keys(league["scoring_settings"])
        gap_note = (
            " Long-TD bonuses (40+/50+ yds) are not projected and are left out."
            if gaps
            else ""
        )
        st.markdown(
            f"<span class='rank-num'>Board built for <b>{league['name']}</b>: "
            f"{num_teams} teams, your league's scoring (yardage-game bonuses estimated "
            f"from season totals), FLEX-aware replacement levels.{gap_note}<br>"
            f"Projections: {projection_note}.</span>",
            unsafe_allow_html=True,
        )
    else:
        scoring_options = list(SCORING_LABELS.keys())
        st.radio(
            "League scoring",
            options=scoring_options,
            index=scoring_options.index(st.session_state.scoring_pref),
            horizontal=True,
            key="scoring_widget",
            on_change=lambda: st.session_state.update(
                scoring_pref=st.session_state.scoring_widget
            ),
        )
        st.radio(
            "League size",
            options=LEAGUE_SIZES,
            index=LEAGUE_SIZES.index(st.session_state.league_size_pref),
            horizontal=True,
            key="league_size_widget",
            on_change=lambda: st.session_state.update(
                league_size_pref=st.session_state.league_size_widget
            ),
        )
        st.markdown(
            f"<span class='rank-num'>Projections: {projection_note}.</span>",
            unsafe_allow_html=True,
        )

    # ---- Draft status cards ----
    picks_made = (st.session_state.draft_info or {}).get("picks") or len(drafted_keys)
    round_num = picks_made // num_teams + 1
    pick_in_round = picks_made % num_teams + 1

    def stat_card(label, value):
        return f"<div class='stat-card'><div class='stat-val'>{value}</div><div class='stat-lbl'>{label}</div></div>"

    next_pick = (st.session_state.draft_info or {}).get("next")
    if next_pick is None:
        your_pick = "—"
    elif next_pick["picks_until_mine"] == 0:
        your_pick = "NOW"
    else:
        your_pick = next_pick["picks_until_mine"]

    m1, m2, m3, m4 = st.columns(4)
    m1.markdown(
        stat_card("Round · Pick", f"{round_num} · {pick_in_round}"),
        unsafe_allow_html=True,
    )
    m2.markdown(stat_card("Overall Picks", picks_made), unsafe_allow_html=True)
    m3.markdown(stat_card("Your Roster", len(my_roster)), unsafe_allow_html=True)
    m4.markdown(stat_card("Your Pick In", your_pick), unsafe_allow_html=True)

    # ---- Live sync from the Sleeper draft room ----
    if league and league.get("draft_id"):
        sc1, sc2, sc3 = st.columns([1.2, 1.2, 4], vertical_alignment="center")
        if sc1.button("Sync picks", use_container_width=True):
            try:
                sync_picks_from_sleeper()
                st.rerun()
            except SleeperError as e:
                st.error(str(e))
        auto_sync = sc2.toggle("Auto-sync", key="auto_sync")
        info = st.session_state.draft_info
        if info:
            unmatched = f" · {info['unmatched']} unmatched" if info["unmatched"] else ""
            sc3.markdown(
                f"<span class='rank-num'>Sleeper draft {info['status']} · "
                f"{info['picks']} picks synced{unmatched}</span>",
                unsafe_allow_html=True,
            )
        else:
            sc3.markdown(
                f"<span class='rank-num'>Not synced yet. Auto-sync polls every {SYNC_INTERVAL}.</span>",
                unsafe_allow_html=True,
            )
        if auto_sync:

            @st.fragment(run_every=SYNC_INTERVAL)
            def auto_sync_fragment():
                try:
                    if sync_picks_from_sleeper():
                        st.rerun(scope="app")
                except SleeperError as e:
                    st.warning(str(e))

            auto_sync_fragment()

    # ---- Recommendation ----
    pick, reason = recommend_pick(available, needs, len(my_roster), total_picks)
    if pick:
        photo = sleeper_photo(pick.get("player_id"))
        rc1, rc2 = st.columns([1, 6], vertical_alignment="center")
        if photo:
            rc1.image(photo, width=80)
        rc2.markdown(
            f"<div class='rec-panel'><div class='rec-label'>Recommended Pick</div>"
            f"<div class='rec-name'>{pick['name']} &nbsp; {badge(pick['position'], pick.get('tier', ''))}</div>"
            f"<div class='rec-meta'>{reason} · PROJ {pick['points']}{source_txt(pick)} · VOR {pick['vor']}</div></div>",
            unsafe_allow_html=True,
        )
        # ---- Quick Entry (fast pick marking for live drafts) ----
    st.markdown("<div class='sec-head'>Quick Entry</div>", unsafe_allow_html=True)
    qcol1, qcol2, qcol3 = st.columns([3, 1, 1])
    with qcol1:
        quick_name = st.text_input(
            "Quick mark",
            label_visibility="collapsed",
            placeholder="Type a player name…",
            key="quick_entry",
        )

    def quick_mark(mine):
        typed = st.session_state.quick_entry.strip().lower()
        if not typed:
            return
        # Find undrafted players whose name contains what you typed
        matches = [p for p in available if typed in p["name"].lower()]
        if len(matches) == 1:
            draft_player(matches[0], mine)
            st.session_state.quick_msg = (
                f"✓ {'Drafted' if mine else 'Marked taken'}: {matches[0]['name']}"
            )
            st.session_state.quick_entry = ""
        elif len(matches) == 0:
            st.session_state.quick_msg = f"⚠️ No match for '{typed}'"
        else:
            names = ", ".join(m["name"] for m in matches[:5])
            st.session_state.quick_msg = (
                f"⚠️ Multiple matches — be more specific: {names}"
            )

    qcol2.button(
        "Mine",
        key="quick_mine",
        on_click=quick_mark,
        args=(True,),
        type="primary",
        use_container_width=True,
    )
    qcol3.button(
        "Taken",
        key="quick_taken",
        on_click=quick_mark,
        args=(False,),
        use_container_width=True,
    )

    if st.session_state.get("quick_msg"):
        color = "#34d399" if st.session_state.quick_msg.startswith("✓") else "#fb923c"
        st.markdown(
            f"<span style='color:{color};font-size:0.85rem'>{st.session_state.quick_msg}</span>",
            unsafe_allow_html=True,
        )

        # ---- Positional Scarcity (tiers remaining) ----
    st.markdown(
        "<div class='sec-head'>Position Scarcity · Tiers Left</div>",
        unsafe_allow_html=True,
    )

    scarcity_cols = st.columns(6)
    for col, pos in zip(scarcity_cols, ["QB", "RB", "WR", "TE", "K", "DEF"]):
        pos_avail = [p for p in available if p["position"] == pos]
        # Count how many remain in each within-position tier
        tier_counts = {}
        for p in pos_avail:
            t = p.get("tier", "?")
            tier_counts[t] = tier_counts.get(t, 0) + 1

        # Build a small readout, top 3 tiers
        lines = ""
        for t in sorted(k for k in tier_counts if isinstance(k, int))[:3]:
            n = tier_counts[t]
            # Warn (orange) when a top tier is nearly gone
            warn = n <= 2 and t <= 2
            color = "#fb923c" if warn else "#9aa4b2"
            flag = " ⚠️" if warn else ""
            lines += (
                f"<div style='color:{color};font-size:0.72rem'>T{t}: {n}{flag}</div>"
            )

        col.markdown(
            f"<div style='text-align:center'>{badge(pos)}</div>{lines}",
            unsafe_allow_html=True,
        )

    # ---- Board ----
    st.markdown("<div class='sec-head'>Best Available</div>", unsafe_allow_html=True)

    filters = ["All", "QB", "RB", "WR", "TE", "K", "DEF"]
    fcols = st.columns(len(filters))
    for col, pos in zip(fcols, filters):
        btn_type = "primary" if st.session_state.pos_filter == pos else "secondary"
        if col.button(
            pos, key=f"filter_{pos}", type=btn_type, use_container_width=True
        ):
            st.session_state.pos_filter = pos
            st.rerun()

    sort_by = st.radio(
        "Sort by",
        options=["VOR", "Consensus", "ESPN", "Berry"],
        horizontal=True,
        key="sort_by",
    )

    if st.session_state.pos_filter == "All":
        shown = list(available)
    else:
        shown = [p for p in available if p["position"] == st.session_state.pos_filter]

    if sort_by == "VOR":
        shown.sort(key=lambda p: p.get("vor", 0), reverse=True)
    elif sort_by == "Consensus":
        shown.sort(key=lambda p: p.get("consensus") or 9999)
    elif sort_by == "ESPN":
        shown.sort(key=lambda p: p.get("espn_rank") or 9999)
    elif sort_by == "Berry":
        shown.sort(key=lambda p: p.get("berry_rank") or 9999)
    st.markdown(
        f"<div style='color:#9aa4b2;font-size:0.85rem;margin:6px 0'>"
        f"Showing {min(len(shown), TOP_N)} of {len(shown)} available"
        f"{'' if st.session_state.pos_filter == 'All' else ' ' + st.session_state.pos_filter}"
        f"</div>",
        unsafe_allow_html=True,
    )
    # ---- Stack detection ----
    # Teams where you have a QB → highlight available WR/TE on those teams
    my_qb_teams = {p["team"] for p in my_roster if p["position"] == "QB"}
    # Teams where you have a WR/TE → highlight available QB on those teams
    my_pass_catcher_teams = {
        p["team"] for p in my_roster if p["position"] in ("WR", "TE")
    }

    def is_stack(p):
        if p["position"] in ("WR", "TE") and p["team"] in my_qb_teams:
            return True
        if p["position"] == "QB" and p["team"] in my_pass_catcher_teams:
            return True
        return False

    last_tier = None
    for i, p in enumerate(shown[:TOP_N], start=1):
        if st.session_state.pos_filter == "All":
            this_tier = p.get("global_tier")
            tier_label = f"Tier {this_tier}"
        else:
            this_tier = p.get("tier")
            tier_label = f"{st.session_state.pos_filter} · Tier {this_tier}"

        if sort_by == "VOR" and this_tier != last_tier:
            st.markdown(
                f"<div style='color:#00e0a4;font-size:0.72rem;letter-spacing:2px;"
                f"text-transform:uppercase;border-bottom:1px solid #1f2a3a;"
                f"margin:10px 0 4px;padding-bottom:4px;'>{tier_label}</div>",
                unsafe_allow_html=True,
            )
            last_tier = this_tier

        key = player_key(p)
        c = st.columns([0.5, 3.2, 1.3, 1.8, 1, 1], vertical_alignment="center")
        c[0].markdown(f"<span class='rank-num'>{i:>2}</span>", unsafe_allow_html=True)
        stack_tag = ""
        if is_stack(p):
            # Highlighted stack row: tinted background, glowing border, bold tag
            c[1].markdown(
                f"<div style='background:rgba(0,224,164,0.12);border-left:3px solid #00e0a4;"
                f"border-radius:6px;padding:4px 10px;'>"
                f"<span style='color:#ffffff;font-weight:700;'>{p['name']}</span> "
                f"<span class='rank-num'>{p['team']}</span> "
                f"<span style='background:#00e0a4;color:#0b0f17;font-size:0.68rem;"
                f"font-weight:700;border-radius:4px;padding:1px 6px;margin-left:4px'>"
                f"🔗 STACK</span></div>",
                unsafe_allow_html=True,
            )
        else:
            c[1].markdown(
                f"<span style='color:#ffffff;font-weight:600;'>{p['name']}</span> "
                f"<span class='rank-num'>{p['team']}</span>",
                unsafe_allow_html=True,
            )
        c[2].markdown(badge(p["position"], p.get("tier", "")), unsafe_allow_html=True)
        bye_txt = f" · Bye {p['bye']}" if p.get("bye") else ""
        ranks_txt = ""
        if p.get("sleeper_rank"):
            ranks_txt += f" · Sleeper #{p['sleeper_rank']}"
        if p.get("espn_rank"):
            ranks_txt += f" · ESPN #{p['espn_rank']}"
        if p.get("berry_rank"):
            ranks_txt += f" · Berry #{p['berry_rank']}"
        split = ""
        if p.get("disagreement"):
            split = (
                f" <span style='color:#fbbf24;font-size:0.7rem'>"
                f"⚡ SPLIT ({p.get('rank_spread')})</span>"
            )
        if sources_disagree(p):
            split += (
                f" <span style='color:#e879f9;font-size:0.7rem'>📊"
                f"{source_txt(p).replace(' (', ' ').rstrip(')')}</span>"
            )
        c[3].markdown(
            f"<span class='mono'>{p['points']} / {p['vor']}"
            f"<span class='rank-num'>{bye_txt}{ranks_txt}</span></span>{split}",
            unsafe_allow_html=True,
        )
        c[4].button(
            "Mine",
            key=f"mine_{i}_{key}",
            on_click=draft_player,
            args=(p, True),
            type="primary",
            use_container_width=True,
        )
        c[5].button(
            "Taken",
            key=f"taken_{i}_{key}",
            on_click=draft_player,
            args=(p, False),
            use_container_width=True,
        )

    # ---- Draft Insights ----
    st.markdown("<div class='sec-head'>Draft Insights</div>", unsafe_allow_html=True)

    def show_list(players, stat_label, stat_key):
        for i, p in enumerate(players, start=1):
            cols = st.columns([0.5, 3, 1.2, 2], vertical_alignment="center")
            cols[0].markdown(
                f"<span class='rank-num'>{i}</span>", unsafe_allow_html=True
            )
            cols[1].markdown(
                f"<span style='color:#ffffff'>{p['name']}</span> "
                f"<span class='rank-num'>{p['team']}</span>",
                unsafe_allow_html=True,
            )
            cols[2].markdown(
                badge(p["position"], p.get("tier", "")), unsafe_allow_html=True
            )
            cols[3].markdown(
                f"<span class='mono'>{stat_label}: {p.get(stat_key)}</span>",
                unsafe_allow_html=True,
            )

    def caption(text):
        st.markdown(
            f"<div style='color:#9aa4b2;font-size:0.85rem;margin-bottom:6px'>{text}</div>",
            unsafe_allow_html=True,
        )

    t1, t2, t3, t4 = st.tabs(
        ["💤 Sleepers", "🌟 Top Rookies", "💥 Boom / Ceiling", "🛡️ High Floor"]
    )
    with t1:
        caption("Drafted later than their projected value — target these late.")
        show_list(sleepers(board), "Value", "value_gap")
    with t2:
        caption("Best first-year players by value over replacement.")
        show_list(top_rookies(board), "VOR", "vor")
    with t3:
        caption(
            "Scoring leans on TDs and long plays — exciting but week-to-week volatile."
        )
        show_list(boom_ceiling(board), "Boom", "boom_score")
    with t4:
        caption("High projected touch volume — the safest weekly floor.")
        show_list(high_floor(board), "Touches", "touches")

    # ---- Draft Grade ----
    st.markdown("<div class='sec-head'>Draft Grade</div>", unsafe_allow_html=True)
    grade = grade_draft(my_roster, starters)
    if grade is None:
        st.markdown(
            "<span class='rank-num'>Draft some players (mark them \"Mine\") to see your grade.</span>",
            unsafe_allow_html=True,
        )
    else:
        grade_colors = {
            "A": "#34d399",
            "B": "#38bdf8",
            "C": "#fbbf24",
            "D": "#fb923c",
            "F": "#f87171",
        }
        color = grade_colors.get(grade["letter"], "#94a3b8")
        g1, g2 = st.columns([1, 3], vertical_alignment="center")
        g1.markdown(
            f"<div style='font-size:3.5rem;font-weight:700;color:{color};"
            f"font-family:JetBrains Mono,monospace;text-align:center'>{grade['letter']}</div>"
            f"<div style='text-align:center;color:#9aa4b2;font-size:0.8rem'>{grade['score']}/100</div>",
            unsafe_allow_html=True,
        )
        details = (
            f"<span style='color:#ffffff'>Total value (VOR): <b>{grade['total_vor']}</b></span> &nbsp;·&nbsp; "
            f"<span style='color:#ffffff'>Avg per pick: <b>{grade['avg_vor']}</b></span><br>"
            f"<span style='color:#ffffff'>Starting slots filled: "
            f"<b>{grade['slots_filled']}/{grade['slots_total']}</b></span>"
        )
        if grade["missing"]:
            details += (
                f"<br><span style='color:#fb923c'>Still missing starters: "
                f"{', '.join(grade['missing'])}</span>"
            )
        g2.markdown(
            f"<div style='line-height:1.7'>{details}</div>", unsafe_allow_html=True
        )


# ==================== IN-SEASON MODE ====================
if mode == "In-Season":
    # ---- Waiver Targets (live from ESPN) ----
    st.markdown(
        "<div class='sec-head'>Waiver Targets · Live</div>", unsafe_allow_html=True
    )

    if st.button("Load waiver targets", type="primary"):
        try:
            st.session_state.waivers = load_waivers()
        except Exception as e:
            st.session_state.waivers = None
            st.error(f"Couldn't reach ESPN: {e}")

    if st.session_state.get("waivers"):
        for i, t in enumerate(st.session_state.waivers[:25], start=1):
            c = st.columns([0.4, 0.7, 3, 1.3, 1.6, 1.4], vertical_alignment="center")
            c[0].markdown(f"<span class='rank-num'>{i}</span>", unsafe_allow_html=True)
            photo = espn_photo(t.get("player_id"))
            if photo:
                c[1].image(photo, width=45)
            name_html = f"<span style='color:#ffffff;font-weight:600;'>{t['name']}</span> <span class='rank-num'>{t['team']}</span>"
            if t["status"] != "ACTIVE":
                name_html += f" <span style='color:#fb923c;font-size:0.75rem'>⚠️ {t['status']}</span>"
            c[2].markdown(name_html, unsafe_allow_html=True)
            c[3].markdown(badge(t["position"]), unsafe_allow_html=True)
            c[4].markdown(
                f"<span class='mono'>proj {t['proj']}</span>", unsafe_allow_html=True
            )
            need = (
                "<span style='color:#34d399;font-size:0.75rem'>★ NEED</span>"
                if t["fills_need"]
                else ""
            )
            c[5].markdown(need, unsafe_allow_html=True)

    # ---- Start/Sit (coming after your draft) ----
    st.markdown("<div class='sec-head'>Start / Sit</div>", unsafe_allow_html=True)
    st.markdown(
        "<span class='rank-num'>Unlocks after your draft — compares your rostered "
        "players' weekly projections to set your lineup.</span>",
        unsafe_allow_html=True,
    )

    # ---- Trade Evaluator (coming after your draft) ----
    st.markdown("<div class='sec-head'>Trade Evaluator</div>", unsafe_allow_html=True)
    st.markdown(
        "<span class='rank-num'>Unlocks after your draft — weighs value on each "
        "side of a proposed trade.</span>",
        unsafe_allow_html=True,
    )
