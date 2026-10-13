"""JUnit failure grouping (Issue #5 stage 1)."""

from __future__ import annotations

import importlib
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import pytest

TRIAGE_MOD = importlib.import_module("triage")

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "junit_corpus" / "cases"
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
    assert emit.call_args.args[2:] == (labels, messages, blobs)
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
