"""
Shared digest state store for Slack Digest Agent.

Why this file exists:
  app.py owns the button-click handlers, and blockkit_formatter.py owns
  send_digest_dm() (which builds and posts the card). Both need to agree
  on what "sections" and "user_name" go with a given user's digest message,
  so that clicking a tab button can redraw the correct content.

  Previously app.py held its own local `digest_state = {}` dict, but
  send_digest_dm() in blockkit_formatter.py had no reference to it and
  never populated it — so every button click hit an empty dict and did
  nothing. This module gives both files a single shared place to read
  from and write to.
"""

from typing import Optional, TypedDict


class DigestState(TypedDict):
    channel: str
    ts: str
    user_name: str
    sections: dict


_digest_state: dict[str, DigestState] = {}


def set_digest_state(user_id: str, channel: str, ts: str, user_name: str, sections: dict) -> None:
    """Stores everything needed to redraw a user's digest card later."""
    _digest_state[user_id] = {
        "channel": channel,
        "ts": ts,
        "user_name": user_name,
        "sections": sections,
    }


def get_digest_state(user_id: str) -> Optional[DigestState]:
    """Returns the stored state for a user, or None if no digest was sent yet."""
    return _digest_state.get(user_id)


def clear_digest_state(user_id: str) -> None:
    """Removes stored state for a user (e.g. once their digest is stale)."""
    _digest_state.pop(user_id, None)
