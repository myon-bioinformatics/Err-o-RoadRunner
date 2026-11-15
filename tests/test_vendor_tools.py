"""Vendored shared tools (vendor/): standalone imports and offline contracts.

- parent_tools: Offline compatibility of canonical parent tools with adjacent vendored GHI.
- shared_vendor_toolkit: every vendored module compiles and imports with `python -S`.
"""
import json, subprocess, sys
from pathlib import Path

import pytest

from vendor import fleet_check as fleet


# ──── parent_tools ────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"


def test_parent_tools_match_recorded_bytes():
    rows = fleet.check_lock(ROOT)
    assert any(row["repository"] == fleet.PARENT for row in rows.values())


def test_gh_ops_help_is_standalone():
    result = subprocess.run([sys.executable, "-S", str(VENDOR / "gh_ops.py"), "--help"],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "git-push" in result.stdout


def test_git_push_dry_run_uses_actual_adjacent_identity_without_network(tmp_path):
    # A deliberately unusable remote and guarded real Git runner keep this offline.
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True,
                              text=True, check=True, timeout=15)
    git("init", "--initial-branch=refresh")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-m", "fixture")
    git("remote", "add", "origin", "https://invalid.invalid/never/pushed.git")
    head = git("rev-parse", "HEAD").stdout.strip()
    code = """
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import gh_ops
assert pathlib.Path(gh_ops.gh_identity.__file__).resolve() == pathlib.Path(sys.argv[1], 'gh_identity.py').resolve()
actual_run = gh_ops.gh_identity._git_run
calls = []
def local_only(cwd, args, **kwargs):
    assert args[0] in {'branch', 'check-ref-format', 'rev-parse', 'remote'}
    assert kwargs.get('inherit_auth') is False
    calls.append(args[0])
    return actual_run(cwd, args, **kwargs)
gh_ops.gh_identity._git_run = local_only
receipt = gh_ops.git_push(cwd=sys.argv[2], write=False, timeout=10)
assert calls == ['branch', 'check-ref-format', 'rev-parse', 'remote']
print(json.dumps(receipt))
"""
    result = subprocess.run([sys.executable, "-S", "-c", code, str(VENDOR), str(tmp_path)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["ok"] and receipt["dry_run"]
    assert receipt["pushed"] is False
    assert receipt["head_sha"] == head
    assert receipt["branch"] == "refresh"
    assert receipt["mutation_status"] == receipt["verification_status"] == "not_attempted"
    assert "invalid.invalid" not in result.stdout
    assert not git("for-each-ref", "refs/remotes/").stdout


# ──── shared_vendor_toolkit ───────────────────────────────────────

MODULES = (
    "yourself.py",
    "xprobe.py",
    "gh_ops.py",
    "check_evidence.py",
    "check_png.py",
    "jsonl_digest.py",
    "cli_args.py",
    "gh_identity.py",
)


@pytest.mark.parametrize("name", MODULES)
def test_shared_toolkit_compiles_and_imports_standalone(name: str) -> None:
    path = VENDOR / name
    subprocess.run([sys.executable, "-m", "py_compile", str(path)], check=True)
    subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import sys; sys.path.insert(0, r'"
            + str(VENDOR)
            + "'); __import__('"
            + name[:-3]
            + "')",
        ],
        check=True,
        cwd=ROOT,
    )


def test_environment_probe_is_safe_no_argument_json() -> None:
    result = subprocess.run(
        [sys.executable, "-S", str(VENDOR / "yourself.py"), "--minimal", "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert set(payload) == {"schema_version", "os", "runtime", "host", "directory"}


def test_gh_identity_local_contract_prefers_github_environment() -> None:
    sys.path.insert(0, str(VENDOR))
    import gh_identity

    value = gh_identity.local_identity(
        env={
            "GITHUB_SHA": "A" * 40,
            "GITHUB_HEAD_REF": "feature/runtime",
        }
    )
    assert value["schema"] == "gh-identity-local/1"
    assert value["sha"] == "a" * 40
    assert value["ref"] == "feature/runtime"
    assert value["source"] == "github-env"


def test_gh_identity_provides_the_git_inspection_api() -> None:
    sys.path.insert(0, str(VENDOR))
    import gh_identity

    for name in (
        "status",
        "ls_files",
        "diff",
        "log",
        "log_numstat",
        "show",
        "blame",
        "grep",
        "check_ignore",
    ):
        assert callable(getattr(gh_identity, "git_" + name)), name
    observed = gh_identity.git_status(ROOT)
    assert isinstance(observed, dict)
