"""Explicit room configuration; no credentials or cross-room context."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

from .config import ConfigurationError, Settings


def configured_rooms(settings: Settings) -> list[Settings]:
    if not settings.rooms_file:
        return [settings]
    try:
        path = Path(settings.rooms_file)
        if path.stat().st_size > 16384:
            raise ValueError()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not 1 <= len(data) <= 3:
            raise ValueError()
        result = []
        keys, groups = set(), set()
        for index, item in enumerate(data):
            if not isinstance(item, dict) or set(item) - {
                "key", "label", "group_id", "mode", "participants", "participant_scheduling",
                "daily_time", "check_every", "max_messages", "max_seconds", "turn_seconds"}:
                raise ValueError()
            key, group = item["key"], item["group_id"]
            mode = item.get("mode", "assistant")
            if (not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", key)
                    or key in keys or not isinstance(group, str) or not 1 <= len(group) <= 128
                    or group in groups or mode not in {"assistant", "shared"}):
                raise ValueError()
            if index == 0 and group != settings.group_id:
                raise ValueError()  # Existing state must remain bound to its original room.
            people = item["participants"]
            if not isinstance(people, list) or len(people) != (2 if mode == "shared" else 1):
                raise ValueError()
            roster = []
            for person in people:
                if not isinstance(person, dict) or set(person) != {"id", "name"}:
                    raise ValueError()
                if any(not isinstance(person[k], str) or not 1 <= len(person[k].strip()) <= 128
                       or any(ord(c) < 32 for c in person[k]) for k in ("id", "name")):
                    raise ValueError()
                if person["id"] in {"ai", "user"}:
                    raise ValueError()
                roster.append((person["id"], person["name"]))
            if len({p[0] for p in roster}) != len(roster) or len({p[1].casefold() for p in roster}) != len(roster):
                raise ValueError()
            label = item.get("label", key)
            chosen = item.get("participant_scheduling", mode == "assistant")
            if not isinstance(label, str) or not 1 <= len(label) <= 100 or type(chosen) is not bool:
                raise ValueError()
            daily = item.get("daily_time", "18:00")
            if not isinstance(daily, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", daily):
                raise ValueError()
            options = {}
            for name, default, low, high in (("check_every", 6, 2, 50), ("max_messages", 60, 2, 500),
                    ("max_seconds", 3600, 60, 14400), ("turn_seconds", 30, 1, 600)):
                value = item.get(name, default)
                if type(value) is not int or not low <= value <= high:
                    raise ValueError()
                options["shared_" + name] = value
            state_path = settings.state_path if index == 0 else settings.state_path.parent / ("room-" + key + ".sqlite3")
            result.append(replace(settings, rooms_file="", room_key=key, room_label=label,
                group_id=group, room_mode=mode, participants=tuple(roster),
                kindroid_display_name=roster[0][1], state_path=state_path,
                account_state_path=settings.state_path, participant_scheduling=True,
                exploration_enabled=False, curiosity_enabled=False,
                cooperative_turns=True, shared_hour=int(daily[:2]), shared_minute=int(daily[3:]), **options))
            keys.add(key)
            groups.add(group)
        if len({r.state_path.resolve() for r in result}) != len(result):
            raise ValueError()
        return result
    except (OSError, ValueError, TypeError, KeyError):
        raise ConfigurationError("Invalid rooms file: use the documented unique room/participant configuration; first group must match KINDROID_GROUP_ID") from None
