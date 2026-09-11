---
name: ui-headless-checks
description: Use when verifying draft_app.py behaviour without a browser (Streamlit's AppTest headless harness, fake feeds, session-state assertions) or when taking Playwright screenshots of the running app for layout checks.
---

# Headless UI checks

## Streamlit AppTest

Run from `src/` as the working directory (Python adds the cwd of a stdin script to `sys.path`; from anywhere else you need `sys.path.insert(0, "src")`):

```python
from streamlit.testing.v1 import AppTest
at = AppTest.from_file("draft_app.py", default_timeout=240)
```

Drive widgets by key (`quick_entry` + `quick_taken`/`quick_mine` form buttons, `sleeper_username`, `sort_widget`, `auto_sync_widget`, `filter_RB`, `ss_adjust_widget`) or by label (`Find my leagues`, `Connect`, `Sync picks`). Set `at.session_state["auto_sync_pref"] = False` before the first run when a test injects `draft_info`, otherwise the auto-sync fragment overwrites it with the real draft.

**Always** run headless sessions with `DRAFT_STATE_FILE=/path/in/scratchpad.json`, `PROJECTION_LOG_FILE=/path/in/scratchpad.jsonl`, `PROPS_LOG_FILE`, `PROPS_PULL_FILE` and `SURVIVOR_FILE` (scratch paths too) in the environment; otherwise their Mine/Taken marks, projection rows, props log, saved pull and knockout picks are persisted into the live app's `.draft_state.json` / `.projection_log.jsonl` / `.props_log.jsonl` / `.props_pull.json` / `.survivor.json` and show up in the owner's browser. `PROPS_PULL_FILE` may point at the real `.props_pull.json` read-only when a session only prices (the fetch handlers are the only writers).

Gotchas learned the hard way:

- Every module shares one global `requests` module. To fake the network, replace `requests.get` **once** with a single URL-routing function; assigning `draft_board.requests.get`, `sleeper_league.requests.get` etc. separately clobbers the same object and the last assignment wins.
- `st.cache_data` persists across AppTest instances in one process, so a second scenario in the same script may see the first scenario's feeds. Use a fresh process per scenario, or clear the loaders.
- `at.root` does not exist; walk `at._tree` when you need the raw element tree.
- A `st.selectbox` with `format_func` breaks AppTest's widget-state collection; key options by label instead.
- When a smoke script pipes through `grep -v`, the pipeline's exit code masks the assertion. Capture `rc=$?` on the Python step and write output to a log file.

There are no committed AppTest tests yet; smoke scripts live in the session scratchpad.

## Screenshots for layout checks

`playwright` and its Chromium are installed in `.venv`. With the app running, a script that does `page.goto("http://localhost:8501")`, waits about 12 seconds for the board, scrolls with `mouse.wheel` and calls `page.screenshot` gives a real render. Streamlit scrolls inside its own container, so `full_page=True` does not capture below the fold. Verify the compact layout with `page.set_viewport_size({"width": 760, "height": 1000})` or `?compact=1`.
