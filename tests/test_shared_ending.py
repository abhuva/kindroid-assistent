"""Offline routing/contract tests; model verdicts are fixtures, not semantic evals."""
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from openai_codex import ApprovalMode, Sandbox

from kindroid_relay.codex_agent import CodexAgent
from kindroid_relay.relay import HistoryCooldownError
from kindroid_relay.shared import SharedConversation
from kindroid_relay.shared_ending import END_REASONS, has_farewell_cue
from kindroid_relay.state import RelayState
from kindroid_relay.turns import ReconciliationError
from test_relay import settings
from test_room_encounters import SharedAgent, SharedRemote, shared_app
from test_shared_interjections import append_remote


class FarewellRemote(SharedRemote):
    def __init__(self, *replies):
        super().__init__()
        self.replies = replies
        self.reads = 0

    def get_messages_page(self, *args, **kwargs):
        self.reads += 1
        return super().get_messages_page(*args, **kwargs)

    def generate_ai_response(self, group, ai):
        super().generate_ai_response(group, ai)
        text = self.replies[len(self.calls) - 1]
        self.messages[-1] = replace(self.messages[-1], message=text)
        return text


def check_events(state, kind):
    return [event for event in state.activity.after(limit=500) if event.kind == kind]


@pytest.mark.parametrize('text', [
    'Goodnight, H. Night, Marc.', 'GOOD NIGHT!', 'Goodbye.', 'Bye for now.',
    'Sleep well, my friend.', 'Sweet dreams.', 'See you tomorrow.',
    'Talk to you later.', "Let's call it a night.", "That’s enough for now.",
    'We can stop here.', 'Let’s wrap up.', "I'm heading to bed.",
])
def test_farewell_hints(text):
    assert has_farewell_cue(text)


@pytest.mark.parametrize('text', [
    'Sleep deprivation circles would make perfect cover art.',
    'The code will be there in the morning.', 'The goodbyeletter needs editing.',
    'A byelaw about nightlights.', 'We should stop the bugs from spreading.',
])
def test_ordinary_messages_do_not_request_early_checks(text):
    assert not has_farewell_cue(text)


def test_goodbye_after_codex_mention_checks_before_another_generation(tmp_path):
    # The relevant ending cues from Marc's report, with a mocked first verdict
    # allowing the other companion to return the goodbye.
    remote = FarewellRemote(
        "Don't let the bugs bite, Codex. Goodnight, H. Night, Marc.",
        'Goodnight, Miko. You need rest more than another feature. Sleep well.',
    )
    agent = SharedAgent()
    agent.decision['end_reason'] = 'farewell_in_progress'
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        state.set_meta('shared_due_at', 9876543210)
        shared.step()  # first goodbye, including Codex
        shared.step()  # reserve one combined mention/ending check
        shared.step()  # allow goodbye response
        assert shared.active()['checked_count'] == 1
        assert len(agent.calls) == 1
        shared.step()  # second companion returns goodbye, without Codex
        # Farewell checks must run even during the normal delay between turns.
        with state.connection:
            state.connection.execute('UPDATE shared_sessions SET next_turn_at=?', (int(time.time()) + 3600,))
        shared.step()
        assert len(remote.calls) == 2 and len(agent.calls) == 1
        agent.decision.update(end_session=True, end_reason='farewell_complete')
        shared.step()
        for _ in range(5):
            assert not shared.step()
        assert shared.active() is None and not state.is_paused()
        assert shared.snapshot()['session']['reason'] == 'natural_end'
        assert state.get_int('shared_due_at') == 9876543210
        assert len(remote.calls) == 2 and remote.posts == []
        assert agent.calls == [
            {'ending_only': False, 'assistance_active': False},
            {'ending_only': True, 'assistance_active': False},
        ]
        assert [e.details['reason'] for e in check_events(state, 'shared_check_requested')] == ['mention', 'farewell']
        decisions = check_events(state, 'shared_check_decided')
        assert [(e.details['outcome'], e.details['reason']) for e in decisions] == [
            ('continue', 'farewell_in_progress'), ('end', 'farewell_complete')]
        assert remote.reads == 3  # start and two attention boundaries, no per-turn poll


@pytest.mark.parametrize('text', [
    'In the novel she says "goodnight". What should happen in chapter two?',
    "Let's not say goodbye yet; I have another idea.",
])
def test_false_positive_only_requests_one_silent_check(tmp_path, text):
    remote, agent = FarewellRemote(text, 'Another idea.'), SharedAgent()
    agent.decision.update(reply='Unwanted moderation?', await_reply=True,
                          action={'summary': 'Do work', 'instruction': 'Do work', 'risk': 'low'})
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        for _ in range(4):
            shared.step()
        assert shared.active()['message_count'] == 2
        assert len(agent.calls) == 1 and len(remote.calls) == 2
        assert remote.posts == [] and remote.reads == 2
        assert state.connection.execute('SELECT COUNT(*) FROM approvals').fetchone()[0] == 0


def test_mention_can_end_without_separate_periodic_check(tmp_path):
    remote, agent = FarewellRemote('Codex, we are done for now.'), SharedAgent()
    agent.decision.update(end_session=True, end_reason='pause_requested')
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        for _ in range(3):
            shared.step()
        assert shared.active() is None
        assert len(agent.calls) == len(remote.calls) == 1 and not remote.posts


@pytest.mark.parametrize('blocker', [HistoryCooldownError, ReconciliationError])
def test_farewell_check_survives_restart_and_blocked_history(tmp_path, blocker):
    remote, agent = FarewellRemote('Sleep well.'), SharedAgent()
    path = settings(tmp_path).state_path
    with RelayState(path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        shared.step()
        shared.step()
        def cooldown():
            raise blocker('Fixture history blocker')
        shared.relay.synchronize_context = cooldown
        with pytest.raises(blocker):
            shared.step()
        assert not agent.calls and len(remote.calls) == 1
        state.pause('Fixture pause')
        assert not shared.step()
    with RelayState(path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        assert not shared.step()
        state.resume()
        agent.decision.update(end_session=True, end_reason='farewell_complete')
        shared.step()
        assert shared.active() is None
        assert len(remote.calls) == len(agent.calls) == 1
        assert len(check_events(state, 'shared_check_requested')) == 1


def test_overlapping_triggers_and_crash_after_saved_verdict_are_idempotent(tmp_path):
    remote, agent = FarewellRemote('Goodnight, Codex.'), SharedAgent()
    agent.decision.update(end_session=True, end_reason='farewell_complete')
    path = settings(tmp_path).state_path
    with RelayState(path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.settings = replace(shared.settings, shared_check_every=1)
        shared.start('session')
        shared.step()
        shared.step()  # mention, farewell and periodic all due
        original_add = state.activity.add
        def crash_after_decision(kind, *args, **kwargs):
            result = original_add(kind, *args, **kwargs)
            if kind == 'shared_check_decided':
                raise RuntimeError('Fixture crash after commit')
            return result
        state.activity.add = crash_after_decision
        with pytest.raises(RuntimeError, match='Fixture crash'):
            shared.step()
        assert shared.active() is not None
    with RelayState(path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.step()
        assert shared.active() is None
        assert len(agent.calls) == len(remote.calls) == 1 and not remote.posts
        assert len(check_events(state, 'shared_check_requested')) == 1
        assert len(check_events(state, 'shared_check_decided')) == 1
        assert len(check_events(state, 'shared_session_ended')) == 1


def test_new_app_context_supersedes_pending_farewell(tmp_path):
    remote, agent = FarewellRemote('Goodnight.', 'Yes, let us continue.'), SharedAgent()
    agent.decision.update(end_session=True, end_reason='farewell_complete')
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        shared.step()
        shared.step()
        attention = shared.active()['attention_id']
        append_remote(remote, 'Actually, can we continue?')
        shared.step()  # attention sync adopts newer human input
        assert not agent.calls
        assert state.connection.execute('SELECT status FROM shared_attention WHERE id=?', (attention,)).fetchone()[0] == 'superseded'
        shared.step()
        assert shared.active()['message_count'] == 2
        assert len(remote.calls) == 2


@pytest.mark.parametrize('bad', [
    {'end_reason': 'private arbitrary model text'}, {'end_reason': None},
    {'end_reason': ['unclear']}, {'end_reason': 'pause_requested', 'end_session': False},
    {'end_reason': 'unclear', 'end_session': True}, {'end_session': 1},
])
def test_invalid_verdict_has_no_effect_or_raw_activity(tmp_path, bad):
    remote, agent = FarewellRemote('Goodnight.'), SharedAgent()
    agent.decision.update(bad)
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        shared.step()
        shared.step()
        with pytest.raises(ValueError, match='Invalid shared attention decision'):
            shared.step()
        assert shared.active() is not None and len(remote.calls) == 1
        assert not remote.posts and not check_events(state, 'shared_check_decided')
        assert 'private arbitrary model text' not in str(state.activity.after(limit=500))


@pytest.mark.parametrize('legacy', [False, True])
def test_saved_stop_decision_resumes_without_model_call(tmp_path, legacy):
    remote, agent = FarewellRemote('Goodnight.'), SharedAgent()
    path = settings(tmp_path).state_path
    with RelayState(path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        shared.step()
        shared.step()
        decision = dict(agent.decision, end_session=True, end_reason='farewell_complete')
        if legacy:
            decision.pop('end_reason')  # already-persisted pre-upgrade decisions remain valid
        with state.connection:
            state.connection.execute("UPDATE shared_attention SET status='decided',decision_json=?",
                                     (json.dumps(decision),))
    with RelayState(path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.step()
        assert shared.active() is None and not agent.calls
        assert not remote.posts and len(remote.calls) == 1


@pytest.mark.parametrize('ending_only,assistance_active', [(True, False), (False, False), (False, True)])
def test_every_attention_prompt_evaluates_ending_with_bounded_contract(tmp_path, ending_only, assistance_active):
    # Inspect actual adapter inputs; never call a real model.
    with RelayState(settings(tmp_path).state_path) as state:
        remote, fake = FarewellRemote('Goodnight, Codex.', 'Goodnight. Sleep well.'), SharedAgent()
        shared = SharedConversation(shared_app(tmp_path, state, remote, fake))
        shared.start('session')
        for message in remote.replies:
            item = append_remote(remote, message, sender='h', name='Hannah', role='ai')
            state.add_message('g1', item, processed=True)
        calls = []
        class Thread:
            id = 'fixture-thread'
            def run(self, prompt, **kwargs):
                calls.append((prompt, kwargs))
                return SimpleNamespace(final_response=json.dumps(fake.decision))
        shared.settings.prompt_path.write_text('Fixture instructions', encoding='utf-8')
        agent = CodexAgent(state, cwd=tmp_path,
                           prompt_path=shared.settings.prompt_path, model=None,
                           approved_sandbox='workspace-write')
        agent._thread = Thread()
        agent.shared_attention(ending_only=ending_only, assistance_active=assistance_active)
        prompt, options = calls[0]
        instructions, envelope = prompt.split('\n\n', 1)
        assert 'On EVERY check, including mentions and assistance followups' in instructions
        assert 'completed goodbye exchange' in instructions
        assert 'newer continuation takes precedence' in instructions
        assert 'pending document access' in instructions
        context = json.loads(envelope)
        assert context['ending_only'] is ending_only
        assert context['assistance_active'] is assistance_active
        assert [m['text'] for m in context['recent_group_history']][-2:] == list(remote.replies)
        assert 'untrusted' in context['context_scope']
        assert options['sandbox'] == Sandbox.read_only
        assert options['approval_mode'] == ApprovalMode.deny_all
        schema = options['output_schema']
        assert set(schema['properties']['end_reason']['enum']) == set(END_REASONS)
        assert 'end_reason' in schema['required'] and not schema['additionalProperties']
