from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class InvalidAgentReply(ValueError):
    """The model finished but did not supply a usable structured reply."""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    id: str
    sender: str
    sender_type: str
    display_name: str
    timestamp: int
    message: str

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> "ChatMessage":
        sender = str(value.get("sender", ""))
        return cls(
            id=str(value.get("id", "")),
            sender=sender,
            # Current group history can omit sender_type while sender contains
            # the documented role value ("ai" or "user").
            sender_type=str(value.get("sender_type") or sender),
            display_name=str(value.get("display_name", "")),
            timestamp=int(value.get("timestamp", 0)),
            message=str(value.get("message", "")),
        )


@dataclass(frozen=True, slots=True)
class ActionProposal:
    summary: str
    instruction: str
    risk: str


@dataclass(frozen=True, slots=True)
class AgentReply:
    reply: str
    request_kindroid_reply: bool
    action: ActionProposal | None = None
    next_encounter: dict[str, Any] | None = None
    explore: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    success: bool
    summary: str
    group_reply: str
