#!/usr/bin/env python3
"""Download failed-run JUnit XML and keep only failing testcases (redacted)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "junit_corpus"
MANIFEST = CORPUS / "manifest.json"

sys.path.insert(0, str(ROOT / "src"))

from err_o_roadrunner.junit_redact import redact_attribute, redact_text

ORG = "myon-bioinformatics"
REPOS = [
    "gh_identity",
    "Err-o-RoadRunner",
    "Ironmate",
    "mcp-toolcall-lab",
    "browser-test-kit",
    "flutter_navigation_basic",
    "Aoi",
]


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
            "databaseId,displayTitle,createdAt",
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
    return (
        name.startswith("junit")
        or name in ("failure-identity", "python-failure-identity")
        or "junit" in name.lower()
    )


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


def download_artifact(
    repo: str,
    run_id: int,
    name: str,
    dest: Path,
    failures: list[HarvestFailure],
) -> bool:
    proc = subprocess.run(
        ["gh", "run", "download", str(run_id), "--repo", f"{ORG}/{repo}", "-n", name],
        cwd=dest,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"download exit {proc.returncode}"
        failures.append(
            HarvestFailure(
                repo=repo,
                run_id=run_id,
                artifact=name,
                stage="download_artifact",
                detail=detail[:500],
            )
        )
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


def serialize_xml(tree: ET.ElementTree) -> str:
    xml = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        tree.getroot(), encoding="unicode"
    )
    ET.fromstring(xml)
    return xml


def harvest(
    entries_limit_per_repo: int = 5,
) -> tuple[list[dict], list[HarvestFailure], list[PendingCorpusWrite]]:
    failures: list[HarvestFailure] = []
    entries: list[dict] = []
    pending: list[PendingCorpusWrite] = []
    seen_hashes: set[str] = set()

    for repo in REPOS:
        for run in list_failed_runs(repo, failures):
            run_id = int(run["databaseId"])
            for art in list_artifacts(repo, run_id, failures):
                name = art["name"]
                if not is_junit_artifact(name):
                    continue
                if art.get("expired"):
                    failures.append(
                        HarvestFailure(
                            repo=repo,
                            run_id=run_id,
                            artifact=name,
                            stage="artifact_expired",
                            detail="artifact expired",
                        )
                    )
                    continue
                with tempfile.TemporaryDirectory() as tmp:
                    dest = Path(tmp)
                    if not download_artifact(repo, run_id, name, dest, failures):
                        continue
                    for xml_path in find_junit_xml(dest):
                        try:
                            tree = ET.parse(xml_path)
                        except ET.ParseError as exc:
                            failures.append(
                                HarvestFailure(
                                    repo=repo,
                                    run_id=run_id,
                                    artifact=name,
                                    stage="parse_xml",
                                    detail=str(exc)[:500],
                                )
                            )
                            continue
                        trimmed = extract_failing_xml(tree)
                        if trimmed is None:
                            continue
                        xml_text = serialize_xml(trimmed)
                        digest = str(hash(xml_text))
                        if digest in seen_hashes:
                            continue
                        seen_hashes.add(digest)
                        case_id = f"{repo}_{run_id}_{name}_{len(entries)}"
                        rel = Path("cases") / f"{case_id}.xml"
                        pending.append(PendingCorpusWrite(rel_path=rel, content=xml_text))
                        entries.append(
                            {
                                "id": case_id,
                                "repo": f"{ORG}/{repo}",
                                "run_id": run_id,
                                "artifact": name,
                                "job": name,
                                "date": run.get("createdAt", ""),
                                "xml": str(rel.as_posix()),
                                "golden": str(Path("golden") / f"{case_id}.out"),
                            }
                        )
                        if len([e for e in entries if repo in e["id"]]) >= entries_limit_per_repo:
                            break
            if len([e for e in entries if e["id"].startswith(repo)]) >= entries_limit_per_repo:
                continue
    return entries, failures, pending


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


def main() -> int:
    entries, failures, pending = harvest()
    if failures:
        report = {
            "entries": len(entries),
            "failures": [f.as_dict() for f in failures],
        }
        print(json.dumps(report, indent=2), file=sys.stderr)
        return 1
    CORPUS.mkdir(parents=True, exist_ok=True)
    apply_harvest_success(CORPUS, MANIFEST, entries, pending)
    print(f"Wrote {len(entries)} corpus entries to {CORPUS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
