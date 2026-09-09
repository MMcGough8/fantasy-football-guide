"""Persist manual draft marks so a refresh or laptop sleep does not lose them.

Synced picks come back from Sleeper on their own; this only covers the marks
made by hand (Mine / Taken / Draft buttons, Quick Entry). A manual draft
(Yahoo, ESPN) also keeps every household team's roster and the ordered pick
log, which is what gives it a pick count and a draft position.
"""
import json
import os


def save_state(path, draft_id, drafted, mine, rosters=None, pick_log=None):
    path = str(path)
    if not drafted and not mine and not pick_log:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    payload = {"draft_id": draft_id, "drafted": sorted(drafted), "mine": list(mine)}
    if rosters:
        payload["rosters"] = {team: list(keys) for team, keys in rosters.items()}
    if pick_log:
        payload["pick_log"] = list(pick_log)
    with open(path, "w") as f:
        json.dump(payload, f)


def load_state(path, draft_id):
    """{"drafted": set, "mine": [keys], "rosters": {team: [keys]}, "pick_log": [...]} or None."""
    try:
        with open(str(path)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("draft_id") != draft_id:
        return None
    return {
        "drafted": set(data.get("drafted") or []),
        "mine": list(data.get("mine") or []),
        "rosters": {t: list(k) for t, k in (data.get("rosters") or {}).items()},
        "pick_log": list(data.get("pick_log") or []),
    }
