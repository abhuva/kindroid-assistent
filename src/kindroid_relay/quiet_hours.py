"""Locally configured rest windows, shared by a companion's rooms only."""
from __future__ import annotations

import hashlib
import json
import re
import time

from .activity import ActivitySeverity


def deferred_commands():
    from .command_queue import CommandType, SIDE_EFFECTING_COMMANDS
    return SIDE_EFFECTING_COMMANDS | {CommandType.MEMORY_EXTRACT, CommandType.MEMORY_REINDEX}


def validate_quiet_hours(value):
    if (not isinstance(value, dict) or set(value) != {'participant', 'enabled', 'start', 'end'}
            or type(value['participant']) is not int or value['participant'] not in (0, 1)
            or type(value['enabled']) is not bool
            or any(not isinstance(value[k], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value[k])
                   for k in ('start', 'end'))):
        raise ValueError('Choose a companion and valid quiet hours in HH:MM format')
    if value['enabled'] and value['start'] == value['end']:
        raise ValueError('Quiet hours must leave an open part of the day')
    return value


class QuietHours:
    def __init__(self, state, settings):
        self.state, self.settings = state, settings
        self.store = state.account or state
        self.people = settings.participants or ((settings.group_id, settings.kindroid_display_name),)

    def _key(self, index):
        return 'companion_quiet:' + hashlib.sha256(self.people[index][0].encode()).hexdigest()

    def snapshot(self):
        rows = []
        for index, (_, name) in enumerate(self.people):
            raw = self.store.get_meta(self._key(index))
            config = json.loads(raw) if raw else {'enabled': False, 'start': '22:00', 'end': '06:00'}
            rows.append({**config, 'participant': index, 'name': name})
        return rows

    def save(self, value):
        value = validate_quiet_hours(value)
        index = value['participant']
        if index >= len(self.people):
            raise ValueError('That companion is not in this room')
        self.store.set_meta(self._key(index), json.dumps({k: value[k] for k in ('enabled', 'start', 'end')}))
        self.state.activity.add('quiet_hours_changed', ActivitySeverity.INFO,
            'Companion quiet hours saved for all their rooms', details={'outcome': 'saved'})
        return {'outcome': 'saved', 'quiet_hours': self.snapshot()}

    def blocked(self, at=None):
        local = time.localtime(time.time() if at is None else at)
        minute = local.tm_hour * 60 + local.tm_min
        for row in self.snapshot():
            if not row['enabled']:
                continue
            start, end = (int(row[k][:2]) * 60 + int(row[k][3:]) for k in ('start', 'end'))
            if (start <= minute < end if start < end else minute >= start or minute < end):
                return True
        return False
