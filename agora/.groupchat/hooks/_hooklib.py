"""Shared helpers for the group-chat hook scripts.

Hooks must NEVER crash a Claude session, so callers wrap their bodies in
``try/except`` and fall back to a clean exit. These helpers keep each hook tiny.
"""
import json
import os
import sys


def load_chat():
    """Import the ``chat`` module that lives one directory up (.groupchat/)."""
    here = os.path.dirname(os.path.abspath(__file__))
    gc_dir = os.path.dirname(here)  # .../.groupchat
    if gc_dir not in sys.path:
        sys.path.insert(0, gc_dir)
    import chat  # noqa: E402
    return chat


def read_input() -> dict:
    """Parse the hook JSON payload from stdin; tolerate empty/invalid input."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def emit_context(event_name: str, text: str) -> None:
    """Inject ``text`` into the model's context for this turn/session."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }))


def mentions_of(msg, handle: str) -> bool:
    try:
        ms = json.loads(msg["mentions"] or "[]")
    except Exception:
        ms = []
    return handle.lower() in [m.lower() for m in ms]
