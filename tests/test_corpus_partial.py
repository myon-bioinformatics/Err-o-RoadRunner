"""Rules that moved the harvested JUnit corpus "partial" cases to "well_handled".

Each rule is generic (no case ids in triage.py). The corpus tests pin the real cases; the
synthetic ones pin each rule's edges. ``scripts/check_mutations.py partial`` reverts each
rule and requires the ``mutation_guard`` tests here to fail.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from scripts import capture_goldens, harvest_corpus
from tests.support import FIXTURES, ROOT, RUNTIME_LINE_PREFIXES
from triage import Report, parse_junit_xml

pytestmark = pytest.mark.mutation_guard

CORPUS = FIXTURES / "junit_corpus"
_spec = importlib.util.spec_from_file_location("usage_graph_partial", ROOT / "scripts" / "usage_graph.py")
usage_graph = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(usage_graph)

# Corpus ids of the 29 cases that were "partial" on main 6724b6d6, grouped by the rule that fixes them.
STOP_ITERATION = [
    "Ironmate_44510534953_junit-py3.15_pytest-3.15_14522627297_54156375f270460a5fe68a64f0b31af2373c43599d5da1bac6bde30c3ad6003f",
    "Ironmate_44510960142_junit-py3.15_pytest-3.15_14524096295_54156375f270460a5fe68a64f0b31af2373c43599d5da1bac6bde30c3ad6003f",
    "Ironmate_44511089240_junit-py3.15_pytest-3.15_14522378082_54156375f270460a5fe68a64f0b31af2373c43599d5da1bac6bde30c3ad6003f",
    "Ironmate_44511089308_junit-py3.15_pytest-3.15_14523876194_54156375f270460a5fe68a64f0b31af2373c43599d5da1bac6bde30c3ad6003f",
]
SETUP_ERROR = [
    "Ironmate_44529792812_junit-setup-clipboard-webkit_setup_14533555561_ac09dfc797bc65d1cfcb1b966a1b953a4be3d0f7dc85713ea27fb578d154e797",
    "Ironmate_45185927281_junit-setup-test_setup_14823207095_ac09dfc797bc65d1cfcb1b966a1b953a4be3d0f7dc85713ea27fb578d154e797",
]
IMPORT_MISMATCH = [
    f"Aoi_{run}_junit-py{v}_pytest-{v}" for run in (38528298362, 38528537882)
    for v in ("3.11", "3.12", "3.13", "3.14", "3.15")
]
PLAYWRIGHT = [
    f"flutter_navigation_basic_{run}_junit-playwright-e2e_pl"
    for run in (38405636026, 38408515971, 38409707959, 38416774348, 38424277205, 38425066777, 41029254164)
]
PLAYWRIGHT_NO_PROJECT = ["browser-test-kit_41365520483_junit-node-browserless_browserl"]
FLUTTER_EXCEPTION_IN_LOG = [
    "flutter_navigation_basic_41029253857_junit-flutter-core-0_fl",
    "flutter_navigation_basic_41029253857_junit-flutter-latest-1_",
    "flutter_navigation_basic_41035298007_junit-flutter-core-1_fl",
    "flutter_navigation_basic_41035298007_junit-flutter-latest-0_",
    "flutter_navigation_basic_41040587092_junit-flutter-latest-1_",
]
FIXED = STOP_ITERATION + SETUP_ERROR + IMPORT_MISMATCH + PLAYWRIGHT + PLAYWRIGHT_NO_PROJECT


def _report(xml: str) -> list[str]:
    report = Report()
    parse_junit_xml(xml, "s.xml", report)
    return report.lines()


def _case(case_id: str) -> list[str]:
    return _report((CORPUS / "cases" / f"{case_id}.xml").read_text(encoding="utf-8"))


def _found(lines: list[str], label: str) -> list[str]:
    prefix = f"FOUND: {label} ("
    return [line.split("): ", 1)[1] for line in lines if line.startswith(prefix)]


def _xml(*cases: str) -> str:
    return f'<testsuites><testsuite name="s">{"".join(cases)}</testsuite></testsuites>'


# ──── the 29 corpus cases ─────────────────────────────────────────


def test_former_partial_ids_are_29_distinct_corpus_cases() -> None:
    ids = FIXED + FLUTTER_EXCEPTION_IN_LOG
    assert len(ids) == len(set(ids)) == 29
    assert all((CORPUS / "cases" / f"{i}.xml").is_file() for i in ids)


@pytest.mark.parametrize("case_id", FIXED)
def test_fixed_case_is_well_handled_from_fresh_triage(case_id: str) -> None:
    lines = _case(case_id)
    assert harvest_corpus.classify(lines) == "well_handled"
    assert usage_graph.classify_lines(lines) == "well_handled"


@pytest.mark.parametrize("case_id", FLUTTER_EXCEPTION_IN_LOG)
def test_flutter_case_without_exception_text_stays_partial(case_id: str) -> None:
    lines = _case(case_id)
    assert _found(lines, "junit flutter test description")
    assert not _found(lines, "junit pytest exception type")
    assert harvest_corpus.classify(lines) == "partial"


@pytest.mark.parametrize("case_id", STOP_ITERATION)
def test_corpus_bare_builtin_e_line(case_id: str) -> None:
    assert _found(_case(case_id), "junit pytest exception type") == ["StopIteration"]


@pytest.mark.parametrize("case_id", SETUP_ERROR)
def test_corpus_type_attribute(case_id: str) -> None:
    assert _found(_case(case_id), "junit pytest exception type") == ["SetupError"]


@pytest.mark.parametrize("case_id", IMPORT_MISMATCH)
def test_corpus_import_mismatch_found(case_id: str) -> None:
    assert _found(_case(case_id), "junit collection import mismatch") == [
        "module 'test_vendor_lock' imported from <runner-work>/tests/test_vendor_lock.py, "
        "collection wants <runner-work>/blueprobe/tests/test_vendor_lock.py"
    ]


def test_corpus_playwright_header_without_project() -> None:
    lines = _case(PLAYWRIGHT_NO_PROJECT[0])
    assert _found(lines, "junit playwright test") == [
        "page-inspect-scenario.spec.mjs:5:1 › platform fixture state machines and Node runtime contract"
    ]
    assert _found(lines, "junit playwright error") == [
        "Error: ✖ workspace selects Node 24 and this test uses a supported runtime (5.536618ms)"
    ]


# ──── exception type fallbacks ────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("E   StopIteration", ["StopIteration"]),
        ("E   Exception: boom", ["Exception"]),
        ("E   builtins.StopAsyncIteration", ["builtins.StopAsyncIteration"]),
        # A specific type still wins over the builtin fallback.
        ("E   Exception: wrapped\nE   KeyError: 'k'", ["KeyError"]),
        ("E   Ordinary", []),
        ("E   GeneratorExitNow", []),
    ],
)
def test_e_line_builtin_fallback(body: str, expected: list[str]) -> None:
    lines = _report(_xml(f'<testcase classname="t" name="n"><failure message="m">{body}</failure></testcase>'))
    assert _found(lines, "junit pytest exception type") == expected


@pytest.mark.parametrize(
    ("type_attr", "body", "expected"),
    [
        ("SetupError", "", ["SetupError"]),
        ("pkg.SetupError", "", ["pkg.SetupError"]),
        ("expect.toBe", "", []),
        ("Setup Error", "", []),
        ("", "", []),
        # Text that names a type wins over the attribute.
        ("SetupError", "E   ValueError: v", ["ValueError"]),
    ],
)
def test_type_attribute_fallback(type_attr: str, body: str, expected: list[str]) -> None:
    case = f'<testcase classname="t" name="n"><error type="{type_attr}" message="x failed">{body}</error></testcase>'
    assert _found(_report(_xml(case)), "junit pytest exception type") == expected


@pytest.mark.parametrize("tag", ["failure", "error"])
def test_type_attribute_comes_from_the_primary_element(tag: str) -> None:
    first = f'<testcase classname="t" name="a"><{tag} type="FirstError" message="a failed"/></testcase>'
    second = f'<testcase classname="t" name="b"><{tag} type="SecondError" message="b failed"/></testcase>'
    assert _found(_report(_xml(first, second)), "junit pytest exception type") == ["FirstError"]


# ──── Playwright header without a project ─────────────────────────


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("a.spec.ts:3:1 › suite › title ───", "a.spec.ts:3:1 › suite › title"),
        ("[chromium] › a.spec.ts:3:1 › title", "[chromium] a.spec.ts:3:1 › title"),
        ("[webkit] › a.spec.ts:3:1 › title ───", "[webkit] a.spec.ts:3:1 › title"),
    ],
)
def test_playwright_header_forms(header: str, expected: str) -> None:
    body = f"  {header}\n\n    Error: expect(received).toBe(expected)\n"
    lines = _report(_xml(f'<testcase classname="a.spec.ts" name="t"><failure message="m">{body}</failure></testcase>'))
    assert _found(lines, "junit playwright test") == [expected]
    assert _found(lines, "junit playwright error") == ["Error: expect(received).toBe(expected)"]


def test_plain_location_line_is_not_a_playwright_header() -> None:
    body = "tests/test_x.py:3: in test_x\nE   ValueError: v"
    lines = _report(_xml(f'<testcase classname="t" name="n"><failure message="m">{body}</failure></testcase>'))
    assert not _found(lines, "junit playwright test")


# ──── import file mismatch FOUND ──────────────────────────────────


def test_import_mismatch_found_and_hint_share_one_parse() -> None:
    body = (
        "import file mismatch:\nimported module 'test_a' has this __file__ attribute:\n  /w/x/test_a.py\n"
        "which is not the same as the test file we want to collect:\n  /w/y/test_a.py\n"
    )
    lines = _report(_xml(f'<testcase classname="" name="y.test_a"><error message="collection failure">{body}</error></testcase>'))
    assert _found(lines, "junit collection import mismatch") == [
        "module 'test_a' imported from /w/x/test_a.py, collection wants /w/y/test_a.py"
    ]
    hint = [line for line in lines if line.startswith("HINT: junit collection (")]
    assert len(hint) == 1 and "already imported from /w/x/test_a.py but collection wants /w/y/test_a.py" in hint[0]


def test_other_collection_error_has_no_import_mismatch_line() -> None:
    body = "ImportError while importing test module '/w/test_a.py'.\nE   ModuleNotFoundError: No module named 'q'"
    lines = _report(_xml(f'<testcase classname="" name="test_a"><error message="collection failure">{body}</error></testcase>'))
    assert _found(lines, "junit collection error (first)")
    assert not _found(lines, "junit collection import mismatch")
    assert not [line for line in lines if line.startswith("HINT: junit collection (")]


# ──── classification keys ─────────────────────────────────────────


def test_both_classifiers_share_the_richness_keys() -> None:
    assert harvest_corpus.KEY_FOUND == usage_graph.KEY_FOUND
    assert "junit playwright error" in harvest_corpus.KEY_FOUND
    assert "junit collection import mismatch" in harvest_corpus.KEY_FOUND


@pytest.mark.parametrize("key", ["junit playwright error", "junit collection import mismatch"])
@pytest.mark.parametrize("classify", [harvest_corpus.classify, usage_graph.classify_lines])
def test_new_key_counts_toward_well_handled(key: str, classify) -> None:
    lines = ["FOUND: junit failures (s): 1", "FOUND: junit testcase classname (first) (s): c", f"FOUND: {key} (s): v"]
    assert classify(lines) == "well_handled"
    assert classify(lines[:2]) == "partial"


def test_checkout_events_carry_new_found_keys() -> None:
    events = {}
    for line in (ROOT / "docs" / "usage" / "triage-events.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        events[row["case_id"]] = row
    assert "junit playwright error" in events[PLAYWRIGHT[0]]["found_keys"]
    assert "junit collection import mismatch" in events[IMPORT_MISMATCH[0]]["found_keys"]
    assert all(events[i]["classification"] == "well_handled" for i in FIXED)
    assert all(events[i]["classification"] == "partial" for i in FLUTTER_EXCEPTION_IN_LOG)


# ──── golden re-capture keeps committed runtime lines ─────────────


def test_keep_runtime_lines_takes_committed_values() -> None:
    committed = (
        "FOUND: exit code: 1\nFOUND: python version: 3.15.2 (cpython)\nFOUND: python version: dup\n"
        "FOUND: gh version: gh version 2.104.0\nFOUND: environment variable names: CI, HOME\n"
    )
    fresh = (
        "FOUND: exit code: 1\nFOUND: python version: 3.13.5 (cpython)\nFOUND: node version: v24.0.0\n"
        "FOUND: gh version: gh version 2.103.0\nFOUND: environment variable names: HOME\nHINT: new rule\n"
    )
    assert capture_goldens.keep_runtime_lines(fresh, committed) == (
        "FOUND: exit code: 1\nFOUND: python version: 3.15.2 (cpython)\nFOUND: node version: v24.0.0\n"
        "FOUND: gh version: gh version 2.104.0\nFOUND: environment variable names: CI, HOME\nHINT: new rule\n"
    )


def test_keep_runtime_lines_preserves_line_endings_and_other_lines() -> None:
    fresh = "FOUND: python version: new\r\nNOT FOUND: x\r\nFOUND: python version: also new"
    assert capture_goldens.keep_runtime_lines(fresh, "FOUND: python version: old\n") == (
        "FOUND: python version: old\r\nNOT FOUND: x\r\nFOUND: python version: old"
    )
    assert capture_goldens.keep_runtime_lines("A\nB\n", "") == "A\nB\n"
    assert set(RUNTIME_LINE_PREFIXES) >= {"FOUND: python version:", "FOUND: environment variable names:"}


def test_recapture_writes_committed_runtime_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = tmp_path / "junit_corpus"
    (corpus / "cases").mkdir(parents=True)
    (corpus / "golden").mkdir()
    xml = (CORPUS / "cases" / f"{SETUP_ERROR[0]}.xml").read_bytes()
    entries = []
    for name, committed in (("old", "FOUND: python version: committed\n"), ("new", None)):
        (corpus / "cases" / f"{name}.xml").write_bytes(xml)
        if committed is not None:
            (corpus / "golden" / f"{name}.out").write_text(committed, encoding="utf-8")
        entries.append({"xml": f"cases/{name}.xml", "golden": f"golden/{name}.out"})
    (corpus / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(capture_goldens, "FIXTURES", tmp_path)
    capture_goldens.capture_junit_corpus()
    old = (corpus / "golden" / "old.out").read_text(encoding="utf-8").splitlines()
    new = (corpus / "golden" / "new.out").read_text(encoding="utf-8").splitlines()
    assert [ln for ln in old if ln.startswith("FOUND: python version:")] == ["FOUND: python version: committed"]
    assert [ln for ln in new if ln.startswith("FOUND: python version:")] != ["FOUND: python version: committed"]
    assert "FOUND: junit pytest exception type" in "\n".join(new)
