#!/usr/bin/env python3
"""Harvested failure corpus: collect failed-run results, redact, name jobs, classify.

  python scripts/harvest_corpus.py harvest [--allow-partial] [--per-repo N] [--report F] [--summary F]
  python scripts/harvest_corpus.py redact [--corpus-only]   # re-apply shared redaction to fixture XML
  python scripts/harvest_corpus.py jobs                     # manifest ``job`` from workflow job names
  python scripts/harvest_corpus.py classify                 # triage report richness per corpus case

``harvest`` keeps only failures (redacted) of failed-run test results (JUnit XML, JSON, JSONL).

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

import argparse, hashlib, io, json, os, re, subprocess, sys, tempfile, xml.etree.ElementTree as ET, zipfile
from collections import Counter; from dataclasses import dataclass; from datetime import datetime, timedelta, timezone; from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
CORPUS = FIXTURES / "junit_corpus"
MANIFEST = CORPUS / "manifest.json"

sys.path.insert(0, str(ROOT))

from errlib.junit_redact import find_corpus_leaks, redact_attribute, redact_text, redact_xml_document

SOURCES = ROOT / "scripts" / "harvest_sources.json"
RESULTS_CORPUS = FIXTURES / "results_corpus"
RESULTS_MANIFEST = RESULTS_CORPUS / "manifest.json"

_SOURCES = json.loads(SOURCES.read_text(encoding="utf-8"))
ORG = _SOURCES["org"]
REPOS: list[str] = list(_SOURCES["repos"])
ARTIFACT_MARKERS: tuple[str, ...] = tuple(_SOURCES["artifact_markers"])
SKIP_WORKFLOWS = frozenset(_SOURCES["skip_workflows"])

MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
MAX_STRING_CHARS = 4000
MAX_RECORDS = 200
# Fleet result artifacts are uploaded with ``retention-days: 14``. A failed run last updated
# before that can only still hold long-lived receipts (failure-identity keeps 90 days), so the
# daily harvest has already had its chance at the raw results: it is outside the scope.
RUN_WINDOW_DAYS = 14
# Failed runs per repository dropped by that window in the latest ``list_failed_runs`` call.
OUTSIDE_WINDOW: dict[str, int] = {}
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
    artifact_id: int | None = None

    def as_dict(self) -> dict:
        return {
            "repo": self.repo,
            "run_id": self.run_id,
            "artifact": self.artifact,
            "stage": self.stage,
            "detail": self.detail,
            **({"artifact_id": self.artifact_id} if self.artifact_id is not None else {}),
        }


# fetch-guard: gh never prompts and every call ends within a deadline; a timeout is
# recorded as that stage's failure like any other gh error, and collection continues.
GH_TIMEOUT_SECONDS = 120
ARTIFACT_TIMEOUT_SECONDS = 600


def gh_env() -> dict:
    return {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat"}


def run_gh(args: list[str], failures: list[HarvestFailure], *, repo: str, run_id: int | None, artifact: str | None, stage: str) -> str | None:
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=GH_TIMEOUT_SECONDS,
            env=gh_env(),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        failures.append(HarvestFailure(repo=repo, run_id=run_id, artifact=artifact, stage=stage,
                                       detail=f"gh timed out after {GH_TIMEOUT_SECONDS}s"))
        return None
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


def list_failed_runs(repo: str, failures: list[HarvestFailure], limit: int = 15, *, now: datetime | None = None) -> list[dict]:
    """Failed runs, ignoring Copilot review runs (they never carry test results and used to
    crowd test runs out of the window) and runs older than ``RUN_WINDOW_DAYS``."""
    runs = _list_failed_runs_raw(repo, failures, limit=limit * 3)
    kept = [r for r in runs if (r.get("workflowName") or "") not in SKIP_WORKFLOWS]
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=RUN_WINDOW_DAYS)
    recent = [r for r in kept if not _older_than(r, cutoff)]
    OUTSIDE_WINDOW[repo] = len(kept) - len(recent)
    return recent[:limit]


def _older_than(run: dict, cutoff: datetime) -> bool:
    """True only for a readable timestamp before ``cutoff``; an unknown age stays in scope."""
    stamp = run.get("updatedAt") or run.get("createdAt")
    if not isinstance(stamp, str):
        return False
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if when.tzinfo is None:
        return False
    return when < cutoff


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
            "databaseId,displayTitle,createdAt,updatedAt,workflowName",
        ],
        failures,
        repo=repo,
        run_id=None,
        artifact=None,
        stage="list_failed_runs",
    )
    if out is None:
        return []
    try:
        runs = json.loads(out)
        if not isinstance(runs, list) or not all(isinstance(r, dict) and isinstance(r.get("databaseId"), int) for r in runs):
            raise ValueError("expected run list with integer databaseId")
        return runs
    except ValueError as exc:
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
            item = json.loads(line)
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not _valid_artifact_id(item.get("id")) or not isinstance(item.get("expired"), bool):
                raise ValueError("expected artifact name/id/expired")
            items.append(item)
        except ValueError as exc:
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


def _valid_artifact_id(value: object) -> bool:
    return type(value) is int and value > 0


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
    *,
    artifact_id: int,
) -> bool:
    if not _valid_artifact_id(artifact_id):
        failures.append(HarvestFailure(repo, run_id, name, "download_artifact", "invalid listed artifact id"))
        return False
    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{ORG}/{repo}/actions/artifacts/{artifact_id}/zip"],
            capture_output=True,
            check=False,
            timeout=ARTIFACT_TIMEOUT_SECONDS,
            env=gh_env(),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        failures.append(HarvestFailure(repo=repo, run_id=run_id, artifact=name, stage="download_artifact",
                                       detail=f"gh timed out after {ARTIFACT_TIMEOUT_SECONDS}s", artifact_id=artifact_id))
        return False
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip() or f"download exit {proc.returncode}"
        failures.append(HarvestFailure(repo=repo, run_id=run_id, artifact=name, stage="download_artifact", detail=detail[:500], artifact_id=artifact_id))
        return False
    try:
        safe_extract(proc.stdout, dest)
    except zipfile.BadZipFile as exc:
        failures.append(HarvestFailure(repo=repo, run_id=run_id, artifact=name, stage="download_artifact", detail=f"bad zip: {exc}"[:500], artifact_id=artifact_id))
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
    from errlib.junit_redact import ISO_TIMESTAMP_RE

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
    keys: set[tuple] = set()
    hashes: set[str] = set()
    for e in entries:
        digest = e.get("sha256")
        if not digest:
            path = None
            if e.get("xml"):
                path = CORPUS / e["xml"]
            elif e.get("data"):
                path = RESULTS_CORPUS / e["data"]
            if path is not None and path.is_file():
                digest = hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        if digest:
            hashes.add(digest)
        if _valid_artifact_id(e.get("artifact_id")):
            keys.add((e.get("repo"), e.get("run_id"), e["artifact_id"], e.get("source_file", "*")))
        elif not digest:
            # Legacy identity without readable contents: retain its original
            # name/path fallback, never assign it a guessed artifact ID.
            keys.add((e.get("repo"), e.get("run_id"), e.get("artifact"), e.get("source_file", "*")))
    return keys, hashes


def harvest(
    entries_limit_per_repo: int = 5,
    existing: list[dict] | None = None,
    coverage: dict | None = None,
) -> tuple[list[dict], list[HarvestFailure], list[PendingCorpusWrite]]:
    """Return new entries for all formats. JUnit entries carry ``xml``/``golden``; JSON/JSONL
    entries carry ``format`` and ``data`` (path relative to the results corpus)."""
    failures: list[HarvestFailure] = []
    entries: list[dict] = []
    pending: list[PendingCorpusWrite] = []
    seen_keys, seen_hashes = _existing_keys(existing or [])

    coverage = coverage if coverage is not None else {}
    for repo in REPOS:
        count = 0
        start = len(failures)
        checked = 0
        runs = list_failed_runs(repo, failures)
        for run in runs:
            if count >= entries_limit_per_repo:
                break
            run_id = int(run["databaseId"])
            listed = list_artifacts(repo, run_id, failures)
            ids_by_name: dict[str, set[int]] = {}
            for art in listed:
                ids_by_name.setdefault(art["name"], set()).add(art["id"])
            for art in listed:
                if count >= entries_limit_per_repo:
                    break
                name, artifact_id = art["name"], art["id"]
                if not is_junit_artifact(name):
                    continue
                if art.get("expired"):
                    failures.append(
                        HarvestFailure(repo=repo, run_id=run_id, artifact=name, stage="artifact_expired", detail="artifact expired", artifact_id=artifact_id)
                    )
                    continue
                with tempfile.TemporaryDirectory() as tmp:
                    dest = Path(tmp)
                    if not download_artifact(repo, run_id, name, dest, failures, artifact_id=artifact_id):
                        continue
                    files = sorted(p for p in dest.rglob("*") if p.is_file())
                    valid_files = [p for p in files if is_valid_result(p)]
                    if not valid_files:
                        failures.append(HarvestFailure(repo, run_id, name, "inspect_artifact", "no valid test result files", artifact_id=artifact_id))
                    checked += len(valid_files)
                    for path in files:
                        if count >= entries_limit_per_repo:
                            break
                        rel_src = path.relative_to(dest).as_posix()
                        prefix = (f"{ORG}/{repo}", run_id)
                        key = (*prefix, artifact_id, rel_src)
                        if key in seen_keys or (*prefix, artifact_id, "*") in seen_keys:
                            continue
                        # An old manifest has no attempt-specific ID. Its name
                        # still deduplicates a unique artifact, but must not
                        # mask another artifact with that name after a rerun.
                        if len(ids_by_name[name]) == 1 and (
                            (*prefix, name, rel_src) in seen_keys or (*prefix, name, "*") in seen_keys
                        ):
                            continue
                        made = _harvest_file(repo, run, name, path, rel_src, failures, artifact_id=artifact_id)
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
        issues = failures[start:]
        confirmed_empty = not runs and not issues
        usable = checked > 0 or confirmed_empty
        coverage[repo] = {
            "status": ("partial" if issues else "complete") if usable else "unavailable",
            "failed_runs": len(runs),
            "checked_files": checked,
            "no_failed_runs": confirmed_empty,
            "failures": [f.as_dict() for f in issues],
        }
        if not usable and not issues:
            failure = HarvestFailure(repo, None, None, "no_targets", "no valid collection targets confirmed")
            failures.append(failure)
            coverage[repo]["failures"].append(failure.as_dict())
    return entries, failures, pending


def is_valid_result(path: Path) -> bool:
    """Count readable result documents, including documents with no failing cases."""
    try:
        if path.suffix.lower() == ".xml":
            return ET.parse(path).getroot().tag.split("}")[-1] in ("testsuite", "testsuites")
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            try:
                obj = json.loads(text)
            except ValueError:
                pass  # Some reporters put JSONL in a .json file.
            else:
                records = obj if isinstance(obj, list) else [obj]
                return any(isinstance(r, dict) and (
                    any(k in r for k in ("suites", "failures", "failed", "errors", "tests", "results", "cases", "records"))
                    or bool(_state(r))
                ) for r in records)
        if path.suffix.lower() in (".json", ".jsonl", ".ndjson", ".txt", ".log"):
            records = [json.loads(line) for line in text.splitlines() if line.strip()]
            return any(isinstance(r, dict) and (bool(_state(r)) or r.get("type") in ("testStart", "testDone", "suite", "error")) for r in records)
    except (ValueError, OSError, UnicodeError, ET.ParseError):
        return False
    return False


def report_markdown(report: dict) -> str:
    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("<", "&lt;").replace(">", "&gt;")

    lines = ["## Harvest coverage", "", f"Status: **{report['status']}**; new entries: {report['new_entries']}",
             "", "| Repository | Status | Checked files |", "| --- | --- | --- |"]
    for repo, item in report["repositories"].items():
        lines.append(f"| {cell(repo)} | {item['status']} | {item['checked_files']} |")
    older = [(repo, item["older_failed_runs"]) for repo, item in report["repositories"].items() if item.get("older_failed_runs")]
    if older:
        lines += ["", f"Failed runs last updated more than {report['run_window_days']} days ago (raw result retention) are outside the scope:", ""]
        lines.extend(f"- {cell(repo)}: {count}" for repo, count in older)
    lines += ["", "### Collection failures / expired artifacts", ""]
    for f in report["failures"]:
        artifact_id = f" (ID {f['artifact_id']})" if f.get('artifact_id') is not None else ""
        lines.append(f"- {cell(f['repo'])}, run {f['run_id']}, {cell(f['artifact'])}{artifact_id}: {cell(f['stage'])}: {cell(f['detail'])}")
    if not report["failures"]:
        lines.append("- None")
    lines += ["", "### New entries by repository", ""]
    lines.extend(f"- {cell(repo)}: {count}" for repo, count in sorted(report["by_repo"].items()))
    if not report["by_repo"]:
        lines.append("- No new corpus entries in the confirmed scope; unavailable repositories remain unknown.")
    return "\n".join(lines) + "\n"


def _harvest_file(
    repo: str, run: dict, artifact: str, path: Path, rel_src: str, failures: list[HarvestFailure],
    *, artifact_id: int | None = None,
) -> tuple[dict, PendingCorpusWrite] | None:
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
    if artifact_id is not None:
        base["artifact_id"] = artifact_id
        # Keep the identity suffix outside the clipped display slug.
        source_key = hashlib.sha256(rel_src.encode("utf-8")).hexdigest()
        stem = f"{stem}_{artifact_id}_{source_key}"
    if suffix == ".xml":
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            failures.append(HarvestFailure(repo=repo, run_id=run_id, artifact=artifact, stage="parse_xml", detail=str(exc)[:500], artifact_id=artifact_id))
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


def run_harvest(args: argparse.Namespace) -> int:
    """``harvest``: append new failing results to both manifests (nothing is written when blocked)."""
    existing_xml = _load_manifest(MANIFEST)
    existing_json = _load_manifest(RESULTS_MANIFEST)
    coverage: dict = {}
    OUTSIDE_WINDOW.clear()
    entries, failures, pending = harvest(entries_limit_per_repo=args.per_repo, existing=existing_xml + existing_json, coverage=coverage)
    for repo, item in coverage.items():
        item["older_failed_runs"] = OUTSIDE_WINDOW.get(repo, 0)
    usable = any(item["status"] in ("complete", "partial") for item in coverage.values())
    blocked = not usable or (bool(failures) and not args.allow_partial)
    status = "unavailable" if not usable else ("partial" if failures else "complete")
    report = {
        "status": status,
        "exit_code": int(blocked),
        "repositories": coverage,
        "new_entries": len(entries),
        "run_window_days": RUN_WINDOW_DAYS,
        "by_repo": {},
        "failures": [f.as_dict() for f in failures],
    }
    for e in entries:
        repo_name = e.get("repo", "?")
        report["by_repo"][repo_name] = report["by_repo"].get(repo_name, 0) + 1
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.summary:
        args.summary.write_text(report_markdown(report), encoding="utf-8")
    if failures or blocked:
        print(json.dumps(report, indent=2), file=sys.stderr)
    if blocked:
        return 1
    xml_e, xml_p, json_e, json_p = _split(entries, pending)
    CORPUS.mkdir(parents=True, exist_ok=True)
    apply_harvest_success(CORPUS, MANIFEST, existing_xml + xml_e, xml_p)
    if json_e:
        RESULTS_CORPUS.mkdir(parents=True, exist_ok=True)
        apply_harvest_success(RESULTS_CORPUS, RESULTS_MANIFEST, existing_json + json_e, json_p)
    print(f"Wrote {len(xml_e)} new JUnit and {len(json_e)} new JSON/JSONL corpus entries")
    return 0


# ──── redact ────

def redact_corpus(corpus_only: bool = False) -> int:
    """``redact``: re-apply JUnit redaction to checked-in fixture XML; 1 when a leak remains."""
    scope = CORPUS if corpus_only else FIXTURES
    paths = sorted(scope.rglob("*.xml"))
    entries = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else []
    changed_hashes = {}
    updated = 0
    errors = 0
    leaks_after: list[tuple[str, list[str]]] = []
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        try:
            new = redact_xml_document(raw)
        except Exception as exc:
            print(f"SKIP {path.relative_to(ROOT)}: {exc}", file=sys.stderr)
            errors += 1
            continue
        if new != raw:
            path.write_text(new, encoding="utf-8")
            updated += 1
            if path.is_relative_to(CORPUS):
                changed_hashes[path.relative_to(CORPUS).as_posix()] = hashlib.sha256(new.encode("utf-8")).hexdigest()
        post_leaks = find_corpus_leaks(new)
        if post_leaks:
            leaks_after.append((str(path.relative_to(ROOT)), post_leaks))
    manifest_changed = False
    for entry in entries:
        digest = changed_hashes.get(entry.get("xml"))
        if digest is not None and "sha256" in entry:
            entry["sha256"] = digest
            manifest_changed = True
    if manifest_changed:
        MANIFEST.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    print(f"Re-redacted {updated}/{len(paths)} XML files under {scope.relative_to(ROOT)}")
    if leaks_after:
        for rel, labels in leaks_after[:20]:
            print(f"LEAK {rel}: {', '.join(labels)}", file=sys.stderr)
        print(f"{len(leaks_after)} files still leak after redaction", file=sys.stderr)
        return 1
    return int(bool(errors))


# ──── jobs ────

PYTHON_VER_RE = re.compile(
    r"junit-(?:controlled-)?(?:python-)?(?:py)?(\d+\.\d+)",
    re.IGNORECASE,
)
PYTHON_VERX_RE = re.compile(r"junit-py(\d+)\.x\b", re.IGNORECASE)


def run_jobs(repo: str, run_id: int) -> list[dict]:
    # fetch-guard via run_gh: no prompt, deadline; a failed or stuck call means no jobs.
    out = run_gh(["run", "view", str(run_id), "--repo", repo, "--json", "jobs"], [],
                 repo=repo, run_id=run_id, artifact=None, stage="run_jobs")
    if out is None:
        return []
    data = json.loads(out)
    return data.get("jobs") or []


def _python_version_keys(artifact: str) -> list[str]:
    keys: list[str] = []
    m = PYTHON_VER_RE.search(artifact)
    if m:
        keys.append(m.group(1))
        keys.append(f"({m.group(1)})")
    mx = PYTHON_VERX_RE.search(artifact)
    if mx:
        keys.append("3.x")
        keys.append("py3.x")
    return keys


def resolve_job(artifact: str, jobs: list[dict]) -> str | None:
    names = [j.get("name") or "" for j in jobs if j.get("name")]
    if not names:
        return None

    art_low = artifact.lower()
    is_setup_report = art_low.startswith("junit-setup-")
    version_artifact = "junit-" + art_low.removeprefix("junit-setup-") if is_setup_report else artifact
    for key in _python_version_keys(version_artifact):
        matches = [n for n in names if key in n]
        if len(matches) == 1:
            return matches[0]

    # Preserve the owning job of each phase; never assign setup to a test
    # job just because both names contain "test" or "collect".
    if art_low in {"setup-identity", "failure-identity"}:
        exact = [name for name in names if name.lower() == f"{art_low} / collect"]
        if len(exact) == 1:
            return exact[0]
    if is_setup_report:
        phase = art_low.removeprefix("junit-setup-")
        normalized = [(name, "-".join(re.findall(r"[a-z0-9]+", name.lower()))) for name in names]
        exact = [name for name, norm in normalized if phase == norm]
        matches = exact or [name for name, norm in normalized if phase and phase in norm]
        return matches[0] if len(matches) == 1 else None

    slug = art_low.replace("junit-", "").replace("controlled-", "")
    for name in names:
        norm = name.lower().replace(" ", "-").replace("(", "").replace(")", "")
        if slug and slug in norm:
            if sum(1 for n in names if slug in n.lower().replace(" ", "-")) == 1:
                return name

    keyword_sets = [
        ("flutter-refactor", ("flutter", "refactor")),
        ("flutter-core", ("flutter", "core")),
        ("junit-marp", ("marp",)),
        ("failure-identity", ("identity", "collect")),
        ("junit-controlled", ("controlled", "identity")),
    ]
    for art_needle, job_tokens in keyword_sets:
        if art_needle in art_low:
            matches = [
                n
                for n in names
                if all(tok in n.lower() for tok in job_tokens)
            ]
            if len(matches) == 1:
                return matches[0]

    return None


def patch_manifest_jobs() -> None:
    """``jobs``: set each manifest ``job`` by matching its artifact name to the run's job names."""
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cache: dict[tuple[str, int], list[dict]] = {}
    for entry in entries:
        key = (entry["repo"], int(entry["run_id"]))
        if key not in cache:
            cache[key] = run_jobs(entry["repo"], key[1])
        entry["job"] = resolve_job(entry["artifact"], cache[key])
    MANIFEST.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    resolved = sum(1 for e in entries if e.get("job"))
    print(f"Patched {len(entries)} entries ({resolved} resolved, {len(entries) - resolved} null)")


# ──── classify ────

KEY_FOUND = (
    "junit pytest exception type",
    "junit dart error (first)",
    "junit collection error (first)",
    "junit system-err (first line)",
    "junit testcase classname (first)",
    "junit playwright error",
    "junit collection import mismatch",
)


def classify(lines: list[str]) -> str:
    found = [ln for ln in lines if ln.startswith("FOUND:")]
    if not any("junit failures" in ln or "junit errors" in ln for ln in found):
        return "unknown"
    rich = sum(1 for key in KEY_FOUND if any(key in ln for ln in found))
    if rich >= 2:
        return "well_handled"
    if rich == 1:
        return "partial"
    return "unknown"


def classify_corpus() -> dict:
    """``classify``: write ``classification.json`` and return counts per bucket."""
    from tests.support import triage_cli

    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = []
    counts = Counter()
    for entry in entries:
        xml = CORPUS / entry["xml"]
        lines = triage_cli("--junit", xml, check=False).splitlines()
        bucket = classify(lines)
        counts[bucket] += 1
        rows.append({**entry, "classification": bucket})
    out = CORPUS / "classification.json"
    out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(counts), indent=2))
    return dict(counts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("harvest", help="collect failing results of failed runs (redacted) into the corpus manifests")
    cmd.add_argument("--allow-partial", action="store_true", help="write what succeeded even if some runs/artifacts failed (for scheduled harvests)")
    cmd.add_argument("--per-repo", type=int, default=5, help="max new corpus files per repository per harvest")
    cmd.add_argument("--report", type=Path, help="write a JSON summary (counts, failures) here")
    cmd.add_argument("--summary", type=Path, help="write Markdown for job summary and generated PR body")
    cmd = sub.add_parser("redact", help="re-apply JUnit redaction to checked-in fixture XML")
    cmd.add_argument("--corpus-only", action="store_true",
                     help="Only process the harvested JUnit corpus, preserving other fixtures")
    sub.add_parser("jobs", help="set manifest job fields from workflow job names (gh run view)")
    sub.add_parser("classify", help="classify corpus cases by triage report richness")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "harvest":
        return run_harvest(args)
    if args.command == "redact":
        return redact_corpus(args.corpus_only)
    if args.command == "jobs":
        patch_manifest_jobs()
    else:
        classify_corpus()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
