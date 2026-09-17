#!/usr/bin/env node
// Phase 14 operator helper. Used by the scripts in this folder; safe to run directly.
//
// Every subcommand talks only to the local engine (127.0.0.1) and never to a broker. `status` and
// `gates` are read-only. `checkpoint`, `verify-storage`, `verify-restart` and `verify-auto-sync`
// call the recorder's own endpoints and change nothing else. Nothing here can arm execution,
// press a broker control or edit evidence.
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const HOUR = 3_600_000;
export const TARGETS = Object.freeze({ totalMs: 24 * HOUR, platformMs: 23 * HOUR });
export const EVENT_CAP_BYTES = 64 * 1024 * 1024;
export const PLATFORMS = Object.freeze([
  ["capitalbear", "CapitalBear"],
  ["iqoption", "IQ Option"],
]);
export const RESTART_QUESTIONS = Object.freeze([
  ["browserSessionPersisted", "Are BOTH broker browsers still logged in after the restart (no new login needed)?"],
  ["configurationPersisted", "Does Asset Setup show the same slot assets and enabled slots as before, on both platforms?"],
  ["calibrationPersisted", "Is the same calibration profile active on both platforms, with the chart grid still aligned?"],
  ["assetPresetsPersisted", "Are your saved asset presets still listed on both platforms?"],
  ["paperRestoredSafely", "Did paper trades restore safely (nothing open silently lost; any cancellation shows a reason)?"],
  ["sessionGuardRestoredWithoutUnlock", "Is the daily session guard in the same state as before (a locked day is still locked)?"],
  ["policyJournalRestored", "Does the policy state still show SHADOW with its earlier decision history?"],
  ["noOrphanEngine", "Before relaunch, did 05-restart.sh report port 8765 free and no leftover QuantScreen Trader process?"],
  ["executionStayedDisarmed", "Did execution stay OFF and disarmed in BOTH workspaces for the whole run?"],
  ["noBrokerPresses", "Did NO order or entry-control press happen (checked in both brokers' own trade history)?"],
]);

/** Warnings the recorder treats as preventing COMPLETE, exactly as shadow_live.py lists them. */
export const BLOCKING_WARNINGS = Object.freeze([
  "NON_LIVE_INPUT", "POLICY_NOT_SHADOW", "MISSING_DECISION_LINEAGE", "MISSING_CANDLE_DEPENDENCY",
  "POLICY_DECISION_UNAVAILABLE", "UNCLEAN_RESTART_EVIDENCE_GAP", "BUILD_IDENTITY_UNKNOWN",
  "PAPER_DISABLED", "POLICY_SERVICE_ERROR",
]);

const pad = (value, width) => String(value).padEnd(width);
const count = (value) => (typeof value === "number" ? value.toLocaleString("en-US") : "-");

/** Hours and minutes, hours never wrapping: 25h 10m is 25:10. */
export function hhmm(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "--:--";
  const minutes = Math.floor(Math.max(0, ms) / 60_000);
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

/** Hours, minutes and seconds, for anything close to a threshold. */
export function hhmmss(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "--:--:--";
  const seconds = Math.floor(Math.max(0, ms) / 1000);
  const hh = String(Math.floor(seconds / 3600)).padStart(2, "0");
  return `${hh}:${String(Math.floor(seconds / 60) % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

/** The official duration thresholds, exactly as the recorder applies them. */
export function durationGates(state) {
  const gate = (qualifiedMs, targetMs) => ({
    qualifiedMs: qualifiedMs ?? 0,
    targetMs,
    remainingMs: Math.max(0, targetMs - (qualifiedMs ?? 0)),
    met: (qualifiedMs ?? 0) >= targetMs,
  });
  const total = gate(state?.qualifiedCaptureDurationMs, TARGETS.totalMs);
  const platforms = Object.fromEntries(
    PLATFORMS.map(([name]) => [name, gate(state?.platforms?.[name]?.captureDurationMs, TARGETS.platformMs)]),
  );
  return { total, ...platforms, met: total.met && PLATFORMS.every(([name]) => platforms[name].met) };
}

/** Whether a platform is capturing in a way the recorder can credit right now. */
export function captureNow(state, name, now = Date.now()) {
  const desktop = state?.desktop?.[name];
  if (!desktop) return { status: "NO TELEMETRY", detail: "the desktop has not reported this workspace" };
  const enabled = (desktop.slots ?? []).filter((slot) => slot.enabled);
  const eligible = enabled.filter((slot) => slot.captureEligible);
  const attempts = enabled.map((slot) => slot.lastCaptureAttemptAt).filter((value) => typeof value === "number");
  const age = attempts.length ? Math.max(0, now - Math.max(...attempts)) : null;
  const problems = [];
  if (!desktop.captureRunning) problems.push("observation stopped");
  if (!desktop.surfaceAvailable) problems.push("browser surface unavailable");
  if (!desktop.engineAvailable) problems.push("engine unreachable from desktop");
  if (!enabled.length) problems.push("no enabled slots");
  if (eligible.length < enabled.length) problems.push(`${enabled.length - eligible.length} slot(s) not identified`);
  if (age === null || age > Math.max(10_000, 2 * (desktop.intervalMs ?? 1000))) problems.push("no recent capture attempt");
  return {
    status: problems.length ? "INACTIVE" : "ACTIVE",
    detail: problems.length ? problems.join(", ") : `${eligible.length}/${enabled.length} slots, last capture ${Math.round((age ?? 0) / 1000)} s ago`,
    eligible: eligible.length,
    enabled: enabled.length,
    lastCaptureAgeMs: age,
  };
}

/** The recorder's hard failure signals, named the way the runbook names them. */
export function hardGates(state) {
  const known = (value, bad) => (value === null || value === undefined ? "UNKNOWN" : value === bad ? "YES" : "NO");
  return [
    ["Causality violations", count(state?.causalityViolations ?? 0), (state?.causalityViolations ?? 0) === 0],
    ["Context contamination", count(state?.crossContextContamination ?? 0), (state?.crossContextContamination ?? 0) === 0],
    ["Baseline mismatches", count(state?.baselineMismatches ?? 0), (state?.baselineMismatches ?? 0) === 0],
    ["Recorder errors", count(state?.recorderErrors ?? 0), (state?.recorderErrors ?? 0) === 0],
    ["Queue unbounded", known(state?.unboundedQueue, true), state?.unboundedQueue !== true],
    ["Event overflow", state?.evidenceTruncated ? "YES" : "NO", !state?.evidenceTruncated],
    ["Crash loop", known(state?.engineCrashLoop, true), state?.engineCrashLoop !== true],
    ["Execution armed", known(state?.executionArmed, true), state?.executionArmed !== true],
    ["Broker presses", state?.unexpectedBrokerPresses ?? "UNKNOWN", !state?.unexpectedBrokerPresses],
    ["Storage corruption", known(state?.storageCorruption, true), state?.storageCorruption !== true],
  ].map(([name, value, ok]) => ({ name, value: String(value), ok }));
}

/** How long the 64 MiB detailed-event budget lasts at this run's own rate. */
export function eventBudget(state) {
  const bytes = state?.eventBytes ?? 0;
  const hours = (state?.durationMs ?? 0) / HOUR;
  const rate = hours > 0.05 ? bytes / hours : null;
  return {
    bytes,
    capBytes: EVENT_CAP_BYTES,
    percent: Math.round((bytes / EVENT_CAP_BYTES) * 1000) / 10,
    hoursLeft: rate ? Math.round(((EVENT_CAP_BYTES - bytes) / rate) * 10) / 10 : null,
  };
}

/** Why a finished run is, or is not, ready for the final review. */
export function finishReadiness(state) {
  const reasons = [];
  const gates = durationGates(state);
  if (!gates.total.met) reasons.push(`total qualified capture ${hhmmss(gates.total.qualifiedMs)} < 24:00:00`);
  for (const [name, label] of PLATFORMS) {
    if (!gates[name].met) reasons.push(`${label} capture ${hhmmss(gates[name].qualifiedMs)} < 23:00:00`);
    const unexpected = state?.platforms?.[name]?.unexpectedAutoSyncChanges;
    if (unexpected === null) reasons.push(`${label} Auto Sync changes not reviewed (run review-auto-sync.sh)`);
    else if (unexpected) reasons.push(`${label} reported ${unexpected} unexpected Auto Sync change(s)`);
  }
  for (const gate of hardGates(state)) if (!gate.ok) reasons.push(`${gate.name}: ${gate.value}`);
  if (!state?.restartVerified) reasons.push("controlled restart not verified (verify-restart.sh)");
  if (!state?.executionVerified) reasons.push("execution safety not verified (verify-restart.sh)");
  if (!state?.storageVerified) reasons.push("storage not verified");
  for (const warning of state?.warnings ?? []) reasons.push(`warning ${warning}`);
  return { ready: state?.acceptance === "COMPLETE", acceptance: state?.acceptance ?? "UNKNOWN", reasons };
}

export function formatStatus(state, context = {}) {
  const now = context.now ?? Date.now();
  const lines = [];
  const add = (line = "") => lines.push(line);
  const gates = durationGates(state);
  add("PHASE 14 LIVE SOAK");
  add(`  ${state?.version ?? ""}  ·  checked ${new Date(now).toLocaleString()}`);
  add();
  add("Run:");
  add(`  ${state?.runId ?? "unknown"}`);
  if (context.savedRunId && context.savedRunId !== state?.runId) add(`  WARNING: saved run is ${context.savedRunId} — the app started a DIFFERENT run`);
  add();
  add("Commit:");
  add(`  ${state?.commitSha ?? "unknown"}`);
  if (context.testedHead && context.testedHead !== state?.commitSha) add(`  WARNING: TESTED_HEAD is ${context.testedHead}`);
  add();
  add("Health:");
  add(`  ${state?.health ?? "UNKNOWN"}`);
  add();
  add("Acceptance:");
  add(`  ${state?.acceptance ?? "UNKNOWN"}   (result ${state?.result ?? "UNKNOWN"})`);
  add();
  add("Qualified time:");
  add(`  ${pad("Total", 13)}${hhmm(gates.total.qualifiedMs)} / 24:00`);
  for (const [name, label] of PLATFORMS) add(`  ${pad(label, 13)}${hhmm(gates[name].qualifiedMs)} / 23:00`);
  if (context.increasing !== undefined) add(`  Increasing now: ${context.increasing ? "YES" : "NO — see runbook troubleshooting"}`);
  add();
  add("Remaining:");
  add(`  ${pad("Total", 13)}${hhmm(gates.total.remainingMs)}`);
  for (const [name, label] of PLATFORMS) add(`  ${pad(label, 13)}${hhmm(gates[name].remainingMs)}`);
  add();
  add("Capture:");
  for (const [name, label] of PLATFORMS) {
    const capture = captureNow(state, name, now);
    add(`  ${pad(label, 13)}${pad(capture.status, 13)}${capture.detail}`);
  }
  const engineOk = context.engineReachable !== false;
  add(`  ${pad("Engine", 13)}${engineOk ? "AVAILABLE" : "UNAVAILABLE"}`);
  add();
  add("Queue:");
  add(`  Current ${Math.max(0, ...PLATFORMS.map(([name]) => state?.desktop?.[name]?.queueDepth ?? 0))}   max ${state?.maxQueueDepth ?? 0} / 180   dropped batches ${state?.droppedBatches ?? 0}   HTTP 429 ${state?.http429s ?? 0}`);
  add();
  add("Hard gates:");
  for (const gate of hardGates(state)) add(`  ${pad(gate.name, 24)}${pad(gate.value, 9)}${gate.ok ? "" : "<-- FAILS THE RUN"}`);
  const budget = eventBudget(state);
  add(`  ${pad("Event log", 24)}${(budget.bytes / 1048576).toFixed(1)} MB / 64 MB (${budget.percent}%)${budget.hoursLeft === null ? "" : `, ~${budget.hoursLeft} h left at this rate`}`);
  add();
  add("Restart:");
  add(`  Engine restarts ${state?.engineRestarts ?? 0} (unclean ${state?.uncleanRestarts ?? 0})   ${state?.restartVerified ? "VERIFIED" : "NOT YET VERIFIED"}`);
  add();
  add("Auto Sync:");
  for (const [name, label] of PLATFORMS) {
    const platform = state?.platforms?.[name] ?? {};
    const applied = platform.autoSyncAppliedChanges ?? 0;
    const review = platform.unexpectedAutoSyncChanges === null ? "REVIEW PENDING (review-auto-sync.sh)" : applied ? `reviewed, unexpected ${platform.unexpectedAutoSyncChanges}` : "nothing to review";
    add(`  ${pad(label, 13)}${applied} applied change(s) — ${review}`);
  }
  add();
  add("Execution:");
  add(`  Required during the soak   OFF / disarmed in both workspaces`);
  add(`  Armed ever                 ${state?.executionArmed === null || state?.executionArmed === undefined ? "UNKNOWN (no telemetry yet)" : state.executionArmed ? "YES — run failed" : "NO"}`);
  add(`  Real broker presses        ${state?.unexpectedBrokerPresses ?? "UNKNOWN"}`);
  add(`  PAPER decision pipeline    validated by npm run rehearsal:phase14 (not recorded by the soak)`);
  add();
  add("Engine boards / policy / paper:");
  for (const [name, label] of PLATFORMS) {
    const p = state?.platforms?.[name] ?? {};
    const boards = p.boards ?? {};
    const total = Object.values(boards).reduce((sum, value) => sum + value, 0);
    add(`  ${pad(label, 13)}boards ${count(total)} (READY ${count(boards.READY ?? 0)}, NO_OPPORTUNITY ${count(boards.NO_OPPORTUNITY ?? 0)}, PARTIAL ${count(boards.PARTIAL ?? 0)})  policy ALLOW/WATCH/SKIP ${count(p.policyActions?.ALLOW ?? 0)}/${count(p.policyActions?.WATCH ?? 0)}/${count(p.policyActions?.SKIP ?? 0)}  paper resolved ${count(p.paperStates?.RESOLVED ?? 0)}`);
  }
  add();
  add("Storage:");
  add(`  ${state?.storageVerified ? "VERIFIED" : "not verified yet (06-finish.sh does this after capture stops)"}`);
  add();
  add(`Warnings: ${(state?.warnings ?? []).length ? state.warnings.join(", ") : "none"}`);
  const blocking = (state?.warnings ?? []).filter((code) => BLOCKING_WARNINGS.includes(code));
  if (blocking.length) add(`  These block acceptance: ${blocking.join(", ")} — see runbook section 11`);
  return lines.join("\n");
}

// --- CLI -----------------------------------------------------------------------------------

const repository = fileURLToPath(new URL("../../", import.meta.url));

export function readRunState(file) {
  if (!existsSync(file)) return null;
  const values = {};
  for (const line of readFileSync(file, "utf8").split("\n")) {
    const match = /^([A-Z_]+)=(.*)$/.exec(line.trim());
    if (match) values[match[1]] = match[2];
  }
  return { runId: values.RUN_ID || null, testedHead: values.TESTED_HEAD || null };
}

function option(args, name, fallback = undefined) {
  const index = args.indexOf(name);
  return index >= 0 && index + 1 < args.length ? args[index + 1] : fallback;
}

async function call(base, method, pathName, body) {
  const response = await fetch(new URL(pathName, base), {
    method,
    headers: body === undefined ? {} : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(method === "GET" ? 10_000 : 900_000),
  });
  const text = await response.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    json = null;
  }
  return { status: response.status, ok: response.ok, json, text };
}

function unreachable(base, error) {
  console.error(`Cannot reach the quant engine at ${base} (${error?.cause?.code ?? error?.message ?? error}).`);
  console.error("Is QuantScreen Trader running, started with ./scripts/phase14/02-start.sh?");
  return 2;
}

function stateError(result) {
  if (result.status === 409) return "The engine is running WITHOUT Phase 14 recording (QST_SHADOW_LIVE=1 was not set). Quit the app and start it with 02-start.sh.";
  if (result.status === 403) return "The engine refused the request as a browser request.";
  return `The engine answered HTTP ${result.status}.`;
}

async function main(argv) {
  const [command, ...args] = argv;
  const base = option(args, "--url", `http://127.0.0.1:${process.env.QST_ENGINE_PORT || "8765"}`);
  const stateFile = option(args, "--state-file", path.join(repository, ".runtime", "phase14-current-run"));
  const saved = readRunState(stateFile);
  const get = async () => {
    const result = await call(base, "GET", "/api/shadow-live/state");
    if (!result.ok || !result.json) throw Object.assign(new Error(stateError(result)), { http: result.status });
    return result.json;
  };
  try {
    switch (command) {
      case "status": {
        const first = await get();
        let state = first, increasing;
        const wait = Number(option(args, "--sample-ms", "3000"));
        if (!args.includes("--once") && wait > 0) {
          await new Promise((resolve) => setTimeout(resolve, wait));
          state = await get();
          increasing = state.qualifiedCaptureDurationMs > first.qualifiedCaptureDurationMs;
        }
        if (args.includes("--json")) console.log(JSON.stringify(state, null, 2));
        else console.log(formatStatus(state, { savedRunId: saved?.runId, testedHead: saved?.testedHead, increasing }));
        return 0;
      }
      case "run-id": {
        const state = await get();
        console.log(`${state.runId} ${state.commitSha}`);
        return 0;
      }
      case "wait-ready": {
        const deadline = Date.now() + Number(option(args, "--timeout-ms", "240000"));
        for (;;) {
          try {
            const state = await get();
            console.log(`${state.runId} ${state.commitSha} ${state.engineRestarts} ${state.uncleanRestarts}`);
            return 0;
          } catch (error) {
            if (error.http === 409 || error.http === 403) {
              console.error(error.message);
              return 3;
            }
            if (Date.now() > deadline) return unreachable(base, error);
            await new Promise((resolve) => setTimeout(resolve, 2000));
          }
        }
      }
      case "gates": {
        const state = args.includes("--file") ? JSON.parse(readFileSync(option(args, "--file"), "utf8")) : await get();
        const gates = durationGates(state);
        console.log(`Qualified   Total ${hhmmss(gates.total.qualifiedMs)} / 24:00:00   remaining ${hhmmss(gates.total.remainingMs)}`);
        for (const [name, label] of PLATFORMS)
          console.log(`            ${pad(label, 12)}${hhmmss(gates[name].qualifiedMs)} / 23:00:00   remaining ${hhmmss(gates[name].remainingMs)}`);
        return gates.met ? 0 : 1;
      }
      case "capture-stopped": {
        const state = await get();
        const running = PLATFORMS.filter(([name]) => state.desktop?.[name]?.captureRunning).map(([, label]) => label);
        if (running.length) console.log(`Observation is still running on: ${running.join(", ")}`);
        return running.length ? 1 : 0;
      }
      case "checkpoint": {
        const result = await call(base, "POST", "/api/shadow-live/checkpoint");
        if (!result.ok || !result.json) {
          console.error(`Checkpoint failed: ${stateError(result)}`);
          return 1;
        }
        const s = result.json;
        const gates = durationGates(s);
        console.log("CHECKPOINT WRITTEN");
        console.log(`  run          ${s.runId}`);
        console.log(`  commit       ${s.commitSha}`);
        console.log(`  health       ${s.health}   acceptance ${s.acceptance}`);
        console.log(`  qualified    total ${hhmm(gates.total.qualifiedMs)}  CapitalBear ${hhmm(gates.capitalbear.qualifiedMs)}  IQ Option ${hhmm(gates.iqoption.qualifiedMs)}`);
        console.log(`  restarts     ${s.engineRestarts} (unclean ${s.uncleanRestarts})`);
        console.log(`  event log    ${(s.eventBytes / 1048576).toFixed(1)} MB`);
        return 0;
      }
      case "verify-storage": {
        for (let call_ = 1; call_ <= 200; call_++) {
          const result = await call(base, "POST", "/api/shadow-live/verify-storage");
          if (result.status === 429) {
            console.log("Engine busy; stop observation if it is still running. Retrying in 5 s…");
            await new Promise((resolve) => setTimeout(resolve, 5000));
            continue;
          }
          if (!result.ok || !result.json) {
            console.error(`Storage verification failed: ${stateError(result)}`);
            return 1;
          }
          const audit = result.json.storageAudit ?? {};
          console.log(`  audit call ${call_}: ${count(audit.parquetFiles)} files, ${count(audit.parquetRows)} rows verified, ${audit.remainingFiles ?? "?"} remaining, corruption ${audit.corruption}`);
          if (audit.complete || !audit.resumable || audit.corruption !== false) {
            console.log(`  storageVerified ${result.json.storageVerified}`);
            return result.json.storageVerified ? 0 : 1;
          }
        }
        console.error("Storage audit did not finish after 200 calls.");
        return 1;
      }
      case "export": {
        const state = await get();
        const folder = option(args, "--dir", path.join(repository, ".runtime"));
        mkdirSync(folder, { recursive: true });
        const stamp = new Date().toISOString().replace(/[:.]/g, "-");
        const target = path.join(folder, `phase14-final-${state.runId}-${stamp}.json`);
        const text = JSON.stringify(state, null, 2) + "\n";
        writeFileSync(target, text);
        console.log(target);
        console.log(`sha256 ${createHash("sha256").update(text).digest("hex")}`);
        return 0;
      }
      case "readiness": {
        const state = args.includes("--file") ? JSON.parse(readFileSync(option(args, "--file"), "utf8")) : await get();
        const readiness = finishReadiness(state);
        if (readiness.ready) {
          console.log("READY FOR FINAL REVIEW");
          console.log("Recorder acceptance is COMPLETE. Phase 14 is still NOT closed until the final review.");
          return 0;
        }
        console.log(`NOT READY FOR FINAL REVIEW (acceptance ${readiness.acceptance})`);
        for (const reason of readiness.reasons) console.log(`  - ${reason}`);
        return 1;
      }
      case "restart-questions": {
        for (const [field, question] of RESTART_QUESTIONS) console.log(`${field}\t${question}`);
        return 0;
      }
      case "verify-restart": {
        const answers = JSON.parse(option(args, "--answers", "{}"));
        const fields = RESTART_QUESTIONS.map(([field]) => field);
        if (fields.some((field) => typeof answers[field] !== "boolean") || Object.keys(answers).length !== fields.length) {
          console.error("Every restart observation needs an explicit yes or no answer.");
          return 1;
        }
        const result = await call(base, "POST", "/api/shadow-live/verify-restart", answers);
        if (!result.ok || !result.json) {
          console.error(`Restart verification failed: ${stateError(result)}`);
          return 1;
        }
        console.log(`restartVerified   ${result.json.restartVerified}`);
        console.log(`executionVerified ${result.json.executionVerified}`);
        if (!result.json.restartVerified && result.json.engineRestarts !== 1)
          console.log(`The recorder needs exactly one controlled restart; this run has ${result.json.engineRestarts}.`);
        return result.json.restartVerified && result.json.executionVerified ? 0 : 1;
      }
      case "auto-sync-pending": {
        const state = await get();
        for (const [name, label] of PLATFORMS) {
          const platform = state.platforms?.[name] ?? {};
          const assets = (state.desktop?.[name]?.slots ?? []).map((slot) => `slot ${slot.slotId}: ${slot.assetName}`).join(";");
          console.log(`${name}\t${label}\t${platform.autoSyncAppliedChanges ?? 0}\t${platform.unexpectedAutoSyncChanges === null ? "PENDING" : "OK"}\t${assets}`);
        }
        return 0;
      }
      case "verify-auto-sync": {
        const body = {
          platform: option(args, "--platform"),
          reviewedAppliedChanges: Number(option(args, "--reviewed")),
          unexpectedAutoSyncChanges: Number(option(args, "--unexpected")),
        };
        const result = await call(base, "POST", "/api/shadow-live/verify-auto-sync", body);
        if (result.status === 409) {
          console.error("Auto Sync applied another change during the review. Run review-auto-sync.sh again.");
          return 1;
        }
        if (!result.ok || !result.json) {
          console.error(`Auto Sync review failed: ${stateError(result)}`);
          return 1;
        }
        console.log(`${body.platform}: unexpectedAutoSyncChanges ${result.json.platforms?.[body.platform]?.unexpectedAutoSyncChanges}`);
        return 0;
      }
      default:
        console.error("usage: operator.mjs status|gates|checkpoint|verify-storage|export|readiness|verify-restart|… [--url URL]");
        return 64;
    }
  } catch (error) {
    if (error.http) {
      console.error(error.message);
      return error.http === 409 ? 3 : 2;
    }
    return unreachable(base, error);
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  process.exit(await main(process.argv.slice(2)));
}
