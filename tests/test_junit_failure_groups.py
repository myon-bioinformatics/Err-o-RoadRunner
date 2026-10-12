"""JUnit failure grouping (Issue #5 stage 1)."""

from __future__ import annotations

import importlib
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import pytest

TRIAGE_MOD = importlib.import_module("err_o_roadrunner.triage")

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
    display = TRIAGE_MOD._junit_group_display_type(groups[0].type_key)
    assert display == "Error when reading <q>: No such file or directory"
    assert "lib/shared/screens/generic_screen.dart" in groups[0].representative_message


def test_flutter_cli_group_msg_shows_missing_file_path() -> None:
    from err_o_roadrunner.triage import Report, parse_junit_xml

    report = Report()
    parse_junit_xml(FLUTTER.read_text(encoding="utf-8"), "flutter.xml", report)
    group_lines = [
        ln for ln in report.lines() if ln.startswith("FOUND: junit failure group ")
    ]
    assert len(group_lines) == 1
    assert "lib/shared/screens/generic_screen.dart" in group_lines[0]


def test_aoi_module_not_found_groups_show_distinct_module_names() -> None:
    from err_o_roadrunner.triage import Report, parse_junit_xml

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


def test_group_counts_sum_to_failure_count() -> None:
    for xml_path in (FLUTTER, AOI):
        labels, messages, blobs = _grouping_inputs(xml_path)
        groups = TRIAGE_MOD._junit_group_failures(labels, messages, blobs)
        assert sum(g.count for g in groups) == len(labels)


@pytest.mark.gap
def test_mutation_disabling_failure_type_key_collapses_aoi_groups() -> None:
    labels, messages, blobs = _grouping_inputs(AOI)
    baseline = TRIAGE_MOD._junit_group_failures(labels, messages, blobs)
    assert len(baseline) > 1

    with mock.patch.object(TRIAGE_MOD, "_junit_failure_type_key", return_value="same|key"):
        collapsed = TRIAGE_MOD._junit_group_failures(labels, messages, blobs)
    assert len(collapsed) == 1
    assert collapsed[0].count == len(labels)


def test_truncation_omitted_failure_count_not_swapped_with_type_count() -> None:
    from err_o_roadrunner.triage import Report

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
        if TRIAGE_MOD._junit_group_display_type(g.type_key).startswith("ModuleNotFoundError")
    ]
    assert all("sets" not in g.representative_message for g in mod_mut)
    assert all("neighbors" not in g.representative_message for g in mod_mut)


@pytest.mark.gap
def test_mutation_skipping_emit_removes_group_found_lines(tmp_path: Path) -> None:
    from err_o_roadrunner.triage import Report, parse_junit_xml

    report = Report()
    text = AOI.read_text(encoding="utf-8")
    parse_junit_xml(text, "aoi.xml", report)
    assert any("junit failure groups" in ln for ln in report.lines())

    report2 = Report()
    with mock.patch.object(TRIAGE_MOD, "_junit_emit_failure_groups"):
        parse_junit_xml(text, "aoi.xml", report2)
    assert not any("junit failure groups" in ln for ln in report2.lines())
