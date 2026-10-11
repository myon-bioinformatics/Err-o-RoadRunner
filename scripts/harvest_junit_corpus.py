#!/usr/bin/env python3
"""Download failed-run JUnit XML and keep only failing testcases (redacted)."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "junit_corpus"
MANIFEST = CORPUS / "manifest.json"

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

SECRET_PATTERNS = [
    (re.compile(r"ghs_[A-Za-z0-9_]{20,}"), "ghs_<redacted>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]+"), "Bearer <redacted>"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk-<redacted>"),
    (re.compile(r"/home/runner/work/[^/]+/[^/]+"), "<runner-work>"),
    (re.compile(r"/Users/[^/\s]+"), "<user-home>"),
]


def redact(text: str) -> str:
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def run_gh(args: list[str]) -> str:
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout


def list_failed_runs(repo: str, limit: int = 15) -> list[dict]:
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
        ]
    )
    if not out.strip():
        return []
    return json.loads(out)


def list_artifacts(repo: str, run_id: int) -> list[dict]:
    out = run_gh(
        [
            "api",
            f"repos/{ORG}/{repo}/actions/runs/{run_id}/artifacts",
            "--jq",
            ".artifacts[] | {name,id,expired}",
        ]
    )
    if not out.strip():
        return []
    items = []
    for line in out.splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


def download_artifact(repo: str, run_id: int, name: str, dest: Path) -> bool:
    proc = subprocess.run(
        ["gh", "run", "download", str(run_id), "--repo", f"{ORG}/{repo}", "-n", name],
        cwd=dest,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def find_junit_xml(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*.xml"))


def _clone_element(elem: ET.Element) -> ET.Element:
    tag = elem.tag.split("}")[-1]
    attribs = {k: redact(v) for k, v in elem.attrib.items()}
    clone = ET.Element(tag, attrib=attribs)
    clone.text = redact(elem.text) if elem.text else elem.text
    clone.tail = redact(elem.tail) if elem.tail else elem.tail
    for child in elem:
        clone.append(_clone_element(child))
    return clone


def extract_failing_xml(tree: ET.ElementTree) -> ET.ElementTree | None:
    root = tree.getroot()
    tag = root.tag.split("}")[-1]
    if tag == "testsuites":
        suites = [c for c in root if c.tag.split("}")[-1] == "testsuite"]
    elif tag == "testsuite":
        suites = [root]
    else:
        return None

    new_root = ET.Element("testsuites")
    kept_any = False
    for suite in suites:
        new_suite = ET.Element("testsuite", attrib=dict(suite.attrib))
        for testcase in suite:
            if testcase.tag.split("}")[-1] != "testcase":
                continue
            fail_children = [
                c
                for c in testcase
                if c.tag.split("}")[-1] in ("failure", "error")
            ]
            if not fail_children:
                continue
            tc_attribs = {k: redact(v) for k, v in testcase.attrib.items()}
            new_tc = ET.Element("testcase", attrib=tc_attribs)
            for child in testcase:
                if child.tag.split("}")[-1] in ("failure", "error", "system-out", "system-err"):
                    new_tc.append(_clone_element(child))
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


def main() -> None:
    CORPUS.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    seen_hashes: set[str] = set()

    for repo in REPOS:
        for run in list_failed_runs(repo):
            run_id = int(run["databaseId"])
            title = run.get("displayTitle") or ""
            if "Copilot Code Review" in title and "pytest" not in title.lower():
                pass  # still try artifacts; some copilot runs lack junit
            for art in list_artifacts(repo, run_id):
                if art.get("expired"):
                    continue
                name = art["name"]
                if not (
                    name.startswith("junit")
                    or name == "failure-identity"
                    or name == "python-failure-identity"
                    or "junit" in name.lower()
                ):
                    continue
                with tempfile.TemporaryDirectory() as tmp:
                    dest = Path(tmp)
                    if not download_artifact(repo, run_id, name, dest):
                        continue
                    for xml_path in find_junit_xml(dest):
                        try:
                            tree = ET.parse(xml_path)
                        except ET.ParseError:
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
                        out_path = CORPUS / rel
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        out_path.write_text(xml_text, encoding="utf-8")
                        entries.append(
                            {
                                "id": case_id,
                                "repo": f"{ORG}/{repo}",
                                "run_id": run_id,
                                "artifact": name,
                                "job": name,  # patched later by patch_junit_manifest_jobs.py
                                "date": run.get("createdAt", ""),
                                "xml": str(rel.as_posix()),
                                "golden": str(
                                    Path("golden") / f"{case_id}.out"
                                ),
                            }
                        )
                        if len([e for e in entries if repo in e["id"]]) >= 5:
                            break
            if len([e for e in entries if e["id"].startswith(repo)]) >= 5:
                continue

    MANIFEST.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(entries)} corpus entries to {CORPUS}")


if __name__ == "__main__":
    main()
