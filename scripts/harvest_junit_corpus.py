#!/usr/bin/env python3
"""Harvest failed-run test results (JUnit XML, JSON, JSONL) and keep only failures (redacted).

Sources: every public, non-archived repository listed in ``scripts/harvest_sources.json``.
Formats are treated equally:

* JUnit XML (pytest ``--junitxml``, Playwright/Flutter JUnit) -> ``tests/fixtures/junit_corpus``
  (golden-tested through ``triage --junit``).
* JSON / JSONL (xprobe ``pytest-events.jsonl`` and ``failures.json``, Flutter machine JSONL,
  Playwright JSON reporter, other ``status``/``outcome`` records) -> ``tests/fixtures/results_corpus``.

Artifacts are fetched with ``gh api .../actions/artifacts/{id}/zip`` and extracted in Python with
path checks (``gh run download`` refuses some upload-artifact@v7 archives with "would result in
path traversal", which is why mcp-toolcall-lab produced 0 entries before).

The harvest is incremental: entries already in a manifest (same repo, run, artifact, file) or with
identical redacted content are skipped, and new entries are appended.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import zipfile
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "junit_corpus"
MANIFEST = CORPUS / "manifest.json"

sys.path.insert(0, str(ROOT))

from junit_redact import redact_attribute, redact_text

SOURCES = ROOT / "scripts" / "harvest_sources.json"
RESULTS_CORPUS = ROOT / "tests" / "fixtures" / "results_corpus"
RESULTS_MANIFEST = RESULTS_CORPUS / "manifest.json"

_SOURCES = json.loads(SOURCES.read_text(encoding="utf-8"))
ORG = _SOURCES["org"]
REPOS: list[str] = list(_SOURCES["repos"])
ARTIFACT_MARKERS: tuple[str, ...] = tuple(_SOURCES["artifact_markers"])
SKIP_WORKFLOWS = frozenset(_SOURCES["skip_workflows"])

MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
MAX_STRING_CHARS = 4000
MAX_RECORDS = 200
FAIL_STATES = frozenset({"failed", "failure", "error", "errored", "timedout", "unexpected", "interrupted", "crashed"})


@dataclass(frozen=True)
class PendingCorpusWrite:
    rel_path: Path
    content: str


@dataclass(frozen=True)
class HarvestFailure:
    repo: str
    run_id: int | None
    artifact: str | None
    stage: str
    detail: str

    def as_dict(self) -> dict:
        return {
            "repo": self.repo,
            "run_id": self.run_id,
            "artifact": self.artifact,
            "stage": self.stage,
            "detail": self.detail,
        }


def run_gh(args: list[str], failures: list[HarvestFailure], *, repo: str, run_id: int | None, artifact: str | None, stage: str) -> str | None:
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"gh exit {proc.returncode}"
        failures.append(
            HarvestFailure(
                repo=repo,
                run_id=run_id,
                artifact=artifact,
                stage=stage,
                detail=detail[:500],
            )
        )
        return None
    return proc.stdout


def list_failed_runs(repo: str, failures: list[HarvestFailure], limit: int = 15) -> list[dict]:
    """Failed runs, ignoring Copilot review runs (they never carry test results and used to
    crowd test runs out of the window)."""
    runs = _list_failed_runs_raw(repo, failures, limit=limit * 3)
    kept = [r for r in runs if (r.get("workflowName") or "") not in SKIP_WORKFLOWS]
    return kept[:limit]


def _list_failed_runs_raw(repo: str, failures: list[HarvestFailure], limit: int) -> list[dict]:
    out = run_gh(
        [
            "run",
            "list",
            "--repo",
            f"{ORG}/{repo}",
            "--status",
            "failure",
            "--limit",
            str(limit),
            "--json",
            "databaseId,displayTitle,createdAt,workflowName",
        ],
        failures,
        repo=repo,
        run_id=None,
        artifact=None,
        stage="list_failed_runs",
    )
    if out is None or not out.strip():
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        failures.append(
            HarvestFailure(
                repo=repo,
                run_id=None,
                artifact=None,
                stage="list_failed_runs",
                detail=f"invalid json: {exc}"[:500],
            )
        )
        return []


def is_junit_artifact(name: str) -> bool:
    """True for any artifact that may carry test results (any format)."""
    lower = name.lower()
    return any(marker in lower for marker in ARTIFACT_MARKERS)


is_result_artifact = is_junit_artifact


def list_artifacts(repo: str, run_id: int, failures: list[HarvestFailure]) -> list[dict]:
    out = run_gh(
        [
            "api",
            f"repos/{ORG}/{repo}/actions/runs/{run_id}/artifacts",
            "--jq",
            ".artifacts[] | {name,id,expired}",
        ],
        failures,
        repo=repo,
        run_id=run_id,
        artifact=None,
        stage="list_artifacts",
    )
    if out is None or not out.strip():
        return []
    items = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError as exc:
            failures.append(
                HarvestFailure(
                    repo=repo,
                    run_id=run_id,
                    artifact=None,
                    stage="list_artifacts",
                    detail=f"invalid json: {exc}"[:500],
                )
            )
    return items


def _artifact_id(repo: str, run_id: int, name: str, failures: list[HarvestFailure]) -> int | None:
    out = run_gh(
        ["api", f"repos/{ORG}/{repo}/actions/runs/{run_id}/artifacts", "--jq", f'.artifacts[] | select(.name == "{name}") | .id'],
        failures, repo=repo, run_id=run_id, artifact=name, stage="download_artifact",
    )
    if not out or not out.strip():
        return None
    return int(out.split()[0])


def safe_extract(data: bytes, dest: Path) -> list[Path]:
    """Extract a zip refusing absolute paths, '..' components and oversized payloads."""
    written: list[Path] = []
    root = dest.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        total = 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            parts = [p for p in name.split("/") if p not in ("", ".")]
            if name.startswith("/") or ".." in parts or not parts:
                continue
            total += info.file_size
            if total > MAX_ARTIFACT_BYTES:
                break
            target = root.joinpath(*parts).resolve()
            if not target.is_relative_to(root):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            written.append(target)
    return written


def download_artifact(
    repo: str,
    run_id: int,
    name: str,
    dest: Path,
    failures: list[HarvestFailure],
) -> bool:
    artifact_id = _artifact_id(repo, run_id, name, failures)
    if artifact_id is None:
        failures.append(HarvestFailure(repo=repo, run_id=run_id, artifact=name, stage="download_artifact", detail="artifact id not found"))
        return False
    proc = subprocess.run(
        ["gh", "api", f"repos/{ORG}/{repo}/actions/artifacts/{artifact_id}/zip"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip() or f"download exit {proc.returncode}"
        failures.append(HarvestFailure(repo=repo, run_id=run_id, artifact=name, stage="download_artifact", detail=detail[:500]))
        return False
    try:
        safe_extract(proc.stdout, dest)
    except zipfile.BadZipFile as exc:
        failures.append(HarvestFailure(repo=repo, run_id=run_id, artifact=name, stage="download_artifact", detail=f"bad zip: {exc}"[:500]))
        return False
    return True


def find_junit_xml(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*.xml"))


def _clone_element(elem: ET.Element) -> ET.Element:
    tag = elem.tag.split("}")[-1]
    attribs = {k: redact_attribute(k, v) for k, v in elem.attrib.items()}
    clone = ET.Element(tag, attrib=attribs)
    clone.text = redact_text(elem.text) if elem.text else elem.text
    clone.tail = redact_text(elem.tail) if elem.tail else elem.tail
    for child in elem:
        clone.append(_clone_element(child))
    return clone


def extract_failing_xml(tree: ET.ElementTree) -> ET.ElementTree | None:
    root = tree.getroot()
    tag = root.tag.split("}")[-1]
    if tag == "testsuites":
        suites = [c for c in root if c.tag.split("}")[-1] == "testsuite"]
        root_attribs = {k: redact_attribute(k, v) for k, v in root.attrib.items()}
    elif tag == "testsuite":
        suites = [root]
        root_attribs = {}
    else:
        return None

    new_root = ET.Element("testsuites", attrib=root_attribs)
    kept_any = False
    for suite in suites:
        new_suite = ET.Element(
            "testsuite",
            attrib={k: redact_attribute(k, v) for k, v in suite.attrib.items()},
        )
        for child in suite:
            if child.tag.split("}")[-1] != "testcase":
                continue
            testcase = child
            fail_children = [
                c
                for c in testcase
                if c.tag.split("}")[-1] in ("failure", "error")
            ]
            if not fail_children:
                continue
            tc_attribs = {k: redact_attribute(k, v) for k, v in testcase.attrib.items()}
            new_tc = ET.Element("testcase", attrib=tc_attribs)
            for tc_child in testcase:
                if tc_child.tag.split("}")[-1] in (
                    "failure",
                    "error",
                    "system-out",
                    "system-err",
                ):
                    new_tc.append(_clone_element(tc_child))
            new_suite.append(new_tc)
            kept_any = True
        if len(new_suite):
            new_root.append(new_suite)
    if not kept_any:
        return None
    return ET.ElementTree(new_root)


def redact_json(value: object, key: str = "") -> object:
    """Recursively redact strings; normalise host/timestamp-like fields; cap long strings."""
    k = key.lower()
    if isinstance(value, dict):
        return {kk: redact_json(vv, kk) for kk, vv in value.items()}
    if isinstance(value, list):
        return [redact_json(v, key) for v in value]
    if isinstance(value, str):
        if k in ("hostname", "host"):
            return "<redacted-host>"
        if k in ("timestamp", "starttime", "start_time", "time_utc", "fetched_at", "created_at") or ISO_LIKE(value):
            return "<timestamp>"
        text = redact_text(value)
        if len(text) > MAX_STRING_CHARS:
            text = text[:MAX_STRING_CHARS] + "...<truncated>"
        return text
    return value


def ISO_LIKE(value: str) -> bool:  # noqa: N802 - tiny predicate
    from junit_redact import ISO_TIMESTAMP_RE

    return bool(ISO_TIMESTAMP_RE.match(value))


def _state(rec: dict) -> str:
    for key in ("outcome", "status", "result", "conclusion"):
        v = rec.get(key)
        if isinstance(v, str):
            return v.lower()
    return ""


def extract_failing_jsonl(text: str) -> tuple[str, list[dict]] | None:
    """Keep failing records from a JSONL stream (xprobe pytest-events, Flutter machine output,
    generic status/outcome records). Returns (detected kind, kept records)."""
    records: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    if not records:
        return None
    flutter = any(r.get("type") in ("testStart", "testDone", "suite") for r in records)
    if flutter:
        failing: set = set()
        for r in records:
            if r.get("type") == "testDone" and r.get("result") not in (None, "success") and not r.get("skipped"):
                failing.add(r.get("testID"))
            if r.get("type") == "error":
                failing.add(r.get("testID"))
        kept = []
        for r in records:
            tid = r.get("testID")
            if r.get("type") == "testStart":
                tid = (r.get("test") or {}).get("id")
            if tid in failing and r.get("type") in ("testStart", "error", "print", "testDone"):
                kept.append(r)
        kind = "flutter-machine-jsonl"
    else:
        kept = [r for r in records if _state(r) in FAIL_STATES]
        kind = "pytest-events-jsonl" if any("nodeid" in r or "node_id" in r for r in records) else "status-jsonl"
    if not kept:
        return None
    return kind, kept[:MAX_RECORDS]


def _playwright_failures(report: dict) -> list[dict]:
    out: list[dict] = []

    def walk(suite: dict, titles: list[str]) -> None:
        here = titles + ([suite["title"]] if suite.get("title") else [])
        for spec in suite.get("specs") or []:
            for test in spec.get("tests") or []:
                results = test.get("results") or []
                bad = [r for r in results if str(r.get("status", "")).lower() in FAIL_STATES]
                if str(test.get("status", "")).lower() not in ("unexpected", "flaky") and not bad:
                    continue
                out.append({
                    "file": spec.get("file"),
                    "title": here + [spec.get("title")],
                    "project": test.get("projectName"),
                    "status": test.get("status"),
                    "errors": [e for r in bad for e in (r.get("errors") or ([r["error"]] if r.get("error") else []))],
                    "stdout": [o for r in bad for o in (r.get("stdout") or [])][:20],
                })
        for child in suite.get("suites") or []:
            walk(child, here)

    for s_ in report.get("suites") or []:
        walk(s_, [])
    return out


def extract_failing_json(obj: object) -> tuple[str, object] | None:
    """Keep failures from a JSON document (Playwright JSON reporter, xprobe failures.json,
    generic lists of status/outcome records)."""
    if isinstance(obj, dict) and isinstance(obj.get("suites"), list) and ("config" in obj or "stats" in obj):
        fails = _playwright_failures(obj)
        return ("playwright-json", {"failures": fails[:MAX_RECORDS]}) if fails else None
    if isinstance(obj, dict):
        for key in ("failures", "failed", "errors"):
            v = obj.get(key)
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return "failures-json", {key: v[:MAX_RECORDS]}
        for key in ("tests", "results", "cases", "records"):
            v = obj.get(key)
            if isinstance(v, list):
                kept = [x for x in v if isinstance(x, dict) and _state(x) in FAIL_STATES]
                if kept:
                    return "status-json", {key: kept[:MAX_RECORDS]}
        return None
    if isinstance(obj, list):
        kept = [x for x in obj if isinstance(x, dict) and _state(x) in FAIL_STATES]
        if kept:
            return "status-json", kept[:MAX_RECORDS]
    return None


def serialize_xml(tree: ET.ElementTree) -> str:
    xml = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        tree.getroot(), encoding="unicode"
    )
    ET.fromstring(xml)
    return xml


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-._" else "_" for c in text)[:60]


def _load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _existing_keys(entries: list[dict]) -> tuple[set[tuple], set[str]]:
    keys = {(e.get("repo"), e.get("run_id"), e.get("artifact"), e.get("source_file")) for e in entries}
    # Entries harvested before source_file existed: treat the whole artifact as already taken.
    keys |= {(e.get("repo"), e.get("run_id"), e.get("artifact"), "*") for e in entries if "source_file" not in e}
    hashes = {e["sha256"] for e in entries if e.get("sha256")}
    for e in entries:
        if e.get("sha256"):
            continue
        rel = e.get("xml")
        path = CORPUS / rel if rel else None
        if path is not None and path.is_file():
            hashes.add(hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest())
    return keys, hashes


def harvest(
    entries_limit_per_repo: int = 5,
    existing: list[dict] | None = None,
) -> tuple[list[dict], list[HarvestFailure], list[PendingCorpusWrite]]:
    """Return new entries for all formats. JUnit entries carry ``xml``/``golden``; JSON/JSONL
    entries carry ``format`` and ``data`` (path relative to the results corpus)."""
    failures: list[HarvestFailure] = []
    entries: list[dict] = []
    pending: list[PendingCorpusWrite] = []
    seen_keys, seen_hashes = _existing_keys(existing or [])

    for repo in REPOS:
        count = 0
        for run in list_failed_runs(repo, failures):
            if count >= entries_limit_per_repo:
                break
            run_id = int(run["databaseId"])
            for art in list_artifacts(repo, run_id, failures):
                if count >= entries_limit_per_repo:
                    break
                name = art["name"]
                if not is_junit_artifact(name):
                    continue
                if (f"{ORG}/{repo}", run_id, name, "*") in seen_keys:
                    continue
                if art.get("expired"):
                    failures.append(
                        HarvestFailure(repo=repo, run_id=run_id, artifact=name, stage="artifact_expired", detail="artifact expired")
                    )
                    continue
                with tempfile.TemporaryDirectory() as tmp:
                    dest = Path(tmp)
                    if not download_artifact(repo, run_id, name, dest, failures):
                        continue
                    for path in sorted(p for p in dest.rglob("*") if p.is_file()):
                        if count >= entries_limit_per_repo:
                            break
                        rel_src = path.relative_to(dest).as_posix()
                        key = (f"{ORG}/{repo}", run_id, name, rel_src)
                        if key in seen_keys:
                            continue
                        made = _harvest_file(repo, run, name, path, rel_src, failures)
                        if made is None:
                            continue
                        entry, write = made
                        if entry["sha256"] in seen_hashes:
                            continue
                        seen_keys.add(key)
                        seen_hashes.add(entry["sha256"])
                        pending.append(write)
                        entries.append(entry)
                        count += 1
    return entries, failures, pending


def _harvest_file(repo: str, run: dict, artifact: str, path: Path, rel_src: str, failures: list[HarvestFailure]) -> tuple[dict, PendingCorpusWrite] | None:
    run_id = int(run["databaseId"])
    suffix = path.suffix.lower()
    base = {
        "repo": f"{ORG}/{repo}",
        "run_id": run_id,
        "artifact": artifact,
        "job": artifact,
        "workflow": run.get("workflowName", ""),
        "date": run.get("createdAt", ""),
        "source_file": rel_src,
    }
    stem = _slug(f"{repo}_{run_id}_{artifact}_{Path(rel_src).stem}")
    if suffix == ".xml":
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            failures.append(HarvestFailure(repo=repo, run_id=run_id, artifact=artifact, stage="parse_xml", detail=str(exc)[:500]))
            return None
        trimmed = extract_failing_xml(tree)
        if trimmed is None:
            return None
        content = serialize_xml(trimmed)
        rel = Path("cases") / f"{stem}.xml"
        entry = {"id": stem, **base, "format": "junit-xml", "xml": rel.as_posix(), "golden": (Path("golden") / f"{stem}.out").as_posix()}
    elif suffix in (".jsonl", ".ndjson") or (suffix in (".json", ".txt", ".log") and "machine" in path.name.lower()):
        got = extract_failing_jsonl(path.read_text(encoding="utf-8", errors="replace"))
        if got is None:
            return None
        kind, records = got
        content = "".join(json.dumps(redact_json(r), ensure_ascii=False, sort_keys=True) + "\n" for r in records)
        rel = Path("cases") / f"{stem}.jsonl"
        entry = {"id": stem, **base, "format": kind, "data": rel.as_posix(), "records": len(records)}
    elif suffix == ".json":
        try:
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            got_l = extract_failing_jsonl(path.read_text(encoding="utf-8", errors="replace"))
            if got_l is None:
                return None
            kind, records = got_l
            content = "".join(json.dumps(redact_json(r), ensure_ascii=False, sort_keys=True) + "\n" for r in records)
            rel = Path("cases") / f"{stem}.jsonl"
            entry = {"id": stem, **base, "format": kind, "data": rel.as_posix(), "records": len(records)}
        else:
            got = extract_failing_json(obj)
            if got is None:
                return None
            kind, data = got
            content = json.dumps(redact_json(data), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            rel = Path("cases") / f"{stem}.json"
            entry = {"id": stem, **base, "format": kind, "data": rel.as_posix()}
    else:
        return None
    entry["sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return entry, PendingCorpusWrite(rel_path=rel, content=content)


def write_manifest_atomic(manifest_path: Path, entries: list[dict]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    tmp.replace(manifest_path)


def apply_harvest_success(corpus_dir: Path, manifest_path: Path, entries: list[dict], pending: list[PendingCorpusWrite]) -> None:
    for item in pending:
        out_path = corpus_dir / item.rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(item.content, encoding="utf-8")
    write_manifest_atomic(manifest_path, entries)


def _split(entries: list[dict], pending: list[PendingCorpusWrite]) -> tuple[list[dict], list[PendingCorpusWrite], list[dict], list[PendingCorpusWrite]]:
    xml_e = [e for e in entries if "data" not in e]
    json_e = [e for e in entries if "data" in e]
    json_paths = {e["data"] for e in json_e}
    json_p = [p for p in pending if p.rel_path.as_posix() in json_paths]
    xml_p = [p for p in pending if p.rel_path.as_posix() not in json_paths]
    return xml_e, xml_p, json_e, json_p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--allow-partial", action="store_true", help="write what succeeded even if some runs/artifacts failed (for scheduled harvests)")
    ap.add_argument("--per-repo", type=int, default=5, help="max new corpus files per repository per harvest")
    ap.add_argument("--report", type=Path, help="write a JSON summary (counts, failures) here")
    args = ap.parse_args(argv if argv is not None else [])
    existing_xml = _load_manifest(MANIFEST)
    existing_json = _load_manifest(RESULTS_MANIFEST)
    entries, failures, pending = harvest(entries_limit_per_repo=args.per_repo, existing=existing_xml + existing_json)
    report = {
        "new_entries": len(entries),
        "by_repo": {},
        "failures": [f.as_dict() for f in failures],
    }
    for e in entries:
        repo_name = e.get("repo", "?")
        report["by_repo"][repo_name] = report["by_repo"].get(repo_name, 0) + 1
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    hard = [f for f in failures if f.stage != "artifact_expired"] if args.allow_partial else failures
    if failures and not args.allow_partial:
        print(json.dumps({"entries": len(entries), "failures": report["failures"]}, indent=2), file=sys.stderr)
        return 1
    if hard:
        print(json.dumps({"partial_failures": [f.as_dict() for f in hard]}, indent=2), file=sys.stderr)
    xml_e, xml_p, json_e, json_p = _split(entries, pending)
    CORPUS.mkdir(parents=True, exist_ok=True)
    apply_harvest_success(CORPUS, MANIFEST, existing_xml + xml_e, xml_p)
    if json_e:
        RESULTS_CORPUS.mkdir(parents=True, exist_ok=True)
        apply_harvest_success(RESULTS_CORPUS, RESULTS_MANIFEST, existing_json + json_e, json_p)
    print(f"Wrote {len(xml_e)} new JUnit and {len(json_e)} new JSON/JSONL corpus entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
