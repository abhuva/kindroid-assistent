from pathlib import Path
from contextlib import nullcontext

import pytest

from kindroid_relay.application import RelayApplication
from kindroid_relay.command_queue import CommandType, CommandValidationError, validate_payload
from kindroid_relay.state import NON_SPENDING_INITIATIVE_OUTCOMES, RelayState
from kindroid_relay.worker import RelayWorker
from test_relay import settings
from test_web import dashboard, MUTATION_HEADERS
from test_initiative import setup_application


def test_old_skips_do_not_consume_budget_and_unknown_attempts_still_do(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("kindroid_relay.state.time.time", lambda: 100000)
    with RelayState(tmp_path / "state.db") as state:
        for outcome in NON_SPENDING_INITIATIVE_OUTCOMES:
            assert state.record_initiative_check(1, outcome)
        assert state.initiative_check_count() == 0
        assert state.record_initiative_check(1, "started")
        assert not state.record_initiative_check(1, "started")
        # Keep a skip visible without replacing the earlier spending attempt.
        assert state.record_initiative_check(1, "quiet_hours")
        assert state.initiative_check_count() == 1
        state.connection.execute("INSERT INTO initiative_checks VALUES(?, ?)", (100000, "old_unknown_failure"))
        state.connection.execute("INSERT INTO initiative_checks VALUES(?, ?)", (13600, "check_in_sent"))
        state.connection.commit()
        assert state.initiative_check_count() == 2  # Exact 24-hour boundary expires.


def test_budget_availability_accounts_for_lowered_limit_and_rolling_expiry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("kindroid_relay.state.time.time", lambda: 100000)
    with RelayState(tmp_path / "state.db") as state:
        state.connection.executemany("INSERT INTO initiative_checks VALUES(?, ?)", [(90000, "started"), (91000, "check_in_sent"), (92000, "check_in_answered"), (93000, "quiet_hours")])
        state.connection.commit()
        state.set_meta("initiative_limit_override", 2)
        budget = state.initiative_budget(50)
        assert budget == {"used": 3, "limit": 2, "source": "dashboard", "available_at": 91000 + 86400}
        monkeypatch.setattr("kindroid_relay.state.time.time", lambda: 91000 + 86400)
        assert state.initiative_budget(50) == {"used": 1, "limit": 2, "source": "dashboard", "available_at": None}


@pytest.mark.parametrize("limit", [0, -1, 1001, True, "50", 50.1, None])
def test_limit_command_validates_again_at_worker_boundary(limit) -> None:
    with pytest.raises(CommandValidationError):
        validate_payload(CommandType.SET_INITIATIVE_LIMIT, {"limit": limit})


def test_dashboard_setting_is_queued_idempotent_audited_and_persistent(tmp_path: Path) -> None:
    config, factory, _wake, client = dashboard(tmp_path)
    path = "/api/v1/settings/initiative-limit"
    body = {"idempotency_key": "budget-setting", "limit": 50}
    assert client.post(path, json=body).status_code == 403
    response = client.post(path, json=body, headers=MUTATION_HEADERS)
    assert response.status_code == 202
    with factory.connect() as state:
        assert state.get_meta("initiative_limit_override") is None
        state.pause("operator pause")
        command = state.commands.claim_next()
        app = RelayApplication(config, state, None, lambda: nullcontext(None))
        RelayWorker(config)._run_command(state, app, command)
        assert state.is_paused()
        assert state.initiative_limit(config.initiative_max_checks_24h) == 50
        assert any(event.kind == "initiative_limit_changed" and event.details["limit"] == 50 for event in state.activity.after())
    # New connection, same override. The loaded Settings instance is unchanged.
    snapshot = client.get("/api/v1/snapshot").json()
    assert snapshot["schedule"]["initiative_budget"]["limit"] == 50
    assert snapshot["schedule"]["initiative_budget"]["source"] == "dashboard"
    assert client.post(path, json=body, headers=MUTATION_HEADERS).json()["id"] == response.json()["id"]
    assert client.post(path, json={**body, "limit": 51}, headers=MUTATION_HEADERS).status_code == 409
    for bad in (0, 1001, True, "50", 1.5):
        assert client.post(path, json={**body, "limit": bad}, headers=MUTATION_HEADERS).status_code == 422
    assert client.get(f"/api/v1/commands/{command.id}").json()["result"]["limit"] == 50


def test_limit_update_and_audit_roll_back_together(tmp_path: Path, monkeypatch) -> None:
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        app = RelayApplication(config, state, None, lambda: nullcontext(None))
        def fail_audit(*_args, **_kwargs):
            raise RuntimeError("fake audit failure")
        monkeypatch.setattr(state.activity, "add", fail_audit)
        with pytest.raises(RuntimeError, match="audit"):
            app.set_initiative_limit(50)
        assert state.get_meta("initiative_limit_override") is None


def test_saved_limit_is_used_by_existing_relay_and_preserves_spent_usage(tmp_path: Path, monkeypatch) -> None:
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        app, remote, agent = setup_application(state, config)
        app.bootstrap()
        app.set_initiative_limit(1)
        state.record_initiative_check(1, "started")
        assert app.trigger_initiative()["initiative_outcome"] == "daily_budget"
        app.set_initiative_limit(50)
        # If Codex work fails, the reserved attempt remains charged.
        def fail_check_in(_latest):
            raise RuntimeError("mock Codex failure")
        monkeypatch.setattr(agent, "scheduled_check_in", fail_check_in)
        with pytest.raises(RuntimeError, match="mock Codex"):
            app.trigger_initiative()
        assert state.initiative_check_count() == 2
        assert remote.sent == []
