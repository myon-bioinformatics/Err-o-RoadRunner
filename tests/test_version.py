"""Project version: checkout smoke tests and the pyproject x metadata x run-mode partition matrix (mutation_guard).

- version: Smoke tests for project version (the partition matrix follows).
- version_matrix: Partition tests for project_version resolution (pyproject × metadata × run mode).
"""

from __future__ import annotations

import itertools, os, shutil, sys
from pathlib import Path
from unittest import mock

import pytest

from vendor import cli_args
import triage
from errlib.project_version import (
    DISTRIBUTION_NAME,
    parse_pyproject_version,
    read_pyproject_version,
    resolve_version,
    VERSION_UNKNOWN,
)


# ──── version ─────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"


def test_pyproject_name_is_err_o_roadrunner_distribution() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    assert f'name = "{DISTRIBUTION_NAME}"' in text


def test_triage_version_matches_pyproject_in_checkout() -> None:
    expected = read_pyproject_version(PYPROJECT)
    assert expected is not None
    assert triage.__version__ == expected


def test_cli_version_flag_matches_pyproject_in_checkout() -> None:
    proc = cli_args.run_cli(ROOT / "triage.py", "--version", cwd=ROOT)
    assert proc.returncode == 0
    expected = read_pyproject_version(PYPROJECT)
    assert expected is not None
    assert proc.stdout.strip() == f"err-o-roadrunner {expected}"


def test_unknown_version_string_constant() -> None:
    assert VERSION_UNKNOWN == "unknown"


# ──── version_matrix ──────────────────────────────────────────────

# Partition tests for project_version resolution (pyproject × metadata × run mode).
#
# Matrix size: 25 pyproject states × 3 metadata states × 4 run modes = 300 cells.
#
# Unreachable (51 cells, see UNREACHABLE):
# - wheel_console × metadata absent (12): installed wheels always ship dist-info metadata.
# - wheel_console × other pyproject fixtures (39): console scripts read wheel metadata only.
#
# Covered: 249 cells exercised by test_version_resolution_matrix_cell.


# Run triage from SANDBOX without the ambient err-o-roadrunner install (``python -c``, argv as before).
ISOLATED_RUNNER = [sys.executable, "-c", '''
import importlib.util, runpy, sys
from pathlib import Path


def _scrub_site_packages() -> None:
    sys.path[:] = [
        entry
        for entry in sys.path
        if "site-packages" not in entry and "dist-packages" not in entry
    ]


def _run_as_script(sandbox: Path, triage_args: list[str]) -> None:
    sys.argv = ["triage.py", *triage_args]
    runpy.run_path(str(sandbox / "triage.py"), run_name="__main__")


def _run_as_module(sandbox: Path, triage_args: list[str]) -> None:
    triage_path = sandbox / "triage.py"
    spec = importlib.util.spec_from_file_location("triage", triage_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load triage module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules["triage"] = module
    spec.loader.exec_module(module)
    sys.argv = ["triage", *triage_args]
    raise SystemExit(module.main())


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        raise SystemExit("usage: ISOLATED_RUNNER SANDBOX [--as-module] [triage argv...]")
    sandbox = Path(argv[1]).resolve()
    rest = list(argv[2:])
    as_module = False
    if rest and rest[0] == "--as-module":
        as_module = True
        rest = rest[1:]
    triage_args = rest or ["--version"]
    _scrub_site_packages()
    if str(sandbox) not in sys.path:
        sys.path.insert(0, str(sandbox))
    if as_module:
        _run_as_module(sandbox, triage_args)
    else:
        _run_as_script(sandbox, triage_args)


main(sys.argv)
''']
WHEEL_BUILD_PREFIX = (
    "[build-system]\n"
    'requires = ["hatchling>=1.32.4"]\n'
    'build-backend = "hatchling.build"\n\n'
)
WHEEL_BUILD_SUFFIX = (
    "\n[project.scripts]\n"
    'erroroadrunner = "triage:main"\n'
    "\n[tool.hatch.build.targets.wheel]\n"
    'only-include = ["triage.py", "errlib"]\n'
)
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
    "version_in_multiline_string",
    "toml_header_comment",
    "toml_spaced_project_table",
    "toml_quoted_keys",
    "toml_inline_project_table",
    "toml_version_unicode_escape",
    "name_pep503_mixed_case",
    "name_pep503_underscore",
    "name_pep503_dots",
    "name_pep503_mixed_separators",
)
DUPLICATE_FIRST_VERSION = "1.2.3"
DUPLICATE_SECOND_VERSION = "9.9.9"
DECOY_VERSION_IN_STRING = "6.6.6"
METADATA_STATES = ("same", "different", "absent")
RUN_MODES = ("checkout_script", "module", "other_cwd", "wheel_console")


# wheel_console needs a buildable pyproject at wheel build time.
WHEEL_BUILDABLE_PYPROJECT_STATES = frozenset(
    {
        "valid",
        "single_quoted",
        "version_trailing_comment",
        "version_in_multiline_string",
        "toml_header_comment",
        "toml_spaced_project_table",
        "toml_quoted_keys",
        "toml_version_unicode_escape",
        "name_pep503_mixed_case",
        "name_pep503_underscore",
        "name_pep503_dots",
        "name_pep503_mixed_separators",
    }
)
CHECKOUT_USABLE_PYPROJECT_STATES = WHEEL_BUILDABLE_PYPROJECT_STATES | frozenset(
    {"toml_inline_project_table"}
)
_WHEEL_CONSOLE_BASE_REASON = (
    "wheel_console resolves version from the installed wheel's dist-info metadata only; "
    "checkout pyproject.toml beside the console script is not read"
)


# Cells that cannot be exercised (see the module docstring).
UNREACHABLE: dict[tuple[str, str, str], str] = {}


for _state in PYPROJECT_STATES:
    if _state not in WHEEL_BUILDABLE_PYPROJECT_STATES:
        for _meta in METADATA_STATES:
            UNREACHABLE[(_state, _meta, "wheel_console")] = _WHEEL_CONSOLE_BASE_REASON


for _state in WHEEL_BUILDABLE_PYPROJECT_STATES:
    UNREACHABLE[(_state, "absent", "wheel_console")] = (
        f"{_WHEEL_CONSOLE_BASE_REASON}; absent metadata is impossible for installed wheels"
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
    if state in {"missing", "invalid_utf8"}:
        return None
    if state == "empty_file":
        return ""
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
    if state == "version_in_multiline_string":
        return (
            "[project]\n"
            f'name = "{DISTRIBUTION_NAME}"\n'
            f'version = "{CHECKOUT_VERSION}"\n'
            'description = """\n'
            f'version = "{DECOY_VERSION_IN_STRING}"\n'
            '"""\n'
        )
    if state == "toml_header_comment":
        return (
            f"[project]  # main\n"
            f'name = "{DISTRIBUTION_NAME}"\n'
            f'version = "{CHECKOUT_VERSION}"\n'
        )
    if state == "toml_spaced_project_table":
        return (
            "[ project ]\n"
            f'name = "{DISTRIBUTION_NAME}"\n'
            f'version = "{CHECKOUT_VERSION}"\n'
        )
    if state == "toml_quoted_keys":
        return (
            "[project]\n"
            f'"name" = "{DISTRIBUTION_NAME}"\n'
            f'"version" = "{CHECKOUT_VERSION}"\n'
        )
    if state == "toml_inline_project_table":
        return (
            f'project = {{name = "{DISTRIBUTION_NAME}", version = "{CHECKOUT_VERSION}"}}\n'
        )
    if state == "toml_version_unicode_escape":
        return (
            "[project]\n"
            f'name = "{DISTRIBUTION_NAME}"\n'
            f'version = "7.7.\\u0037"\n'
        )
    if state == "name_pep503_mixed_case":
        return (
            "[project]\n"
            'name = "Err-O-RoadRunner"\n'
            f'version = "{CHECKOUT_VERSION}"\n'
        )
    if state == "name_pep503_underscore":
        return (
            "[project]\n"
            'name = "err_o_roadrunner"\n'
            f'version = "{CHECKOUT_VERSION}"\n'
        )
    if state == "name_pep503_dots":
        return (
            "[project]\n"
            'name = "err.o.roadrunner"\n'
            f'version = "{CHECKOUT_VERSION}"\n'
        )
    if state == "name_pep503_mixed_separators":
        return (
            "[project]\n"
            'name = "err--o__roadrunner"\n'
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
    if pyproject_state in CHECKOUT_USABLE_PYPROJECT_STATES:
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


def copy_runtime_modules(sandbox: Path) -> None:
    """Copy triage.py and the errlib package into ``sandbox`` (no caches)."""
    shutil.copy2(ROOT / "triage.py", sandbox / "triage.py")
    shutil.copytree(
        ROOT / "errlib",
        sandbox / "errlib",
        ignore=shutil.ignore_patterns("__pycache__"),
    )


def populate_sandbox(sandbox: Path, pyproject_state: str) -> None:
    copy_runtime_modules(sandbox)
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
    if pyproject_state not in WHEEL_BUILDABLE_PYPROJECT_STATES:
        raise ValueError(pyproject_state)
    base = pyproject_text(pyproject_state)
    assert base is not None
    if pyproject_state == "toml_version_unicode_escape":
        text = (
            "[project]\n"
            f'name = "{DISTRIBUTION_NAME}"\n'
            f'version = "{version}"\n'
        )
    else:
        text = base.replace(CHECKOUT_VERSION, version)
    (sandbox / "pyproject.toml").write_text(text, encoding="utf-8")


def write_wheel_build_pyproject(sandbox: Path, pyproject_state: str, version: str) -> None:
    write_buildable_pyproject(sandbox, pyproject_state, version)
    body = (sandbox / "pyproject.toml").read_text(encoding="utf-8")
    (sandbox / "pyproject.toml").write_text(
        WHEEL_BUILD_PREFIX + body + WHEEL_BUILD_SUFFIX,
        encoding="utf-8",
    )


def build_wheel(sandbox: Path, wheel_dir: Path) -> Path:
    proc = cli_args.run_command(
        cli_args.Command((sys.executable, "-m", "pip", "wheel", str(sandbox), "-w", str(wheel_dir), "-q"))
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
) -> cli_args.CommandResult:
    if run_mode == "checkout_script":
        cmd = [*ISOLATED_RUNNER, str(sandbox), "--version"]
        cwd = sandbox
    elif run_mode == "module":
        cmd = [*ISOLATED_RUNNER, str(sandbox), "--as-module", "--version"]
        cwd = sandbox
    elif run_mode == "other_cwd":
        cmd = [*ISOLATED_RUNNER, str(sandbox), "--version"]
        cwd = sandbox.parent
    elif run_mode == "wheel_console":
        prefix = sandbox / "prefix"
        assert wheel is not None
        installed = cli_args.run_command(
            cli_args.Command((sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--prefix", str(prefix), str(wheel)))
        )
        assert installed.returncode == 0, installed.stderr
        console = next(prefix.rglob("erroroadrunner"), None)
        if console is None:
            raise RuntimeError("erroroadrunner console script not found after wheel install")
        cmd = [str(console), "--version"]
        cwd = sandbox
        site = prefix / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        local_site = prefix / "local" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "dist-packages"
        paths = [str(p) for p in (site, local_site) if p.is_dir()]
        env = {"PYTHONPATH": os.pathsep.join(paths)}  # the installed wheel only; the caller's PYTHONPATH is dropped
    else:
        raise ValueError(run_mode)
    return cli_args.run_command(cli_args.Command(cmd), cwd=cwd, env=cli_args.build_env(env))


def matrix_totals() -> tuple[int, int, int]:
    total = len(PYPROJECT_STATES) * len(METADATA_STATES) * len(RUN_MODES)
    unreachable = len(UNREACHABLE)
    covered = total - unreachable
    return total, unreachable, covered


MATRIX_CASES = list(
    itertools.product(PYPROJECT_STATES, METADATA_STATES, RUN_MODES)
)


@pytest.mark.parametrize(
    ("pyproject_state", "metadata_state", "run_mode"),
    MATRIX_CASES,
    ids=[f"{a}|{b}|{c}" for a, b, c in MATRIX_CASES],
)
def test_version_resolution_matrix_cell(
    pyproject_state: str,
    metadata_state: str,
    run_mode: str,
    tmp_path: Path,
) -> None:
    key = (pyproject_state, metadata_state, run_mode)
    if key in UNREACHABLE:
        pytest.skip(UNREACHABLE[key])

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    meta_dir = tmp_path / "meta"
    meta_dir.mkdir()
    install_fake_metadata(meta_dir, metadata_version(metadata_state))

    env = {"PYTHONPATH": os.pathsep.join([str(sandbox), str(meta_dir)])}
    wheel = None
    if run_mode == "wheel_console":
        copy_runtime_modules(sandbox)
        write_wheel_build_pyproject(
            sandbox,
            pyproject_state,
            wheel_pyproject_version(metadata_state),
        )
        shutil.copy2(ROOT / "README.md", sandbox / "README.md")
        wheel = build_wheel(sandbox, tmp_path / "wheels")
    else:
        populate_sandbox(sandbox, pyproject_state)
        if pyproject_state == "unreadable_oserror":
            pyproject = sandbox / "pyproject.toml"
            if os.access(pyproject, os.R_OK):
                pytest.skip("cannot simulate unreadable pyproject.toml on this runner")

    proc = run_version_cli(
        sandbox=sandbox,
        run_mode=run_mode,
        env=env,
        wheel=wheel,
    )
    assert proc.returncode == 0
    want = expected_version(pyproject_state, metadata_state, run_mode)
    assert proc.stdout.strip() == f"err-o-roadrunner {want}"

    if run_mode != "wheel_console":
        merged = os.environ.copy()
        merged.update(env)
        merged["PYTHONPATH"] = os.pathsep.join([str(sandbox), str(meta_dir)])
        if run_mode == "other_cwd":
            cmd = [*ISOLATED_RUNNER, str(sandbox), "--exit-code", "0"]
            cwd = sandbox.parent
        elif run_mode == "module":
            cmd = [
                *ISOLATED_RUNNER,
                str(sandbox),
                "--as-module",
                "--exit-code",
                "0",
            ]
            cwd = sandbox
        else:
            cmd = [*ISOLATED_RUNNER, str(sandbox), "--exit-code", "0"]
            cwd = sandbox
        proc2 = cli_args.run_command(cli_args.Command(cmd), cwd=cwd, env=merged)
        assert proc2.returncode == 0
        assert "FOUND: exit code: 0" in proc2.stdout


def test_version_matrix_coverage_summary() -> None:
    total, unreachable, covered = matrix_totals()
    assert total == 300
    assert unreachable == 51
    assert covered == 249


@pytest.mark.parametrize("pyproject_state", PYPROJECT_STATES)
def test_tomllib_parse_matches_parse_pyproject_version(pyproject_state: str) -> None:
    if pyproject_state in {"missing", "invalid_utf8"}:
        pytest.skip("no UTF-8 pyproject fixture text")
    payload = pyproject_bytes(pyproject_state)
    assert payload is not None
    text = payload.decode("utf-8")

    import tomllib

    from errlib.project_version import _version_from_toml_data

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        assert parse_pyproject_version(text) is None
        return
    assert parse_pyproject_version(text) == _version_from_toml_data(data)


def test_resolve_version_direct_matches_matrix_expectations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("errlib.project_version._installed_version", lambda: None)
    for pyproject_state in PYPROJECT_STATES:
        sandbox = tmp_path / pyproject_state
        sandbox.mkdir()
        populate_sandbox(sandbox, pyproject_state)
        if pyproject_state == "unreadable_oserror":
            with mock.patch.object(
                Path,
                "read_text",
                side_effect=OSError("permission denied"),
            ):
                got = resolve_version(package_dir=sandbox)
        else:
            got = resolve_version(package_dir=sandbox)
        want = expected_version(pyproject_state, "absent", "checkout_script")
        assert got == want


@pytest.mark.mutation_guard
def test_import_succeeds_with_unknown_version(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "iso"
    sandbox.mkdir()
    copy_runtime_modules(sandbox)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(sandbox)
    proc = cli_args.run_command(cli_args.Command((*ISOLATED_RUNNER, str(sandbox), "--version")), cwd=sandbox, env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "err-o-roadrunner unknown"
    proc2 = cli_args.run_command(
        cli_args.Command((*ISOLATED_RUNNER, str(sandbox), "--exit-code", "0")), cwd=sandbox, env=env
    )
    assert proc2.returncode == 0, proc2.stderr


@pytest.mark.mutation_guard
def test_mutation_guard_pep503_name_mixed_case_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    populate_sandbox(sandbox, "name_pep503_mixed_case")
    monkeypatch.setattr("errlib.project_version._installed_version", lambda: META_VERSION)
    assert resolve_version(package_dir=sandbox) == CHECKOUT_VERSION


@pytest.mark.mutation_guard
def test_mutation_guard_pep503_name_underscore_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    populate_sandbox(sandbox, "name_pep503_underscore")
    monkeypatch.setattr("errlib.project_version._installed_version", lambda: META_VERSION)
    assert resolve_version(package_dir=sandbox) == CHECKOUT_VERSION


@pytest.mark.mutation_guard
def test_mutation_guard_pep503_name_dots_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    populate_sandbox(sandbox, "name_pep503_dots")
    monkeypatch.setattr("errlib.project_version._installed_version", lambda: META_VERSION)
    assert resolve_version(package_dir=sandbox) == CHECKOUT_VERSION


@pytest.mark.mutation_guard
def test_mutation_guard_pep503_name_mixed_separators_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    populate_sandbox(sandbox, "name_pep503_mixed_separators")
    monkeypatch.setattr("errlib.project_version._installed_version", lambda: META_VERSION)
    assert resolve_version(package_dir=sandbox) == CHECKOUT_VERSION


@pytest.mark.mutation_guard
def test_mutation_guard_pyproject_name_check_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    populate_sandbox(sandbox, "wrong_name")
    monkeypatch.setattr("errlib.project_version._installed_version", lambda: META_VERSION)
    assert resolve_version(package_dir=sandbox) == META_VERSION


@pytest.mark.mutation_guard
def test_mutation_guard_pyproject_before_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    populate_sandbox(sandbox, "valid")
    monkeypatch.setattr("errlib.project_version._installed_version", lambda: META_VERSION)
    assert resolve_version(package_dir=sandbox) == CHECKOUT_VERSION


@pytest.mark.mutation_guard
def test_mutation_guard_version_whitespace_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    populate_sandbox(sandbox, "version_whitespace")
    monkeypatch.setattr("errlib.project_version._installed_version", lambda: META_VERSION)
    assert resolve_version(package_dir=sandbox) == META_VERSION


@pytest.mark.mutation_guard
def test_mutation_guard_version_in_later_table_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    populate_sandbox(sandbox, "version_in_later_table")
    monkeypatch.setattr("errlib.project_version._installed_version", lambda: META_VERSION)
    assert resolve_version(package_dir=sandbox) == META_VERSION


@pytest.mark.mutation_guard
def test_mutation_guard_oserror_on_read_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    populate_sandbox(sandbox, "valid")
    monkeypatch.setattr("errlib.project_version._installed_version", lambda: META_VERSION)
    with mock.patch.object(Path, "read_text", side_effect=OSError("permission denied")):
        assert resolve_version(package_dir=sandbox) == META_VERSION


@pytest.mark.mutation_guard
def test_mutation_guard_duplicate_version_invalid_toml_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    populate_sandbox(sandbox, "duplicate_version_in_project")
    monkeypatch.setattr("errlib.project_version._installed_version", lambda: META_VERSION)
    assert resolve_version(package_dir=sandbox) == META_VERSION


@pytest.mark.mutation_guard
def test_mutation_guard_multiline_string_decoy_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    populate_sandbox(sandbox, "version_in_multiline_string")
    monkeypatch.setattr("errlib.project_version._installed_version", lambda: META_VERSION)
    assert resolve_version(package_dir=sandbox) == CHECKOUT_VERSION
    assert resolve_version(package_dir=sandbox) != DECOY_VERSION_IN_STRING


@pytest.mark.mutation_guard
def test_mutation_guard_unknown_when_no_version_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    populate_sandbox(sandbox, "missing")
    monkeypatch.setattr("errlib.project_version._installed_version", lambda: None)
    assert resolve_version(package_dir=sandbox) == VERSION_UNKNOWN


def test_repo_checkout_version_matches_pyproject() -> None:
    expected = read_pyproject_version(ROOT / "pyproject.toml")
    assert expected is not None
    assert triage.__version__ == expected
