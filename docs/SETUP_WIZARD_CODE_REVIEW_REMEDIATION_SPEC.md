# Setup Wizard Code-Review Remediation Specification

**Status:** Active remediation  
**Baseline:** `master` at `49d48611e392e11bceb7dd4ea81967733e86eb78`  
**Parent spec:** `docs/SETUP_WIZARD_SPEC.md`  
**Parent TODO:** `docs/SETUP_WIZARD_TODO.md`

## Purpose

The setup wizard is implemented and qualified by the parent WIZ track. This companion specification closes architectural and safety risks identified during review of both the wizard design and the Ralph Bridge setup application.

The remediation does **not** replace the parent wizard spec. It hardens implementation boundaries, resumable state, reviewed writes, diagnostics, external integration, and long-term drift detection.

## Required architecture

The Textual application is presentation/orchestration only. Production configuration semantics remain authoritative in `ServerConfig`, nested production models, `load_config()`, and existing repository/Docker/SSH/Git policy helpers.

Domain operations must be callable and testable without launching Textual. Presentation code must not directly parse or serialize YAML, implement atomic writes, reimplement production validation, mutate repositories during verification, or persist raw credentials.

The implementation must maintain explicit configuration ownership. Every top-level `ServerConfig` field and every safety-sensitive nested field must have a disposition: managed, preserved, derived, or intentionally unsupported. Exhaustiveness tests must fail when production configuration changes without a wizard decision.

## State and invalidation

Resumable state is strict, versioned, non-secret, bounded, UTF-8, and owner-only. Raw tokens, passwords, private-key content, API keys, and equivalent credentials must not be serialized. Process-local credentials must be reacquired after resume, and downstream completion must be invalidated whenever an upstream choice changes.

At minimum, storage, Docker, repositories, SSH/GitHub, Git policy, runtime/resource, ChatGPT/tunnel, config review, and final validation dependencies must be explicit and test-covered.

## Reviewed configuration transaction

All persistent production-config changes follow one path:

1. inspect the source safely;
2. parse with duplicate-key rejection;
3. validate through production semantics;
4. preserve valid non-owned settings;
5. materialize only reviewed fields;
6. validate and serialize the candidate;
7. reparse/revalidate serialized bytes;
8. bind review to source existence/digest and setup-state identity;
9. display a non-secret structured summary;
10. require explicit write authorization;
11. recheck source/state identity;
12. create a timestamped backup when replacing a file;
13. atomically replace the file;
14. byte-verify persisted content;
15. reload through production `load_config()`;
16. report success only after reload succeeds.

There is no `write anyway` path after a stale review or source-digest mismatch.

## Existing installations

Resume, Repair, Edit configuration, and Start fresh are distinct operations. Start fresh resets wizard progress only and must not delete repositories, database contents, audit history, Docker resources, SSH keys, known-hosts material, credentials, or operational logs.

## Repository and Git policy behavior

Repository CRUD is guided and verification is read-only. Verification must not alter Git config, remotes, branches, files, or credentials.

Recommended, Conservative, and Custom Git policy modes are projections of the real `GitPolicyConfig`, never a second policy engine. Custom mode must remain exhaustive as production fields evolve.

## Advanced configuration

Normal setup inherits production defaults. Merely exposing advanced controls must not materialize changes. Existing valid custom values outside the current edit must survive unrelated wizard updates.

## ChatGPT / Secure MCP Tunnel

The connection step remains optional. Runtime credentials stay process-local. Browser launch obeys `--no-browser`. Tunnel-profile creation or repair is reviewed before mutation. External ChatGPT UI completion is labeled as user-attested and never represented as machine PASS.

## Shared diagnostics

The final screen and `setup --check`/doctor consume one diagnostics engine and one result model. Unexpected failures are fail-closed. Mandatory unresolved checks block Finish. Optional/skipped/user-attested work remains visibly distinct from PASS.

## Silent-failure rules

The setup package must not swallow consequential failures, silently replace malformed config with defaults, automatically retry ambiguous writes, treat unavailable external verification as success, leak secret-bearing exception text, or ignore stale review/invalidation state.

Any surviving broad catch requires a fail-closed boundary rationale and permanent test coverage.

## TUI usability

The wizard must remain keyboard-operable, usable in narrow terminals, scrollable, and understandable without color alone. Consequential operations require explicit confirmation/review. Secret inputs and snapshots must remain masked.

## Qualification

Completion requires focused setup tests, Textual pilot tests, full Docker-backed pytest, Ruff, strict mypy, contract/schema/docs gates, an unsafe-fallback/secret-leak audit, one exact green candidate SHA, guarded merge, and fresh green validation on the resulting exact `master` SHA.
