"""Cheap attention hints and bounded model verdicts, never keyword stop commands."""
from __future__ import annotations

import re


# Values are the required end_session flag. No model prose enters activity logs.
END_REASONS = {
    'farewell_complete': True,
    'pause_requested': True,
    'farewell_in_progress': False,
    'conversation_ongoing': False,
    'unclear': False,
}

# Deliberately a hint, not a semantic classifier. Quotes, jokes and negation can
# match; the existing read-only attention call decides using the room context.
_FAREWELL = re.compile(
    r"\b(?:good\s*night|goodbye|bye(?:[ -]bye)?|sleep\s+(?:well|tight)|sweet\s+dreams|"
    r"see\s+you\s+(?:later|tomorrow|soon)|talk\s+(?:to\s+you\s+)?(?:later|tomorrow)|"
    r"call\s+it\s+a\s+(?:day|night)|that['’]s\s+(?:all|enough)\s+for\s+(?:now|today)|"
    r"(?:let['’]s|we\s+can)\s+(?:stop|pause|wrap\s+up)|"
    r"(?:heading|going|off)\s+to\s+bed)\b",
    re.IGNORECASE,
)


def has_farewell_cue(text: str) -> bool:
    return _FAREWELL.search(text) is not None
