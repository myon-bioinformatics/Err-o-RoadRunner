"""Shape a public ErrORR footprint into an ONTO graph, JSONL events, and a Mermaid view.

  python3 -S scripts/usage_graph.py from-checkout
  python3 -S scripts/usage_graph.py check docs/usage/fleet-footprint.json --events docs/usage/triage-events.jsonl
  python3 -S scripts/usage_graph.py graph docs/usage/fleet-footprint.json
  python3 -S scripts/usage_graph.py mermaid docs/usage/fleet-footprint.json
  python3 -S scripts/usage_graph.py html docs/usage/fleet-footprint.json -o fleet.html

``from-checkout`` reads the adoption snapshot, harvest source list, and JUnit corpus
goldens already in this repository. It does not call GitHub. Mermaid and HTML use
ONTO ``knowledgegraph`` when that module is on ``PYTHONPATH``; this repository does
not pin ONTO. Classification matches ``scripts/harvest_corpus.py`` ``classify``.
Python 3.9+ standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOOTPRINT_SCHEMA = "errorr-footprint/v1"
EVENT_SCHEMA = "errorr-triage-event/v1"
ADOPTION_SCHEMA = "errorr-adoption/v1"

# Same richness keys as scripts/harvest_corpus.py classify().
KEY_FOUND = (
    "junit pytest exception type",
    "junit dart error (first)",
    "junit collection error (first)",
    "junit system-err (first line)",
    "junit testcase classname (first)",
    "junit playwright error",
    "junit collection import mismatch",
)

FEATURES = (
    ("triage", "triage"),
    ("digest", "digest"),
    ("sentinel", "sentinel"),
    ("corpus-harvest", "corpus harvest"),
)

# Domain tuple, range class. Same shape ontology.violations() reads.
VOCABULARY = {
    "exposes": (["Tool"], "Feature"),
    "observed_use": (["Repository"], "Feature"),
    "harvests": (["Feature"], "Repository"),
    "triaged": (["Repository"], "Outcome"),
    "same_as": (["Repository"], "Repository"),
}

_REPO = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-/")


def classify_lines(lines):
    """Return well_handled, partial, or unknown. Same rule as harvest_corpus.classify."""
    found = [line for line in lines if line.startswith("FOUND:")]
    if not any("junit failures" in line or "junit errors" in line for line in found):
        return "unknown"
    rich = sum(1 for key in KEY_FOUND if any(key in line for line in found))
    if rich >= 2:
        return "well_handled"
    if rich == 1:
        return "partial"
    return "unknown"


def _found_tail(line, label):
    prefix = "FOUND: " + label + " "
    if not line.startswith(prefix) or "): " not in line:
        return None
    return line.rsplit("): ", 1)[-1].strip()


def _count(line, label):
    tail = _found_tail(line, label)
    if tail is None or not tail.isdigit():
        return None
    return int(tail)


def event_from_golden(entry, text):
    """One errorr-triage-event/v1 object from a manifest row and its golden stdout."""
    lines = text.splitlines()
    exception_type = None
    failure_count = None
    error_count = None
    for line in lines:
        if exception_type is None:
            exception_type = _found_tail(line, "junit pytest exception type")
            if exception_type is None:
                exception_type = _found_tail(line, "junit dart error (first)")
        if failure_count is None:
            failure_count = _count(line, "junit failures")
        if error_count is None:
            error_count = _count(line, "junit errors")
    if exception_type is not None and len(exception_type) > 80:
        exception_type = exception_type[:79] + "…"
    found = [line for line in lines if line.startswith("FOUND:")]
    found_keys = [key for key in KEY_FOUND if any(key in line for line in found)]
    repository = entry["repo"]
    run_id = int(entry["run_id"])
    return {
        "schema": EVENT_SCHEMA,
        "repository": repository,
        "run_id": run_id,
        "run_url": "https://github.com/%s/actions/runs/%s" % (repository, run_id),
        "artifact": entry.get("artifact") or "",
        "case_id": entry["id"],
        "observed_at": entry.get("date") or "",
        "classification": classify_lines(lines),
        "exception_type": exception_type,
        "failure_count": failure_count,
        "error_count": error_count,
        "found_keys": found_keys,
    }


def _require_repo(value, where):
    if not isinstance(value, str) or value.count("/") != 1:
        raise ValueError("%s must be owner/name" % where)
    if any(ch not in _REPO for ch in value) or value.startswith("/") or ".." in value.split("/"):
        raise ValueError("%s has an unexpected character" % where)
    return value


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path):
    digest = hashlib.sha256()
    digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def _select_manifest_row(corpus, rows):
    """Pick the row whose file is still on disk when several rows share one id.

    Exact copies collapse. Different rows that name the same golden are a
    collision: the event keeps the row whose ``sha256`` matches the XML bytes,
    and lists the others. A collision with no matching file raises ValueError.
    """
    unique = []
    seen = set()
    duplicates = 0
    for entry in rows:
        canonical = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        if canonical in seen:
            duplicates += 1
            continue
        seen.add(canonical)
        unique.append(entry)
    if len(unique) == 1:
        return unique[0], [], duplicates
    xml = Path(corpus) / unique[0]["xml"]
    if any(Path(corpus) / entry["xml"] != xml for entry in unique):
        raise ValueError("corpus id %s points at more than one file" % unique[0]["id"])
    file_sha = _sha256(xml)
    matched = [entry for entry in unique if entry.get("sha256") == file_sha]
    if len(matched) != 1:
        raise ValueError("corpus id %s collides and no single sha256 matches the file" % unique[0]["id"])
    omitted = [
        {"source_file": entry.get("source_file") or "", "sha256": entry.get("sha256") or ""}
        for entry in unique if entry is not matched[0]
    ]
    return matched[0], omitted, duplicates + len(omitted)


def events_from_corpus(corpus):
    """Read manifest.json and each golden. Returns ``(events, stats)``."""
    corpus = Path(corpus)
    manifest = load_json(corpus / "manifest.json")
    if not isinstance(manifest, list):
        raise ValueError("manifest.json must be a list")
    groups = {}
    order = []
    for entry in manifest:
        case_id = entry["id"]
        if case_id not in groups:
            groups[case_id] = []
            order.append(case_id)
        groups[case_id].append(entry)
    events = []
    duplicates = 0
    collisions = 0
    for case_id in order:
        entry, omitted, dropped = _select_manifest_row(corpus, groups[case_id])
        duplicates += dropped - len(omitted)
        collisions += len(omitted)
        _require_repo(entry["repo"], case_id)
        golden = corpus / entry["golden"]
        if not golden.is_file():
            raise ValueError("missing golden for %s" % case_id)
        event = event_from_golden(entry, golden.read_text(encoding="utf-8"))
        event["source_file"] = entry.get("source_file") or ""
        event["sha256"] = entry.get("sha256") or ""
        event["colliding_sources"] = omitted
        events.append(event)
    events.sort(key=lambda item: (item["observed_at"], item["repository"], item["case_id"]))
    stats = {
        "manifest_rows": len(manifest),
        "duplicate_manifest_rows": duplicates,
        "colliding_manifest_rows": collisions,
    }
    return events, stats


def _tally(events):
    by_repo = {}
    for event in events:
        bucket = by_repo.setdefault(event["repository"], {
            "cases": 0, "well_handled": 0, "partial": 0, "unknown": 0, "exception_types": Counter(),
        })
        bucket["cases"] += 1
        bucket[event["classification"]] += 1
        if event["exception_type"]:
            bucket["exception_types"][event["exception_type"]] += 1
    return by_repo


def _adoption_uses(adoption):
    uses = []
    for row in adoption.get("uses") or []:
        feature = row["feature"]
        if feature not in {item[0] for item in FEATURES}:
            raise ValueError("unknown feature %s" % feature)
        kind = row["kind"]
        if not isinstance(kind, str) or not kind or any(ch.isspace() for ch in kind):
            raise ValueError("kind must be a single token")
        uses.append({
            "repository": _require_repo(row["repository"], "uses.repository"),
            "feature": feature,
            "kind": kind,
            "evidence": row["evidence"],
        })
    return uses


def footprint_from_parts(adoption, harvest_sources, events, classification_rows=None, manifest_stats=None):
    """Build errorr-footprint/v1. Raises ValueError on a malformed adoption file."""
    if adoption.get("schema") != ADOPTION_SCHEMA:
        raise ValueError("adoption schema must be %s" % ADOPTION_SCHEMA)
    inventory = []
    for row in adoption.get("inventory") or []:
        inventory.append({
            "repository": _require_repo(row["repository"], "inventory.repository"),
            "archived": bool(row.get("archived")),
        })
    harvest = []
    for name in harvest_sources.get("repos") or []:
        harvest.append(_require_repo("myon-bioinformatics/" + name, "harvest source"))
    uses = _adoption_uses(adoption)
    renames = []
    for row in adoption.get("renames") or []:
        renames.append({
            "from": _require_repo(row["from"], "renames.from"),
            "to": _require_repo(row["to"], "renames.to"),
            "evidence": row.get("evidence") or "",
        })
    tallies = _tally(events)
    names = []
    seen = set()
    for row in inventory:
        if row["repository"] not in seen:
            names.append(row["repository"])
            seen.add(row["repository"])
    for name in harvest + list(tallies) + [row["repository"] for row in uses]:
        if name not in seen:
            names.append(name)
            seen.add(name)
    for row in renames:
        for name in (row["from"], row["to"]):
            if name not in seen:
                names.append(name)
                seen.add(name)
    archived = {row["repository"]: row["archived"] for row in inventory}
    use_by = {}
    for row in uses:
        use_by.setdefault(row["repository"], []).append(
            {"feature": row["feature"], "kind": row["kind"], "evidence": row["evidence"]}
        )
    repositories = []
    for name in names:
        bucket = tallies.get(name)
        triage = {
            "cases": 0, "well_handled": 0, "partial": 0, "unknown": 0, "exception_types": {},
        }
        if bucket:
            triage = {
                "cases": bucket["cases"],
                "well_handled": bucket["well_handled"],
                "partial": bucket["partial"],
                "unknown": bucket["unknown"],
                "exception_types": dict(sorted(bucket["exception_types"].items())),
            }
        repositories.append({
            "repository": name,
            "archived": archived.get(name, False),
            "harvest_source": name in set(harvest),
            "uses": use_by.get(name, []),
            "triage": triage,
        })
    totals = Counter()
    types = Counter()
    for bucket in tallies.values():
        for key in ("cases", "well_handled", "partial", "unknown"):
            totals[key] += bucket[key]
        types.update(bucket["exception_types"])
    return {
        "schema": FOOTPRINT_SCHEMA,
        "observed_at": adoption.get("observed_at") or "",
        "subject": "myon-bioinformatics/Err-o-RoadRunner",
        "scope": adoption.get("scope") or "",
        "vendor_entries_sourcing_errorr": int((adoption.get("vendor_consumers") or {}).get("entries_sourcing_errorr") or 0),
        "vendor_evidence": (adoption.get("vendor_consumers") or {}).get("evidence") or "",
        "workflow_invocations_outside_errorr": list((adoption.get("workflow_scan") or {}).get("invocations_outside_errorr") or []),
        "classification_file_rows": classification_rows,
        "manifest_rows": (manifest_stats or {}).get("manifest_rows", len(events)),
        "duplicate_manifest_rows": (manifest_stats or {}).get("duplicate_manifest_rows", 0),
        "colliding_manifest_rows": (manifest_stats or {}).get("colliding_manifest_rows", 0),
        "renames": renames,
        "totals": {
            "cases": totals["cases"],
            "well_handled": totals["well_handled"],
            "partial": totals["partial"],
            "unknown": totals["unknown"],
            "exception_types": dict(sorted(types.items())),
        },
        "repositories": repositories,
    }


def graph_from_footprint(doc):
    """ONTO graph: nodes[{id,type,name}] and edges[{source,relation,target,basis?}]."""
    if doc.get("schema") != FOOTPRINT_SCHEMA:
        raise ValueError("footprint schema must be %s" % FOOTPRINT_SCHEMA)
    nodes = [{"id": "tool:err-o-roadrunner", "type": "Tool", "name": "Err-o-RoadRunner"}]
    for feature_id, label in FEATURES:
        nodes.append({"id": "feature:" + feature_id, "type": "Feature", "name": label})
    edges = [
        {"source": "tool:err-o-roadrunner", "relation": "exposes", "target": "feature:" + feature_id}
        for feature_id, _label in FEATURES
    ]
    outcomes = set()
    repos = set()

    def add_repo(repository):
        node_id = "repo:" + repository
        if node_id in repos:
            return node_id
        repos.add(node_id)
        nodes.append({"id": node_id, "type": "Repository", "name": repository.split("/", 1)[1]})
        return node_id

    for row in doc.get("repositories") or []:
        repository = row["repository"]
        triage = row.get("triage") or {}
        linked = bool(row.get("uses")) or row.get("harvest_source") or triage.get("cases")
        if not linked:
            continue
        source = add_repo(repository)
        for use in row.get("uses") or []:
            edges.append({
                "source": source,
                "relation": "observed_use",
                "target": "feature:" + use["feature"],
                "basis": use["kind"],
            })
        if row.get("harvest_source"):
            edges.append({
                "source": "feature:corpus-harvest",
                "relation": "harvests",
                "target": source,
                "basis": "source-list",
            })
        for outcome, count in (
            ("well_handled", triage.get("well_handled") or 0),
            ("partial", triage.get("partial") or 0),
            ("unknown", triage.get("unknown") or 0),
        ):
            if not count:
                continue
            outcomes.add(outcome)
            edges.append({
                "source": source,
                "relation": "triaged",
                "target": "outcome:" + outcome,
                "basis": str(count),
            })
    for outcome in sorted(outcomes):
        nodes.append({"id": "outcome:" + outcome, "type": "Outcome", "name": outcome})
    known = {node["id"] for node in nodes}
    for row in doc.get("renames") or []:
        source = "repo:" + row["from"]
        target = "repo:" + row["to"]
        if source in known and target in known:
            edges.append({"source": source, "relation": "same_as", "target": target, "basis": "renamed"})
        elif source in known or target in known:
            # The other name may have been skipped as an isolated inventory row.
            if source not in known:
                add_repo(row["from"])
            if target not in known:
                add_repo(row["to"])
            edges.append({
                "source": "repo:" + row["from"],
                "relation": "same_as",
                "target": "repo:" + row["to"],
                "basis": "renamed",
            })
    nodes.sort(key=lambda node: node["id"])
    edges.sort(key=lambda edge: (edge["relation"], edge["source"], edge["target"], edge.get("basis") or ""))
    return {"nodes": nodes, "edges": edges}


def violations(graph, relations=None):
    """Same checks as ONTO ontology.violations for this node/edge shape."""
    relations = VOCABULARY if relations is None else relations
    known = set()
    for domains, range_type in relations.values():
        known.update(domains)
        known.add(range_type)
    findings = []
    nodes = {}
    for index, node in enumerate(graph["nodes"]):
        if node["id"] in nodes:
            findings.append({"kind": "duplicate-node", "node": index, "detail": "%s is listed twice" % node["id"]})
        nodes[node["id"]] = node
        if node["type"] not in known:
            findings.append({"kind": "unknown-class", "node": index, "detail": "%s has class %r" % (node["id"], node["type"])})
    for index, edge in enumerate(graph["edges"]):
        missing = [end for end in ("source", "target") if edge[end] not in nodes]
        for end in missing:
            findings.append({"kind": "missing-reference", "edge": index, "detail": "%s %s is not a known node" % (end, edge[end])})
        if edge["relation"] not in relations:
            findings.append({"kind": "unknown-relation", "edge": index, "detail": "unknown relation %r" % edge["relation"]})
        elif not missing:
            domains, range_type = relations[edge["relation"]]
            source = nodes[edge["source"]]["type"]
            target = nodes[edge["target"]]["type"]
            if source not in domains or target != range_type:
                findings.append({
                    "kind": "domain-range",
                    "edge": index,
                    "detail": "%s joins %s → %s, expected %s → %s" % (
                        edge["relation"], source, target, " | ".join(domains), range_type,
                    ),
                })
    return findings


def _import_knowledgegraph():
    try:
        import knowledgegraph
    except ImportError:
        return None
    return knowledgegraph


def mermaid_from_graph(graph):
    """Return a fenced Mermaid document from ONTO knowledgegraph.graph_mermaid."""
    knowledgegraph = _import_knowledgegraph()
    if knowledgegraph is None:
        raise RuntimeError("knowledgegraph is not importable; put an ONTO checkout on PYTHONPATH")
    return knowledgegraph.graph_mermaid(graph)


def html_from_graph(graph, title):
    """HTML page that draws the graph with ONTO's Mermaid renderer module."""
    knowledgegraph = _import_knowledgegraph()
    if knowledgegraph is None:
        raise RuntimeError("knowledgegraph is not importable; put an ONTO checkout on PYTHONPATH")
    diagram = knowledgegraph.graph_mermaid(graph)
    lines = diagram.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    source = "\n".join(lines).strip() + "\n"
    renderer = knowledgegraph.mermaid_renderer_js()
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>%s</title>
<style>body{font-family:sans-serif;margin:1.5rem} #diagram{overflow:auto}</style>
</head>
<body>
<h1>%s</h1>
<p>Drawn with ONTO knowledgegraph.graph_mermaid and mermaid_renderer_js.</p>
<div id="diagram"></div>
<script type="application/json" id="diagram-source">%s</script>
<script type="module">
%s
const source = JSON.parse(document.getElementById("diagram-source").textContent);
const svg = await renderDiagram("fleet", source);
document.getElementById("diagram").innerHTML = svg;
</script>
</body>
</html>
""" % (title, title, json.dumps(source), renderer)


def _dump(path, payload):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def write_events(path, events):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(event, ensure_ascii=False, sort_keys=True) for event in events]
    Path(path).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def read_events(path):
    events = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def check_footprint(doc, events=None):
    """Return a list of problems. Empty means the footprint matches its graph vocabulary."""
    problems = []
    if doc.get("schema") != FOOTPRINT_SCHEMA:
        problems.append("schema")
        return problems
    try:
        graph = graph_from_footprint(doc)
    except (KeyError, TypeError, ValueError) as exc:
        return [str(exc)]
    problems.extend(item["detail"] for item in violations(graph))
    totals = doc.get("totals") or {}
    summed = Counter()
    for row in doc.get("repositories") or []:
        triage = row.get("triage") or {}
        for key in ("cases", "well_handled", "partial", "unknown"):
            summed[key] += triage.get(key) or 0
    for key in ("cases", "well_handled", "partial", "unknown"):
        if summed[key] != (totals.get(key) or 0):
            problems.append("totals.%s" % key)
    if events is not None:
        if len(events) != (totals.get("cases") or 0):
            problems.append("event-count")
        counted = Counter(event["classification"] for event in events)
        for key in ("well_handled", "partial", "unknown"):
            if counted[key] != (totals.get(key) or 0):
                problems.append("events.%s" % key)
        ids = [event.get("case_id") for event in events]
        if len(ids) != len(set(ids)):
            problems.append("duplicate-case-id")
    return problems


def markdown_view(doc, diagram):
    """Markdown page: the ONTO diagram plus the counts a reader needs beside it."""
    totals = doc["totals"]
    lines = [
        "# ErrORR fleet footprint",
        "",
        "Point-in-time snapshot (`%s`, observed %s). Regenerate with" % (doc["schema"], doc.get("observed_at") or "unknown"),
        "`python3 -S scripts/usage_graph.py from-checkout` from the repository root.",
        "The diagram is ONTO `knowledgegraph.graph_mermaid` output. It is not a live counter.",
        "",
        diagram.rstrip(),
        "",
        "## Triage of harvested JUnit goldens",
        "",
        "| | |",
        "| --- | --- |",
        "| manifest rows | %s |" % doc.get("manifest_rows"),
        "| duplicate rows collapsed | %s |" % doc.get("duplicate_manifest_rows"),
        "| colliding rows not in the surviving file | %s |" % doc.get("colliding_manifest_rows"),
        "| cases | %s |" % totals["cases"],
        "| well_handled | %s |" % totals["well_handled"],
        "| partial | %s |" % totals["partial"],
        "| unknown | %s |" % totals["unknown"],
        "| classification.json rows | %s |" % doc.get("classification_file_rows"),
        "",
        "Exception types named on `FOUND: junit pytest exception type` or the first Dart error line:",
        "",
    ]
    pairs = []
    for name, count in (totals.get("exception_types") or {}).items():
        pairs.append((str(name), int(count)))
    for name, count in sorted(pairs, key=lambda item: (-item[1], item[0])):
        lines.append("- %s: %s" % (name, count))
    lines.extend(["", "## Repositories in the diagram", ""])
    lines.append("| Repository | Harvest source | Cases | Uses |")
    lines.append("| --- | --- | --- | --- |")
    for row in doc["repositories"]:
        triage = row["triage"]
        if not (row["uses"] or row["harvest_source"] or triage["cases"]):
            continue
        uses = ", ".join("%s (%s)" % (item["feature"], item["kind"]) for item in row["uses"]) or "—"
        lines.append("| `%s` | %s | %s | %s |" % (
            row["repository"], "yes" if row["harvest_source"] else "no", triage["cases"], uses,
        ))
    quiet = [
        row["repository"] for row in doc["repositories"]
        if not row["uses"] and not row["harvest_source"] and not row["triage"]["cases"]
    ]
    lines.extend(["", "## Public inventory with no harvest edge and no triage cases", ""])
    if quiet:
        lines.extend("- `%s`" % name for name in quiet)
    else:
        lines.append("None.")
    lines.extend(["", "## Renames recorded on older corpus rows", ""])
    if doc.get("renames"):
        for row in doc["renames"]:
            lines.append("- `%s` → `%s`" % (row["from"], row["to"]))
    else:
        lines.append("None.")
    lines.append("")
    return "\n".join(lines)


def from_checkout(adoption_path, corpus, harvest_path, footprint_path, events_path, view_path=None):
    adoption = load_json(adoption_path)
    harvest = load_json(harvest_path)
    events, manifest_stats = events_from_corpus(corpus)
    classification_path = Path(corpus) / "classification.json"
    classification_rows = None
    if classification_path.is_file():
        classification_rows = len(load_json(classification_path))
    doc = footprint_from_parts(adoption, harvest, events, classification_rows, manifest_stats)
    problems = check_footprint(doc, events)
    if problems:
        raise ValueError("footprint check failed: " + ", ".join(problems))
    _dump(footprint_path, doc)
    write_events(events_path, events)
    if view_path is None:
        return doc
    graph = graph_from_footprint(doc)
    diagram = mermaid_from_graph(graph)
    Path(view_path).write_text(markdown_view(doc, diagram), encoding="utf-8")
    return doc


def _load_footprint(path):
    doc = load_json(path)
    problems = check_footprint(doc)
    if problems:
        raise ValueError("footprint check failed: " + ", ".join(problems))
    return doc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("from-checkout", help="write the footprint JSON, JSONL events, and optional Mermaid view")
    build.add_argument("--adoption", type=Path, default=ROOT / "docs" / "usage" / "adoption.json")
    build.add_argument("--corpus", type=Path, default=ROOT / "tests" / "fixtures" / "junit_corpus")
    build.add_argument("--harvest-sources", type=Path, default=ROOT / "scripts" / "harvest_sources.json")
    build.add_argument("--out", type=Path, default=ROOT / "docs" / "usage" / "fleet-footprint.json")
    build.add_argument("--events", type=Path, default=ROOT / "docs" / "usage" / "triage-events.jsonl")
    build.add_argument("--view", type=Path, default=ROOT / "docs" / "usage" / "fleet-graph.md")
    build.add_argument("--skip-view", action="store_true", help="do not import ONTO knowledgegraph")

    check = sub.add_parser("check", help="validate a footprint and the graph vocabulary")
    check.add_argument("footprint", type=Path)
    check.add_argument("--events", type=Path)

    graph = sub.add_parser("graph", help="print the ONTO graph JSON")
    graph.add_argument("footprint", type=Path)

    mermaid = sub.add_parser("mermaid", help="print ONTO Mermaid for the footprint")
    mermaid.add_argument("footprint", type=Path)

    html = sub.add_parser("html", help="write an HTML page that uses ONTO's Mermaid renderer")
    html.add_argument("footprint", type=Path)
    html.add_argument("-o", "--output", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "from-checkout":
            from_checkout(
                args.adoption, args.corpus, args.harvest_sources, args.out, args.events,
                None if args.skip_view else args.view,
            )
            return 0
        doc = _load_footprint(args.footprint)
        drawn = graph_from_footprint(doc)
        if args.command == "check":
            events = read_events(args.events) if args.events else None
            problems = check_footprint(doc, events)
            if problems:
                print("\n".join(problems), file=sys.stderr)
                return 2
            return 0
        if args.command == "graph":
            print(json.dumps(drawn, indent=2, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "mermaid":
            print(mermaid_from_graph(drawn), end="")
            return 0
        Path(args.output).write_text(
            html_from_graph(drawn, "ErrORR fleet footprint"), encoding="utf-8",
        )
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print("usage_graph: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
