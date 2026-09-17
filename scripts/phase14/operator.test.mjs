// Tests for the Phase 14 operator scripts. They run against a fake local engine: nothing here
// starts the app, touches a broker or reads the real data root.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import {
  BLOCKING_WARNINGS,
  durationGates,
  eventBudget,
  finishReadiness,
  formatStatus,
  hardGates,
  hhmm,
  hhmmss,
  RESTART_QUESTIONS,
} from "./operator.mjs";

const HOUR = 3_600_000;
const here = path.dirname(fileURLToPath(import.meta.url));
const repository = path.resolve(here, "../..");

function state(overrides = {}) {
  const platform = (ms) => ({
    captureDurationMs: ms,
    liveObservations: 10,
    observations: 10,
    boards: { COLLECTING: 5, PARTIAL: 1, READY: 0, NO_OPPORTUNITY: 4, INVALID: 0 },
    policyActions: { ALLOW: 0, WATCH: 0, SKIP: 9 },
    paperStates: { PENDING_ENTRY: 0, OPEN: 0, RESOLVED: 0, CANCELLED: 0, INVALID: 0 },
    autoSyncAppliedChanges: 0,
    unexpectedAutoSyncChanges: 0,
  });
  const desktop = (name) => ({
    platform: name,
    captureRunning: true,
    surfaceAvailable: true,
    engineAvailable: true,
    intervalMs: 1000,
    queueDepth: 2,
    slots: Array.from({ length: 9 }, (_, index) => ({
      slotId: index + 1,
      enabled: true,
      assetName: `ASSET ${index + 1}`,
      captureEligible: true,
      lastCaptureAttemptAt: Date.now() - 1500,
    })),
  });
  return {
    runId: "11111111-2222-4333-8444-555555555555",
    version: "qst-shadow-live-v2",
    commitSha: "a".repeat(40),
    health: "HEALTHY",
    acceptance: "PENDING",
    result: "DEGRADED",
    durationMs: 8 * HOUR,
    qualifiedCaptureDurationMs: 8 * HOUR + 14 * 60_000,
    platforms: { capitalbear: platform(8 * HOUR + 12 * 60_000), iqoption: platform(8 * HOUR + 14 * 60_000) },
    desktop: { capitalbear: desktop("capitalbear"), iqoption: desktop("iqoption") },
    causalityViolations: 0,
    crossContextContamination: 0,
    baselineMismatches: 0,
    recorderErrors: 0,
    unboundedQueue: false,
    evidenceTruncated: false,
    engineCrashLoop: false,
    executionArmed: false,
    unexpectedBrokerPresses: 0,
    storageCorruption: null,
    engineRestarts: 0,
    uncleanRestarts: 0,
    restartVerified: false,
    executionVerified: false,
    storageVerified: false,
    maxQueueDepth: 12,
    eventBytes: 3 * 1024 * 1024,
    warnings: [],
    ...overrides,
  };
}

describe("duration gates", () => {
  it("formats hours past 24 without wrapping", () => {
    assert.equal(hhmm(25 * HOUR + 10 * 60_000), "25:10");
    assert.equal(hhmmss(24 * HOUR - 1000), "23:59:59");
  });

  it("keeps 23:59:59 total PENDING and names one second remaining", () => {
    const gates = durationGates(state({ qualifiedCaptureDurationMs: 24 * HOUR - 1000,
      platforms: { capitalbear: { captureDurationMs: 23.5 * HOUR }, iqoption: { captureDurationMs: 23.5 * HOUR } } }));
    assert.equal(gates.total.met, false);
    assert.equal(gates.total.remainingMs, 1000);
    assert.equal(gates.met, false);
  });

  it("requires each broker to reach 23h even when the total reached 24h", () => {
    const gates = durationGates(state({ qualifiedCaptureDurationMs: 25 * HOUR,
      platforms: { capitalbear: { captureDurationMs: 23 * HOUR - 1000 }, iqoption: { captureDurationMs: 24 * HOUR } } }));
    assert.equal(gates.total.met, true);
    assert.equal(gates.capitalbear.met, false);
    assert.equal(gates.met, false);
    const met = durationGates(state({ qualifiedCaptureDurationMs: 24 * HOUR,
      platforms: { capitalbear: { captureDurationMs: 23 * HOUR }, iqoption: { captureDurationMs: 23 * HOUR } } }));
    assert.equal(met.met, true);
  });
});

describe("dashboard", () => {
  it("shows the run, remaining time, capture and every hard gate", () => {
    const text = formatStatus(state(), { testedHead: "a".repeat(40), savedRunId: state().runId, increasing: true });
    for (const expected of ["PHASE 14 LIVE SOAK", "Acceptance:", "PENDING", "Total        08:14 / 24:00",
      "CapitalBear  08:12 / 23:00", "IQ Option    08:14 / 23:00", "Total        15:46", "CapitalBear  14:48",
      "IQ Option    14:46", "ACTIVE", "Causality violations", "Context contamination", "Queue unbounded",
      "Event overflow", "Crash loop", "NOT YET VERIFIED", "Real broker presses        0", "Increasing now: YES"]) {
      assert.ok(text.includes(expected), `missing: ${expected}\n${text}`);
    }
    assert.ok(!text.includes("FAILS THE RUN"));
  });

  it("flags a different run, a different commit and a failed gate", () => {
    const text = formatStatus(state({ causalityViolations: 2, executionArmed: true }),
      { testedHead: "b".repeat(40), savedRunId: "other" });
    assert.ok(text.includes("DIFFERENT run"));
    assert.ok(text.includes("WARNING: TESTED_HEAD"));
    assert.equal(hardGates(state({ causalityViolations: 2 })).find((g) => g.name === "Causality violations").ok, false);
    assert.ok(text.includes("FAILS THE RUN"));
  });

  it("names warnings that block acceptance, matching the recorder's list", () => {
    const text = formatStatus(state({ warnings: ["DESKTOP_TELEMETRY_GAP", "MISSING_DECISION_LINEAGE"] }));
    assert.ok(text.includes("These block acceptance: MISSING_DECISION_LINEAGE"));
    const recorder = readFileSync(path.join(repository, "services/quant-engine/src/quant_engine/shadow_live.py"), "utf8");
    const listed = recorder.slice(recorder.indexOf("for code in ("), recorder.indexOf("self.data[\"acceptance\"]"));
    for (const code of BLOCKING_WARNINGS) assert.ok(listed.includes(`"${code}"`), code);
    assert.equal((listed.match(/"[A-Z_]+"/g) ?? []).length, BLOCKING_WARNINGS.length);
  });

  it("projects the event budget from the run's own rate", () => {
    const budget = eventBudget(state({ durationMs: 10 * HOUR, eventBytes: 10 * 1024 * 1024 }));
    assert.equal(budget.hoursLeft, 54);
  });

  it("is ready for review only when the recorder says COMPLETE", () => {
    assert.equal(finishReadiness(state()).ready, false);
    const pending = finishReadiness(state({ platforms: { capitalbear: { unexpectedAutoSyncChanges: null }, iqoption: {} } }));
    assert.ok(pending.reasons.some((reason) => reason.includes("not reviewed")));
    assert.equal(finishReadiness(state({ acceptance: "COMPLETE" })).ready, true);
  });

  it("asks every restart observation the endpoint requires, once", () => {
    const fields = RESTART_QUESTIONS.map(([field]) => field);
    assert.deepEqual([...fields].sort(), ["assetPresetsPersisted", "browserSessionPersisted", "calibrationPersisted",
      "configurationPersisted", "executionStayedDisarmed", "noBrokerPresses", "noOrphanEngine", "paperRestoredSafely",
      "policyJournalRestored", "sessionGuardRestoredWithoutUnlock"]);
  });
});

describe("operator scripts against a fake engine", () => {
  let server, port, current, requests, workspace, stateFile;
  before(async () => {
    requests = [];
    current = state();
    server = createServer((request, response) => {
      let body = "";
      request.on("data", (chunk) => { body += chunk; });
      request.on("end", () => {
        requests.push({ method: request.method, url: request.url, body: body ? JSON.parse(body) : null });
        response.setHeader("content-type", "application/json");
        if (request.method === "GET" && request.url === "/api/shadow-live/state") return response.end(JSON.stringify(current));
        if (request.method === "POST" && request.url === "/api/shadow-live/checkpoint") return response.end(JSON.stringify(current));
        if (request.method === "POST" && request.url === "/api/shadow-live/verify-restart")
          return response.end(JSON.stringify({ ...current, restartVerified: Object.values(JSON.parse(body)).every(Boolean), executionVerified: true }));
        response.statusCode = 404;
        response.end("{}");
      });
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    port = server.address().port;
    workspace = mkdtempSync(path.join(tmpdir(), "qst-phase14-operator-"));
    stateFile = path.join(workspace, "phase14-current-run");
    writeFileSync(stateFile, `RUN_ID=${current.runId}\nTESTED_HEAD=${"a".repeat(40)}\n`);
  });
  after(() => {
    server.close();
    rmSync(workspace, { recursive: true, force: true });
  });

  // Asynchronous on purpose: the fake engine lives in this process and must keep answering.
  const run = (name, args = [], input = "", env = {}) =>
    new Promise((resolve) => {
      const child = spawn("bash", [path.join(here, name), ...args], {
        cwd: repository,
        env: { ...process.env, QST_ENGINE_PORT: String(port), QST_PHASE14_STATE_FILE: stateFile, NO_COLOR: "1", ...env },
      });
      let stdout = "", stderr = "";
      child.stdout.on("data", (chunk) => { stdout += chunk; });
      child.stderr.on("data", (chunk) => { stderr += chunk; });
      child.on("close", (status) => resolve({ status, stdout, stderr }));
      child.stdin.end(input);
    });
  const script = (name, args = [], input = "") => run(name, args, input);

  it("every script parses under bash", () => {
    for (const name of ["lib.sh", "01-preflight.sh", "02-start.sh", "03-status.sh", "04-checkpoint.sh",
      "05-restart.sh", "06-finish.sh", "verify-restart.sh", "review-auto-sync.sh"]) {
      const result = spawnSync("bash", ["-n", path.join(here, name)], { encoding: "utf8" });
      assert.equal(result.status, 0, `${name}: ${result.stderr}`);
    }
  });

  it("03-status.sh is read-only and prints the dashboard", async () => {
    requests.length = 0;
    const result = await script("03-status.sh", ["--once"]);
    assert.equal(result.status, 0, result.stderr);
    assert.ok(result.stdout.includes("PHASE 14 LIVE SOAK"));
    assert.ok(result.stdout.includes("Remaining:"));
    assert.ok(requests.every((request) => request.method === "GET"));
  });

  it("04-checkpoint.sh posts one checkpoint and exits 0", async () => {
    requests.length = 0;
    const result = await script("04-checkpoint.sh");
    assert.equal(result.status, 0, result.stderr);
    assert.ok(result.stdout.includes("CHECKPOINT WRITTEN"));
    assert.deepEqual(requests.map((r) => `${r.method} ${r.url}`), ["POST /api/shadow-live/checkpoint"]);
  });

  it("06-finish.sh refuses early completion and sends nothing", async () => {
    requests.length = 0;
    current = state({ qualifiedCaptureDurationMs: 24 * HOUR - 1000,
      platforms: { capitalbear: { captureDurationMs: 23.9 * HOUR }, iqoption: { captureDurationMs: 23.9 * HOUR } } });
    const result = await script("06-finish.sh", [], "yes\n");
    assert.equal(result.status, 1);
    assert.ok(result.stdout.includes("NOT reached"), result.stdout);
    assert.ok(result.stdout.includes("00:00:01"), result.stdout);
    assert.ok(requests.every((request) => request.method === "GET"));
    current = state();
  });

  it("06-finish.sh refuses to finalize while observation is still running", async () => {
    requests.length = 0;
    current = state({ qualifiedCaptureDurationMs: 25 * HOUR,
      platforms: { capitalbear: { captureDurationMs: 24 * HOUR }, iqoption: { captureDurationMs: 24 * HOUR } } });
    const result = await script("06-finish.sh", [], "yes\n");
    assert.equal(result.status, 1);
    assert.ok(result.stdout.includes("Stop observation"), result.stdout);
    assert.ok(requests.every((request) => request.method === "GET"));
    current = state();
  });

  it("verify-restart.sh submits exactly the answers typed, never pre-filled", async () => {
    requests.length = 0;
    current = state({ engineRestarts: 1 });
    const answers = RESTART_QUESTIONS.map((_, index) => (index === 3 ? "no" : "yes")).join("\n");
    const result = await script("verify-restart.sh", [], `${answers}\nyes\n`);
    const post = requests.find((request) => request.method === "POST");
    assert.ok(post, result.stdout + result.stderr);
    assert.equal(post.url, "/api/shadow-live/verify-restart");
    assert.equal(post.body.assetPresetsPersisted, false);
    assert.equal(Object.values(post.body).filter((value) => value === true).length, RESTART_QUESTIONS.length - 1);
    assert.equal(result.status, 1, "a single 'no' leaves the restart unverified");
    current = state();
  });

  it("verify-restart.sh submits nothing when answers stop", async () => {
    requests.length = 0;
    current = state({ engineRestarts: 1 });
    const result = await script("verify-restart.sh", [], "yes\nmaybe\n");
    assert.notEqual(result.status, 0);
    assert.ok(requests.every((request) => request.method === "GET"));
    current = state();
  });

  it("05-restart.sh --resume refuses a commit that is not TESTED_HEAD", async () => {
    const wrong = path.join(workspace, "wrong-head");
    writeFileSync(wrong, `RUN_ID=${current.runId}\nTESTED_HEAD=${"0".repeat(40)}\n`);
    const result = await run("05-restart.sh", ["--resume"], "", { QST_PHASE14_STATE_FILE: wrong });
    assert.equal(result.status, 1);
    assert.ok(result.stdout.includes("is not TESTED_HEAD"), result.stdout);
  });

  it("02-start.sh --dry-run exports the Phase 14 environment and starts nothing", async () => {
    const fresh = path.join(workspace, "no-run-yet");
    const result = await run("02-start.sh", ["--dry-run"], "", { QST_PHASE14_STATE_FILE: fresh, QST_DATA_DIR: "",
      QST_SHADOW_LIVE_RUN_ID: "should-be-removed" });
    const head = spawnSync("git", ["rev-parse", "HEAD"], { cwd: repository, encoding: "utf8" }).stdout.trim();
    const clean = spawnSync("git", ["status", "--porcelain"], { cwd: repository, encoding: "utf8" }).stdout.trim() === "";
    if (!clean) {
      assert.equal(result.status, 1, "a dirty checkout must be refused");
      assert.ok(result.stdout.includes("Working tree has changes"));
      return;
    }
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.ok(result.stdout.includes("QST_SHADOW_LIVE=1"));
    assert.ok(result.stdout.includes(`QST_COMMIT_SHA=${head}`));
    assert.ok(result.stdout.includes("QST_SHADOW_LIVE_RUN_ID=<unset>"));
    assert.throws(() => readFileSync(fresh));
  });
});
