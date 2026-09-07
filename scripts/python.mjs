import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const repositoryRoot = fileURLToPath(new URL("../", import.meta.url));
const envFile = path.join(repositoryRoot, ".env");

if (existsSync(envFile)) {
  process.loadEnvFile(envFile);
}

const localPython =
  process.platform === "win32"
    ? path.join(repositoryRoot, "services", "quant-engine", ".venv", "Scripts", "python.exe")
    : path.join(repositoryRoot, "services", "quant-engine", ".venv", "bin", "python");

const candidates = [
  process.env.QST_PYTHON_EXECUTABLE
    ? { command: process.env.QST_PYTHON_EXECUTABLE, prefix: [] }
    : null,
  existsSync(localPython) ? { command: localPython, prefix: [] } : null,
  ...(process.platform === "win32"
    ? [
        { command: "py", prefix: ["-3.12"] },
        { command: "python", prefix: [] },
      ]
    : [
        { command: "python3", prefix: [] },
        { command: "python", prefix: [] },
      ]),
].filter(Boolean);

for (const candidate of candidates) {
  const result = spawnSync(candidate.command, [...candidate.prefix, ...process.argv.slice(2)], {
    cwd: repositoryRoot,
    env: process.env,
    stdio: "inherit",
  });

  if (result.error?.code === "ENOENT") {
    continue;
  }

  if (result.error) {
    console.error(`Unable to start Python: ${result.error.message}`);
    process.exit(1);
  }

  process.exit(result.status ?? 1);
}

console.error(
  "Python 3.12+ was not found. Create services/quant-engine/.venv or set QST_PYTHON_EXECUTABLE.",
);
process.exit(1);

