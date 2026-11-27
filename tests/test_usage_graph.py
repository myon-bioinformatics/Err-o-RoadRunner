"""Footprint JSON/JSONL and the ONTO graph built from corpus goldens."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from scripts import harvest_corpus
from vendor.cli_args import run_cli

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "usage_graph.py"


def _load():
    spec = importlib.util.spec_from_file_location("usage_graph", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


usage_graph = _load()


def _corpus(tmp: Path) -> Path:
    corpus = tmp / "corpus"
    (corpus / "golden").mkdir(parents=True)
    golden = "\n".join([
        "FOUND: exit code: 1",
        "FOUND: junit failures (case.xml): 2",
        "FOUND: junit pytest exception type (case.xml): AssertionError",
        "FOUND: junit testcase classname (first) (case.xml): tests.Test",
        "NOT FOUND: junit dart error (first) (case.xml)",
        "",
    ])
    (corpus / "golden" / "demo.out").write_text(golden, encoding="utf-8")
    manifest = [{
        "id": "demo_1",
        "repo": "myon-bioinformatics/demo",
        "run_id": 42,
        "artifact": "junit-py",
        "date": "2026-11-01T00:00:00Z",
        "xml": "cases/demo.xml",
        "golden": "golden/demo.out",
    }]
    (corpus / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return corpus


def _adoption(tmp: Path) -> Path:
    path = tmp / "adoption.json"
    path.write_text(json.dumps({
        "schema": "errorr-adoption/v1",
        "observed_at": "2026-11-24",
        "scope": "fixture",
        "inventory": [
            {"repository": "myon-bioinformatics/demo", "archived": False},
            {"repository": "myon-bioinformatics/quiet", "archived": False},
        ],
        "uses": [{
            "repository": "myon-bioinformatics/demo",
            "feature": "digest",
            "kind": "documented",
            "evidence": "https://example.test/demo",
        }],
        "renames": [{
            "from": "myon-bioinformatics/demo",
            "to": "myon-bioinformatics/DemoKit",
            "evidence": "https://example.test/rename",
        }],
        "vendor_consumers": {"entries_sourcing_errorr": 0, "evidence": "https://example.test/vendor"},
        "workflow_scan": {"invocations_outside_errorr": []},
    }), encoding="utf-8")
    return path


def _harvest(tmp: Path) -> Path:
    path = tmp / "harvest_sources.json"
    path.write_text(json.dumps({"repos": ["demo"]}), encoding="utf-8")
    return path


def test_fixture_footprint_graph_and_cli(tmp_path: Path):
    corpus = _corpus(tmp_path)
    adoption = _adoption(tmp_path)
    harvest = _harvest(tmp_path)
    footprint = tmp_path / "footprint.json"
    events_path = tmp_path / "events.jsonl"
    doc = usage_graph.from_checkout(adoption, corpus, harvest, footprint, events_path, None)
    assert doc["duplicate_manifest_rows"] == 0
    assert doc["manifest_rows"] == 1
    assert doc["totals"] == {
        "cases": 1,
        "well_handled": 1,
        "partial": 0,
        "unknown": 0,
        "exception_types": {"AssertionError": 1},
    }
    assert doc["repositories"][0]["repository"] == "myon-bioinformatics/demo"
    quiet = [row for row in doc["repositories"] if row["repository"].endswith("/quiet")]
    assert quiet and quiet[0]["triage"]["cases"] == 0
    graph = usage_graph.graph_from_footprint(doc)
    assert usage_graph.violations(graph) == []
    relations = {edge["relation"] for edge in graph["edges"]}
    assert {"exposes", "observed_use", "harvests", "triaged", "same_as"} <= relations
    event = json.loads(events_path.read_text(encoding="utf-8"))
    assert event["run_url"] == "https://github.com/myon-bioinformatics/demo/actions/runs/42"
    assert event["classification"] == "well_handled"
    checked = run_cli(
        SCRIPT, "check", str(footprint), "--events", str(events_path),
        python_options=("-S",), cwd=ROOT,
    )
    assert checked.returncode == 0, checked.stderr
    drawn = run_cli(SCRIPT, "graph", str(footprint), python_options=("-S",), cwd=ROOT)
    assert drawn.returncode == 0, drawn.stderr
    assert json.loads(drawn.stdout)["nodes"]
    empty = tmp_path / "no-onto"
    empty.mkdir()
    missing = run_cli(
        SCRIPT, "mermaid", str(footprint), python_options=("-S",), cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(empty)},
    )
    assert missing.returncode == 2
    assert "knowledgegraph" in missing.stderr


def test_mermaid_and_html_use_an_importable_knowledgegraph(tmp_path: Path):
    module = tmp_path / "knowledgegraph.py"
    module.write_text(
        "def graph_mermaid(data, focus=None):\n"
        "    return '```mermaid\\nflowchart TD\\n  n0[\"Err-o-RoadRunner\"]\\n```\\n'\n"
        "def mermaid_renderer_js():\n"
        "    return 'export async function renderDiagram(id, source) { return \"<svg id=\\\"\" + id + \"\\\"></svg>\"; }\\n'\n",
        encoding="utf-8",
    )
    corpus = _corpus(tmp_path)
    footprint = tmp_path / "footprint.json"
    usage_graph.from_checkout(_adoption(tmp_path), corpus, _harvest(tmp_path), footprint, tmp_path / "events.jsonl", None)
    rendered = run_cli(
        SCRIPT, "mermaid", str(footprint), python_options=("-S",), cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
    )
    assert rendered.returncode == 0, rendered.stderr
    assert "flowchart TD" in rendered.stdout
    page = tmp_path / "fleet.html"
    written = run_cli(
        SCRIPT, "html", str(footprint), "-o", str(page), python_options=("-S",), cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
    )
    assert written.returncode == 0, written.stderr
    text = page.read_text(encoding="utf-8")
    assert "renderDiagram" in text and "flowchart TD" in text


def test_domain_range_violation_is_reported():
    graph = {
        "nodes": [
            {"id": "feature:triage", "type": "Feature", "name": "triage"},
            {"id": "outcome:partial", "type": "Outcome", "name": "partial"},
        ],
        "edges": [{"source": "feature:triage", "relation": "triaged", "target": "outcome:partial"}],
    }
    findings = usage_graph.violations(graph)
    assert findings and findings[0]["kind"] == "domain-range"


def test_exact_duplicate_manifest_rows_collapse_and_hash_collisions_keep_the_file(tmp_path: Path):
    corpus = _corpus(tmp_path)
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    (corpus / "manifest.json").write_text(json.dumps(manifest + manifest), encoding="utf-8")
    events, stats = usage_graph.events_from_corpus(corpus)
    assert stats == {"manifest_rows": 2, "duplicate_manifest_rows": 1, "colliding_manifest_rows": 0}
    assert len(events) == 1
    xml = corpus / "cases" / "demo.xml"
    xml.parent.mkdir(parents=True, exist_ok=True)
    xml.write_bytes(b"<testsuite></testsuite>")
    digest = usage_graph._sha256(xml)
    kept = json.loads(json.dumps(manifest[0]))
    kept["sha256"] = digest
    kept["source_file"] = "junit.xml"
    other = json.loads(json.dumps(manifest[0]))
    other["sha256"] = "0" * 64
    other["source_file"] = "controlled-failure/junit.xml"
    (corpus / "manifest.json").write_text(json.dumps([other, kept]), encoding="utf-8")
    events, stats = usage_graph.events_from_corpus(corpus)
    assert stats["colliding_manifest_rows"] == 1
    assert events[0]["sha256"] == digest
    assert events[0]["colliding_sources"] == [{"source_file": "controlled-failure/junit.xml", "sha256": "0" * 64}]
    other["xml"] = "cases/other.xml"
    (corpus / "manifest.json").write_text(json.dumps([kept, other]), encoding="utf-8")
    with pytest.raises(ValueError, match="more than one file"):
        usage_graph.events_from_corpus(corpus)


def test_checkout_events_match_harvest_classify_and_onto_vocabulary():
    events, stats = usage_graph.events_from_corpus(ROOT / "tests" / "fixtures" / "junit_corpus")
    manifest = json.loads((ROOT / "tests" / "fixtures" / "junit_corpus" / "manifest.json").read_text(encoding="utf-8"))
    assert stats["manifest_rows"] == len(manifest)
    assert stats["duplicate_manifest_rows"] + stats["colliding_manifest_rows"] == len(manifest) - len(events)
    assert len({event["case_id"] for event in events}) == len(events)
    by_id = {entry["id"]: entry for entry in manifest}
    for event in events:
        entry = by_id[event["case_id"]]
        golden = (ROOT / "tests" / "fixtures" / "junit_corpus" / entry["golden"]).read_text(encoding="utf-8")
        assert event["classification"] == harvest_corpus.classify(golden.splitlines())
    adoption = ROOT / "docs" / "usage" / "adoption.json"
    harvest = ROOT / "scripts" / "harvest_sources.json"
    doc = usage_graph.footprint_from_parts(
        usage_graph.load_json(adoption),
        usage_graph.load_json(harvest),
        events,
        len(json.loads((ROOT / "tests" / "fixtures" / "junit_corpus" / "classification.json").read_text(encoding="utf-8"))),
        stats,
    )
    assert usage_graph.check_footprint(doc, events) == []
    graph = usage_graph.graph_from_footprint(doc)
    assert usage_graph.violations(graph) == []
    outside = [
        edge for edge in graph["edges"]
        if edge["relation"] == "observed_use" and edge["source"] != "repo:myon-bioinformatics/Err-o-RoadRunner"
        and edge.get("basis") in {"workflow", "harvest-step"}
    ]
    assert outside == []


def test_committed_snapshot_matches_the_checker():
    footprint = ROOT / "docs" / "usage" / "fleet-footprint.json"
    events = ROOT / "docs" / "usage" / "triage-events.jsonl"
    if not footprint.is_file() or not events.is_file():
        pytest.skip("snapshot is produced by from-checkout")
    checked = run_cli(
        SCRIPT, "check", str(footprint), "--events", str(events),
        python_options=("-S",), cwd=ROOT,
    )
    assert checked.returncode == 0, checked.stderr


def test_committed_snapshot_harvest_sources_match_the_source_list():
    """The checker validates vocabulary only, so compare ``harvest_source`` with the list itself."""
    footprint = json.loads((ROOT / "docs" / "usage" / "fleet-footprint.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "scripts" / "harvest_sources.json").read_text(encoding="utf-8"))
    expected = {"%s/%s" % (sources["org"], name) for name in sources["repos"]}
    flagged = {row["repository"] for row in footprint["repositories"] if row["harvest_source"]}
    assert flagged == expected


def test_case_graph_links_saved_evidence_from_cli_and_shares_ci_run(tmp_path):
    corpus = _corpus(tmp_path)
    (corpus / "cases").mkdir()
    (corpus / "cases/demo.xml").write_text("<testsuite/>", encoding="utf-8")
    manifest = json.loads((corpus / "manifest.json").read_text())
    manifest.append(dict(manifest[0], id="demo_2"))
    (corpus / "manifest.json").write_text(json.dumps(manifest))
    graph = usage_graph.case_graph_from_corpus(corpus)
    assert usage_graph.violations(graph, usage_graph.CASE_VOCABULARY) == []
    assert len([n for n in graph["nodes"] if n["type"] == "CIRun"]) == 1
    expected = {"source": "case:demo_1:assessment", "relation": "supported_by",
                "target": "case:demo_1:triage"}
    assert expected in graph["edges"] and expected in graph["expected_links"]
    result = run_cli(SCRIPT, "case-graph", "--corpus", str(corpus), "--case", "demo_2",
                     python_options=("-S",), cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert [n["id"] for n in json.loads(result.stdout)["nodes"] if n["type"] == "FailureCase"] == ["case:demo_2"]


def test_case_graph_distinguishes_missing_file_and_historical_collision(tmp_path):
    corpus = _corpus(tmp_path)
    row = json.loads((corpus / "manifest.json").read_text())[0]
    (corpus / "unavailable-evidence.json").write_text(json.dumps({"records": [
        {"original_manifest_row": dict(row, sha256="lost"), "surviving_sha256": "current"}
    ]}))
    graph = usage_graph.case_graph_from_corpus(corpus, case_id="demo_1")
    nodes = [n for n in graph["nodes"] if n["type"] == "JUnitEvidence"]
    assert {n["status"] for n in nodes} == {"lost-original", "missing"}
    assert all(n["available"] is False for n in nodes)
    assert len([e for e in graph["expected_links"] if e["relation"] == "has_junit"]) == 2
    assert not any(e["relation"] == "has_junit" for e in graph["edges"])
    assert any(e["relation"] == "documented_by" for e in graph["edges"])
    assert all(not n.get("path") for n in nodes if n["status"] == "lost-original")


def test_case_graph_keeps_expectations_when_producer_omits_assessment(tmp_path, monkeypatch):
    corpus = _corpus(tmp_path)
    monkeypatch.setattr(usage_graph, "_add_case_assessment", lambda *args: None)
    graph = usage_graph.case_graph_from_corpus(corpus)
    assert not any(n["type"] == "TriageAssessment" for n in graph["nodes"])
    assert any(n["type"] == "TriageEvidence" and n["available"] for n in graph["nodes"])
    assert {e["relation"] for e in graph["expected_links"] if "case:demo_1:assessment" in e.values()} == {
        "has_assessment", "supported_by",
    }


def test_case_markdown_requires_pinned_source(monkeypatch):
    graph = {"nodes": [{"type": "JUnitEvidence", "name": "JUnit XML", "available": True,
                       "path": "cases/a.xml", "sha256": "abc"}], "edges": []}
    monkeypatch.setattr(usage_graph, "mermaid_from_graph", lambda graph: "```mermaid\nflowchart TD\n```\n")
    for invalid in (None, "main", "a" * 39):
        with pytest.raises(ValueError, match="full Git commit SHA"):
            usage_graph.case_markdown(graph, invalid)
    sha = "a" * 40
    output = usage_graph.case_markdown(graph, sha)
    assert f"/blob/{sha}/tests/fixtures/junit_corpus/cases/a.xml" in output
    assert "SHA-256: `abc`" in output
