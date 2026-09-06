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
      pending_message: "Skipped: Hannah already has a pending message",
      empty_room: "Skipped: group history is empty",
      latest_not_codex: "Skipped: latest speaker is not Codex",
      quiet_hours: "Skipped: quiet hours",
      missed: "Skipped: scheduled time was missed",
      check_in_sent: "Check-in sent",
      check_in_answered: "Check-in sent; Hannah answered",
      check_in_sent_user_turn: "Check-in sent; Hannah did not take the offered turn",
      check_in_waiting_for_history: "Check-in sent; waiting for history cooldown",
    };
    const isCheckIn = command.command_type === "trigger_initiative" && command.status === "completed";
    let detail = isCheckIn
      ? initiativeLabels[command.result?.initiative_outcome] || "Check-in outcome unavailable"
      : "";
    if (command.command_type === "set_initiative_limit" && command.status === "completed") {
      detail = `Saved daily check-in limit: ${command.result?.limit ?? "unknown"}`;
    }
    return {
      pending: !terminal,
      terminal,
      detail,
      text: `${commandName} — ${command.status}${detail ? ` · ${detail}` : ""}`,
      tone: command.status === "completed"
        ? isCheckIn && command.result?.check_in_sent !== true ? "warning" : "success"
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
      note: ratio >= 0.8
        ? "Approaching the local history-read safety limit. Kindroid’s server-side count may differ."
        : "Local relay count; Kindroid’s server-side count may differ.",
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
