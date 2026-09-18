---
name: gates
description: Run this repository's four quality gates (ruff format, ruff lint, strict mypy, full pytest) in CI order after verifying the Docker preconditions the suite needs. Use before committing, when asked to verify or check changes, or when a test run failed for environmental rather than code reasons.
---

# Quality gates

Run the same checks CI runs, in CI order, and report precisely which one failed.

Every command goes through `uv run`. Never a bare `ruff`/`mypy`/`pytest` from PATH — a
PATH-resolved tool can be a different version with a different verdict (a bare ruff 0.12.0
once rewrote an import block that the project-pinned ruff then rejected in CI).

## Step 1: preconditions

The suite is Docker-heavy and *any* pytest skip is converted into a session failure
(`tests/_skip_policy.py`), so a missing precondition looks like a test failure. Check these
before running anything, and report a missing one as a precondition problem rather than a
code defect:

```bash
docker info >/dev/null 2>&1 && echo "docker: ok" || echo "docker: UNREACHABLE"
docker image inspect mcp-chatgpt-sandbox-test-s7:24.04 >/dev/null 2>&1 \
  && echo "test image: ok" || echo "test image: MISSING"
```

- Docker unreachable → the suite cannot pass. Say so; do not run it.
- Test image missing → build it: `docker build -t mcp-chatgpt-sandbox-test-s7:24.04 docker/default/`.
  The tag must match `DEFAULT_IMAGE_TEST_TAG` in `tests/workspace/conftest.py`.
- Overlapping a CI run or another suite run is **safe but slower**: every test context has its
  own namespace (G3R-015) and every absence/count assertion is scoped to it (PSCR-010), so runs
  no longer adopt each other's containers. Note an overlap in your report; don't wait on it —
  **except** that Step 2 below runs `pytest --cov`, and a `--cov` run overlapping CI's own
  `--cov` run has twice been killed by the Claude Code background-task memory guard (no test
  failed). For this skill, wait for an idle runner before Step 2.
  Do avoid running against a Docker daemon that is serving real workspaces from a live
  `mcp-chatgpt-sandbox serve` — that case is protected by design but not demonstrated.

## Step 2: the gates

Run in this order and stop at the first failure — a formatting failure makes the later
output noisy and misleading:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest --cov --cov-report=term-missing
```

`mypy` is strict and covers `src/` only; `tests/` is excluded, so do not "fix" a mypy
complaint by editing tests.

## Step 3: report

State each gate's result plainly. For a failure, quote the actual error and name the file and
line. Distinguish the three cases explicitly:

- a real code defect,
- a structural guard tripping (module line budgets, the setup presentation manifest,
  acceptance-criterion coverage, `docs/TOOLS.md` drift, schema/`contract_version` drift) —
  these fail on structure, not behavior, and usually mean a manifest or doc needs the matching
  entry,
- a precondition problem from Step 1, or a **collision** — which since PSCR-010 means the test
  namespacing has regressed, and is a real defect rather than an environmental one.

Never resolve a failure by adding a skip, a retry, or a timeout increase. A skip in this suite
means Docker was unreachable, and retries hide real container collisions.
