# Contributing

## Work locally

Follow the platform-specific setup in [README.md](README.md), then branch from an up-to-date `main`:

```bash
git switch main
git pull --ff-only
git switch -c feat/short-description
```

Keep changes focused and leave `main` buildable. Before opening a pull request, run:

```bash
npm run lint
npm run typecheck
npm test
npm run build
```

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat(desktop): add independent workspace window
fix(engine): recover when the database is unavailable
test(shared): cover slot schema bounds
docs: clarify Windows setup
chore(ci): cache npm dependencies
```

Prefer a small series of reviewable commits. Do not mix unrelated formatting or refactors into a feature commit.

## Security and repository hygiene

Before staging files, inspect `git status --short` and `git diff --cached`. Never commit:

- passwords, API tokens, keys, cookies, or session secrets;
- `.env` files other than `.env.example` with placeholder values;
- embedded-browser profiles or local storage;
- SQLite databases, logs, screenshots, or local market datasets;
- dependency directories, coverage, packaged applications, or other generated binaries.

Use sanitized deterministic fixtures for tests. If a fixture could contain account, platform-session, or market data from a real environment, do not add it.

## Pull requests

Describe what changed, how it was tested, and any genuine limitation. CI must pass lint, type checking, automated tests, and the build before merge. Never describe a mocked, simulated, unsupported, or incomplete path as production-ready.

