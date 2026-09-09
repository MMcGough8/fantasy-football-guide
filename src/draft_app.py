import json
import os
import time

import streamlit as st
from dotenv import load_dotenv
from streamlit_js_eval import streamlit_js_eval

load_dotenv()  # secrets come from .env at the repo root, never from code
from collections import Counter
from draft_board import build_board
from categories import sleepers, top_rookies, boom_ceiling, high_floor
from grader import grade_draft
from roster_slots import allocate_slots
from recommend import (
    HEADLINE_REACH,
    LATE_ONLY_POSITIONS,
    LATE_ROUND_WINDOW,
    cost_of_waiting,
    headline_and_plan,
    held_reason,
    late_window_open,
    is_unavailable,
    likely_gone,
    survival_for,
)
from pick_sync import apply_picks, index_board_by_player_id, next_pick_info
from espn_ranks import match_key
import sleeper_league
from sleeper_league import SleeperError
from draft_state import load_state, save_state
from lineup import COIN_FLIP_POINTS, current_lineup, expected_points, lineup_diff, lineup_points, optimal_lineup
from roster_slots import IGNORED_SLOTS, starters_from_roster_positions
from sleeper_league import find_my_roster, lineup_week
from weekly_board import attach_weekly_ranks, build_weekly_pool, roster_rows
from manual_draft import build_draft as build_manual_draft, draft_info as manual_draft_info
from opponents import demand_multipliers
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
SYNC_INTERVAL = "15s"  # auto-sync polling before the draft starts
SYNC_INTERVAL_DRAFTING = "4s"  # while picks are being made
COMPACT_BELOW_PX = 1100  # browser width under which the compact layout switches on by itself
SYNC_INTERVAL_NEAR_TURN = "1s"  # within NEAR_TURN_PICKS of my turn, poll faster
NEAR_TURN_PICKS = 2
FEED_CACHE_SECONDS = 1800  # projections refresh twice an hour; a rebuild takes ~4 s
SYNC_STALE_SECONDS = 45  # heartbeat turns orange when the last sync is older than this
SYNC_MIN_GAP_SECONDS = 0.5  # a rerun right after a sync does not sync again
SOURCE_SPLIT_PCT = 0.15  # flag a player when Sleeper and FantasyPros differ by this much
CLOSE_VOR = 8  # players within this much VOR of the pick are shown as alternatives
REHEARSAL_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".rehearsal_draft.json"
)
# DRAFT_STATE_FILE lets headless test runs keep their marks away from the live app's
STATE_FILE = os.getenv("DRAFT_STATE_FILE") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".draft_state.json"
)
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
.cc-title { font-weight:700; font-size:1.5rem; letter-spacing:2px; text-transform:uppercase; color:#e6edf3; margin-bottom:0; }
.cc-title .accent { color:#00e0a4; }
.cc-sub { color:#7d8590; letter-spacing:3px; font-size:0.72rem; text-transform:uppercase; }
.stat-card { background:linear-gradient(180deg,#121826,#0d1420); border:1px solid #1f2a3a; border-radius:10px; padding:12px 16px; }
.stat-val { font-family:'JetBrains Mono',monospace; font-size:1.5rem; color:#00e0a4; font-weight:600; }
.stat-lbl { color:#7d8590; font-size:0.68rem; letter-spacing:2px; text-transform:uppercase; }
.rec-panel { padding:4px 0 2px; }
[data-testid="stVerticalBlockBorderWrapper"] { border:1px solid rgba(0,224,164,0.35) !important; border-left:4px solid #00e0a4 !important; border-radius:12px !important; background:linear-gradient(90deg, rgba(0,224,164,0.10), rgba(0,224,164,0.01)); }
.status-line { color:#9aa4b2; font-family:'JetBrains Mono',monospace; font-size:0.85rem; }
/* Streamlit pads the main column ~6rem for its header; keep only what the collapse chevron needs */
.block-container { padding-top:2.2rem !important; padding-bottom:2rem !important; }
[data-testid="stSidebarUserContent"] { padding-top:1.5rem !important; }
.cc-title { margin-top:0; }
/* the width probe is an invisible component; give it no height */
iframe[title="streamlit_js_eval.streamlit_js_eval"] { height:0 !important; min-height:0 !important; display:block; }
[data-testid="stElementContainer"]:has(> iframe[title="streamlit_js_eval.streamlit_js_eval"]) { margin:0 !important; padding:0 !important; height:0 !important; }
.status-line b { color:#e6edf3; font-weight:600; }
.rec-label { color:#00e0a4; letter-spacing:3px; font-size:0.72rem; text-transform:uppercase; margin-bottom:4px; }
.rec-name { font-size:1.5rem; font-weight:700; color:#f0f6fc; }
.rec-meta { color:#9aa4b2; font-size:0.9rem; margin-top:4px; font-family:'JetBrains Mono',monospace; }
.badge { display:inline-block; white-space:nowrap; }
.chip { display:inline-block; white-space:nowrap; background:#1f2a3a; color:#e5e7eb; border:1px solid #334155;
        border-radius:4px; padding:1px 8px; margin:2px 6px 2px 0; font-family:'JetBrains Mono',monospace; font-size:0.78rem; }
.tags > span { margin-right:4px; }
/* popover trigger buttons inherit a light theme background; match the dark buttons */
[data-testid="stPopover"] > button, [data-testid="stPopoverButton"] {
  background:#131a26 !important; color:#e5e7eb !important; border:1px solid #1f2a3a !important; }
.sec-head { color:#e6edf3; letter-spacing:2px; text-transform:uppercase; font-weight:600; font-size:0.95rem; border-bottom:1px solid #1f2a3a; padding-bottom:8px; margin:6px 0; }
.badge { padding:2px 9px; border-radius:6px; font-weight:600; font-size:0.78rem; font-family:'JetBrains Mono',monospace; }
.mono { font-family:'JetBrains Mono',monospace; color:#c9d1d9; }
.rank-num { font-family:'JetBrains Mono',monospace; color:#4d5866; }
.stButton > button, .stFormSubmitButton > button { border-radius:8px; border:1px solid #2a3a4f; font-family:'Chakra Petch',sans-serif; letter-spacing:0.5px; font-weight:600; transition:all .15s ease; background:#1a2230; color:#ffffff; white-space:nowrap; padding-left:10px; padding-right:10px; min-width:0; }
.stButton > button p, .stFormSubmitButton > button p { white-space:nowrap; }
[data-testid="stToggle"] label p, [data-testid="stCheckbox"] label p { white-space:nowrap; }
.stButton > button:hover { border-color:#00e0a4; box-shadow:0 0 12px rgba(0,224,164,0.25); }
.stButton > button[kind="primary"] { background:#00e0a4; color:#0b0f17; border-color:#00e0a4; }
.stButton > button[kind="primary"]:hover { background:#33e8b6; color:#0b0f17; }
.stFormSubmitButton > button[kind="primary"] { background:#00e0a4; color:#0b0f17; border-color:#00e0a4; }
[data-testid="stSidebar"] { background:#0a0e15; border-right:1px solid #1a2230; }
</style>
""",
    unsafe_allow_html=True,
)


def badge(pos, tier=""):
    c = POS_COLORS.get(pos, "#94a3b8")
    label = f"{pos} T{tier}" if tier != "" and tier is not None else pos
    return f"<span class='badge' style='background:{c}22;color:{c};border:1px solid {c}55;'>{label}</span>"


STATUS_SHORT = {"Questionable": "Q", "Doubtful": "D", "Out": "OUT"}
NEWS_FRESH_HOURS = 24


def status_badge(p):
    """Amber tag for an injury status; red for statuses the recommender excludes."""
    status = p.get("injury_status")
    if not status:
        return ""
    c = "#f87171" if is_unavailable(p) else "#fbbf24"
    label = STATUS_SHORT.get(status, status)
    part = p.get("injury_body_part")
    if part and part.lower() != "undisclosed":
        label += f" · {part}"
    return f" <span class='badge' style='background:{c}22;color:{c};border:1px solid {c}55;'>{label}</span>"


def news_badge(p):
    """Flag players with news in the last day; Sleeper stamps news_updated in ms."""
    stamp = p.get("news_updated")
    if not stamp:
        return ""
    hours = (time.time() - stamp / 1000) / 3600
    if hours > NEWS_FRESH_HOURS:
        return ""
    age = f"{int(hours)}h" if hours >= 1 else f"{int(hours * 60)}m"
    return f" <span style='font-size:0.7rem;color:#fb923c' title='News updated {age} ago'>🔥 {age}</span>"


def sleeper_photo(player_id):
    if not player_id:
        return None
    return f"https://sleepercdn.com/content/nfl/players/{player_id}.jpg"


# A board with a feed missing is cached like any other, but under a retry bucket
# that rolls every few minutes, so the missing feed is retried on a schedule
# instead of on every rerun (a down feed would otherwise block every click).
DEGRADED_RETRY_SECONDS = 300


@st.cache_data(ttl=FEED_CACHE_SECONDS, show_spinner="Loading FantasyPros consensus...")
def load_fantasypros(season, retry_bucket, scoring_code):
    key = os.getenv("FANTASYPROS_API_KEY", "").strip()
    try:
        data = fantasypros.fetch_all(season, key)
    except FantasyProsError as e:
        return {"ok": False, "error": str(e)}
    try:
        data["rankings"] = fantasypros.fetch_consensus_rankings(season, key, scoring=scoring_code)
    except FantasyProsError as e:
        data["rankings"] = None
        data["missing"] = list(data["missing"]) + [f"rankings ({e})"]
    return {"ok": True, "data": data}


@st.cache_data(ttl=FEED_CACHE_SECONDS, show_spinner="Loading ESPN projections...")
def load_espn(season, retry_bucket):
    try:
        return {"ok": True, "data": espn_projections.fetch_all(season)}
    except EspnError as e:
        return {"ok": False, "error": str(e)}


def refresh_projections():
    load_board.clear()
    load_fantasypros.clear()
    load_espn.clear()
    load_weekly_pool.clear()
    load_weekly_fantasypros.clear()
    load_weekly_espn.clear()
    st.session_state.board_degraded = False


@st.cache_data(ttl=FEED_CACHE_SECONDS, show_spinner="Loading projections from Sleeper...")
def load_board(scoring, num_teams, scoring_items, starters_items, use_fantasypros, retry_bucket):
    """Returns (board, projection_note, degraded). Dicts arrive as sorted tuples so the
    cache can hash them; `retry_bucket` only changes while a feed is missing."""
    scoring_settings = dict(scoring_items) if scoring_items else None
    extra, live_ranks, fp_ranks, problems = {}, None, None, []
    if use_fantasypros:
        fp = load_fantasypros(SEASON, retry_bucket, fantasypros.scoring_code_for(scoring_settings))
        if fp["ok"]:
            extra["fp"] = fp["data"]["projections"]
            fp_ranks = fp["data"].get("rankings")
            if fp["data"]["missing"]:
                problems.append(f"FantasyPros missing {'/'.join(fp['data']['missing'])}")
        else:
            problems.append(f"FantasyPros unavailable: {fp['error']}")
    espn = load_espn(SEASON, retry_bucket)
    if espn["ok"]:
        extra["espn"] = espn["data"]["projections"]
        live_ranks = espn["data"]["ranks"]
        if espn["data"]["missing"]:
            problems.append(f"ESPN missing {'/'.join(espn['data']['missing'])}")
    else:
        problems.append(f"ESPN unavailable: {espn['error']}")

    names = ["Sleeper (Rotowire)"] + [
        label for key, label in (("fp", "FantasyPros consensus"), ("espn", "ESPN")) if key in extra
    ]
    how = {1: "", 2: ", averaged stat by stat", 3: ", per-stat median (one outlier ignored)"}[len(names)]
    note = " + ".join(names) + how
    if live_ranks:
        note += ". ESPN ranks are live"
    if fp_ranks:
        note += f"; FantasyPros consensus ranks ({len(fp_ranks)} players, {fantasypros.scoring_code_for(scoring_settings)})"
    if problems:
        note += ". " + "; ".join(problems)
    board = build_board(
        scoring, num_teams, scoring_settings, dict(starters_items), extra or None, live_ranks, fp_ranks
    )
    return board, note, bool(problems)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_news(name, team, position):
    from news import get_player_news

    return get_player_news(name, team, position)


# ---- Start/Sit loaders: same conventions as the board loaders, keyed on the week ----
NFL_STATE_CACHE_SECONDS = 300
LINEUP_CACHE_SECONDS = 120
START_SIT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".start_sit.json")


def load_start_sit_prefs():
    """{"username", "league_id"} remembered from the last visit, or {}."""
    try:
        with open(START_SIT_FILE) as f:
            prefs = json.load(f)
        return prefs if isinstance(prefs, dict) else {}
    except (OSError, ValueError):
        return {}


def save_start_sit_prefs(username, league_id):
    try:
        with open(START_SIT_FILE, "w") as f:
            json.dump({"username": username, "league_id": league_id}, f)
    except OSError:
        pass  # a lost preference is not worth an error on the page


@st.cache_data(ttl=NFL_STATE_CACHE_SECONDS, show_spinner=False)
def load_nfl_state(retry_bucket):
    try:
        return {"ok": True, "data": sleeper_league.get_nfl_state()}
    except SleeperError as e:
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=600, show_spinner="Finding leagues...")
def load_my_leagues(username, season):
    try:
        user = sleeper_league.get_user(username)
        leagues = sleeper_league.get_leagues(user["user_id"], season)
    except SleeperError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "user_id": user["user_id"], "leagues": leagues}


@st.cache_data(ttl=LINEUP_CACHE_SECONDS, show_spinner="Loading your roster from Sleeper...")
def load_lineup_context(league_id, retry_bucket):
    try:
        return {
            "ok": True,
            "league": sleeper_league.get_league(league_id),
            "rosters": sleeper_league.get_rosters(league_id),
        }
    except SleeperError as e:
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=FEED_CACHE_SECONDS, show_spinner="Loading FantasyPros for the week...")
def load_weekly_fantasypros(season, week, retry_bucket, scoring_code):
    key = os.getenv("FANTASYPROS_API_KEY", "").strip()
    try:
        data = fantasypros.fetch_all(season, key, week=week)
    except FantasyProsError as e:
        return {"ok": False, "error": str(e)}
    try:
        ranks = fantasypros.fetch_weekly_rankings(season, week, key, scoring_code)
        data["rankings"] = ranks["ranks"]
        data["missing"] = list(data["missing"]) + [f"rankings for {p}" for p in ranks["missing"]]
    except FantasyProsError as e:
        data["rankings"] = None
        data["missing"] = list(data["missing"]) + [f"rankings ({e})"]
    return {"ok": True, "data": data}


@st.cache_data(ttl=FEED_CACHE_SECONDS, show_spinner="Loading ESPN for the week...")
def load_weekly_espn(season, week, retry_bucket):
    try:
        return {"ok": True, "data": espn_projections.fetch_all(season, week=week)}
    except EspnError as e:
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=FEED_CACHE_SECONDS, show_spinner="Building this week's projections...")
def load_weekly_pool(week, scoring_items, use_fantasypros, retry_bucket):
    """Returns (pool, note, degraded) for one week, mirroring load_board."""
    scoring_settings = dict(scoring_items)
    extra, fp_ranks, problems = {}, None, []
    if use_fantasypros:
        fp = load_weekly_fantasypros(SEASON, week, retry_bucket, fantasypros.scoring_code_for(scoring_settings))
        if fp["ok"]:
            extra["fp"] = fp["data"]["projections"]
            fp_ranks = fp["data"]["rankings"]
            if fp["data"]["missing"]:
                problems.append("FantasyPros missing " + ", ".join(fp["data"]["missing"]))
        else:
            problems.append(f"FantasyPros: {fp['error']}")
    espn = load_weekly_espn(SEASON, week, retry_bucket)
    if espn["ok"]:
        extra["espn"] = espn["data"]["projections"]
        if espn["data"]["missing"]:
            problems.append("ESPN missing " + ", ".join(espn["data"]["missing"]))
    else:
        problems.append(f"ESPN: {espn['error']}")
    pool = attach_weekly_ranks(build_weekly_pool(week, scoring_settings, extra), fp_ranks)
    feeds = ["Sleeper"] + (["FantasyPros"] if "fp" in extra else []) + (["ESPN"] if "espn" in extra else [])
    note = f"Week {week} projections: " + " + ".join(feeds)
    if fp_ranks:
        note += " · FantasyPros weekly consensus"
    if problems:
        note += " (" + "; ".join(problems) + ")"
    return pool, note, bool(problems)


def player_key(p):
    return f"{p['name']}|{p['team']}|{p['position']}"


SOURCE_LABELS = {"sleeper": "Sleeper", "fp": "FP", "espn": "ESPN"}


def fills_need_ui(p):
    from recommend import need_kind

    return need_kind(p["position"], needs) is not None


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


def state_draft_id():
    """What the persisted marks are keyed to, so a new draft always starts clean."""
    league = st.session_state.get("league") or {}
    if league.get("manual_draft"):
        return f"manual:{league.get('league_id') or league.get('name')}"
    return st.session_state.get("rehearsal_draft_id") or league.get("draft_id") or "manual"


def all_rosters():
    """Every household team's roster keys; the active team's roster lives in my_roster."""
    rosters = {t: [player_key(p) for p in ps] for t, ps in st.session_state.rosters.items()}
    active = st.session_state.get("team_pref")
    if active:
        rosters[active] = [player_key(p) for p in st.session_state.my_roster]
    return rosters


def persist_marks():
    """Manual marks survive a refresh; keyed to the draft so a new draft starts clean."""
    try:
        save_state(
            STATE_FILE,
            state_draft_id(),
            drafted=st.session_state.drafted,
            mine=[player_key(p) for p in st.session_state.my_roster],
            rosters=all_rosters(),
            pick_log=st.session_state.pick_log,
        )
    except OSError:
        pass  # losing persistence is not worth interrupting a live draft


def draft_player(p, mine):
    st.session_state.drafted = st.session_state.drafted | {player_key(p)}
    if mine:
        st.session_state.my_roster = st.session_state.my_roster + [p]
    st.session_state.last_mark = {"key": player_key(p), "name": p["name"], "mine": mine}
    # The ordered log is the pick count (and draft position) of a manual draft
    st.session_state.pick_log = st.session_state.pick_log + [
        {"key": player_key(p), "team": st.session_state.get("team_pref") if mine else None}
    ]
    persist_marks()


def undo_last_mark():
    mark = st.session_state.pop("last_mark", None)
    if not mark:
        return
    st.session_state.drafted = st.session_state.drafted - {mark["key"]}
    st.session_state.my_roster = [
        p for p in st.session_state.my_roster if player_key(p) != mark["key"]
    ]
    st.session_state.rosters = {
        t: [p for p in ps if player_key(p) != mark["key"]] for t, ps in st.session_state.rosters.items()
    }
    log = st.session_state.pick_log
    if log and log[-1]["key"] == mark["key"]:
        st.session_state.pick_log = log[:-1]
    st.session_state.quick_msg = f"↩ Undid: {mark['name']}"
    persist_marks()


def reset_draft():
    st.session_state.pop("last_mark", None)
    st.session_state.drafted = set()
    st.session_state.my_roster = []
    st.session_state.rosters = {}
    st.session_state.pick_log = []
    st.session_state.synced_taken = set()
    st.session_state.synced_mine = []
    st.session_state.draft_info = None
    persist_marks()


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
if "rosters" not in st.session_state:
    st.session_state.rosters = {}  # a manual draft: household teams other than the active one
if "ss_username" not in st.session_state:
    _prefs = load_start_sit_prefs()
    st.session_state.ss_username = _prefs.get("username", "")
    st.session_state.ss_league_pref = _prefs.get("league_id")
    st.session_state.ss_league_options = {}
    st.session_state.ss_user_id = None
    st.session_state.ss_week_pref = None
if "pick_log" not in st.session_state:
    st.session_state.pick_log = []
if "pos_filter" not in st.session_state:
    st.session_state.pos_filter = "All"
if "league" not in st.session_state:
    st.session_state.league = load_saved_league()
if "draft_info" not in st.session_state:
    st.session_state.draft_info = None
if "auto_sync_pref" not in st.session_state:
    st.session_state.auto_sync_pref = bool((st.session_state.league or {}).get("draft_id"))
if "rehearsal_draft_id" not in st.session_state and os.path.exists(REHEARSAL_FILE):
    try:
        with open(REHEARSAL_FILE) as f:
            st.session_state.rehearsal_draft_id = json.load(f)["draft_id"]
        st.session_state.sync_requested = True
    except (OSError, ValueError, KeyError):
        pass
if "sort_pref" not in st.session_state:
    st.session_state.sort_pref = "VOR"
if "layout_pref" not in st.session_state:
    # "Auto" follows the browser width; ?compact=1 / ?compact=0 pin it
    q = st.query_params.get("compact")
    st.session_state.layout_pref = "Auto" if q is None else ("Compact" if q in ("1", "true") else "Wide")

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

# ---- Manual draft (Yahoo/ESPN): no sync, a hand-kept pick log, one or more household teams ----
manual_cfg = (league or {}).get("manual_draft") or None
team_names = list(manual_cfg["slots"]) if manual_cfg else []
if team_names and st.session_state.get("team_pref") not in team_names:
    st.session_state.team_pref = team_names[0]
active_team = st.session_state.get("team_pref") if manual_cfg else None
mine_label = active_team if manual_cfg else "Mine"

# While a feed is missing the cache key rolls every DEGRADED_RETRY_SECONDS so it retries
retry_bucket = (
    int(time.time() // DEGRADED_RETRY_SECONDS) if st.session_state.get("board_degraded") else 0
)
try:
    board, projection_note, degraded = load_board(
        SCORING_LABELS[st.session_state.scoring_pref],
        num_teams,
        scoring_items,
        tuple(sorted(starters.items())),
        bool(os.getenv("FANTASYPROS_API_KEY", "").strip()),
        retry_bucket,
    )
    st.session_state.board_degraded = degraded
    if degraded:
        projection_note += f" (retrying every {DEGRADED_RETRY_SECONDS // 60} min)"
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

if not st.session_state.get("marks_restored"):
    st.session_state.marks_restored = True
    saved = load_state(STATE_FILE, state_draft_id())
    if saved:
        by_key = {player_key(p): p for p in board}
        st.session_state.drafted = saved["drafted"]
        st.session_state.pick_log = saved["pick_log"]
        if saved["rosters"] and active_team:
            st.session_state.my_roster = [by_key[k] for k in saved["rosters"].get(active_team, []) if k in by_key]
            st.session_state.rosters = {
                t: [by_key[k] for k in keys if k in by_key]
                for t, keys in saved["rosters"].items() if t != active_team
            }
        else:
            st.session_state.my_roster = [by_key[k] for k in saved["mine"] if k in by_key]

sleeper_stamp = max(((p.get("sleeper_updated_at") or 0) for p in board), default=0)
hours_old = (time.time() - sleeper_stamp / 1000) / 3600 if sleeper_stamp else None
age_txt = f" Sleeper projections updated {hours_old:.0f}h ago." if hours_old is not None else ""
gap_note = (
    " Long-TD bonuses (40+/50+ yds) are not projected and are left out."
    if league and unprojected_bonus_keys(league["scoring_settings"])
    else ""
)
board_details_html = (
    f"<span class='rank-num'>Your league's scoring (yardage-game bonuses estimated from season "
    f"totals), FLEX-aware replacement levels.{gap_note}<br>Projections: {projection_note}.{age_txt}</span>"
)

def active_draft_id():
    """The league's draft, or a mock draft the owner is rehearsing with."""
    return st.session_state.get("rehearsal_draft_id") or league["draft_id"]


def load_replay(path):
    """A simulated draft written by simulate.py --emit (draft.json + picks.json)."""
    try:
        with open(os.path.join(path, "draft.json")) as f:
            draft = json.load(f)
        with open(os.path.join(path, "picks.json")) as f:
            picks = json.load(f)
    except (OSError, ValueError) as e:
        raise SleeperError(f"Couldn't read the replay at {path} ({e})") from e
    return draft, picks


def sync_picks_from_sleeper():
    """Mark every pick from the live Sleeper draft. Returns True if anything changed."""
    draft_id = active_draft_id()
    if draft_id.startswith("file:"):
        draft, picks = load_replay(draft_id[len("file:"):])
    else:
        draft = sleeper_league.get_draft(draft_id)
        picks = sleeper_league.get_draft_picks(draft_id)
    # Who I am in this draft: the published order is authoritative (and the only
    # option in a mock draft); league rosters are the fallback before it is published.
    roster_id = sleeper_league.my_roster_id_from_draft(draft, league["user_id"])
    if roster_id is None and not st.session_state.get("rehearsal_draft_id"):
        if "my_roster_id" not in st.session_state:
            rosters = sleeper_league.get_rosters(league["league_id"])
            st.session_state.my_roster_id = sleeper_league.my_roster_id(rosters, league["user_id"])
        roster_id = st.session_state.my_roster_id
    result = apply_picks(picks, board_by_id, league["user_id"], roster_id)

    new_taken = {player_key(p) for p in result.taken}
    new_mine_keys = [player_key(p) for p in result.mine]
    previous = st.session_state.draft_info or {}
    new_info = {
        "draft_id": draft_id,
        "status": draft.get("status"),
        "has_order": bool(draft.get("draft_order")),
        "rounds": (draft.get("settings") or {}).get("rounds"),
        "picks": len(picks),
        "next": next_pick_info(draft, len(picks), league["user_id"]),
        "unmatched": len(result.unmatched),
        # What the opponent-needs model reads: the order and who has taken what
        "draft_meta": {
            "type": draft.get("type"),
            "draft_order": draft.get("draft_order"),
            "slot_to_roster_id": draft.get("slot_to_roster_id"),
            "settings": {k: (draft.get("settings") or {}).get(k) for k in ("teams", "rounds", "reversal_round")},
        },
        "pick_positions": [
            {"roster_id": pk.get("roster_id"), "metadata": {"position": (pk.get("metadata") or {}).get("position")}}
            for pk in picks
        ],
        "unmatched_names": [
            {
                "name": f"{(pk.get('metadata') or {}).get('first_name', '')} "
                        f"{(pk.get('metadata') or {}).get('last_name', '')}".strip() or "Unknown",
                "position": (pk.get("metadata") or {}).get("position") or "?",
                "team": (pk.get("metadata") or {}).get("team") or "FA",
            }
            for pk in result.unmatched[:8]
        ],
    }
    changed = (
        new_taken != st.session_state.synced_taken
        or new_mine_keys != [player_key(p) for p in st.session_state.synced_mine]
        or new_info["status"] != previous.get("status")
        or new_info["has_order"] != previous.get("has_order")
    )
    st.session_state.synced_taken = new_taken
    st.session_state.synced_mine = result.mine
    st.session_state.draft_info = new_info
    st.session_state.last_sync_at = time.time()
    return changed


def request_sync():
    st.session_state.sync_requested = True


if league and league.get("draft_id") and st.session_state.pop("sync_requested", False):
    # Runs before any widget renders, so a manual sync never resets widget state.
    # Any failure (network or an unexpected payload) is shown, never fatal.
    try:
        sync_picks_from_sleeper()
    except Exception as e:
        st.session_state.sync_error_text = f"Sync failed: {e}"
        st.session_state.sync_error = True


if manual_cfg:
    # No platform sync: the hand-kept pick log is the draft, rebuilt every run (cheap).
    _by_key = {player_key(p): p for p in board}
    try:
        st.session_state.draft_info = manual_draft_info(
            build_manual_draft(manual_cfg["teams"], manual_cfg["rounds"], manual_cfg["slots"]),
            st.session_state.pick_log,
            active_team,
            lambda k: (_by_key.get(k) or {}).get("position"),
        )
    except ValueError as e:
        st.session_state.draft_info = None
        st.error(f"Draft slots are not set up right: {e}")

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




# Round count and team count follow the draft being synced (a mock may differ from the league);
# the board itself (replacement levels, scoring) always stays the league's.
_draft_settings = ((st.session_state.draft_info or {}).get("draft_meta") or {}).get("settings") or {}
total_picks = (st.session_state.draft_info or {}).get("rounds") or (sum(starters.values()) + bench_spots)
draft_teams = _draft_settings.get("teams") or num_teams


# ==================== SIDEBAR ====================
with st.sidebar:
    # ---- Layout: compact rows for a narrow window (side by side with the draft room).
    # Follows the browser width unless the owner flips the toggle.
    viewport_w = streamlit_js_eval(js_expressions="window.parent.innerWidth", key="viewport_w")
    compact_auto = bool(viewport_w) and viewport_w < COMPACT_BELOW_PX
    layout_options = ["Auto", "Wide", "Compact"]
    st.radio(
        "Layout",
        options=layout_options,
        index=layout_options.index(st.session_state.layout_pref),
        horizontal=True,
        key="layout_widget",
        on_change=lambda: st.session_state.update(layout_pref=st.session_state.layout_widget),
        help=f"Auto switches to the compact layout when the window is narrower than {COMPACT_BELOW_PX}px",
    )
    compact = {"Auto": compact_auto, "Wide": False, "Compact": True}[st.session_state.layout_pref]

    if manual_cfg:
        # ---- Which household team the card, needs and roster panel follow ----
        st.markdown("<div class='sec-head'>Drafting for</div>", unsafe_allow_html=True)

        def switch_team():
            new, old = st.session_state.team_widget, st.session_state.team_pref
            if new == old:
                return
            rosters = dict(st.session_state.rosters)
            rosters[old] = st.session_state.my_roster
            st.session_state.my_roster = rosters.pop(new, [])
            st.session_state.rosters = rosters
            st.session_state.team_pref = new

        def update_slot(name):
            lg = st.session_state.league
            slots = {**lg["manual_draft"]["slots"], name: int(st.session_state[f"slot_{name}"])}
            st.session_state.league = {**lg, "manual_draft": {**lg["manual_draft"], "slots": slots}}
            save_league(st.session_state.league)

        st.radio(
            "Drafting for",
            options=team_names,
            index=team_names.index(active_team),
            horizontal=True,
            key="team_widget",
            on_change=switch_team,
            label_visibility="collapsed",
        )
        with st.expander("Draft slots", expanded=False):
            for name in team_names:
                st.number_input(
                    f"{name} picks",
                    min_value=1,
                    max_value=int(manual_cfg["teams"]),
                    value=int(manual_cfg["slots"][name]),
                    key=f"slot_{name}",
                    on_change=update_slot,
                    args=(name,),
                )

    # ---- Mode toggle (top); hidden while a draft is live so it cannot be bumped ----
    draft_live = (st.session_state.draft_info or {}).get("status") == "drafting"
    if draft_live:
        mode = "Draft"
    else:
        st.markdown("<div class='sec-head'>Mode</div>", unsafe_allow_html=True)
        mode = st.radio(
            "App mode",
            options=["Draft", "Start/Sit"],
            label_visibility="collapsed",
            key="app_mode",
        )
        st.divider()

    if mode == "Start/Sit":
        # ---- Which league and week the lineup page reads; never touches the draft connection ----
        st.markdown("<div class='sec-head'>Start/Sit league</div>", unsafe_allow_html=True)

        def find_ss_leagues(username=None):
            name = (username or st.session_state.get("ss_username_widget") or "").strip()
            if not name:
                return
            result = load_my_leagues(name, SEASON)
            if not result["ok"]:
                st.session_state.ss_error = result["error"]
                return
            options = {lg["league_id"]: lg for lg in result["leagues"]}
            st.session_state.ss_username = name
            st.session_state.ss_user_id = result["user_id"]
            st.session_state.ss_league_options = options
            if st.session_state.ss_league_pref not in options:
                st.session_state.ss_league_pref = next(iter(options), None)
            st.session_state.ss_error = None if options else f"No {SEASON} Sleeper leagues for {name}"
            st.session_state.ss_lookup_done = True
            save_start_sit_prefs(name, st.session_state.ss_league_pref)

        def pick_ss_league():
            st.session_state.ss_league_pref = st.session_state.ss_league_widget
            save_start_sit_prefs(st.session_state.ss_username, st.session_state.ss_league_pref)

        if st.session_state.ss_username and not st.session_state.get("ss_lookup_done"):
            find_ss_leagues(st.session_state.ss_username)  # remembered from last time
        st.text_input("Sleeper username", value=st.session_state.ss_username, key="ss_username_widget")
        st.button("Find leagues", on_click=find_ss_leagues, use_container_width=True)
        if st.session_state.get("ss_error"):
            st.error(st.session_state.ss_error)
        league_ids = list(st.session_state.ss_league_options)
        if league_ids:
            st.selectbox(
                "League",
                options=league_ids,
                index=league_ids.index(st.session_state.ss_league_pref) if st.session_state.ss_league_pref in league_ids else 0,
                format_func=lambda lid: st.session_state.ss_league_options[lid]["name"],
                key="ss_league_widget",
                on_change=pick_ss_league,
            )
        if st.session_state.ss_week_pref is None:
            # A failed lookup is retried every minute instead of sitting in the cache for its TTL
            state = load_nfl_state(int(time.time() // 60) if st.session_state.get("ss_state_failed") else 0)
            st.session_state.ss_state_failed = not state["ok"]
            if state["ok"]:
                st.session_state.ss_week_pref = lineup_week(state["data"])
            else:
                st.warning(f"Couldn't read the NFL week from Sleeper ({state['error']}); pick the week below.")
        st.number_input(
            "Week",
            min_value=1,
            max_value=18,
            value=int(st.session_state.ss_week_pref or 1),
            key="ss_week_widget",
            on_change=lambda: st.session_state.update(ss_week_pref=int(st.session_state.ss_week_widget)),
        )
        if st.session_state.get("ss_state_failed") and st.session_state.ss_week_pref is not None:
            st.caption("Week defaulted while Sleeper was unreachable; check it.")
        st.divider()

    # ---- Sleeper League ----
    st.markdown(
        f"<div class='sec-head'>{(league or {}).get('platform', 'Sleeper')} League</div>",
        unsafe_allow_html=True,
    )
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
        with st.expander("Board details", expanded=False):
            st.markdown(board_details_html, unsafe_allow_html=True)
            if st.button("Refresh projections", help="Clear the cached feeds and rebuild the board (about 4s)"):
                refresh_projections()
                st.rerun()
        with st.popover("Disconnect", use_container_width=True):
            st.caption("Forget this league and its saved connection?")
            st.button("Yes, disconnect", on_click=forget_league, type="primary")

        def start_rehearsal():
            raw = (st.session_state.get("rehearsal_input") or "").strip()
            draft_id = raw if raw.startswith("file:") else sleeper_league.parse_draft_id(raw)
            if not draft_id:
                st.session_state.rehearsal_error = "Paste a Sleeper draft link or id, or file:/path from simulate.py --emit"
                return
            st.session_state.rehearsal_draft_id = draft_id
            st.session_state.rehearsal_error = None
            reset_draft()
            st.session_state.sync_requested = True  # show the mock's picks on this same click
            try:
                with open(REHEARSAL_FILE, "w") as f:
                    json.dump({"draft_id": draft_id}, f)  # survives a refresh
            except OSError:
                pass

        def stop_rehearsal():
            st.session_state.pop("rehearsal_draft_id", None)
            reset_draft()
            try:
                os.remove(REHEARSAL_FILE)
            except FileNotFoundError:
                pass

        st.markdown("<div class='sec-head'>Mock Draft Rehearsal</div>", unsafe_allow_html=True)
        if st.session_state.get("rehearsal_draft_id"):
            st.markdown(
                f"<span style='color:#fbbf24'>Rehearsing with draft "
                f"{st.session_state.rehearsal_draft_id.replace('file:', '')}</span>",
                unsafe_allow_html=True,
            )
            st.button("Stop rehearsal", on_click=stop_rehearsal, use_container_width=True)
        else:
            st.caption("Paste a Sleeper mock draft link here and press Enter.")
            # A form so Enter in the box connects, not just the button
            with st.form("rehearsal_form", border=False):
                st.text_input("Mock draft link or id", key="rehearsal_input", label_visibility="collapsed",
                              placeholder="https://sleeper.com/draft/nfl/…")
                st.form_submit_button("Rehearse with this draft", on_click=start_rehearsal, use_container_width=True, type="primary")
            if st.session_state.get("rehearsal_error"):
                st.warning(st.session_state.rehearsal_error)
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

    # ---- Draft-only sidebar sections ----
    if mode == "Draft":
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

        for other, players in sorted(st.session_state.rosters.items()):
            names = ", ".join(f"{p['name'].split()[-1]} ({p['position']})" for p in players) or "nobody yet"
            st.markdown(
                f"<div class='sec-head'>{other}'s roster</div><span class='rank-num'>{names}</span>",
                unsafe_allow_html=True,
            )

        st.divider()
        with st.popover("Reset draft", use_container_width=True):
            st.caption("Clear every mark and synced pick? Sync picks will rebuild from Sleeper.")
            st.button("Yes, reset", on_click=reset_draft, type="primary")
    st.divider()

    roster_names = [p["name"] for p in my_roster]
    label = "📰 Your Players" if roster_names else "📰 The Latest"

    with st.expander(label, expanded=False):
        # Only a click spends a Claude call; nothing here runs on a rerun.
        if st.button("Refresh news", use_container_width=True):
            from news import get_top_stories

            with st.spinner("Searching..."):
                try:
                    st.session_state.top_stories = get_top_stories(roster_names or None)
                except Exception as e:
                    st.session_state.top_stories = {"error": str(e)}
        stories = st.session_state.get("top_stories")
        if isinstance(stories, dict):
            st.markdown(
                f"<span class='rank-num'>News unavailable: {stories['error'][:120]}</span>",
                unsafe_allow_html=True,
            )
        elif stories:
            for story in stories:
                st.markdown(
                    f"<div style='margin-bottom:8px'>"
                    f"<span style='color:#00e0a4;font-size:0.72rem'>{story['player']}</span><br>"
                    f"<span style='color:#ffffff;font-size:0.85rem'>{story['headline']}</span></div>",
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
                    round_num=picks_made // draft_teams + 1,
                    pick_in_round=picks_made % draft_teams + 1,
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
# ==================== HEADER ====================
if mode != "Draft":
    ss_pick = st.session_state.ss_league_options.get(st.session_state.ss_league_pref)
    subtitle = f"Start/Sit · Week {st.session_state.ss_week_pref} · " + (ss_pick["name"] if ss_pick else "pick a league in the sidebar")
elif league:
    feeds = projection_note.split(",")[0].split(".")[0]
    subtitle = f"{league['name']} · {num_teams} teams · league scoring · {feeds}"
else:
    subtitle = f"Manual board · {num_teams} teams · {scoring_label} · {projection_note.split(',')[0].split('.')[0]}"
st.markdown(
    f"<div class='cc-title'>🏈 Fantasy <span class='accent'>Command Center</span></div>"
    f"<div class='cc-sub'>{subtitle}</div>",
    unsafe_allow_html=True,
)


# ==================== DRAFT MODE ====================
if mode == "Draft":
    # ---- Scoring + league size (manual unless a Sleeper league is connected) ----
    if not league:
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
    round_num = picks_made // draft_teams + 1
    pick_in_round = picks_made % draft_teams + 1

    next_pick = (st.session_state.draft_info or {}).get("next")
    if next_pick is None:
        your_pick = "draft order not published"
    elif next_pick["picks_until_mine"] == 0:
        your_pick = "<b style='color:#00e0a4'>YOU ARE ON THE CLOCK</b>"
    elif manual_cfg and next_pick.get("on_clock_user_id") in team_names:
        your_pick = (
            f"<b style='color:#fbbf24'>{next_pick['on_clock_user_id']} is on the clock</b> · "
            f"your pick in <b>{next_pick['picks_until_mine']}</b>"
        )
    else:
        your_pick = f"your pick in <b>{next_pick['picks_until_mine']}</b>"
    team_txt = f"<b>{active_team}</b> · " if manual_cfg else ""
    status_html = (
        f"<span class='status-line'>{team_txt}Round <b>{round_num}</b> · Pick <b>{pick_in_round}</b> · "
        f"<b>{picks_made}</b> picks made · roster <b>{len(my_roster)}</b>/{total_picks} · {your_pick}</span>"
    )
    if compact:
        # The sidebar is usually collapsed in a narrow window; keep the open slots visible
        need_txt = " ".join(f"{slot}{'×' + str(n) if n > 1 else ''}" for slot, n in needs.items())
        status_html += (
            f"<br><span class='status-line'>Still need: <b>{need_txt or 'starters filled'}</b></span>"
        )

    # reach = other teams' picks before I am on the clock (0 = my turn now);
    # horizon = picks between that turn and the one after, the "will he be there" window.
    if next_pick:
        reach = next_pick["picks_until_mine"]
        # 0 is real: back-to-back picks at the turn, or your last pick. Never pad it.
        horizon = next_pick["picks_until_following"]
        gap_note = "before your next pick"
        panel_gap = reach or horizon
    else:
        reach, horizon = 0, None
        gap_note = "in the next round (draft order not published yet)"
        panel_gap = draft_teams

    # Opponent needs: the teams picking in a window scale each position's hazard
    draft_meta = (st.session_state.draft_info or {}).get("draft_meta")
    pick_positions = (st.session_state.draft_info or {}).get("pick_positions") or []
    # League-wide drafted counts per position; opens the K/DEF window when a run starts
    drafted_positions = Counter(
        (pk.get("metadata") or {}).get("position") for pk in pick_positions
    )
    _demand_cache = {}

    def demand(gap):
        if not draft_meta:
            return {}
        if gap not in _demand_cache:
            _demand_cache[gap] = demand_multipliers(draft_meta, pick_positions, starters, picks_made, gap)
        return _demand_cache[gap]

    # ---- Status strip: draft position on the left, Sleeper sync controls on the right ----
    if league and league.get("draft_id"):
        if compact:
            sc3 = st.container()
            sc1, sc2, _ = st.columns([1.2, 1.2, 2], vertical_alignment="center")
        else:
            sc3, sc1, sc2 = st.columns([5, 1.1, 1.1], vertical_alignment="center")
        sc1.button("Sync picks", use_container_width=True, on_click=request_sync)
        auto_sync = sc2.toggle(
            "Auto-sync",
            value=st.session_state.auto_sync_pref,
            key="auto_sync_widget",
            on_change=lambda: st.session_state.update(
                auto_sync_pref=st.session_state.auto_sync_widget
            ),
        )
        if st.session_state.pop("sync_error", None):
            st.error(st.session_state.get("sync_error_text", "Sync failed"))

        def render_heartbeat():
            info = st.session_state.draft_info
            if not info:
                st.markdown(
                    f"{status_html}<br><span class='rank-num'>Not synced yet. Auto-sync polls every {SYNC_INTERVAL}.</span>",
                    unsafe_allow_html=True,
                )
                return
            age = time.time() - st.session_state.get("last_sync_at", 0)
            stale = age > SYNC_STALE_SECONDS
            color = "#fb923c" if stale else "#7d8590"
            unmatched = f" · {info['unmatched']} unmatched" if info["unmatched"] else ""
            rehearsal = " · <b style='color:#fbbf24'>REHEARSAL</b>" if st.session_state.get("rehearsal_draft_id") else ""
            st.markdown(
                f"{status_html}<br><span class='rank-num' style='color:{color}'>"
                f"Sleeper {str(info['status']).replace('_', '-')} · {info['picks']} picks{unmatched}{rehearsal} · "
                f"synced {int(age)}s ago{' ⚠️ stale' if stale else ''}</span>",
                unsafe_allow_html=True,
            )

        if auto_sync:
            near_turn = next_pick is not None and next_pick["picks_until_mine"] <= NEAR_TURN_PICKS
            drafting = (st.session_state.draft_info or {}).get("status") == "drafting"
            poll_every = SYNC_INTERVAL_NEAR_TURN if near_turn else (SYNC_INTERVAL_DRAFTING if drafting else SYNC_INTERVAL)

            @st.fragment(run_every=poll_every)
            def auto_sync_fragment():
                # Runs every SYNC_INTERVAL and inline on every full rerun. The heartbeat
                # lives in here so it redraws on each poll, not just on full reruns.
                if time.time() - st.session_state.get("last_sync_at", 0) >= SYNC_MIN_GAP_SECONDS:
                    try:
                        if sync_picks_from_sleeper():
                            st.rerun(scope="app")
                    except Exception as e:
                        st.warning(f"Sync failed: {e}")
                render_heartbeat()

            with sc3:
                auto_sync_fragment()
        else:
            with sc3:
                render_heartbeat()
    else:
        st.markdown(status_html, unsafe_allow_html=True)

    # ---- Recommendation ----
    roster_counts = Counter(p["position"] for p in my_roster)
    rank_args = (available, needs, len(my_roster), total_picks, roster_counts)
    # The headline is always the here-and-now pick (what to take if on the clock this
    # instant). Between turns the fallback plan is a one-line reminder under it, so the
    # card never leads with a player being saved for later.
    shortlist, plan = headline_and_plan(
        *rank_args, picks_made=picks_made, gap=horizon, reach=reach, demand=demand,
        drafted_positions=drafted_positions,
    )
    # The highest-VOR healthy player the rules are holding back, so the pick never looks arbitrary
    held = None
    if shortlist:
        top_vor = next((p for p in available if not is_unavailable(p)), None)
        if top_vor is not None and top_vor is not shortlist[0]["player"] and top_vor["vor"] > shortlist[0]["vor"]:
            why_not = held_reason(top_vor, roster_counts, len(my_roster), total_picks, needs, drafted_positions)
            if why_not:
                held = (top_vor, why_not)
    tier_left = Counter((p["position"], p.get("tier")) for p in available)
    pick = shortlist[0]["player"] if shortlist else None
    if shortlist:
        top = shortlist[0]
        pos, tier = pick["position"], pick.get("tier")
        between_turns = bool(reach) and top["mode"] == "now"
        reach_odds = survival_for(pick, picks_made, reach, demand) if between_turns else None
        if between_turns:
            pill_color = "#34d399" if reach_odds >= HEADLINE_REACH else "#fbbf24"
            pill_text = f"BEST NOW · {reach_odds:.0%} TO REACH YOU"
        elif top["mode"] == "now":
            pill_text, pill_color = "TAKE NOW", "#34d399"
        else:
            pill_text, pill_color = "BEST VALUE", "#38bdf8"
        pill = (
            f"<span style='background:{pill_color}22;color:{pill_color};border:1px solid {pill_color}66;"
            f"border-radius:4px;padding:1px 8px;font-size:0.7rem;font-weight:700;letter-spacing:1px'>{pill_text}</span>"
        )
        if top["fills_need"]:
            slot = pos if needs.get(pos) else "FLEX"
            why = f"Best value that fills your open {slot}"
        else:
            why = "Best value on the board"
        left = tier_left[(pos, tier)]
        tier_chip = (
            f" <span class='badge' style='background:#1f2a3a;color:#e5e7eb;border:1px solid #334155'>"
            f"{left} LEFT IN TIER</span>"
            if tier is not None
            else ""
        )
        proj = f"PROJ {pick['points']}" + (source_txt(pick) if sources_disagree(pick) else "")
        bye = f" · Bye {pick['bye']}" if pick.get("bye") else ""
        line1 = f"{why} · VOR {pick['vor']} · {proj}{bye}"
        later_txt = f" · best {pos} then ≈ {top['later']:.0f} VOR" if top["later"] is not None else ""
        if top["mode"] == "now" and pos in LATE_ONLY_POSITIONS:
            line2 = "Last rounds: fill the open slot now."
        elif between_turns:
            picks_txt = f"{reach} pick{'s' if reach != 1 else ''} before you're up"
            fallback = plan[0] if plan else None
            if fallback and fallback["player"] is not pick:
                f = fallback["player"]
                line2 = (
                    f"{picks_txt}. <span style='color:#9aa4b2'>If he's gone: {f['name']} "
                    f"({f['position']}, VOR {fallback['vor']:.0f}) · {fallback['reach']:.0%} to reach you</span>"
                )
            else:
                line2 = f"{picks_txt}. He should still be there{later_txt}"
        elif top["mode"] == "now":
            line2 = f"Won't wait: {top['back']:.0%} to still be there next turn{later_txt}"
        else:
            line2 = "Draft order not published yet: ranking by value and need only."
        if held:
            line2 += (
                f"<br><span style='color:#7d8590'>Holding off on {held[0]['name']} "
                f"({held[0]['position']}, VOR {held[0]['vor']:.0f}): {held[1]}.</span>"
            )

        photo = sleeper_photo(pick.get("player_id"))
        with st.container(border=True):
            if compact:
                rc2 = st.container()
            else:
                rc1, rc2 = st.columns([1, 7], vertical_alignment="center")
                if photo:
                    rc1.image(photo, width=72)
            rc2.markdown(
                f"<div class='rec-panel'><div class='rec-label'>Recommended Pick &nbsp;{pill}</div>"
                f"<div class='rec-name'>{pick['name']} &nbsp; {badge(pos, tier)}{tier_chip}"
                f"{status_badge(pick)}{news_badge(pick)}</div>"
                f"<div class='rec-meta'>{line1}<br>{line2}</div></div>",
                unsafe_allow_html=True,
            )
            b1, b2, _ = st.columns([1, 1, 0.01] if compact else [1.2, 1.2, 5], vertical_alignment="center")
            b1.button(
                f"Draft {pick['name'].split()[-1]}" + (f" for {active_team}" if manual_cfg else ""),
                type="primary",
                on_click=draft_player,
                args=(pick, True),
                use_container_width=True,
            )
            b2.button("He got taken", on_click=draft_player, args=(pick, False), use_container_width=True)
            # Everyone the rules would allow who is within a few VOR of the pick, best at each
            # position first, so a close call (RB 41 vs WR 36 with WR2 open) is visible
            close, seen_pos = [], set()
            for p in available:
                if p is pick or is_unavailable(p) or p["position"] in LATE_ONLY_POSITIONS:
                    continue
                if p["vor"] > pick["vor"] + CLOSE_VOR or p["vor"] < pick["vor"] - CLOSE_VOR:
                    continue
                if held_reason(p, roster_counts, len(my_roster), total_picks, needs, drafted_positions):
                    continue
                if p["position"] in seen_pos:
                    continue
                seen_pos.add(p["position"]); close.append(p)
                if len(close) == 4:
                    break
            others = shortlist[1:]
            if others:
                def alt_chip(c):
                    odds = ""
                    if between_turns:
                        odds = f" · {survival_for(c['player'], picks_made, reach, demand):.0%} reach"
                    elif c["mode"] == "now":
                        odds = f" · {c['back']:.0%} back"
                    return (
                        f"<span class='chip'>{c['player']['name']} <span style='color:#9aa4b2'>"
                        f"{c['player']['position']} · VOR {c['vor']:.0f}{odds}</span></span>"
                    )
                st.markdown(
                    "<div style='margin-top:-4px'><span class='rank-num' style='margin-right:6px'>Next best</span>"
                    + "".join(alt_chip(c) for c in others)
                    + "</div>",
                    unsafe_allow_html=True,
                )
            if close:
                def close_chip(p):
                    slot = "fills " + ("FLEX" if not needs.get(p["position"]) else p["position"]) if fills_need_ui(p) else "bench"
                    return (
                        f"<span class='chip'>{p['name']} <span style='color:#9aa4b2'>{p['position']} · VOR {p['vor']:.0f} · {slot}</span></span>"
                    )
                st.markdown(
                    f"<div style='margin-top:2px'><span class='rank-num' style='margin-right:6px'>Close in value (±{CLOSE_VOR})</span>"
                    + "".join(close_chip(p) for p in close)
                    + "</div>",
                    unsafe_allow_html=True,
                )

    # ---- Cost of waiting ----
    if horizon == 0:
        waiting_title = "If you wait · you pick again right away"
    elif next_pick:
        waiting_title = "If you wait · cost by position and who is likely gone before your next pick"
    else:
        waiting_title = "If you wait · cost by position and who is likely gone (assuming one round)"
    wait_box = st.expander(waiting_title, expanded=False)
    with wait_box:
        waiting = cost_of_waiting(
            available, ["QB", "RB", "WR", "TE"], picks_made,
            horizon if horizon is not None else draft_teams, reach_gap=reach, demand=demand,
        )
        needs_ahead = demand(panel_gap)
        if needs_ahead:
            def need_word(m):
                return "hungry" if m >= 1.3 else ("full" if m <= 0.7 else "average")
            st.markdown(
                "<span class='rank-num'>Teams picking before your turn, vs league average: </span>"
                + " ".join(
                    f"<span class='chip'>{pos} <span style='color:{'#fb923c' if m >= 1.3 else ('#34d399' if m <= 0.7 else '#9aa4b2')}'>"
                    f"{m:.1f}× {need_word(m)}</span></span>"
                    for pos, m in needs_ahead.items() if pos not in ("K", "DEF")
                ),
                unsafe_allow_html=True,
            )
        wcols = st.columns(2) * 2 if compact else st.columns(4)
        for col, pos in zip(wcols, ["QB", "RB", "WR", "TE"]):
            row = waiting.get(pos)
            if not row:
                col.markdown(f"<div style='text-align:center'>{badge(pos)}</div>", unsafe_allow_html=True)
                continue
            hot = row["cost"] >= 20
            color = "#fb923c" if hot else "#9aa4b2"
            col.markdown(
                f"<div style='border:1px solid #1f2a3a;border-radius:8px;padding:8px 10px;line-height:1.6'>"
                f"<div>{badge(pos)} <span style='color:#ffffff;font-weight:600'>{row['now']['name']}</span>"
                f" <span class='mono'>{row['now']['vor']:.0f}</span></div>"
                f"<div style='font-size:0.75rem;color:#9aa4b2'>if you wait <span class='mono'>{row['later']:.0f}</span>"
                f" &nbsp;·&nbsp; <span style='color:{color};font-weight:700'>cost {row['cost']:.0f}{' ⚠️' if hot else ''}</span></div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        # ---- Likely gone ----
        gone = [p for p in likely_gone(available, picks_made, panel_gap, limit=7, demand=demand) if p is not pick][:6]
        if gone:
            chips = "".join(
                f"<span class='chip'>{p['name']} <span style='color:#9aa4b2'>{p['position']} · "
                f"ADP {p['adp']:.0f} · {survival_for(p, picks_made, panel_gap, demand):.0%} back</span></span>"
                for p in gone
            )
            st.markdown(
                f"<div style='margin:8px 0 2px'><div class='rank-num' style='margin-bottom:4px'>Likely gone {gap_note}"
                f"{f' ({panel_gap} picks)' if next_pick else ''}</div>{chips}</div>",
                unsafe_allow_html=True,
            )

    synced_draft = bool(league and league.get("draft_id") and st.session_state.auto_sync_pref)
    entry_box = (
        st.expander("Manual entry · Quick Entry and Undo", expanded=False) if synced_draft else st.container()
    )
    with entry_box:
        # ---- Quick Entry (fast pick marking for live drafts) ----
        st.markdown("<div class='sec-head'>Quick Entry</div>", unsafe_allow_html=True)

        def quick_mark(mine):
            typed = (st.session_state.get("quick_entry") or "").strip().lower()
            st.session_state.quick_matches = []
            if not typed:
                return
            matches = [p for p in available if typed in p["name"].lower()]
            if len(matches) == 1:
                draft_player(matches[0], mine)
                st.session_state.quick_msg = (
                    f"✓ {'Drafted' if mine else 'Marked taken'}: {matches[0]['name']}"
                )
            elif not matches:
                gone = [p for p in board if typed in p["name"].lower()]
                st.session_state.quick_msg = (
                    f"⚠️ {gone[0]['name']} is already off the board" if gone else f"⚠️ No match for '{typed}'"
                )
            else:
                st.session_state.quick_matches = matches[:5]
                st.session_state.quick_msg = "Which one?"

        with st.form("quick_entry_form", clear_on_submit=True, border=False):
            qcol1, qcol2, qcol3 = st.columns([3, 1, 1])
            qcol1.text_input(
                "Quick mark",
                label_visibility="collapsed",
                placeholder="Type a player name, Enter = Taken",
                key="quick_entry",
            )
            # First submit button is what Enter triggers: Taken is the 11-of-12 case
            qcol2.form_submit_button("Taken", key="quick_taken", on_click=quick_mark, args=(False,), use_container_width=True)
            qcol3.form_submit_button(mine_label, key="quick_mine", on_click=quick_mark, args=(True,), type="primary", use_container_width=True)

        for i, m in enumerate(st.session_state.get("quick_matches") or []):
            mc = st.columns([3, 1, 1])
            mc[0].markdown(
                f"{badge(m['position'])} <span style='color:#ffffff'>{m['name']}</span> "
                f"<span class='rank-num'>{m['team']}</span>",
                unsafe_allow_html=True,
            )
            mc[1].button("Taken", key=f"pick_taken_{i}", on_click=lambda m=m: (draft_player(m, False), st.session_state.update(quick_matches=[], quick_msg=f"✓ Marked taken: {m['name']}")), use_container_width=True)
            mc[2].button(mine_label, key=f"pick_mine_{i}", type="primary", on_click=lambda m=m: (draft_player(m, True), st.session_state.update(quick_matches=[], quick_msg=f"✓ Drafted: {m['name']}")), use_container_width=True)

        msg_col, undo_col = st.columns([4, 1])
        if st.session_state.get("quick_msg"):
            color = "#34d399" if st.session_state.quick_msg.startswith(("✓", "↩")) else "#fb923c"
            msg_col.markdown(
                f"<span style='color:{color};font-size:0.85rem'>{st.session_state.quick_msg}</span>",
                unsafe_allow_html=True,
            )
        if st.session_state.get("last_mark"):
            undo_col.button(
                f"↩ Undo {st.session_state.last_mark['name'].split()[-1]}",
                on_click=undo_last_mark,
                use_container_width=True,
            )

    # ---- Unmatched Sleeper picks ----
    unmatched_names = (st.session_state.draft_info or {}).get("unmatched_names") or []
    still_unmatched = []
    for u in unmatched_names:
        on_board = next(
            (p for p in board if match_key(p["name"], p["position"]) == match_key(u["name"], u["position"])),
            None,
        )
        target = on_board or {"name": u["name"], "team": u["team"], "position": u["position"]}
        if player_key(target) not in drafted_keys:
            still_unmatched.append((u, target))
    if still_unmatched:
        st.markdown(
            "<span class='rank-num'>Sleeper picks not on the board (mark them so they drop out of recommendations):</span>",
            unsafe_allow_html=True,
        )
        ucols = st.columns(min(4, len(still_unmatched)))
        for i, (u, target) in enumerate(still_unmatched):
            ucols[i % len(ucols)].button(
                f"Taken: {u['name']} ({u['position']})",
                key=f"unmatched_{i}",
                on_click=draft_player,
                args=(target, False),
                use_container_width=True,
            )

    # ---- Board ----
    st.markdown("<div class='sec-head'>Best Available</div>", unsafe_allow_html=True)

    filters = ["All", "QB", "RB", "WR", "TE", "K", "DEF"]
    fcols = st.columns(len(filters))
    for col, pos in zip(fcols, filters):
        btn_type = "primary" if st.session_state.pos_filter == pos else "secondary"
        col.button(
            pos,
            key=f"filter_{pos}",
            type=btn_type,
            use_container_width=True,
            on_click=lambda pos=pos: st.session_state.update(pos_filter=pos),
        )

    sort_options = ["VOR", "Consensus", "FP", "ESPN", "Berry"]
    ctl_left, ctl_right = st.columns([2, 3], vertical_alignment="center")
    sort_by = ctl_right.radio(
        "Sort by",
        options=sort_options,
        index=sort_options.index(st.session_state.sort_pref),
        horizontal=True,
        label_visibility="collapsed",
        key="sort_widget",
        on_change=lambda: st.session_state.update(sort_pref=st.session_state.sort_widget),
    )

    if st.session_state.pos_filter == "All":
        # K/DEF are late-round picks; keep them off the main list until the final
        # picks or a league-wide run on the position opens the window
        shown = [
            p for p in available
            if p["position"] not in LATE_ONLY_POSITIONS
            or late_window_open(p["position"], len(my_roster), total_picks, needs, drafted_positions)
        ]
    else:
        shown = [p for p in available if p["position"] == st.session_state.pos_filter]

    if sort_by == "VOR":
        shown.sort(key=lambda p: p.get("vor", 0), reverse=True)
    elif sort_by == "Consensus":
        shown.sort(key=lambda p: p.get("consensus") or 9999)
    elif sort_by == "FP":
        shown.sort(key=lambda p: p.get("fp_rank") or 9999)
    elif sort_by == "ESPN":
        shown.sort(key=lambda p: p.get("espn_rank") or 9999)
    elif sort_by == "Berry":
        shown.sort(key=lambda p: p.get("berry_rank") or 9999)
    ctl_left.markdown(
        f"<div style='color:#9aa4b2;font-size:0.85rem;margin:6px 0'>"
        f"Showing {min(len(shown), TOP_N)} of {len(shown)} available · sort by →"
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

    # Tier bookkeeping over everything still available (not just the visible rows)
    tier_field = "global_tier" if st.session_state.pos_filter == "All" else "tier"
    tier_pool = available if st.session_state.pos_filter == "All" else [
        p for p in available if p["position"] == st.session_state.pos_filter
    ]
    left_in_tier = Counter(p.get(tier_field) for p in tier_pool)
    best_vor_in_tier = {}
    for p in tier_pool:
        t = p.get(tier_field)
        best_vor_in_tier[t] = max(best_vor_in_tier.get(t, float("-inf")), p["vor"])
    rows = shown[:TOP_N]

    last_tier = None
    for i, p in enumerate(rows, start=1):
        this_tier = p.get(tier_field)
        if st.session_state.pos_filter == "All":
            tier_label = f"Tier {this_tier}"
        else:
            tier_label = f"{st.session_state.pos_filter} · Tier {this_tier}"

        if sort_by == "VOR" and this_tier != last_tier:
            n_left = left_in_tier.get(this_tier, 0)
            next_best = best_vor_in_tier.get((this_tier or 0) + 1)
            drop = f" · next tier −{p['vor'] - next_best:.0f} VOR" if next_best is not None else ""
            st.markdown(
                f"<div style='color:#00e0a4;font-size:0.72rem;letter-spacing:2px;"
                f"text-transform:uppercase;border-bottom:1px solid #1f2a3a;"
                f"margin:10px 0 4px;padding-bottom:4px;'>{tier_label} · {n_left} left{drop}</div>",
                unsafe_allow_html=True,
            )
            last_tier = this_tier
        is_last_in_tier = (
            sort_by == "VOR"
            and left_in_tier.get(this_tier, 0) >= 1
            and (i == len(rows) or rows[i].get(tier_field) != this_tier)
            and left_in_tier.get(this_tier, 0) == sum(1 for r in rows[:i] if r.get(tier_field) == this_tier)
        )

        key = player_key(p)
        if compact:
            cell, btn_mine, btn_taken = st.columns([6, 1, 1], vertical_alignment="center")
            c = [None, cell, cell, btn_mine, btn_taken]
        else:
            c = st.columns([0.3, 3.6, 2.7, 0.8, 0.8], vertical_alignment="center")
            c[0].markdown(f"<span class='rank-num'>{i:>2}</span>", unsafe_allow_html=True)

        # Cell 1: name and team on line 1, every tag on line 2 (tags never wrap mid-chip)
        last_chip = (
            " <span class='badge' style='background:#fb923c22;color:#fb923c;border:1px solid #fb923c55'>LAST IN TIER</span>"
            if is_last_in_tier
            else ""
        )
        stack_chip = (
            " <span style='background:#00e0a4;color:#0b0f17;font-size:0.68rem;font-weight:700;"
            "border-radius:4px;padding:1px 6px'>🔗 STACK</span>"
            if is_stack(p)
            else ""
        )
        tags = badge(p["position"], p.get("tier", "")) + last_chip + status_badge(p) + news_badge(p) + stack_chip
        name_style = "color:#ffffff;font-weight:700;" if is_stack(p) else "color:#ffffff;font-weight:600;"
        wrap_style = (
            "background:rgba(0,224,164,0.12);border-left:3px solid #00e0a4;border-radius:6px;padding:4px 10px;"
            if is_stack(p)
            else ""
        )
        rank_txt = f"<span class='rank-num' style='margin-right:6px'>{i}</span>" if compact else ""
        name_html = (
            f"<div style='{wrap_style}line-height:1.5'>{rank_txt}"
            f"<span style='{name_style}'>{p['name']}</span> <span class='rank-num'>{p['team']}</span>"
            f"<div class='tags' style='margin-top:2px'>{tags}</div></div>"
        )
        if not compact:
            c[1].markdown(name_html, unsafe_allow_html=True)

        # Cell 2: value and odds on line 1, bye and market ranks on line 2
        survive = ""
        if p.get("adp") and p["position"] not in LATE_ONLY_POSITIONS:
            odds = survival_for(p, picks_made, panel_gap, demand)
            color = "#f87171" if odds < 0.35 else ("#fbbf24" if odds < 0.65 else "#9aa4b2")
            survive = (
                f" <span style='color:{color};font-size:0.75rem;white-space:nowrap' "
                f"title='Odds he is still there at your next turn'>{odds:.0%} back</span>"
            )
        split = ""
        if p.get("disagreement"):
            split += f" <span style='color:#fbbf24;font-size:0.7rem;white-space:nowrap'>⚡ SPLIT {p.get('rank_spread')}</span>"
        if sources_disagree(p):
            split += (
                f" <span style='color:#e879f9;font-size:0.7rem;white-space:nowrap' "
                f"title='{source_txt(p).strip(' ()')}'>📊 split</span>"
            )
        detail = []
        if p.get("bye"):
            detail.append(f"Bye {p['bye']}")
        if sort_by != "VOR" and p.get("sleeper_rank"):
            detail.append(f"Board #{p['sleeper_rank']}")
        if p.get("fp_rank"):
            detail.append(f"FP {p['fp_rank']}")
        if p.get("espn_rank"):
            detail.append(f"ESPN {p['espn_rank']}")
        if p.get("berry_rank"):
            detail.append(f"Berry {p['berry_rank']}")
        meta_html = (
            f"<div style='line-height:1.5'><span class='mono' style='white-space:nowrap'>VOR {p['vor']:.0f} · {p['points']:.0f} pts</span>"
            f"{survive}{split}<br><span class='rank-num'>{' · '.join(detail)}</span></div>"
        )
        if compact:
            # One cell: name, tags, then a single muted meta line
            c[1].markdown(
                name_html.replace("</div></div>", "</div>", 1)
                + f"<div style='margin-top:2px'><span class='mono' style='white-space:nowrap;font-size:0.85rem'>VOR {p['vor']:.0f} · {p['points']:.0f}</span>"
                + f"{survive}{split} <span class='rank-num' style='font-size:0.78rem'>· {' · '.join(detail)}</span></div></div>",
                unsafe_allow_html=True,
            )
        else:
            c[2].markdown(meta_html, unsafe_allow_html=True)
        c[3].button(
            mine_label,
            key=f"mine_{i}_{key}",
            on_click=draft_player,
            args=(p, True),
            type="primary",
            use_container_width=True,
        )
        c[4].button(
            "Taken",
            key=f"taken_{i}_{key}",
            on_click=draft_player,
            args=(p, False),
            use_container_width=True,
        )

    with st.expander("Draft Insights", expanded=False):
        # ---- Draft Insights ----

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

    with st.expander("Draft Grade", expanded=False):
        # ---- Draft Grade ----
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


# ==================== START/SIT MODE ====================
def lineup_row_html(slot, row):
    """One starting slot: who is in it and why the numbers say what they say."""
    if row is None:
        return f"<div>{badge(slot)} <span class='rank-num'>empty slot</span></div>"
    opp = f" <span class='rank-num'>vs {row['opponent']}</span>" if row.get("opponent") else ""
    fp = ""
    if row.get("fp_week_pos_rank"):
        spread = f" ±{row['fp_week_rank_std']:.0f}" if row.get("fp_week_rank_std") else ""
        grade = f" · {row['fp_grade']}" if row.get("fp_grade") else ""
        fp = f" <span class='chip'>FP {row['fp_week_pos_rank']}{spread}{grade}</span>"
    reason = f" <span style='color:#fb923c;font-size:0.78rem'>{row['reason']}</span>" if row.get("reason") else ""
    split = source_txt(row) if sources_disagree(row) else ""
    return (
        f"<div style='margin:3px 0'>{badge(slot)} <span style='color:#ffffff;font-weight:600'>{row['name']}</span>"
        f" <span class='rank-num'>{row.get('team') or ''}</span>{opp} <span class='mono'>{row['points']:.1f}</span>"
        f"<span class='rank-num' style='font-size:0.78rem'>{split}</span>{status_badge(row)}{fp}{reason}</div>"
    )


def render_lineup(title, slots):
    total, playable = lineup_points(slots), expected_points(slots)
    label = f"{total:.1f}" if playable == total else f"{playable:.1f} <span class='rank-num'>({total:.1f} listed)</span>"
    st.markdown(f"<div class='sec-head'>{title} · <span class='mono'>{label}</span></div>", unsafe_allow_html=True)
    for slot, rows in slots.items():
        for row in rows:
            st.markdown(lineup_row_html(slot, row), unsafe_allow_html=True)
    return total


def render_swap(swap):
    player, out = swap["in"], swap["out"]
    if out and swap["out_reason"]:
        out_txt = f" for <b>{out['name']}</b> <span style='color:#f87171'>({swap['out_reason']})</span>"
    elif out:
        out_txt = f" for <b>{out['name']}</b> ({out['points']:.1f})"
    else:
        out_txt = " into an empty slot"
    flip = " <span class='chip' style='color:#fbbf24'>COIN FLIP</span>" if swap["coin_flip"] else ""
    color = "#00e0a4" if swap["delta"] >= 0 else "#f87171"
    st.markdown(
        f"<div style='margin:4px 0'>{badge(swap['slot'])} Start <b>{player['name']}</b> ({player['points']:.1f}){out_txt}"
        f" · <span class='mono' style='color:{color}'>{swap['delta']:+.1f}</span>{status_badge(player)}{flip}</div>",
        unsafe_allow_html=True,
    )


if mode == "Start/Sit":
    st.markdown("<div class='sec-head'>Start / Sit</div>", unsafe_allow_html=True)
    ss_league = st.session_state.ss_league_options.get(st.session_state.ss_league_pref)
    if not ss_league:
        st.info("Enter a Sleeper username in the sidebar and pick a league.")
        st.stop()
    ctx_retry = int(time.time() // 60) if st.session_state.get("ss_ctx_failed") else 0
    ctx = load_lineup_context(ss_league["league_id"], ctx_retry)
    st.session_state.ss_ctx_failed = not ctx["ok"]
    if not ctx["ok"]:
        st.error(f"Couldn't load the league from Sleeper: {ctx['error']}")
        if st.button("Retry"):
            load_lineup_context.clear()
            st.rerun()
        st.stop()
    ss_retry = int(time.time() // DEGRADED_RETRY_SECONDS) if st.session_state.get("ss_degraded") else 0
    ss_week = int(st.session_state.ss_week_pref or 1)
    my_team = find_my_roster(ctx["rosters"], st.session_state.ss_user_id)
    if my_team is None:
        st.error(f"{st.session_state.ss_username} has no roster in {ss_league['name']}.")
        st.stop()
    roster_positions = ctx["league"].get("roster_positions") or []
    ss_starters, _ = starters_from_roster_positions(roster_positions)
    try:
        pool, ss_note, ss_degraded = load_weekly_pool(
            ss_week,
            tuple(sorted((ctx["league"].get("scoring_settings") or {}).items())),
            bool(os.getenv("FANTASYPROS_API_KEY", "").strip()),
            ss_retry,
        )
    except Exception as e:
        st.error(f"Couldn't build week {ss_week} projections: {e}")
        if st.button("Retry"):
            refresh_projections()
            st.rerun()
        st.stop()
    st.session_state.ss_degraded = ss_degraded
    byes = {p["team"]: p["bye"] for p in board if p.get("bye")}
    rows = roster_rows(my_team.get("players") or [], pool, board_by_id, byes, ss_week)
    rows_by_id = {r["player_id"]: r for r in rows}
    current = current_lineup(my_team.get("starters") or [], roster_positions, rows_by_id)
    optimal = optimal_lineup(rows, ss_starters)
    swaps = lineup_diff(current, optimal)

    st.markdown(f"<span class='rank-num'>{ss_note}. Set the lineup in the Sleeper app; this page only advises.</span>", unsafe_allow_html=True)
    idp_slots = sorted({s for s in roster_positions if s in IGNORED_SLOTS - {"IR", "TAXI"}})
    if idp_slots:
        st.caption("Not modelled here: " + ", ".join(idp_slots))
    expected_slots = len([s for s in roster_positions if s not in ("BN", "IR", "TAXI")])
    if len(my_team.get("starters") or []) != expected_slots:
        st.caption("Sleeper's starter list does not line up with the league's slots; check the current lineup by hand.")

    if swaps:
        st.markdown("<div class='sec-head'>Swaps</div>", unsafe_allow_html=True)
        for swap in swaps:
            render_swap(swap)
        gain = lineup_points(optimal) - expected_points(current)
        st.markdown(f"<span class='rank-num'>Total: {gain:+.1f} projected points from the players who can play. Swaps under {COIN_FLIP_POINTS} points are inside projection noise.</span>", unsafe_allow_html=True)
    else:
        st.success("Lineup already optimal for this week.")

    left, right = st.columns(2)
    with left:
        render_lineup("Current", current)
    with right:
        render_lineup("Optimal", optimal)

    st.markdown("<div class='sec-head'>Bench</div>", unsafe_allow_html=True)
    started = {r["player_id"] for slot_rows in optimal.values() for r in slot_rows if r}
    for row in sorted((r for r in rows if r["player_id"] not in started), key=lambda r: -r["points"]):
        st.markdown(lineup_row_html(row["position"], row), unsafe_allow_html=True)

    with st.expander("Per-source points and confidence", expanded=False):
        for row in sorted(rows, key=lambda r: -r["points"]):
            by_source = " · ".join(f"{SOURCE_LABELS.get(s, s)} {v}" for s, v in (row.get("points_by_source") or {}).items()) or "no feed"
            fp = f" · FP {row['fp_week_pos_rank']} (rank {row['fp_week_rank']}, spread ±{row['fp_week_rank_std']:.0f})" if row.get("fp_week_pos_rank") else ""
            st.markdown(f"<div class='mono' style='font-size:0.82rem'>{row['name']}: {by_source}{fp}</div>", unsafe_allow_html=True)
