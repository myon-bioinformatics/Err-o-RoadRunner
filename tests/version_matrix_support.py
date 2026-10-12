"""Helpers for version-resolution partition tests (not collected as tests)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from project_version import DISTRIBUTION_NAME, VERSION_UNKNOWN

ROOT = Path(__file__).resolve().parents[1]

CHECKOUT_VERSION = "7.7.7"
META_VERSION = "1.2.3"

PYPROJECT_STATES = (
    "valid",
    "missing",
    "invalid_utf8",
    "invalid_toml",
    "wrong_name",
    "name_missing",
    "version_missing",
    "version_malformed",
    "single_quoted",
    "version_trailing_comment",
    "unreadable_oserror",
    "empty_file",
    "version_whitespace",
    "version_in_later_table",
    "duplicate_version_in_project",
)

DUPLICATE_FIRST_VERSION = "1.2.3"
DUPLICATE_SECOND_VERSION = "9.9.9"

METADATA_STATES = ("same", "different", "absent")

RUN_MODES = ("checkout_script", "module", "other_cwd", "wheel_console")

# wheel_console needs a buildable pyproject at wheel build time.
WHEEL_BUILDABLE_PYPROJECT_STATES = frozenset(
    {"valid", "single_quoted", "version_trailing_comment"}
)

# Cells that cannot be exercised (documented in test_version_matrix.py).
UNREACHABLE: dict[tuple[str, str, str], str] = {}
for _state in PYPROJECT_STATES:
    if _state not in WHEEL_BUILDABLE_PYPROJECT_STATES:
        for _meta in METADATA_STATES:
            UNREACHABLE[(_state, _meta, "wheel_console")] = (
                "wheel_console requires a successful hatchling wheel build; "
                f"pyproject state '{_state}' is not buildable"
            )
for _state in WHEEL_BUILDABLE_PYPROJECT_STATES:
    UNREACHABLE[(_state, "absent", "wheel_console")] = (
        "installed wheels always carry dist-info metadata; absent is impossible"
    )


def pyproject_bytes(state: str) -> bytes | None:
    if state == "missing":
        return None
    if state == "invalid_utf8":
        return b"\xff\xfe\n[project]\nname = \"err-o-roadrunner\"\n"
    if state == "empty_file":
        return b""
    text = pyproject_text(state)
    return text.encode("utf-8") if text is not None else None


def pyproject_text(state: str) -> str | None:
    if state in {"missing", "invalid_utf8", "empty_file"}:
        return None
    if state == "valid":
        return (
            "[project]\n"
            f'name = "{DISTRIBUTION_NAME}"\n'
            f'version = "{CHECKOUT_VERSION}"\n'
        )
    if state == "single_quoted":
        return (
            "[project]\n"
            f'name = "{DISTRIBUTION_NAME}"\n'
            f"version = '{CHECKOUT_VERSION}'\n"
        )
    if state == "version_trailing_comment":
        return (
            "[project]\n"
            f'name = "{DISTRIBUTION_NAME}"\n'
            f'version = "{CHECKOUT_VERSION}"  # release tag\n'
        )
    if state == "invalid_toml":
        return "[project]\nname = \"err-o-roadrunner\"\nversion = \n"
    if state == "wrong_name":
        return f'[project]\nname = "other-package"\nversion = "{CHECKOUT_VERSION}"\n'
    if state == "name_missing":
        return f'[project]\nversion = "{CHECKOUT_VERSION}"\n'
    if state == "version_missing":
        return f'[project]\nname = "{DISTRIBUTION_NAME}"\n'
    if state == "version_malformed":
        return f'[project]\nname = "{DISTRIBUTION_NAME}"\nversion = ""\n'
    if state == "version_whitespace":
        return f'[project]\nname = "{DISTRIBUTION_NAME}"\nversion = " "\n'
    if state == "version_in_later_table":
        return (
            "[project]\n"
            f'name = "{DISTRIBUTION_NAME}"\n'
            "[tool.foo]\n"
            'version = "9.9.9"\n'
        )
    if state == "duplicate_version_in_project":
        return (
            "[project]\n"
            f'name = "{DISTRIBUTION_NAME}"\n'
            f'version = "{DUPLICATE_FIRST_VERSION}"\n'
            f'version = "{DUPLICATE_SECOND_VERSION}"\n'
        )
    if state == "unreadable_oserror":
        return (
            "[project]\n"
            f'name = "{DISTRIBUTION_NAME}"\n'
            f'version = "{CHECKOUT_VERSION}"\n'
        )
    raise ValueError(state)


def metadata_version(metadata_state: str) -> str | None:
    if metadata_state == "absent":
        return None
    if metadata_state == "same":
        return CHECKOUT_VERSION
    return META_VERSION


def expected_version(pyproject_state: str, metadata_state: str, run_mode: str) -> str:
    if run_mode == "wheel_console":
        meta = metadata_version(metadata_state)
        assert meta is not None
        return meta
    if pyproject_state == "duplicate_version_in_project":
        return DUPLICATE_FIRST_VERSION
    checkout_usable = pyproject_state in {
        "valid",
        "single_quoted",
        "version_trailing_comment",
    }
    if checkout_usable:
        return CHECKOUT_VERSION
    meta = metadata_version(metadata_state)
    if meta is not None:
        return meta
    return VERSION_UNKNOWN


def install_fake_metadata(target_dir: Path, version: str | None) -> None:
    if version is None:
        return
    dist_info = target_dir / "err_o_roadrunner-0.0.0.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        f"Name: {DISTRIBUTION_NAME}\n"
        f"Version: {version}\n",
        encoding="utf-8",
    )


def wheel_pyproject_version(metadata_state: str) -> str:
    return CHECKOUT_VERSION if metadata_state == "same" else META_VERSION


def populate_sandbox(sandbox: Path, pyproject_state: str) -> None:
    for name in ("triage.py", "actions_log.py", "junit_redact.py", "project_version.py"):
        shutil.copy2(ROOT / name, sandbox / name)
    pyproject = sandbox / "pyproject.toml"
    payload = pyproject_bytes(pyproject_state)
    if payload is None:
        if pyproject.exists():
            pyproject.unlink()
        return
    pyproject.write_bytes(payload)
    if pyproject_state == "unreadable_oserror":
        pyproject.chmod(0)


def write_buildable_pyproject(sandbox: Path, pyproject_state: str, version: str) -> None:
    if pyproject_state == "valid":
        text = (
            "[project]\n"
            f'name = "{DISTRIBUTION_NAME}"\n'
            f'version = "{version}"\n'
        )
    elif pyproject_state == "single_quoted":
        text = (
            "[project]\n"
            f'name = "{DISTRIBUTION_NAME}"\n'
            f"version = '{version}'\n"
        )
    elif pyproject_state == "version_trailing_comment":
        text = (
            "[project]\n"
            f'name = "{DISTRIBUTION_NAME}"\n'
            f'version = "{version}"  # tag\n'
        )
    else:
        raise ValueError(pyproject_state)
    (sandbox / "pyproject.toml").write_text(text, encoding="utf-8")


def build_wheel(sandbox: Path, wheel_dir: Path) -> Path:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(sandbox), "-w", str(wheel_dir), "-q"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pip wheel failed: {proc.stderr}")
    wheels = list(wheel_dir.glob("*.whl"))
    if not wheels:
        raise RuntimeError("no wheel produced")
    return wheels[0]


def run_version_cli(
    *,
    sandbox: Path,
    run_mode: str,
    env: dict[str, str],
    wheel: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    runner = ROOT / "tests" / "isolated_triage_runner.py"
    if run_mode == "checkout_script":
        cmd = [sys.executable, str(runner), str(sandbox), "--version"]
        cwd = sandbox
    elif run_mode == "module":
        cmd = [sys.executable, str(runner), str(sandbox), "--as-module", "--version"]
        cwd = sandbox
    elif run_mode == "other_cwd":
        cmd = [sys.executable, str(runner), str(sandbox), "--version"]
        cwd = sandbox.parent
    elif run_mode == "wheel_console":
        prefix = sandbox / "prefix"
        assert wheel is not None
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-q",
                "--no-deps",
                "--prefix",
                str(prefix),
                str(wheel),
            ],
            check=True,
            capture_output=True,
        )
        console = next(prefix.rglob("err-o-roadrunner"), None)
        if console is None:
            raise RuntimeError("err-o-roadrunner console script not found after wheel install")
        cmd = [str(console), "--version"]
        cwd = sandbox
        wheel_env = os.environ.copy()
        site = prefix / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        local_site = prefix / "local" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "dist-packages"
        paths = [str(p) for p in (site, local_site) if p.is_dir()]
        wheel_env["PYTHONPATH"] = os.pathsep.join(paths)
        return subprocess.run(cmd, cwd=cwd, env=wheel_env, capture_output=True, text=True, check=False)
    else:
        raise ValueError(run_mode)
    merged = os.environ.copy()
    merged.update(env)
    return subprocess.run(cmd, cwd=cwd, env=merged, capture_output=True, text=True, check=False)


def matrix_totals() -> tuple[int, int, int]:
    total = len(PYPROJECT_STATES) * len(METADATA_STATES) * len(RUN_MODES)
    unreachable = len(UNREACHABLE)
    covered = total - unreachable
    return total, unreachable, covered
