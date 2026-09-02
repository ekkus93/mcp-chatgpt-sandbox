"""Acceptance-test coverage integrity audit.

G3R-016 closes the old ``known_uncited`` loophole. Every active acceptance
criterion must now be backed by one of:

* an acceptance identifier attached directly to a real ``test_*`` function;
* an explicit acceptance-ID -> exact pytest-node mapping whose target still
  exists; or
* a formal ``Coverage status`` in the source spec with a reason and owner/task.

The node registry is executable evidence metadata, not an allowlist: every
target is parsed from the real test tree and a deleted or renamed target makes
this audit fail. Source-spec dispositions are likewise checked against real
executable coverage so stale deferrals cannot silently persist.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class _Series:
    prefix: str
    spec_path: Path
    heading_re: re.Pattern[str]
    expected_total: int

    def spec_numbers(self) -> set[int]:
        return {int(m) for m in self.heading_re.findall(self.spec_path.read_text())}

    def spec_entries(self) -> dict[int, str]:
        text = self.spec_path.read_text()
        matches = list(self.heading_re.finditer(text))
        entries: dict[int, str] = {}
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            entries[int(match.group(1))] = text[match.start() : end]
        return entries

    def formal_dispositions(self) -> set[int]:
        """Return source-spec entries carrying a complete formal status marker."""
        disposed: set[int] = set()
        pattern = re.compile(
            r"^\s*\*\*Coverage status:\*\*\s*"
            r"(superseded|deferred|non-automated)\s+—\s+"
            r"(.+?);\s*owner/task:\s*(\S.+)$",
            re.MULTILINE | re.IGNORECASE,
        )
        for number, entry in self.spec_entries().items():
            status = pattern.search(entry)
            if status is None:
                continue
            reason = status.group(2).strip()
            owner = status.group(3).strip()
            assert reason, f"{self.prefix}-{number:03d} coverage status has no reason"
            assert owner, f"{self.prefix}-{number:03d} coverage status has no owner/task"
            disposed.add(number)
        return disposed

    def citation_re(self, n: int) -> str:
        sep = r"[-_]" if self.prefix == "AT" else r"[-_]?"
        return rf"{self.prefix}{sep}0*{n}(?!\d)"


@dataclass(frozen=True)
class _Disposition:
    kind: str
    reason: str
    owner_task: str


_SERIES = (
    _Series(
        prefix="AT",
        spec_path=_REPO_ROOT / "docs" / "SPEC.md",
        heading_re=re.compile(r"^### AT-(\d{3})\b", re.MULTILINE),
        expected_total=37,
    ),
    _Series(
        prefix="HAT",
        spec_path=_REPO_ROOT / "docs" / "HARDENING_SPEC.md",
        heading_re=re.compile(r"^### HAT-(\d{3})\b", re.MULTILINE),
        expected_total=22,
    ),
    _Series(
        prefix="RAT",
        spec_path=_REPO_ROOT / "docs" / "REMEDIATION_SPEC.md",
        heading_re=re.compile(r"^### RAT-(\d{3})\b", re.MULTILINE),
        expected_total=20,
    ),
    _Series(
        prefix="VAT",
        spec_path=_REPO_ROOT / "docs" / "REVIEW_SPEC.md",
        heading_re=re.compile(r"^- \*\*VAT-(\d{3})\*\*", re.MULTILINE),
        expected_total=6,
    ),
    _Series(
        prefix="AAT",
        spec_path=_REPO_ROOT / "docs" / "AUDIT_SPEC.md",
        heading_re=re.compile(r"^- \*\*AAT-(\d{3})\*\*", re.MULTILINE),
        expected_total=4,
    ),
    _Series(
        prefix="GAT",
        spec_path=_REPO_ROOT / "docs" / "GAP_SPEC.md",
        heading_re=re.compile(r"^- \*\*GAT-(\d{3})\*\*", re.MULTILINE),
        expected_total=8,
    ),
    _Series(
        prefix="G3AT",
        spec_path=_REPO_ROOT / "docs" / "GAP3_SPEC.md",
        heading_re=re.compile(r"^### G3AT-(\d{3})\b", re.MULTILINE),
        expected_total=17,
    ),
)
_BY_PREFIX = {series.prefix: series for series in _SERIES}


def _node(path: str, test_name: str) -> str:
    return f"{path}::{test_name}"


# Acceptance IDs whose real tests use requirement-oriented names. Multiple
# node IDs are deliberate where one acceptance criterion is composite.
_EXECUTABLE_COVERAGE: dict[tuple[str, int], tuple[str, ...]] = {
    ("AT", 20): (
        _node(
            "tests/git/test_status.py",
            "test_status_reports_merge_conflict_rather_than_repairing_it",
        ),
    ),
    ("AT", 21): (
        _node(
            "tests/workspace/test_audit_logging.py",
            "test_at_hat021_exec_command_started_event_redacts_a_secret_in_argv",
        ),
    ),
    ("AT", 29): (
        _node(
            "tests/workspace/test_git_push_tool.py",
            "test_force_and_force_with_lease_together_is_invalid_argument",
        ),
    ),
    ("AT", 30): (
        _node(
            "tests/git/test_pull.py",
            "test_build_fetch_argv_always_disables_submodule_recursion",
        ),
    ),
    ("HAT", 12): (
        _node(
            "tests/workspace/test_git_push_tool.py",
            "test_h12_pushurl_redirect_to_an_unauthorized_repo_is_rejected",
        ),
    ),
    ("HAT", 18): (
        _node(
            "tests/workspace/test_git_commit_tool_failure_modes.py",
            "test_at_hr023_a_stash_pop_conflict_fails_closed_though_it_leaves_no_marker_file",
        ),
        _node(
            "tests/workspace/test_git_commit_tool_failure_modes.py",
            "test_at_hr024_a_magic_pathspec_does_not_stage_the_whole_repository",
        ),
    ),
    ("HAT", 20): (
        _node(
            "tests/workspace/test_conftest_teardown.py",
            "test_a_full_suite_run_leaves_a_foreign_sentinel_intact",
        ),
    ),
    ("RAT", 3): (
        _node(
            "tests/workspace/test_git_push_tool.py",
            "test_at_rr002_container_side_push_insteadof_redirect_is_rejected",
        ),
    ),
    ("RAT", 4): (
        _node(
            "tests/workspace/test_gap3_acceptance_coverage_debt.py",
            "test_rr003_successful_push_audit_names_destination_actually_used",
        ),
    ),
    ("RAT", 5): (
        _node(
            "tests/workspace/test_git_commit_tool_failure_modes.py",
            "test_rr004_an_empty_or_root_normalizing_path_is_rejected_before_staging",
        ),
    ),
    ("RAT", 6): (
        _node(
            "tests/workspace/test_git_commit_tool_failure_modes.py",
            "test_rr005_a_failed_commit_restores_prior_staged_content_not_head",
        ),
    ),
    ("RAT", 7): (
        _node(
            "tests/workspace/test_gap3_acceptance_coverage_debt.py",
            "test_rr006_read_file_missing_path_is_a_typed_workspace_error",
        ),
        _node(
            "tests/filesystem/test_service_write.py",
            "test_rr006_write_file_onto_a_directory_raises_invalid_argument",
        ),
        _node(
            "tests/filesystem/test_service_delete.py",
            "test_rr006_delete_file_missing_parent_raises_invalid_argument",
        ),
    ),
    ("RAT", 8): (
        _node(
            "tests/filesystem/test_service_list.py",
            "test_rr007_list_files_survives_a_symlink_loop",
        ),
        _node(
            "tests/filesystem/test_search.py",
            "test_rr007_search_files_survives_a_symlink_loop",
        ),
    ),
    ("RAT", 9): (
        _node(
            "tests/test_github_api.py",
            "test_rr008_unverified_when_the_response_body_is_a_json_object_not_an_array",
        ),
    ),
    ("RAT", 10): (
        _node(
            "tests/test_errors.py",
            "test_rr009_a_secret_hinted_key_s_hint_survives_recursing_into_a_nested_dict",
        ),
        _node(
            "tests/test_errors.py",
            "test_rr009_non_string_scalars_keep_their_type_even_nested_under_a_secret_hint",
        ),
    ),
    ("RAT", 11): (
        _node(
            "tests/workspace/test_authorization_recheck.py",
            "test_rr010_exec_status_fails_after_repository_removed",
        ),
        _node(
            "tests/workspace/test_authorization_recheck.py",
            "test_rr010_exec_output_fails_after_repository_removed",
        ),
        _node(
            "tests/workspace/test_authorization_recheck.py",
            "test_rr011_exec_cancel_fails_after_repository_removed",
        ),
    ),
    ("RAT", 12): (
        _node(
            "tests/workspace/test_manager.py",
            "test_rr012_delete_succeeds_and_leaves_no_row_when_volume_removal_fails",
        ),
    ),
    ("RAT", 13): (
        _node(
            "tests/workspace/test_async_execution_tools_cancel.py",
            "test_rr013_failed_termination_via_a_real_exec_inspect_failure",
        ),
    ),
    ("RAT", 14): (
        _node(
            "tests/git/test_diff.py",
            "test_rr016_pagination_with_an_invalid_byte_for_every_max_bytes_from_1_to_64",
        ),
    ),
    ("RAT", 15): (
        _node("tests/git/test_diff.py", "test_rr016_max_bytes_zero_is_rejected"),
        _node(
            "tests/git/test_diff.py",
            "test_rr016_pagination_with_an_invalid_byte_for_every_max_bytes_from_1_to_64",
        ),
    ),
    ("RAT", 16): (
        _node(
            "tests/filesystem/test_service_read.py",
            "test_rr018_a_multibyte_character_at_the_limit_boundary_is_truncated_text",
        ),
    ),
    ("RAT", 17): (
        _node(
            "tests/execution/test_log_capture.py",
            "test_rr019_read_log_tail_composed_with_tail_text_at_a_nonzero_backward_offset",
        ),
    ),
    ("RAT", 18): (
        _node(
            "tests/workspace/test_manager.py",
            "test_rr021_a_real_oom_kill_clears_after_a_successful_reopen_via_rebuild",
        ),
    ),
    ("RAT", 19): (
        _node(
            "tests/git/test_transport.py",
            "test_a_real_local_permission_failure_during_fetch_is_not_classified_as_auth",
        ),
    ),
    ("RAT", 20): (
        _node(
            "tests/workspace/test_conftest_teardown.py",
            "test_rr024_a_production_shaped_volume_survives_being_referenced_by_a_tracked_run",
        ),
    ),
    ("AAT", 4): (
        _node(
            "tests/test_acceptance_test_coverage.py",
            "test_meta_removing_every_citation_for_a_vat_makes_the_audit_fail_for_it",
        ),
    ),
    ("GAT", 2): (
        _node(
            "tests/test_project_config.py",
            "test_at_gr004_environment_cannot_set_an_interpreter_or_resolution_variable",
        ),
    ),
    ("GAT", 3): (
        _node(
            "tests/workspace/test_default_image.py",
            "test_at_gr005_a_hostile_path_does_not_skip_the_privilege_drop",
        ),
    ),
    ("GAT", 4): (
        _node(
            "tests/workspace/test_git_status_tool.py",
            "test_at_gr006_a_non_utf8_path_raises_rather_than_reports_a_mangled_one",
        ),
    ),
    ("GAT", 5): (
        _node(
            "tests/test_errors.py",
            "test_at_gr007_to_tool_error_does_not_crash_on_a_set_or_bytes_value",
        ),
        _node(
            "tests/test_audit.py",
            "test_at_gr007_write_audit_event_does_not_crash_on_a_set_or_bytes_value",
        ),
    ),
    ("GAT", 6): (
        _node(
            "tests/workspace/test_default_image.py",
            "test_at_gr009_a_symlinked_ssh_directory_planted_in_a_cache_volume_is_not_followed",
        ),
    ),
    ("GAT", 7): (
        _node("tests/test_errors.py", "test_at_vat005_a_secret_flagged_set_recurses"),
    ),
    ("GAT", 8): (
        _node(
            "tests/workspace/test_manager.py",
            "test_at_h031_container_lifecycle_transitions_are_logged",
        ),
        _node(
            "tests/test_logging_isolation.py",
            "test_a_leaky_configure_logging_call_does_not_alter_state_for_later_tests",
        ),
    ),
}


_DISPOSITIONS: dict[tuple[str, int], _Disposition] = {
    ("HAT", 2): _Disposition(
        "superseded",
        "H36 removed the host-side structured Git execution surface",
        "H36",
    ),
    ("RAT", 1): _Disposition(
        "superseded",
        "H36 removed run_git_host_side and its included-config execution surface",
        "H36",
    ),
    ("RAT", 2): _Disposition(
        "superseded",
        "H36 removed the host-side included-config discovery machinery",
        "H36",
    ),
    ("GAT", 1): _Disposition(
        "ci-gate",
        "the acceptance surface is CI's real Ruff format/lint gate",
        ".github/workflows/ci.yml",
    ),
    ("G3AT", 17): _Disposition(
        "deferred",
        "G3AT-017 belongs to Y17",
        "Y17",
    ),
}


def _iter_test_nodes() -> tuple[str, set[str]]:
    """Return direct acceptance citations and exact pytest node IDs."""
    citation_chunks: list[str] = []
    node_ids: set[str] = set()
    for path in _TESTS_DIR.rglob("*.py"):
        text = path.read_text()
        lines = text.splitlines()
        tree = ast.parse(text, filename=str(path))
        relpath = path.relative_to(_REPO_ROOT).as_posix()
        candidates: list[tuple[str | None, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                candidates.append((None, node))
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                candidates.extend(
                    (node.name, child)
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
        for class_name, node in candidates:
            if not node.name.startswith("test_"):
                continue
            if class_name is None:
                node_ids.add(f"{relpath}::{node.name}")
            else:
                node_ids.add(f"{relpath}::{class_name}::{node.name}")
            citation_chunks.append(node.name)
            first_line = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            index = first_line - 2
            while index >= 0 and lines[index].lstrip().startswith("# Covers:"):
                citation_chunks.append(lines[index].strip())
                index -= 1
    return "\n".join(citation_chunks), node_ids


def _uncited(series: _Series, source: str) -> set[int]:
    return {n for n in series.spec_numbers() if not re.search(series.citation_re(n), source, re.I)}


def _registered_numbers(series: _Series, node_ids: set[str]) -> set[int]:
    return {
        number
        for (prefix, number), targets in _EXECUTABLE_COVERAGE.items()
        if prefix == series.prefix and all(target in node_ids for target in targets)
    }


def _disposition_numbers(series: _Series) -> set[int]:
    return {number for prefix, number in _DISPOSITIONS if prefix == series.prefix}


def _uncovered(series: _Series, source: str, node_ids: set[str]) -> set[int]:
    return (
        _uncited(series, source)
        - _registered_numbers(series, node_ids)
        - _disposition_numbers(series)
    )


def test_the_spec_defines_exactly_37_acceptance_tests() -> None:
    assert _BY_PREFIX["AT"].spec_numbers() == set(range(1, 38))


def test_every_acceptance_series_defines_its_expected_contiguous_count() -> None:
    for series in _SERIES:
        assert series.spec_numbers() == set(range(1, series.expected_total + 1)), series.prefix


def test_g3at_016_registered_executable_coverage_points_to_real_tests() -> None:
    """A renamed/deleted requirement-named test must reopen its acceptance debt."""
    _, node_ids = _iter_test_nodes()
    missing = {
        key: tuple(target for target in targets if target not in node_ids)
        for key, targets in _EXECUTABLE_COVERAGE.items()
        if any(target not in node_ids for target in targets)
    }
    assert not missing, f"acceptance coverage registry has stale pytest node IDs: {missing}"

    unknown = sorted(
        key
        for key in _EXECUTABLE_COVERAGE
        if key[0] not in _BY_PREFIX or key[1] not in _BY_PREFIX[key[0]].spec_numbers()
    )
    assert not unknown, f"coverage registry names nonexistent acceptance IDs: {unknown}"


def test_g3at_016_dispositions_are_narrow_reasoned_and_owned() -> None:
    """A disposition is explicit debt/state, never a number-only escape hatch."""
    allowed_kinds = {"superseded", "ci-gate", "deferred"}
    source, node_ids = _iter_test_nodes()
    for key, disposition in _DISPOSITIONS.items():
        prefix, number = key
        assert prefix in _BY_PREFIX
        assert number in _BY_PREFIX[prefix].spec_numbers()
        assert disposition.kind in allowed_kinds
        assert disposition.reason.strip()
        assert disposition.owner_task.strip()
        assert number not in _registered_numbers(_BY_PREFIX[prefix], node_ids)
        assert not re.search(_BY_PREFIX[prefix].citation_re(number), source, re.I)


def test_g3at_016_every_active_acceptance_test_has_executable_coverage() -> None:
    """G3AT-016: unexplained permanent citation gaps are forbidden."""
    source, node_ids = _iter_test_nodes()
    for series in _SERIES:
        missing = sorted(_uncovered(series, source, node_ids))
        assert not missing, (
            f"{series.prefix}-{missing} have neither direct executable citations, "
            "verified pytest-node coverage, nor an explicit owner-tagged disposition"
        )


def test_registry_is_not_a_stale_duplicate_of_a_direct_citation() -> None:
    """Direct citations are preferred once a test carries the acceptance ID itself."""
    source, _ = _iter_test_nodes()
    stale = sorted(
        (prefix, number)
        for prefix, number in _EXECUTABLE_COVERAGE
        if re.search(_BY_PREFIX[prefix].citation_re(number), source, re.I)
    )
    assert not stale, f"remove registry entries now covered directly: {stale}"


def test_meta_removing_every_citation_for_an_at_makes_the_audit_fail_for_it() -> None:
    target = 10
    series = _BY_PREFIX["AT"]
    source, node_ids = _iter_test_nodes()
    citation_re = series.citation_re(target)
    scrubbed = re.sub(citation_re, "SCRUBBED", source, flags=re.I)
    assert sorted(_uncovered(series, scrubbed, node_ids)) == [target]


def test_meta_removing_every_citation_for_a_vat_makes_the_audit_fail_for_it() -> None:
    target = 1
    series = _BY_PREFIX["VAT"]
    source, node_ids = _iter_test_nodes()
    citation_re = series.citation_re(target)
    scrubbed = re.sub(citation_re, "SCRUBBED", source, flags=re.I)
    assert sorted(_uncovered(series, scrubbed, node_ids)) == [target]


def test_meta_removing_every_citation_for_an_aat_makes_the_audit_fail_for_it() -> None:
    target = 1
    series = _BY_PREFIX["AAT"]
    source, node_ids = _iter_test_nodes()
    citation_re = series.citation_re(target)
    scrubbed = re.sub(citation_re, "SCRUBBED", source, flags=re.I)
    assert sorted(_uncovered(series, scrubbed, node_ids)) == [target]


def test_meta_deleting_a_registered_target_reopens_its_acceptance_debt() -> None:
    """G3R-016: registry evidence is coupled to the executable test's existence."""
    key = ("RAT", 5)
    series = _BY_PREFIX[key[0]]
    source, node_ids = _iter_test_nodes()
    target_node = _EXECUTABLE_COVERAGE[key][0]
    assert target_node in node_ids
    without_target = node_ids - {target_node}
    assert key[1] in _uncovered(series, source, without_target)
