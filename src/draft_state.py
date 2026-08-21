"""Persist manual draft marks so a refresh or laptop sleep does not lose them.

Synced picks come back from Sleeper on their own; this only covers the marks
made by hand (Mine / Taken / Draft buttons, Quick Entry).
"""
import json
import os


def save_state(path, draft_id, drafted, mine):
    path = str(path)
    if not drafted and not mine:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    with open(path, "w") as f:
        json.dump({"draft_id": draft_id, "drafted": sorted(drafted), "mine": list(mine)}, f)


def load_state(path, draft_id):
    """{"drafted": set, "mine": [keys]} for this draft, or None."""
    try:
        with open(str(path)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("draft_id") != draft_id:
        return None
    return {"drafted": set(data.get("drafted") or []), "mine": list(data.get("mine") or [])}
