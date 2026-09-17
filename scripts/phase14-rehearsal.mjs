#!/usr/bin/env node
// Phase 14 accelerated rehearsal. REHEARSAL ONLY — NOT REAL LIVE ACCEPTANCE.
//
//   npm run rehearsal:phase14            full 25h+ virtual run (developer machine, minutes)
//   npm run rehearsal:phase14 -- --smoke 40-minute virtual run with the same events
//   npm run rehearsal:phase14 -- --reuse-engine   rerun only the desktop half on an existing engine output
//
// 1. services/quant-engine/tests/phase14_rehearsal.py drives the production engine and recorder
//    on virtual time and exports what the desktop execution layer would have read.
// 2. apps/desktop/tests/rehearsal/phase14.rehearsal.ts replays that through the real
//    ExecutionManager in PAPER mode and exercises the real transport queue.
// 3. This script checks a real desktop telemetry payload against the engine's closed schema and
//    writes artifacts/phase14-rehearsal/rehearsal-summary.json.
//
// Output never goes to docs/evidence. The real Phase 14 acceptance still requires the real soak.
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repository = fileURLToPath(new URL("../", import.meta.url));
const args = process.argv.slice(2);
const smoke = args.includes("--smoke");
const reuseEngine = args.includes("--reuse-engine");
const outIndex = args.indexOf("--out");
const out = path.resolve(repository, outIndex >= 0 ? args[outIndex + 1] : "artifacts/phase14-rehearsal");
const LABEL = "REHEARSAL ONLY - NOT REAL LIVE ACCEPTANCE";

if (out.split(path.sep).join("/").includes("docs/evidence")) {
  console.error("Rehearsal output must never be written under docs/evidence.");
  process.exit(2);
}
mkdirSync(out, { recursive: true });
const stale = reuseEngine ? ["execution-summary.json", "rehearsal-summary.json"]
  : ["engine-summary.json", "execution-summary.json", "rehearsal-summary.json", "execution-timeline.jsonl.gz"];
for (const name of stale) rmSync(path.join(out, name), { force: true });

function run(label, command, commandArgs, options = {}) {
  console.log(`\n== ${label} ==`);
  const result = spawnSync(command, commandArgs, { stdio: "inherit", cwd: repository, ...options });
  if (result.error) {
    console.error(`${label}: ${result.error.message}`);
    return 1;
  }
  return result.status ?? 1;
}

const engineStatus = reuseEngine && existsSync(path.join(out, "engine-summary.json"))
  ? 0
  : run("engine rehearsal (Python)", process.execPath, [
      "scripts/python.mjs",
      "services/quant-engine/tests/phase14_rehearsal.py",
      "--scenario",
      smoke ? "smoke" : "full",
      "--out",
      out,
    ]);

let executionStatus = 1;
if (existsSync(path.join(out, "execution-timeline.jsonl.gz"))) {
  executionStatus = run(
    "execution rehearsal (desktop, PAPER)",
    process.execPath,
    [path.join(repository, "node_modules", "vitest", "vitest.mjs"), "run", "--config", "vitest.rehearsal.config.ts"],
    { cwd: path.join(repository, "apps", "desktop"), env: { ...process.env, QST_REHEARSAL_DIR: out } },
  );
}

const read = (name) => {
  try {
    return JSON.parse(readFileSync(path.join(out, name), "utf8"));
  } catch {
    return null;
  }
};
const engine = read("engine-summary.json");
const execution = read("execution-summary.json");

// Cross-runtime contract: a telemetry body the real desktop observer produced must pass the
// engine's closed schema, or the live recorder would refuse it.
let telemetryContract = { checked: false, accepted: false };
if (execution?.telemetrySample) {
  const samplePath = path.join(out, "telemetry-sample.json");
  writeFileSync(samplePath, JSON.stringify(execution.telemetrySample));
  const status = run("closed telemetry schema check", process.execPath, [
    "scripts/python.mjs",
    "-c",
    "import json,sys; from quant_engine.shadow_live_api import DesktopTelemetry; " +
      "DesktopTelemetry.model_validate(json.load(open(sys.argv[1]))); print('telemetry sample accepted')",
    samplePath,
  ]);
  telemetryContract = { checked: true, accepted: status === 0 };
}

const passed =
  engineStatus === 0 &&
  executionStatus === 0 &&
  engine?.engineResult === "PASS" &&
  execution?.executionResult === "PASS" &&
  telemetryContract.accepted;
const failedChecks = [
  ...(engine?.checks ?? []).filter((check) => !check.passed).map((check) => `engine: ${check.name}`),
  ...(execution?.checks ?? []).filter((check) => !check.passed).map((check) => `execution: ${check.name}`),
];
const totals = (key) =>
  (execution?.timeline?.platforms?.capitalbear?.[key] ?? 0) + (execution?.timeline?.platforms?.iqoption?.[key] ?? 0);
const summary = {
  label: LABEL,
  notRealLiveAcceptance: true,
  result: passed ? "REHEARSAL PASS" : "REHEARSAL FAIL",
  realPhase14: {
    acceptance: "PENDING",
    reason: "Rehearsal validates logic and instrumentation only. The real 24h soak is required.",
    phase14: "NOT CLOSED",
  },
  source: engine?.source ?? "SYNTHETIC_REHEARSAL",
  scenario: engine?.scenario ?? null,
  commitSha: engine?.commitSha ?? null,
  worktreeClean: engine?.worktreeClean ?? null,
  recorderVersion: engine?.recorderVersion ?? null,
  virtualStartedAt: engine?.virtualStartedAt ?? null,
  virtualFinishedAt: engine?.virtualFinishedAt ?? null,
  virtualDuration: engine?.virtualDuration ?? null,
  simulatedTotalQualified: engine?.simulated?.totalQualified ?? null,
  simulatedCapitalBear: engine?.simulated?.capitalbear ?? null,
  simulatedIQ: engine?.simulated?.iqoption ?? null,
  rehearsalAcceptance: engine?.recorder?.acceptance ?? null,
  causality: engine?.causality ?? null,
  contextContamination: engine?.context ?? null,
  queue: { engineSide: engine?.queue ?? null, desktopTransport: execution?.queue ?? null },
  restart: engine?.restart ?? null,
  storage: engine?.storage ?? null,
  eventBudget: engine?.eventBudget ?? null,
  recorderLimits: engine?.recorderLimits ?? null,
  negativeFixtures: engine?.negativeFixtures ?? null,
  acceptanceGate: engine?.acceptanceGate ?? null,
  executionPaper: execution
    ? {
        mode: execution.mode,
        boardsSeen: execution.boardsSeen,
        boardsEvaluated: execution.boardsEvaluated,
        paperTickets: execution.paperTickets,
        wouldPressScenarios: execution.wouldPressGateScenarios,
        pressCallsInPaper: execution.pressCalls,
        realBrokerPresses: execution.realBrokerPresses,
        boardRefusals: {
          capitalbear: execution.timeline.platforms.capitalbear.boardRefusals,
          iqoption: execution.timeline.platforms.iqoption.boardRefusals,
        },
        stateBlockedTicks: {
          capitalbear: execution.timeline.platforms.capitalbear.stateBlockedTicks,
          iqoption: execution.timeline.platforms.iqoption.stateBlockedTicks,
        },
        directionMismatch: totals("directionMismatch"),
        slotMismatch: totals("slotMismatch"),
        assetMismatch: totals("assetMismatch"),
        staleContextTickets: totals("staleContextTickets"),
        duplicateTickets: totals("duplicateTickets"),
        oracleMismatches: totals("oracleMismatches"),
      }
    : null,
  telemetryContract,
  failedChecks,
};
writeFileSync(path.join(out, "rehearsal-summary.json"), JSON.stringify(summary, null, 2) + "\n");

console.log(`\n${LABEL}`);
console.log(`${summary.result} (${summary.scenario ?? "no scenario"})`);
console.log(
  `virtual ${summary.virtualDuration} · qualified total ${summary.simulatedTotalQualified} · ` +
    `CapitalBear ${summary.simulatedCapitalBear} · IQ Option ${summary.simulatedIQ}`,
);
if (summary.executionPaper) {
  console.log(
    `execution PAPER: boards evaluated ${summary.executionPaper.boardsEvaluated} · paper tickets ` +
      `${summary.executionPaper.paperTickets} · press calls ${summary.executionPaper.pressCallsInPaper}`,
  );
}
for (const name of failedChecks) console.log(`FAILED ${name}`);
console.log(`Real Phase 14: PENDING (not closed). Summary: ${path.relative(repository, path.join(out, "rehearsal-summary.json"))}`);
process.exit(passed ? 0 : 1);
