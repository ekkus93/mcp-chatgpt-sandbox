---
name: lint-n-test
description: Lint the files with Ruff and run the full pytest suite. Use when the user invokes /lint-n-test.
model: haiku
disable-model-invocation: true
allowed-tools:
  - Bash
---

# Lint and test

Lint with Ruff, then run the whole test suite. Report what failed.

Run every command through `uv run` — never a bare `ruff` or `pytest` from PATH. A
PATH-resolved tool can be a different version with a different verdict (a bare ruff 0.12.0
once rewrote an import block that the project-pinned ruff then rejected in CI). Add
`--no-sync` so a lint run never reinstalls packages as a side effect.

## Step 1: lint

```bash
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
```

If either fails, apply the automatic fixes and show what changed:

```bash
uv run --no-sync ruff check . --fix
uv run --no-sync ruff format .
git diff --stat
```

Re-run both checks afterwards. Report anything Ruff could not fix itself — those need a
real edit, so name the file and line rather than guessing at a fix.

## Step 2: test preconditions

Check these before running pytest. The suite is Docker-backed, and **any skip is converted
into a session failure** (`tests/_skip_policy.py`), so a missing precondition looks exactly
like a broken test:

```bash
docker info >/dev/null 2>&1 && echo "docker: ok" || echo "docker: UNREACHABLE"
docker image inspect mcp-chatgpt-sandbox-test-s7:24.04 >/dev/null 2>&1 \
  && echo "test image: ok" || echo "test image: MISSING"
```

- Docker unreachable → the suite cannot pass. Say so and stop; do not run it.
- Test image missing → build it, then continue:
  `docker build -t mcp-chatgpt-sandbox-test-s7:24.04 docker/default/`

Then note what else is driving Docker:

```bash
gh run list --limit 3
pgrep -af 'pytest|mcp-chatgpt-sandbox serve' | grep -v pgrep
```

CI runs on a **self-hosted runner on this same machine**. Overlapping it is **safe but
slower**: every test context has its own namespace (G3R-015) and every absence/count
assertion is scoped to it (PSCR-010), so concurrent runs no longer adopt each other's
containers. Record an overlap in the report and carry on. Avoid running against a daemon
serving real workspaces from a live `serve` — protected by design, but not demonstrated.

## Step 3: run all tests

```bash
uv run --no-sync pytest
```

This is Docker-heavy and takes several minutes. Let it finish; do not narrow it to a subset
unless the user asks.

## Step 4: report

State the lint result and the test result plainly, with counts. For failures, quote the real
error and name the file and line. Separate the three kinds, because they need different
responses:

- a genuine code defect,
- a structural guard tripping — module line budgets, the setup presentation manifest,
  acceptance-criterion coverage, `docs/TOOLS.md` drift, schema drift — which fails on
  structure rather than behavior and usually means a manifest or doc needs a matching entry,
- a precondition problem from Step 2, or a **collision** — which since PSCR-010 means the test
  namespacing has regressed, and is a real defect rather than an environmental one.

Never make a failure disappear by adding a skip, a retry, or a longer timeout. A skip in this
suite means Docker was unreachable, and retries hide real container collisions.

This skill deliberately omits strict mypy. Use `/gates` for the full CI set
(`ruff format --check`, `ruff check`, `mypy src`, `pytest --cov`).
