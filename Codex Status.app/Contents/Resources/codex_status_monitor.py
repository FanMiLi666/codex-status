#!/usr/bin/python3
"""Watch Codex's local event log and maintain Running/Done menu-bar state."""

import json
import os
import re
import sqlite3
import tempfile
import time
import fcntl
import select

CODEX_DIR = os.path.expanduser("~/.codex")
DB_PATH = os.path.join(CODEX_DIR, "logs_2.sqlite")
STATE_PATH = os.path.join(CODEX_DIR, "codex-status.json")
LOCK_PATH = os.path.join(CODEX_DIR, "codex-status.lock")
WAL_PATH = f"{DB_PATH}-wal"

TURN_RE = re.compile(r'(?:turn\.id=|turn_id=|sub_id="?)([0-9a-f-]{20,})')
HANDLER_TURN_RE = re.compile(
    r'Submission sub=Submission \{ id: "([0-9a-f-]{20,})"'
)
THREAD_RE = re.compile(r"thread_id=([0-9a-f-]{20,})")
CWD_RE = re.compile(r"cwd=(.*?)(?:\}:|}:|$)")


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


def ids(text):
    turn = TURN_RE.search(text)
    thread = THREAD_RE.search(text)
    if not turn:
        return None, None
    return (thread.group(1) if thread else "codex"), turn.group(1)


def apply_rows(rows, reset_active=False):
    with open(LOCK_PATH, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load_state()
        previous_state = json.dumps(
            state, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        now = time.time()
        active = {} if reset_active else {
            item.get("key"): item
            for item in state.get("active", [])
            if item.get("key") and now - float(item.get("started_at", 0)) < 43_200
        }
        ignored = {} if reset_active else {
            key: float(timestamp)
            for key, timestamp in state.get("ignored", {}).items()
            if now - float(timestamp) < 43_200
        }

        for _, ts, target, body in rows:
            target = target or ""
            body = body or ""
            context = f"{target} {body}"

            is_automation = (
                "codex_core::session::handlers" in target
                and "<heartbeat>" in body
                and "<automation_id>" in body
            )
            if is_automation:
                thread_match = THREAD_RE.search(context)
                turn_match = HANDLER_TURN_RE.search(body)
                if thread_match and turn_match:
                    automation_key = f"{thread_match.group(1)}:{turn_match.group(1)}"
                    ignored[automation_key] = float(ts)
                    active.pop(automation_key, None)
                continue

            thread_id, turn_id = ids(context)
            if not turn_id:
                continue
            key = f"{thread_id}:{turn_id}"

            is_start = (
                "feedback_tags" in target
                and "submission_dispatch" in body
                and "model=" in body
            )
            is_normal_done = (
                "codex_core::session::turn" in target
                and "post sampling token usage" in body
                and "needs_follow_up=false" in body
                and "has_pending_input=false" in body
            )
            is_terminal_error = (
                "codex_core::session::turn" in target
                and any(
                    marker in body
                    for marker in (
                        "Turn error:",
                        "Turn aborted",
                        "Turn cancelled",
                        "Turn canceled",
                    )
                )
            )
            is_interrupted = (
                "codex_core::tasks" in target
                and "aborting running task" in body
            )
            is_done = is_normal_done or is_terminal_error or is_interrupted

            if is_start and key not in active and key not in ignored:
                cwd_match = CWD_RE.search(context)
                active[key] = {
                    "key": key,
                    "session_id": thread_id,
                    "turn_id": turn_id,
                    "cwd": cwd_match.group(1) if cwd_match else None,
                    "started_at": float(ts),
                }
            if is_done:
                active.pop(key, None)
                ignored.pop(key, None)
                state["last_completed_at"] = float(ts)

        state["active"] = list(active.values())
        state["ignored"] = ignored
        current_state = json.dumps(
            state, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        if current_state != previous_state:
            save_state(state)
        return bool(active)


def connect():
    connection = sqlite3.connect(
        f"file:{DB_PATH}?mode=ro",
        uri=True,
        timeout=2,
    )
    connection.execute("PRAGMA query_only=ON")
    return connection


def process_new_rows(last_id):
    with connect() as db:
        max_id = db.execute(
            "SELECT COALESCE(MAX(id), 0) FROM logs"
        ).fetchone()[0]
        rebuilding = last_id == 0
        start_id = max(0, max_id - 50_000) if rebuilding else last_id
        while start_id < max_id:
            rows = db.execute(
                """
                SELECT id, ts, target, feedback_log_body
                FROM logs
                WHERE id > ?
                  AND id <= ?
                  AND (
                    target LIKE '%feedback_tags%'
                    OR target LIKE '%codex_core::session::turn%'
                    OR target LIKE '%codex_core::tasks%'
                    OR target LIKE '%codex_core::session::handlers%'
                  )
                ORDER BY id
                LIMIT 10000
                """,
                (start_id, max_id),
            ).fetchall()
            if rows:
                apply_rows(rows, reset_active=rebuilding)
            if len(rows) < 10_000:
                break
            start_id = rows[-1][0]
            rebuilding = False
        return max_id


def wait_for_log_change(last_id):
    """Block in the kernel until SQLite changes; no timer-based DB polling."""
    event_only = getattr(os, "O_EVTONLY", os.O_RDONLY)
    while True:
        descriptors = []
        changes = []
        try:
            for path in (WAL_PATH, DB_PATH):
                try:
                    descriptor = os.open(path, event_only)
                except OSError:
                    continue
                descriptors.append(descriptor)
                changes.append(select.kevent(
                    descriptor,
                    filter=select.KQ_FILTER_VNODE,
                    flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                    fflags=(
                        select.KQ_NOTE_WRITE
                        | select.KQ_NOTE_EXTEND
                        | select.KQ_NOTE_RENAME
                        | select.KQ_NOTE_DELETE
                    ),
                ))
            if not changes:
                time.sleep(1)
                continue
            queue = select.kqueue()
            try:
                queue.control(changes, 0, 0)
                # Close the small setup race: a row may arrive after the
                # previous query but before the vnode watches are installed.
                with connect() as db:
                    current_id = db.execute(
                        "SELECT COALESCE(MAX(id), 0) FROM logs"
                    ).fetchone()[0]
                if current_id > last_id:
                    return
                queue.control(None, 1)
                return
            finally:
                queue.close()
        finally:
            for descriptor in descriptors:
                os.close(descriptor)


def main():
    os.makedirs(CODEX_DIR, exist_ok=True)
    last_id = 0
    while True:
        try:
            last_id = process_new_rows(last_id)
            wait_for_log_change(last_id)
            # Coalesce the short burst of WAL writes produced by one event.
            time.sleep(0.1)
        except (OSError, sqlite3.Error, ValueError):
            time.sleep(1)


if __name__ == "__main__":
    main()
