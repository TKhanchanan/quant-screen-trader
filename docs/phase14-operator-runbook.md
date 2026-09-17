# Phase 14 operator runbook — the real 24-hour soak

This runbook is for the person who runs the real Phase 14 shadow-live soak on a Mac.
Follow it step by step. You do not need an AI assistant, Codex or ChatGPT open while the soak runs.

> **What the soak proves.** QuantScreen Trader, the quant engine and both real broker sessions
> can run for a day and produce trustworthy Phase 14 evidence. It **cannot** be replaced by the
> accelerated rehearsal (`npm run rehearsal:phase14`). The rehearsal checked the logic in minutes;
> only this soak checks real Electron, real brokers, real OCR and a real Mac over time.
>
> **Phase 14 stays open** until this soak finishes, the final JSON is reviewed and CI is green.
> Do not edit `README.md` or `docs/evidence/` yourself.

## 0. Quick reference

| When | Command | What it does |
| --- | --- | --- |
| Before starting | `./scripts/phase14/01-preflight.sh` | Checks commit, tools, port 8765, disk, power; runs lint, typecheck, tests, build |
| Start | `./scripts/phase14/02-start.sh` | Starts the app with Phase 14 recording, keeps the Mac awake, saves the run id |
| Any time | `./scripts/phase14/03-status.sh` | Read-only dashboard: time done, time left, capture, hard gates |
| Any time (optional) | `./scripts/phase14/04-checkpoint.sh` | Writes a checkpoint; capture keeps running |
| After 6–12 qualified hours | `./scripts/phase14/05-restart.sh` then `05-restart.sh --resume` | The one controlled restart |
| After the restart | `./scripts/phase14/verify-restart.sh` | You answer 10 yes/no questions about what you checked |
| Only if status says so | `./scripts/phase14/review-auto-sync.sh` | You review Auto Sync changes against the real charts |
| At the end | `./scripts/phase14/06-finish.sh` | Refuses until 24h/23h/23h; then checkpoint, storage audit, final JSON |

Run the commands from the repository folder, for example:

```bash
cd ~/Downloads/quant
```

**The official targets never change:** total qualified capture **24:00** or more,
CapitalBear **23:00** or more, IQ Option **23:00** or more.
"Qualified" time only counts while capture really works. Idle time, login time, restart downtime
and time with an unidentified slot do not count, so the soak takes longer than 24 wall-clock hours.

## 1. Before the run

Do all of these before `02-start.sh`:

1. **Plug the Mac into power.** Keep it plugged in for the whole run.
2. **Plan for no sleep.** `02-start.sh` runs `caffeinate` for exactly as long as the app runs
   (section 2). Do not close the lid.
3. **Keep the network stable.** Use a connection you trust for a whole day.
4. **Do not check out another branch, `git pull` or change files** in this folder until the run
   is finished. The run is tied to one commit (`TESTED_HEAD`).
5. **Do not edit application code** during the run.
6. **Do not run another copy of QuantScreen Trader** (not the packaged app, not a second `npm run dev`).
7. **Keep at least 5 GB free disk space.** The preflight checks this.
8. You will **log in to both brokers yourself** inside the app. The app never asks for passwords.
9. Arrange the **nine charts you intend to use** in each broker, the same way you normally trade.
10. You will run **Sync Assets** on both platforms.
11. You will run **Probe Prices** on both platforms.
12. You will press **Start observation** on both platforms.

## 2. Keep the Mac awake (macOS)

You do not need to change permanent system settings.

- `02-start.sh` starts `caffeinate -dimsu -w <app process>`. While QuantScreen Trader runs, the Mac
  does not sleep, the display does not sleep and disks stay awake. When the app quits,
  `caffeinate` stops by itself.
- If you ever start the app another way, run this in a separate Terminal window and leave it open:

  ```bash
  caffeinate -dimsu
  ```

  Stop it with `Ctrl+C` when the run is finished.
- Check once in **System Settings → Lock Screen** that the screen saver and "require password"
  will not cover the app during the night. If you change anything there, you can change it back
  after the run.
- Keep both platform workspace windows **open and not minimized**. Other windows may overlap them.

## 3. Start the run

### 3.1 Preflight

```bash
./scripts/phase14/01-preflight.sh
```

It must end with **PREFLIGHT PASSED**. It takes several minutes because it runs lint, typecheck,
tests and build. Fix every `FAIL` line first. `WARN` lines are advice (for example, not on AC power).

### 3.2 Start

```bash
./scripts/phase14/02-start.sh
```

The script:

- refuses to start if the working tree has changes, port 8765 is in use, another copy is running
  or an old run state exists (use `--new-run` only when you really want to abandon that old run);
- sets `QST_SHADOW_LIVE=1` and `QST_COMMIT_SHA=<current commit>`, removes `QST_SHADOW_LIVE_RUN_ID`,
  and does **not** change `QST_DATA_DIR`;
- starts `npm run dev` **detached** with its log in `.runtime/phase14-app.log`, so **closing the
  Terminal window does not stop the app**;
- waits for the engine, then prints `TESTED_HEAD` and `RUN_ID` and saves them in
  `.runtime/phase14-current-run` (a local file that Git ignores).

Write down the `RUN_ID` and `TESTED_HEAD` it prints.

### 3.3 In the app — CapitalBear

1. Log in manually in the CapitalBear browser.
2. Make sure the nine charts are visible.
3. Make sure the portfolio panel (**พอร์ตทั้งหมด / Total portfolio**) is **closed**. The app closes
   it when the page loads; if it is still open, press **Close Portfolio Panel**. Never continue with
   the panel covering the charts.
4. Press **Sync Assets**. Every enabled slot should show **DETECTED** with the right instrument.
5. Press **Probe Prices**. Every enabled slot should show a price.
6. Press **Start observation**. The status line should show **Engine receiving**.

### 3.4 In the app — IQ Option

1. Log in manually in the IQ Option browser.
2. Make sure the nine charts are visible.
3. Press **Sync Assets** and check each slot.
4. Press **Probe Prices** and check each slot shows a price.
5. Press **Start observation**.

Leave **Auto Sync Assets** unticked for now (section 6). Keep the default **Sampling** targets.

## 4. Execution setting during the soak

**For the official soak, execution stays OFF and disarmed in both workspaces. Do not arm anything.**

Why not use PAPER during the soak:

- In this application PAPER mode only evaluates boards while the executor is **armed**, and the
  **Arm** button is only enabled when the mode is **AUTO**. Selecting AUTO and pressing Arm sends
  real orders. There is no safe way to arm PAPER from the panel.
- The Phase 14 recorder treats **any armed executor** (PAPER or AUTO) as an execution-safety
  failure, and the restart attestation asks whether execution stayed disarmed. An earlier real
  smoke run failed for exactly this reason.
- The PAPER / WOULD_PRESS decision pipeline was exercised instead by the accelerated rehearsal,
  which drives the real `ExecutionManager` in PAPER mode through every gate and asserts it never
  pressed anything (`artifacts/phase14-rehearsal/rehearsal-summary.json` when you run it).

How to confirm, by looking only (open it with **เปิดแผง AUTO / ตั้งเป้ากำไร-ขาดทุน** on each workspace):

| Look at | Must show |
| --- | --- |
| **โหมด** (mode) | **OFF — ไม่ส่งอะไรเลย** |
| Status next to the buttons | **ยังไม่พร้อม** (never **พร้อมยิง (ARMED)**) |
| **ติดอยู่ที่:** (blocked reasons) | includes **โหมดยังปิดอยู่ (OFF)** and **ยังไม่ได้ Arm** |
| **รายการออเดอร์** (tickets) | **(0)** and **ยังไม่มีรายการ** |
| **ออเดอร์ชั่วโมงนี้** | **0/…** |
| `03-status.sh` → Execution | **Armed ever NO**, **Real broker presses 0** |

Never press **Arm**, never choose **AUTO**, never press **ทดสอบกดครบ … ปุ่ม…** (that button
presses every real broker control). You do not need **วัดตำแหน่งปุ่ม** for the soak either.

## 5. After 10 minutes

```bash
./scripts/phase14/03-status.sh
```

Expected:

- **Engine AVAILABLE**
- **CapitalBear ACTIVE** and **IQ Option ACTIVE**
- **Qualified time** above zero and **Increasing now: YES**
- **Queue** max at most 180, **Queue unbounded NO**
- **Causality violations 0**, **Context contamination 0**
- **Event log** a few hundred KB at most, with many hours left

If qualified time is **not** increasing, check in this order:

1. **Observation running?** The workspace button should say **Stop observation** (meaning it is running).
2. **Sync current?** If a slot says **UNVERIFIED**, **Uncertain** or `TAB: identity is uncertain`,
   press **Sync Assets**. A slot that cannot be identified stops qualified time for that platform.
3. **Browser visible?** The workspace window must be open, not minimized, not reloading.
4. **Engine health?** The status line should say **Engine receiving**. If it says **waiting** for
   more than a minute, check `.runtime/phase14-app.log`.
5. **Grid and calibration?** If the grid looks misaligned, use **Calibrate Chart Area**.
6. **Login?** If the broker logged you out, log in again, then Sync Assets and Probe Prices.

`03-status.sh` names the problem on each platform line, for example `observation stopped` or
`1 slot(s) not identified`.

## 6. After 1 hour

```bash
./scripts/phase14/03-status.sh
```

Confirm:

- both platform times went up by roughly the same amount;
- no slot is stuck unidentified (no slot starvation);
- queue still bounded, **Crash loop NO**;
- **Warnings** shows nothing that blocks acceptance (see section 11).

Now you may tick **Auto Sync Assets** on both workspaces.

If a later status shows **REVIEW PENDING** under Auto Sync, run:

```bash
./scripts/phase14/review-auto-sync.sh
```

Compare every slot the script lists with the instrument the broker chart really shows, then type
how many applied changes were unexpected. Type the real number. **Any unexpected Auto Sync change
fails the run.** A pending review keeps acceptance PENDING until you do it.

## 7. The controlled restart (exactly one)

Do it once, after roughly **6–12 qualified hours**, at a time you can watch it for 20 minutes.
The recorder needs **exactly one** controlled restart.

1. Step 1 (checkpoint and instructions):

   ```bash
   ./scripts/phase14/05-restart.sh
   ```

2. In **both** workspaces press **Stop observation**.
3. Quit QuantScreen Trader normally (**QuantScreen Trader → Quit**, or **Cmd+Q**). Do not force quit.
4. Wait until the script prints **fully closed (port 8765 free, no leftover process)**.
   If it reports a leftover process, write that down — it is an orphan engine.
5. Step 2 (relaunch the same run on the same commit):

   ```bash
   ./scripts/phase14/05-restart.sh --resume
   ```

   It refuses if the commit changed. It must print **Run … continued on …** with the same `RUN_ID`.
6. In the app, **check without changing anything first**:
   - both brokers still logged in;
   - **Asset Setup** shows the same assets and enabled slots;
   - the same calibration is active and the grid is aligned;
   - your asset presets are still listed;
   - the execution panels still show **OFF** and **ยังไม่พร้อม**.
7. On both platforms: **Sync Assets**, **Probe Prices**, **Start observation**.
8. Then answer honestly:

   ```bash
   ./scripts/phase14/verify-restart.sh
   ```

   Answer **no** to anything you did not check or that was not true. Nothing is pre-filled.
9. Run `./scripts/phase14/03-status.sh` and confirm **Restart … VERIFIED** and time increasing again.

## 8. Overnight

Nothing AI-related needs to stay open. Only these must keep running:

- the Mac, awake and on power;
- QuantScreen Trader (started detached by `02-start.sh` or `05-restart.sh --resume`);
- both broker sessions, logged in;
- observation on both platforms;
- the quant engine (the app starts and owns it).

You may close the Terminal window you used for status checks. The app was started detached, so
closing Terminal does not stop it. Do **not** quit the app, log out of macOS or restart the Mac.

## 9. Morning check

```bash
./scripts/phase14/03-status.sh
```

If one broker is behind (for example CapitalBear 22:10 while IQ Option is 23:40), **keep the run
going**. Do not stop at 24 wall-clock hours. Stop only when all three are true:

- **Total ≥ 24:00**
- **CapitalBear ≥ 23:00**
- **IQ Option ≥ 23:00**

## 10. Finish

1. Confirm the durations:

   ```bash
   ./scripts/phase14/03-status.sh
   ```

2. In **both** workspaces press **Stop observation**. Wait 10 seconds.
3. Run:

   ```bash
   ./scripts/phase14/06-finish.sh
   ```

   - If a target is not reached it prints the remaining time, sends nothing and exits. Start
     observation again and wait.
   - If observation is still running it tells you to stop it first.
   - Otherwise it asks you to confirm, writes a checkpoint, runs the storage audit (several
     minutes; it resumes in batches of up to 10,000 files) and saves the final state as
     `.runtime/phase14-final-<runId>-<time>.json`.
4. Read the storage lines. `storageVerified true` is required.
5. **READY FOR FINAL REVIEW** appears only when the recorder's acceptance is **COMPLETE**.
   Otherwise the script lists what is missing.
6. **Do not edit the final JSON.** Keep it and the script output. Give both to the reviewer
   (ChatGPT/Codex/Claude) for the final Phase 14 review. They decide whether it becomes
   `docs/evidence/phase14/shadow-live-acceptance.json` and whether Phase 14 closes.

Quit the app normally when you are done.

## 11. Troubleshooting

"Continue" means keep the same run. "New run" means quit normally, then
`./scripts/phase14/02-start.sh --new-run` and start again from section 3.

| Situation | What to do | Run |
| --- | --- | --- |
| Qualified time not increasing | Follow the checklist in section 5 | Continue |
| One platform stopped (INACTIVE) | Read the reason on its status line; Sync Assets / log in / Start observation | Continue |
| Broker login expired | Log in again in that workspace, Sync Assets, Probe Prices, Start observation if stopped | Continue |
| Engine unavailable for a minute or two | Wait; check `.runtime/phase14-app.log` | Continue |
| Engine exited (the app log shows `Quant engine unavailable … exit code`) | The app does not restart the engine and the recorder could not write its final checkpoint, so resuming would be an unclean restart | New run |
| Queue growing toward 180 | Usually a slow engine; wait 5 minutes. `Queue unbounded YES` means the run failed | Continue / New run |
| Asset changed unexpectedly on a chart | Put the right instrument back in the broker, then Sync Assets | Continue |
| Auto Sync changed a name | Run `review-auto-sync.sh` and compare with the real charts; any unexpected change fails the run | Continue / New run |
| Mac slept | Wake it, check both logins and observation; time during sleep does not count | Continue |
| App crashed or was force-quit | The recorder marks an unclean restart; it can never be accepted | New run |
| A second restart happened | Only one controlled restart can be verified | New run |
| Restart created a new runId | You relaunched without `05-restart.sh --resume`; the old run cannot continue | New run |
| Commit changed (`03-status.sh` warns, or resume refuses) | Check out `TESTED_HEAD` again; a different commit cannot continue the run | Continue if unchanged app; otherwise New run |
| Status shows `FAILS THE RUN` next to a hard gate | Stop, keep the log and `03-status.sh --json` output for review | New run |
| Warning `MISSING_DECISION_LINEAGE`, `MISSING_CANDLE_DEPENDENCY`, `POLICY_NOT_SHADOW`, `POLICY_DECISION_UNAVAILABLE`, `POLICY_SERVICE_ERROR`, `PAPER_DISABLED`, `NON_LIVE_INPUT`, `BUILD_IDENTITY_UNKNOWN` or `UNCLEAN_RESTART_EVIDENCE_GAP` | These block acceptance; report them for review | New run after review |
| Event log projected to run out before the targets | Report it with `03-status.sh --json`; the run will fail at the cap | New run after review |

## 12. When the run is invalid

Hard invalidation — the run cannot be accepted; start a new run after the problem is understood:

- a different commit was resumed or the run was restarted into a new `runId`;
- **Causality violations** above 0;
- **Context contamination** above 0;
- storage corruption (`storageCorruption true`);
- recorder overflow (**Event overflow YES**) or recorder errors;
- engine crash loop (**Crash loop YES**);
- unbounded queue (**Queue unbounded YES**);
- broker capture that never recovers, so a platform cannot reach 23:00;
- any broker order or entry-control press, or execution armed (**Armed ever YES**,
  **Real broker presses** above 0);
- an unclean restart or more than one restart;
- an unexpected Auto Sync change.

Minor issues do **not** invalidate the run:

- one UNCERTAIN sample or one OCR miss;
- one dropped batch or an HTTP 429;
- NO_OPPORTUNITY boards, or zero paper trades;
- a short, recoverable network hiccup;
- health **DEGRADED** because of normal uncertainty.

## 13. AI assistants and token usage

- The 24-hour soak runs **locally**: QuantScreen Trader, the Python quant engine and the Phase 14
  recorder. **No ChatGPT, Codex or Claude session needs to stay open**, and nothing here calls one.
- Tokens are used only when you talk to an assistant.
- Recommended workflow:
  1. **Assistant, before the soak:** build and verify the rehearsal, scripts and this runbook (done).
  2. **You, alone:** run the soak with this runbook.
  3. **Assistant, after the soak:** give it the final JSON and the `06-finish.sh` output (or
     `03-status.sh --json` if something failed) for review and Phase 14 closure.

## 14. Reference

- Recorder details, endpoints and acceptance rules: [Shadow-live validation](shadow-live.md).
- What the accelerated rehearsal covers: [Shadow-live validation → Accelerated rehearsal](shadow-live.md#accelerated-rehearsal).
- Local files this runbook creates (all ignored by Git): `.runtime/phase14-current-run`,
  `.runtime/phase14-app.log`, `.runtime/phase14-final-*.json`, `.runtime/preflight-*.log`.
- Recorder evidence written by the engine: `~/Library/Application Support/QuantScreenTrader/phase14/<runId>/`.
