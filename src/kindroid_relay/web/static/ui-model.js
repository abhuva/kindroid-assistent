"use strict";

(function exposeRelayUiModel(globalScope) {
  const busyPhases = new Set([
    "starting",
    "polling",
    "waiting_for_kindroid",
    "running_codex",
    "sending",
    "executing_approval",
  ]);
  const terminalCommandStatuses = new Set([
    "completed",
    "failed",
    "cancelled",
    "interrupted",
  ]);

  function relayStatusView(snapshot) {
    const worker = snapshot.worker;
    if (!worker || worker.phase === "stopping") {
      return { key: "stopped", label: "Stopped", connection: "Worker stopped", tone: "error" };
    }
    if (worker.stale) {
      return { key: "stale", label: "Worker stale", connection: "Worker stale", tone: "error" };
    }
    if (snapshot.relay.paused) {
      return { key: "paused", label: "Paused", connection: "Connected", tone: "warning" };
    }
    if (worker.phase === "degraded") {
      return { key: "degraded", label: "Degraded", connection: "Degraded", tone: "warning" };
    }
    if (busyPhases.has(worker.phase)) {
      return { key: "busy", label: "Busy", connection: "Connected", tone: "good" };
    }
    return { key: "running", label: "Running", connection: "Connected", tone: "good" };
  }

  function pollDelay(baseDelay, failures, maximum = 30000) {
    return Math.min(baseDelay * (2 ** Math.min(failures, 5)), maximum);
  }

  function commandStatusView(command) {
    if (!command) {
      return {
        pending: false,
        terminal: false,
        text: "No dashboard command running.",
        tone: "muted",
      };
    }
    const commandName = command.command_type.replaceAll("_", " ");
    const terminal = terminalCommandStatuses.has(command.status);
    const initiativeLabels = {
      paused: "Skipped: relay is paused",
      disabled: "Skipped: initiative checks are disabled",
      bootstrapped: "Skipped: initial history loaded; trigger again to check in",
      daily_budget: "Skipped: daily check-in budget exhausted",
      pending_message: "Skipped: a companion already has a pending message",
      empty_room: "Skipped: group history is empty",
      latest_not_codex: "Skipped: legacy last-speaker rule",
      recent_conversation: "Skipped: conversation is recent or its time is unknown",
      delivery_unresolved: "Skipped: unresolved delivery needs inspection",
      quiet_hours: "Skipped: quiet hours",
      missed: "Skipped: scheduled time was missed",
      check_in_sent: "Check-in sent",
      check_in_answered: "Check-in sent; companion answered",
      check_in_sent_user_turn: "Check-in sent; companion did not take the offered turn",
      check_in_waiting_for_history: "Check-in sent; waiting for history cooldown",
    };
    const isCheckIn = command.command_type === "trigger_initiative" && command.status === "completed";
    let detail = isCheckIn
      ? initiativeLabels[command.result?.initiative_outcome] || "Check-in outcome unavailable"
      : "";
    if (command.command_type === "set_initiative_limit" && command.status === "completed") {
      detail = `Saved daily check-in limit: ${command.result?.limit ?? "unknown"}`;
    }
    const isSync = command.command_type === "sync_history";
    const isRecovery = command.command_type === "recover_shared";
    const recoveryBlocked = isRecovery && command.status === "completed" && !["recovered", "already_recovered"].includes(command.result?.outcome);
    if (isRecovery && command.status === "completed") detail = recoveryBlocked
      ? `Recovery blocked: ${command.result?.reason || "Inspect room activity."}`
      : "Relay session recovered; uncertain request retained without retry. Resume and start a new conversation when ready.";
    const syncSucceeded = ["synced", "bootstrapped"].includes(command.result?.sync_outcome);
    if (isSync && command.status === "completed") {
      const result = command.result || {};
      const syncLabels = {
        synced: `History synchronized: ${result.messages_added ?? 0} new messages saved.`,
        bootstrapped: `Initial history imported: ${result.messages_added ?? 0} messages; old chat will not be replayed.`,
        history_cooldown: `Sync blocked: API cooldown${result.retry_at ? ` until ${new Date(result.retry_at * 1000).toLocaleString()}` : ""}.`,
        history_budget: "Sync blocked: rolling history-read allowance exhausted.",
        history_pending: "History fetched, but local messages are not yet confirmed. Sync remains incomplete; try again later.",
        reconciliation_unresolved: "Sync blocked: history was fetched, but delivery/generation remains ambiguous. Saved history was not updated; do not resume yet.",
        history_error: "Kindroid history request failed. No automatic retry.",
      };
      detail = syncLabels[result.sync_outcome] || "History sync outcome unavailable";
      if (!syncSucceeded && typeof result.reason_detail === "string") detail += ` ${result.reason_detail}`;
      if (result.paused) detail += " Relay remains paused.";
    } else if (isSync && terminal) {
      detail = command.error || "Sync did not complete; inspect command activity.";
    }
    return {
      pending: !terminal,
      terminal,
      detail,
      text: `${commandName} — ${(isSync && command.status === "completed" && !syncSucceeded) || recoveryBlocked ? "blocked" : command.status}${detail ? ` · ${detail}` : ""}`,
      tone: command.status === "completed"
        ? (isCheckIn && command.result?.check_in_sent !== true) || (isSync && !syncSucceeded) || recoveryBlocked ? "warning" : "success"
        : terminal
          ? "error"
          : "pending",
    };
  }

  function formatClockMinutes(minutes) {
    const hour = Math.floor(minutes / 60).toString().padStart(2, "0");
    const minute = (minutes % 60).toString().padStart(2, "0");
    return `${hour}:${minute}`;
  }

  function scheduleView(snapshot) {
    const schedule = snapshot.schedule;
    if (schedule.participant_scheduling) {
      return { basis: schedule.initiative_enabled ? "Kin-chosen check-ins · six-hour fallback" : "Automatic check-ins disabled",
        quietHours: "No companion quiet hours enabled" };
    }
    if (schedule.daily?.configured) {
      const config = schedule.daily.config;
      return { basis: config.enabled ? `Shared schedule: every ${config.tick_minutes || 60} minutes (Schedule tab)` : "Scheduled activities off",
        quietHours: `${config.dead_hours.length} dead hours per day` };
    }
    if (!schedule.initiative_enabled) {
      return { basis: "Disabled", quietHours: "Not applicable" };
    }
    const hours = schedule.initiative_schedule_hours;
    const basis = hours?.length
      ? `Fixed daily at ${hours.map((hour) => `${hour.toString().padStart(2, "0")}:00`).join(", ")} local`
      : `Every ${Math.round(schedule.initiative_interval_seconds / 60)} minutes after activity`;
    const quiet = schedule.initiative_quiet_hours;
    return {
      basis,
      quietHours: quiet
        ? `${formatClockMinutes(quiet[0])}–${formatClockMinutes(quiet[1])} local`
        : "None",
    };
  }

  function quotaView(used, maximum) {
    const ratio = maximum > 0 ? used / maximum : 1;
    return {
      warning: ratio >= 0.8,
      text: `${used} / ${maximum}`,
      note: (ratio >= 1
        ? "Local warning threshold reached; requests remain enabled. "
        : ratio >= 0.8 ? "Approaching the local warning threshold; requests remain enabled. "
        : "Measurement only; requests remain enabled. ")
        + "Kindroid’s server-side count may differ.",
    };
  }

  globalScope.RelayUiModel = {
    commandStatusView,
    pollDelay,
    quotaView,
    relayStatusView,
    scheduleView,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      commandStatusView,
      pollDelay,
      quotaView,
      relayStatusView,
      scheduleView,
    };
  }
})(globalThis);
