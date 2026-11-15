"""Build a single-file Err-o-RoadRunner from the checked-in stdlib modules.

  python scripts/build_standalone.py dist/ErroRR.py           # the OLNM-facing single file
  python scripts/build_standalone.py --uv-tool dist/uv-tool   # the same bytes packaged as a uv tool
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MODULES = {
    'errlib': 'errlib/__init__.py',
    'errlib.error_input': 'errlib/error_input.py',
    'errlib.junit_redact': 'errlib/junit_redact.py',
    'errlib.project_version': 'errlib/project_version.py',
    'errlib.actions_log': 'errlib/actions_log.py',
    'triage': 'triage.py',
    'sentinel': 'sentinel.py',
    'squad': 'squad.py',
}


def build() -> str:
    sources = {name: (ROOT / path).read_text(encoding='utf-8') for name, path in MODULES.items()}
    return '''"""Generated standalone Err-o-RoadRunner; see scripts/build_standalone.py."""\n''' + '''from __future__ import annotations\nimport importlib.abc, importlib.util, sys\n\n''' + '_SOURCES = ' + repr(sources) + '''\n\nclass _BundledModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in _SOURCES:
            return None
        return importlib.util.spec_from_loader(fullname, self, is_package=fullname == 'errlib')

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__file__ = globals().get("__file__", "<ErroRR.py>")
        exec(compile(_SOURCES[module.__name__], '<bundled:' + module.__name__ + '>', 'exec'), module.__dict__)


def main(argv=None):
    # Refuse pre-existing modules so host imports cannot silently replace bundled code.
    conflicts = [name for name in _SOURCES if name in sys.modules]
    if conflicts:
        print('error: bundled module name already loaded: ' + ', '.join(sorted(conflicts)), file=sys.stderr)
        return 2
    finder = _BundledModules()
    sys.meta_path.insert(0, finder)
    try:
        from triage import main as run_triage
        return run_triage(argv)
    finally:
        sys.meta_path.remove(finder)
        for name in _SOURCES:
            sys.modules.pop(name, None)


if __name__ == '__main__':
    raise SystemExit(main())
'''


def build_uv_tool(out: Path) -> dict:
    """Package the standalone bundle with Aoi's pinned builder (vendor/uv_tool_build.py).

    Only the temporary package module uses a lowercase name (erroroadrunner.py), as
    required by the common builder; its bytes equal ErroRR.py.
    """
    spec = importlib.util.spec_from_file_location(
        "_err_uv_tool_build", ROOT / "vendor/uv_tool_build.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    with tempfile.TemporaryDirectory(prefix="err-uv-tool-") as temporary:
        entry = Path(temporary) / "erroroadrunner.py"
        entry.write_text(build(), encoding="utf-8", newline="\n")
        # triage is embedded by build(), not a runtime dependency.
        return builder.build(
            [entry], out, name="err-o-roadrunner", version="0.6.0",
            allow_imports=["triage"])


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('output', nargs='?', type=Path, help='output Python file')
    target.add_argument('--uv-tool', type=Path, metavar='DIR',
                        help='write a uv tool package of the same bytes to DIR (must not exist)')
    args = parser.parse_args(argv)
    if args.uv_tool:
        print(json.dumps(build_uv_tool(args.uv_tool), indent=2))
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build(), encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
