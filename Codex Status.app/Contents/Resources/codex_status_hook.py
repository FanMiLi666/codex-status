#!/usr/bin/python3
"""Translate Codex lifecycle hooks into a tiny menu-bar-readable state file."""

import fcntl
import json
import os
import sys
import tempfile
import time

CODEX_DIR = os.path.expanduser("~/.codex")
STATE_PATH = os.path.join(CODEX_DIR, "codex-status.json")
LOCK_PATH = os.path.join(CODEX_DIR, "codex-status.lock")


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as handle:
            state = json.load(handle)
            if isinstance(state.get("active"), list):
                return state
    except (OSError, ValueError, TypeError):
        pass
    return {"active": [], "last_completed_at": None}


def save_state(state):
    fd, temporary = tempfile.mkstemp(prefix=".codex-status-", dir=CODEX_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, STATE_PATH)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def update(payload):
    event = payload.get("hook_event_name", "")
    session_id = payload.get("session_id", "unknown")
    turn_id = payload.get("turn_id", "unknown")
    key = f"{session_id}:{turn_id}"
    now = time.time()

    os.makedirs(CODEX_DIR, exist_ok=True)
    with open(LOCK_PATH, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load_state()
        active = [
            item for item in state["active"]
            if now - float(item.get("started_at", 0)) < 43_200
        ]

        if event == "UserPromptSubmit":
            active = [item for item in active if item.get("key") != key]
            active.append({
                "key": key,
                "session_id": session_id,
                "turn_id": turn_id,
                "cwd": payload.get("cwd"),
                "started_at": now,
            })
        elif event == "Stop":
            active = [item for item in active if item.get("key") != key]
            state["last_completed_at"] = now
        elif event == "SessionEnd":
            active = [
                item for item in active
                if item.get("session_id") != session_id
            ]

        state["active"] = active
        save_state(state)


def main():
    try:
        payload = json.load(sys.stdin)
        update(payload)
    except Exception:
        # Status reporting must never interrupt a Codex turn.
        pass
    # Stop hooks require JSON on stdout; this is accepted by all configured events.
    print("{}")


if __name__ == "__main__":
    main()
