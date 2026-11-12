"""Build a single-file Err-o-RoadRunner from the checked-in stdlib modules."""
from __future__ import annotations

import argparse
from pathlib import Path

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='output Python file')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build(), encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
