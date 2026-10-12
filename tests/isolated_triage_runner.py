"""Run triage CLI without picking up the ambient err-o-roadrunner install."""

from __future__ import annotations

import importlib.util
import runpy
import sys
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
        raise SystemExit("usage: isolated_triage_runner.py SANDBOX [--as-module] [triage argv...]")
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


if __name__ == "__main__":
    main(sys.argv)
