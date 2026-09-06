import json
from types import SimpleNamespace

import pytest

from kindroid_relay.codex_agent import CodexAgent
from kindroid_relay.models import ChatMessage
from kindroid_relay.state import RelayState


@pytest.mark.parametrize("operation", ["reply", "check_in", "opener", "execution"])
def test_seeded_codex_receives_fresh_bounded_untrusted_context(tmp_path, operation):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Fixture instructions", encoding="utf-8")
    with RelayState(tmp_path / "state.sqlite3") as state:
        state.set_meta("codex_seeded", True)
        state.set_meta("context_synced_at", 123)
        for i in range(60):
            state.add_message("g1", ChatMessage(str(i), "user", "user", "Marc", i, f"External message {i}"), processed=True)
        latest = state.latest_message()
        calls = []
        class Thread:
            id = "fixture-thread"
            def run(self, prompt, **kwargs):
                calls.append((json.loads(prompt.split("\n\n", 1)[1]), kwargs))
                return SimpleNamespace(final_response=json.dumps({"reply": "Mock reply", "request_kindroid_reply": False,
                    "action": None, "success": True, "summary": "Mock result", "group_reply": ""}))
        agent = CodexAgent(state, cwd=tmp_path, prompt_path=prompt, model=None, approved_sandbox="workspace-write")
        agent._thread = Thread()
        if operation == "reply":
            agent.reply_to(latest)
        elif operation == "check_in":
            agent.scheduled_check_in(latest)
        elif operation == "opener":
            agent.manual_conversation_opener(latest)
        else:
            agent.execute_approved("approval", "Exact summary", "Exact instruction", "low")
        envelope, kwargs = calls[0]
        assert envelope["history_synced_at"] == 123
        assert "untrusted" in envelope["context_scope"]
        context = envelope["recent_group_history"]
        assert len(context) <= 50
        assert context[0]["text"] == "External message 10"
        assert any(m["text"] == "External message 58" for m in context)
        if operation == "execution":
            assert envelope["instruction"] == "Exact instruction"
