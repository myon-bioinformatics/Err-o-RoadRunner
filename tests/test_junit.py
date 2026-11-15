"""JUnit XML triage: failure grouping, mutation-sensitive behaviour, review regressions.

- junit_failure_groups: JUnit failure grouping (Issue #5 stage 1).
- junit_mutations: Behaviour and mutation-sensitive tests for JUnit triage and actions logs.
- junit_regressions: JUnit parser regressions from PR #4 review.
"""

from __future__ import annotations

import importlib
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import pytest

from errlib import actions_log
from tests.support import CORPUS_CASES, EMPTY, FIXTURES, ROOT, triage_cli
from triage import triage


# ──── junit_failure_groups ────────────────────────────────────────

TRIAGE_MOD = importlib.import_module("triage")
CORPUS = CORPUS_CASES
FLUTTER = CORPUS / "flutter_navigation_basic_38139683688_junit-flutter-core-0_78.xml"
AOI = CORPUS / "Aoi_37561005125_junit-py3.13_87.xml"


def _grouping_inputs(xml_path: Path) -> tuple[list[str], list[str], list[str]]:
    root = ET.fromstring(xml_path.read_text(encoding="utf-8"))
    labels: list[str] = []
    messages: list[str] = []
    blobs: list[str] = []
    for elem in TRIAGE_MOD._bounded_xml_iter(root):
        if elem.tag.split("}")[-1] != "testcase":
            continue
        label = TRIAGE_MOD._junit_case_label(elem)
        for child in elem:
            child_tag = child.tag.split("}")[-1]
            if child_tag == "failure":
                blob = TRIAGE_MOD._junit_failure_blob(elem, child)
                labels.append(label)
                messages.append(
                    TRIAGE_MOD.sanitize_report_text(
                        (child.attrib.get("message") or "").strip()
                    )
                )
                blobs.append(blob)
            elif child_tag == "error":
                blob = TRIAGE_MOD._junit_failure_blob(elem, child)
                if not TRIAGE_MOD._junit_is_pytest_collection_failure(child, blob):
                    labels.append(label)
                    messages.append(
                        TRIAGE_MOD.sanitize_report_text(
                            (child.attrib.get("message") or "").strip()
                        )
                    )
                    blobs.append(blob)
    return labels, messages, blobs


def test_flutter_corpus_twelve_failures_one_type() -> None:
    labels, messages, blobs = _grouping_inputs(FLUTTER)
    assert len(labels) == 12
    groups = TRIAGE_MOD._junit_group_failures(labels, messages, blobs)
    assert len(groups) == 1
    assert groups[0].count == 12
    display = groups[0].display_type
    assert display == "Error when reading <q>: No such file or directory"
    assert "lib/shared/screens/generic_screen.dart" in groups[0].representative_message


def test_flutter_cli_group_msg_shows_missing_file_path() -> None:
    from triage import Report, parse_junit_xml

    report = Report()
    parse_junit_xml(FLUTTER.read_text(encoding="utf-8"), "flutter.xml", report)
    group_lines = [
        ln for ln in report.lines() if ln.startswith("FOUND: junit failure group ")
    ]
    assert len(group_lines) == 1
    assert "lib/shared/screens/generic_screen.dart" in group_lines[0]


def test_aoi_module_not_found_groups_show_distinct_module_names() -> None:
    from triage import Report, parse_junit_xml

    report = Report()
    parse_junit_xml(AOI.read_text(encoding="utf-8"), "aoi.xml", report)
    mod_lines = [
        ln
        for ln in report.lines()
        if ln.startswith("FOUND: junit failure group ") and "ModuleNotFoundError" in ln
    ]
    assert len(mod_lines) == 2
    assert sum("sets" in ln for ln in mod_lines) == 1
    assert sum("neighbors" in ln for ln in mod_lines) == 1


def test_aoi_corpus_nineteen_failures_eleven_types() -> None:
    labels, messages, blobs = _grouping_inputs(AOI)
    assert len(labels) == 19
    groups = TRIAGE_MOD._junit_group_failures(labels, messages, blobs)
    assert len(groups) == 11
    assert sum(g.count for g in groups) == 19


@pytest.mark.parametrize(
    "xml_path", sorted(CORPUS.parent.rglob("*.xml")), ids=lambda p: p.name
)
def test_group_counts_sum_to_failure_count(xml_path: Path) -> None:
    # The target is each failure child plus non-collection error children of
    # testcases, not suite counters or the number of failing testcases.
    root = ET.fromstring(xml_path.read_text(encoding="utf-8"))
    failures = errors = collection_errors = 0
    for testcase in root.iter():
        if testcase.tag.split("}")[-1] != "testcase":
            continue
        for child in testcase:
            tag = child.tag.split("}")[-1]
            if tag == "failure":
                failures += 1
            elif tag == "error":
                blob = TRIAGE_MOD._junit_failure_blob(testcase, child)
                if TRIAGE_MOD._junit_is_pytest_collection_failure(child, blob):
                    collection_errors += 1
                else:
                    errors += 1
    labels, messages, blobs = _grouping_inputs(xml_path)
    groups = TRIAGE_MOD._junit_group_failures(labels, messages, blobs)
    target_count = failures + errors
    assert sum(g.count for g in groups) == len(labels) == target_count

    # Check the actual parser's target selection too, including zero targets.
    report = TRIAGE_MOD.Report()
    with mock.patch.object(
        TRIAGE_MOD, "_junit_emit_failure_groups",
        wraps=TRIAGE_MOD._junit_emit_failure_groups,
    ) as emit:
        TRIAGE_MOD.parse_junit_xml(xml_path.read_text(encoding="utf-8"), "corpus.xml", report)
    assert emit.call_count == 1
    # The JUnit ``type=`` attributes ride along as a fourth, same-length list.
    assert emit.call_args.args[2:5] == (labels, messages, blobs)
    assert len(emit.call_args.args[5]) == len(labels)
    lines = report.lines()
    if target_count:
        assert any(
            line.startswith(f"FOUND: junit failure groups (corpus.xml): {target_count} ")
            for line in lines
        )
    else:
        assert "NOT FOUND: junit failure groups (corpus.xml)" in lines
    if errors + collection_errors:
        assert f"FOUND: junit errors (corpus.xml): {errors + collection_errors}" in lines
    assert not any(line.startswith("HINT: junit failure group") for line in lines)


@pytest.mark.gap
def test_mutation_disabling_failure_type_key_collapses_aoi_groups() -> None:
    labels, messages, blobs = _grouping_inputs(AOI)
    baseline = TRIAGE_MOD._junit_group_failures(labels, messages, blobs)
    assert len(baseline) > 1

    with mock.patch.object(TRIAGE_MOD, "_junit_failure_type_key", return_value=("same", "key", ())):
        collapsed = TRIAGE_MOD._junit_group_failures(labels, messages, blobs)
    assert len(collapsed) == 1
    assert collapsed[0].count == len(labels)


def test_truncation_omitted_failure_count_not_swapped_with_type_count() -> None:
    from triage import Report

    labels = ["t::a1", "t::a2", "t::a3", "t::b1", "t::b2"]
    messages = [
        "AssertionError: assert 0 == 1",
        "AssertionError: assert 0 == 1",
        "AssertionError: assert 0 == 1",
        "StopIteration",
        "StopIteration",
    ]
    blobs = [
        "E       AssertionError\nassert 0 == 1",
        "E       AssertionError\nassert 0 == 1",
        "E       AssertionError\nassert 0 == 1",
        "E       StopIteration\n",
        "E       StopIteration\n",
    ]
    report = Report()
    with mock.patch.object(TRIAGE_MOD, "JUNIT_FAILURE_GROUP_CAP", 1):
        TRIAGE_MOD._junit_emit_failure_groups(report, "src", labels, messages, blobs)
    trunc = next(ln for ln in report.lines() if "truncated" in ln)
    assert "1 type, 2 failures omitted" in trunc


@pytest.mark.gap
def test_mutation_using_normalized_text_for_representative_message() -> None:
    labels, messages, blobs = _grouping_inputs(AOI)
    groups = TRIAGE_MOD._junit_group_failures(labels, messages, blobs)
    mod = [g for g in groups if "sets" in g.representative_message or "neighbors" in g.representative_message]
    assert len(mod) == 2

    def fake_rep(message: str, blob: str, limit: int = 300) -> str:
        first = TRIAGE_MOD._junit_failure_message_first_line(message, blob)
        return TRIAGE_MOD._junit_normalize_type_message(
            TRIAGE_MOD._junit_line_without_exception_prefix(first)
        )[:limit]

    with mock.patch.object(TRIAGE_MOD, "_junit_representative_message_line", side_effect=fake_rep):
        mutated = TRIAGE_MOD._junit_group_failures(labels, messages, blobs)
    mod_mut = [
        g
        for g in mutated
        if g.display_type.startswith("ModuleNotFoundError")
    ]
    assert all("sets" not in g.representative_message for g in mod_mut)
    assert all("neighbors" not in g.representative_message for g in mod_mut)


@pytest.mark.gap
def test_mutation_skipping_emit_removes_group_found_lines(tmp_path: Path) -> None:
    from triage import Report, parse_junit_xml

    report = Report()
    text = AOI.read_text(encoding="utf-8")
    parse_junit_xml(text, "aoi.xml", report)
    assert any("junit failure groups" in ln for ln in report.lines())

    report2 = Report()
    with mock.patch.object(TRIAGE_MOD, "_junit_emit_failure_groups"):
        parse_junit_xml(text, "aoi.xml", report2)
    assert not any("junit failure groups" in ln for ln in report2.lines())


@pytest.mark.parametrize("exc", ["AssertionError", "ValueError", "SystemExit", "Unknown"])
@pytest.mark.parametrize("quote", ["'", '"'])
def test_pipe_unicode_quotes_and_fingerprint_are_separate(exc: str, quote: str) -> None:
    prefix = "" if exc == "Unknown" else f"{exc}: "
    messages = [
        f"{prefix}入力|{quote}資料|版12{quote}|後半34",
        f"{prefix}入力|{quote}資料|版99{quote}|後半56",
        f"{prefix}入力|{quote}別資料|版12{quote}|後半34",
    ]
    groups = TRIAGE_MOD._junit_group_failures(["a", "b", "c"], messages, messages)
    assert [g.count for g in groups] == [2, 1]
    expected = f"{exc}: 入力|<q>|後半N"
    assert all(g.display_type == expected for g in groups)
    assert groups[0].type_key == (exc, "入力|<q>|後半N", ("資料|版N",))
    assert groups[1].type_key[2] == ("別資料|版N",)
    assert groups[0].representative_message == messages[0]
    report = TRIAGE_MOD.Report()
    TRIAGE_MOD._junit_emit_failure_groups(report, "pipes.xml", ["a", "b", "c"], messages, messages)
    lines = [line for line in report.lines() if line.startswith("FOUND: junit failure group ")]
    assert len(lines) == 2
    assert all(expected in line for line in lines)
    assert f"msg: {messages[0]}" in lines[0]


@pytest.mark.parametrize(
    "messages",
    [
        ["ValueError: '<q>|尾'", "ValueError: '<q>'|尾"],
        ["ValueError: 'a|b' 'c'", "ValueError: 'a' 'b|c'"],
        ['ValueError: "資料|版" "末尾"', 'ValueError: "資料" "版|末尾"'],
    ],
)
def test_delimiter_cannot_collide_between_identity_components(messages: list[str]) -> None:
    groups = TRIAGE_MOD._junit_group_failures(["a", "b"], messages, messages)
    assert len(groups) == 2
    assert groups[0].type_key != groups[1].type_key
    assert [g.count for g in groups] == [1, 1]


def test_exception_types_remain_distinct_with_same_message_and_fingerprint() -> None:
    messages = [f"{exc}: 入力|'資料|版12'|後半34" for exc in ("ValueError", "TypeError")]
    groups = TRIAGE_MOD._junit_group_failures(["a", "b"], messages, messages)
    assert len(groups) == 2
    assert groups[0].type_key[1:] == groups[1].type_key[1:]
    assert groups[0].type_key[0] != groups[1].type_key[0]


@pytest.mark.parametrize("namespace", ["", "urn:junit"])
def test_parser_preserves_first_last_cap_overflow_and_collection_exclusion(namespace: str) -> None:
    root = ET.Element("testsuite", {"xmlns": namespace} if namespace else {})
    # Twelve types, with different counts in the two omitted groups. No digits
    # in their discriminator: digit normalization must not collapse them.
    for i, letter in enumerate("abcdefghijkl"):
        for j in range(3 if i < 10 else 2 if i == 10 else 1):
            testcase = ET.SubElement(root, "testcase", name=f"{letter}{j}")
            ET.SubElement(testcase, "failure", message=f"ValueError: 型{letter}|'資料|版12'|末尾")
    # An ordinary error shares the first failure's type space.
    error_case = ET.SubElement(root, "testcase", name="ordinary-error")
    ET.SubElement(error_case, "error", message="ValueError: 型a|'資料|版99'|末尾")
    collection_case = ET.SubElement(root, "testcase", name="collection")
    ET.SubElement(collection_case, "error", message="collection failure").text = "ImportError while importing test module"
    report = TRIAGE_MOD.Report()
    TRIAGE_MOD.parse_junit_xml(ET.tostring(root, encoding="unicode"), "mixed.xml", report)
    lines = report.lines()
    assert "FOUND: junit failures (mixed.xml): 33" in lines
    assert "FOUND: junit errors (mixed.xml): 2" in lines
    assert "FOUND: junit collection errors (mixed.xml): 1" in lines
    assert "FOUND: junit failure groups (mixed.xml): 34 failures in 12 groups" in lines
    assert "FOUND: junit failure groups truncated (mixed.xml): 2 types, 3 failures omitted" in lines
    group_lines = [line for line in lines if line.startswith("FOUND: junit failure group ")]
    assert len(group_lines) == TRIAGE_MOD.JUNIT_FAILURE_GROUP_CAP == 10
    assert "4× ValueError: 型a|<q>|末尾" in group_lines[0]
    assert "first: a0" in group_lines[0]
    assert "FOUND: junit failure (first) (mixed.xml): ValueError: 型a|'資料|版12'|末尾" in lines
    assert "FOUND: junit failure (last) (mixed.xml): ValueError: 型l|'資料|版12'|末尾" in lines
    assert "FOUND: junit error (first) (mixed.xml): ValueError: 型a|'資料|版99'|末尾" in lines
    assert not any(line.startswith("HINT: junit failure group") for line in lines)
    assert any(line.startswith("HINT: web search keywords") for line in lines)


# ──── junit_mutations ─────────────────────────────────────────────

FIX = FIXTURES / "actions_logs"


@pytest.mark.gap
def test_junit_assertion_error_does_not_emit_dart_error(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="1" failures="1">
  <testcase classname="tests.demo" name="test_x">
    <failure message="assert 0">E   AssertionError: boom</failure>
  </testcase>
</testsuite>"""
    path = tmp_path / "assertion.xml"
    path.write_text(xml, encoding="utf-8")
    report = triage(
        exit_code=1,
        junit_paths=[str(path)],
        stderr_text=None,
        log_text=None,
        actions_log_text=None,
        project_root=EMPTY,
    )
    assert not any(ln.startswith("FOUND: junit dart error") for ln in report.lines())


@pytest.mark.gap
def test_testsuite_level_import_error_reports_collection_via_cli(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="0" errors="1">
  <error message="collection failure">ImportError while importing test module 'tests/conftest.py'.</error>
</testsuite>"""
    path = tmp_path / "suite_import.xml"
    path.write_text(xml, encoding="utf-8")
    out = triage_cli("--junit", path)
    assert "FOUND: junit collection errors (" in out
    assert "ImportError while importing" in out


@pytest.mark.gap
def test_junit_failure_duplicate_message_and_body_printed_once(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="1" failures="1">
  <testcase classname="tests.demo" name="test_x">
    <failure message="boom">boom</failure>
  </testcase>
</testsuite>"""
    path = tmp_path / "dedup.xml"
    path.write_text(xml, encoding="utf-8")
    out = triage_cli("--junit", path)
    failure_lines = [ln for ln in out.splitlines() if ln.startswith("FOUND: junit failure (first)")]
    assert len(failure_lines) == 1
    assert failure_lines[0].count("boom") == 1


@pytest.mark.gap
def test_junit_xml_crlf_entities_produce_no_cr_in_cli_output(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="1" failures="1">
  <testcase classname="tests.demo" name="test_x">
    <failure message="line1&#13;&#10;line2"/>
  </testcase>
</testsuite>"""
    path = tmp_path / "crlf.xml"
    path.write_text(xml, encoding="utf-8")
    out = triage_cli("--junit", path)
    assert "\r" not in out
    failure = next(ln for ln in out.splitlines() if ln.startswith("FOUND: junit failure (first)"))
    assert "line1\\nline2" in failure


@pytest.mark.gap
def test_junit_flatten_normalizes_crlf_before_escaping() -> None:
    flat = TRIAGE_MOD._junit_flatten_for_report("line1\r\nline2")
    assert "\r" not in flat
    assert flat == "line1\\nline2"
    assert "line1\\n\\nline2" != flat


@pytest.mark.gap
def test_mutation_dropping_dart_core_filter_pollutes_diff_hint() -> None:
    import re

    blob = "- 'Object' is from 'dart:core'.\n"
    assert "dart:core" not in TRIAGE_MOD._junit_diff_hint(blob)
    with mock.patch.object(TRIAGE_MOD, "DART_NOISE_LINE_RE", re.compile("$^")):
        hint = TRIAGE_MOD._junit_diff_hint(blob)
    assert "dart:core" in hint


@pytest.mark.gap
def test_mutation_unanchored_dart_regex_false_positive_on_assertion(tmp_path: Path) -> None:
    import re

    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="1" failures="1">
  <testcase classname="tests.demo" name="test_x">
    <failure message="assert 0">E   AssertionError: boom</failure>
  </testcase>
</testsuite>"""
    path = tmp_path / "assertion.xml"
    path.write_text(xml, encoding="utf-8")
    loose = re.compile(r"(?P<line>.*Error:\s*.+)$")
    with mock.patch.object(TRIAGE_MOD, "DART_ERROR_LINE_RE", loose):
        report = triage(
            exit_code=1,
            junit_paths=[str(path)],
            stderr_text=None,
            log_text=None,
            actions_log_text=None,
            project_root=EMPTY,
        )
    assert any(ln.startswith("FOUND: junit dart error") for ln in report.lines())


@pytest.mark.gap
def test_testcase_collection_failure_message_is_found(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="0" errors="1">
  <testcase classname="" name="tests.conftest" time="0">
    <error message="collection failure">could not load conftest</error>
  </testcase>
</testsuite>"""
    path = tmp_path / "tc_collection.xml"
    path.write_text(xml, encoding="utf-8")
    report = triage(
        exit_code=1,
        junit_paths=[str(path)],
        stderr_text=None,
        log_text=None,
        actions_log_text=None,
        project_root=EMPTY,
    )
    assert any(ln.startswith("FOUND: junit collection errors (") for ln in report.lines())


@pytest.mark.gap
def test_mutation_dropping_failure_blob_dedup_duplicates_boom(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="1" failures="1">
  <testcase classname="tests.demo" name="test_x">
    <failure message="boom">boom</failure>
  </testcase>
</testsuite>"""
    path = tmp_path / "dedup.xml"
    path.write_text(xml, encoding="utf-8")

    def naive_blob(testcase, elem):  # noqa: ANN001
        parts = []
        msg = (elem.attrib.get("message") or "").strip()
        body = (elem.text or "").strip()
        if msg:
            parts.append(msg)
        if body:
            parts.append(body)
        return "\n".join(parts)

    with mock.patch.object(TRIAGE_MOD, "_junit_failure_blob", naive_blob):
        report = triage(
            exit_code=1,
            junit_paths=[str(path)],
            stderr_text=None,
            log_text=None,
            actions_log_text=None,
            project_root=EMPTY,
        )
    line = next(
        ln for ln in report.lines() if ln.startswith("FOUND: junit failure (first)")
    )
    assert line.count("boom") >= 2


@pytest.mark.gap
def test_actions_log_preprocess_strips_cr_from_crlf_bytes() -> None:
    raw = (FIX / "crlf_bom.log").read_bytes()
    assert b"\r" in raw
    records, _ = actions_log.preprocess_actions_log(raw.decode("utf-8-sig"))
    assert all("\r" not in rec.text for rec in records)


# ──── junit_regressions ───────────────────────────────────────────



@pytest.mark.junit_corpus
def test_aoi_pytest_collection_failure_is_found() -> None:
    xml = CORPUS / "Aoi_37559206978_junit-py3.11_100.xml"
    report = triage(
        exit_code=1,
        junit_paths=[str(xml)],
        stderr_text=None,
        log_text=None,
        actions_log_text=None,
        project_root=EMPTY,
    )
    lines = report.lines()
    assert any(ln.startswith("FOUND: junit collection errors (") for ln in lines)
    assert any(
        "collection failure" in ln or "ImportError while importing" in ln
        for ln in lines
        if ln.startswith("FOUND: junit collection error (first)")
    )


@pytest.mark.gap
def test_junit_setup_error_is_not_collection_error() -> None:
    xml = CORPUS.parent / "inputs" / "setup_error.junit.xml"
    report = triage(
        exit_code=1,
        stderr_text=None,
        log_text=None,
        actions_log_text=None,
        junit_paths=[str(xml)],
        project_root=EMPTY,
    )
    lines = report.lines()
    assert any(ln.startswith("FOUND: junit errors") for ln in lines)
    assert any(ln.startswith("NOT FOUND: junit collection errors (") for ln in lines)


@pytest.mark.junit_corpus
@pytest.mark.parametrize(
    "case_id,expected_substr",
    [
        (
            "flutter_navigation_basic_38139683688_junit-flutter-core-0_78",
            ".dart:",
        ),
        (
            "flutter_navigation_basic_38139683688_junit-flutter-core-1_77",
            ".dart:",
        ),
        (
            "flutter_navigation_basic_38139683671_junit-flutter-refactor_74",
            ".dart:",
        ),
        (
            "flutter_navigation_basic_38139683671_junit-flutter-refactor_75",
            ".dart:",
        ),
    ],
)
def test_junit_flutter_corpus_dart_error(case_id: str, expected_substr: str) -> None:
    xml = CORPUS / f"{case_id}.xml"
    report = triage(
        exit_code=1,
        junit_paths=[str(xml)],
        stderr_text=None,
        log_text=None,
        actions_log_text=None,
        project_root=EMPTY,
    )
    dart_lines = [
        ln
        for ln in report.lines()
        if ln.startswith("FOUND: junit dart error (first)") and expected_substr in ln
    ]
    assert len(dart_lines) == 1
    joined = "\n".join(report.lines())
    assert not any("dart:core" in ln and ln.startswith("HINT:") for ln in report.lines())


@pytest.mark.junit_corpus
def test_junit_flutter_xml_run_38139681545_golden() -> None:

    xml = CORPUS / "flutter_navigation_basic_38139681545_flutter.xml"
    assert xml.is_file()
    label = str(xml).replace(str(ROOT.resolve()), "<repo>").replace("/workspace", "<repo>")
    report = triage(
        exit_code=1,
        junit_paths=[str(xml)],
        stderr_text=None,
        log_text=None,
        actions_log_text=None,
        project_root=EMPTY,
    )
    lines = [ln.replace(str(ROOT.resolve()), "<repo>").replace("/workspace", "<repo>") for ln in report.lines()]
    dart = [ln for ln in lines if ln.startswith("FOUND: junit dart error (first)")]
    assert len(dart) == 1
    assert ".dart:" in dart[0] and "Error:" in dart[0]
    assert any(ln.startswith("NOT FOUND: junit collection errors (") for ln in lines)


# --- 2026-11-07 corpus feedback: the "partial" classes measured over 458 harvested cases ---

def _junit_lines(xml_text: str, tmp_path: Path) -> list[str]:
    path = tmp_path / "case.xml"
    path.write_text(xml_text, encoding="utf-8")
    report = triage(exit_code=1, junit_paths=[str(path)], stderr_text=None, log_text=None,
                    actions_log_text=None, project_root=EMPTY)
    return report.lines()


def _suite(*testcases: str) -> str:
    body = "".join(testcases)
    return f'<testsuite name="pytest" tests="{len(testcases)}" failures="{len(testcases)}">{body}</testsuite>'


PLAIN_ASSERT = _suite(
    '<testcase classname="tests.test_sum" name="test_total">'
    '<failure message="assert 12 == 6">def test_total():\n'
    '&gt;       assert total([3, 3]) == 6\nE       assert 12 == 6\n\n'
    'tests/test_sum.py:60: AssertionError</failure></testcase>'
)
DID_NOT_RAISE = _suite(
    '<testcase classname="tests.test_io" name="test_exists">'
    '<failure message="Failed: DID NOT RAISE &lt;class \'FileExistsError\'&gt;">'
    'tests\\test_io.py:311: in test_exists\n    with pytest.raises(FileExistsError):\n'
    'E   Failed: DID NOT RAISE &lt;class \'FileExistsError\'&gt;</failure></testcase>'
)
PLAYWRIGHT = _suite(
    '<testcase classname="home.spec.ts" name="Home › draw opens Door">'
    '<failure message="home.spec.ts:125:7 draw opens Door" type="expect.toBeVisible">'
    '[chromium] › home.spec.ts:125:7 › Home › draw opens Door\n\n'
    'Error: expect(locator).toBeVisible() failed\n\nLocator: locator(\'[role="menuitem"]\').first()\n'
    'Expected: visible\nTimeout: 3000ms\n\nCall log:\n  - Expect "toBeVisible" with timeout 3000ms\n'
    '  - waiting for locator(\'[role="menuitem"]\').first()\n\n    at home.spec.ts:131:51</failure></testcase>',
    '<testcase classname="home.spec.ts" name="Home › draw opens Door (webkit)">'
    '<failure message="home.spec.ts:125:7 draw opens Door" type="expect.toBeVisible">'
    '[webkit] › home.spec.ts:125:7 › Home › draw opens Door\n\nError: expect(locator).toBeVisible() failed\n</failure></testcase>'
)
FLUTTER_DESCRIPTION_XML = (
    '<testsuite name="flutter" tests="1" errors="1"><testcase classname="/w/test/screens/generic_screen_test.dart" '
    'name="Screen3 compact template is reachable above Door"><error message="Flutter test error">'
    'Test failed. See exception logs above.\nThe test description was: Screen3 compact template is reachable above Door'
    '</error></testcase></testsuite>'
)
IMPORT_MISMATCH = (
    '<testsuite name="pytest" tests="1" errors="1"><testcase classname="blueprobe.tests.test_vendor_lock" name="">'
    '<error message="collection failure">import file mismatch:\n'
    "imported module 'test_vendor_lock' has this __file__ attribute:\n  /w/tests/test_vendor_lock.py\n"
    'which is not the same as the test file we want to collect:\n  /w/blueprobe/tests/test_vendor_lock.py\n'
    'HINT: remove __pycache__ / .pyc files and/or use a unique basename for your test file modules'
    '</error></testcase></testsuite>'
)


def test_plain_pytest_assert_reports_assertion_error_type(tmp_path: Path) -> None:
    lines = _junit_lines(PLAIN_ASSERT, tmp_path)
    assert any(ln.startswith("FOUND: junit pytest exception type (") and ln.endswith(": AssertionError") for ln in lines)
    assert not any(ln.startswith("NOT FOUND: junit pytest exception type") for ln in lines)


def test_did_not_raise_is_typed_and_explained(tmp_path: Path) -> None:
    lines = _junit_lines(DID_NOT_RAISE, tmp_path)
    assert any(ln.endswith("junit pytest exception type (" + str(tmp_path / "case.xml") + "): Failed") for ln in lines)
    hint = next(ln for ln in lines if ln.startswith("HINT: junit pytest outcome ("))
    assert "pytest.raises(FileExistsError) finished without an exception (DID NOT RAISE)" in hint


def test_playwright_block_is_located_typed_and_hinted(tmp_path: Path) -> None:
    lines = _junit_lines(PLAYWRIGHT, tmp_path)
    assert any(ln.startswith("FOUND: junit playwright test (") and ln.endswith("[chromium] home.spec.ts:125:7 › Home › draw opens Door") for ln in lines)
    assert any(ln.endswith("junit playwright error (" + str(tmp_path / "case.xml") + "): Error: expect(locator).toBeVisible() failed") for ln in lines)
    assert any(ln.startswith("HINT: junit playwright call log (") for ln in lines)
    assert any(ln.startswith("HINT: junit framework guess (") and ln.endswith("playwright") for ln in lines)
    # The JUnit type attribute names the group when nothing in the text does.
    assert any("junit failure group 1/1 (" in ln and ": 2× expect.toBeVisible: " in ln for ln in lines)


def test_flutter_junit_description_and_redirect_to_test_events(tmp_path: Path) -> None:
    lines = _junit_lines(FLUTTER_DESCRIPTION_XML, tmp_path)
    assert any(ln.endswith("junit flutter test description (" + str(tmp_path / "case.xml") + "): Screen3 compact template is reachable above Door") for ln in lines)
    hint = next(ln for ln in lines if ln.startswith("HINT: junit flutter detail ("))
    assert "--test-events" in hint and "EXCEPTION CAUGHT BY FLUTTER TEST FRAMEWORK" in hint


def test_import_file_mismatch_hint_names_both_paths(tmp_path: Path) -> None:
    lines = _junit_lines(IMPORT_MISMATCH, tmp_path)
    hint = next(ln for ln in lines if ln.startswith("HINT: junit collection ("))
    assert "module 'test_vendor_lock' is already imported from /w/tests/test_vendor_lock.py" in hint
    assert "collection wants /w/blueprobe/tests/test_vendor_lock.py" in hint
    assert "--import-mode=importlib" in hint


def test_unrelated_failures_keep_old_output(tmp_path: Path) -> None:
    xml = _suite('<testcase classname="t" name="x"><failure message="boom">RuntimeError: boom</failure></testcase>')
    lines = _junit_lines(xml, tmp_path)
    assert not any("playwright" in ln or "flutter" in ln or "pytest outcome" in ln or "junit collection (" in ln for ln in lines)
