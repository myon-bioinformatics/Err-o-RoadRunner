"""scripts/build_standalone.py: the single-file bundle and its uv tool packaging.

- standalone_build: ErroRR.py agrees with the checkout, is deterministic and isolated from host modules.
- uv_tool_build: The shared packager must preserve the maintained standalone bundle.
"""
import importlib.util
import sys, tomllib
from pathlib import Path


def load_vendored(name, relpath):
    """Load a vendored file by path under a repo-unique name (cli_args README: Loading a vendored copy)."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / relpath)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


fleet = load_vendored("err_o_roadrunner_fleet_check", "vendor/fleet_check.py")
_cli_args = load_vendored("err_o_roadrunner_cli_args", "vendor/cli_args.py")
Command = _cli_args.Command
run_cli = _cli_args.run_cli
run_command = _cli_args.run_command


# ──── standalone_build ────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]


def build(target):
    proc = run_cli(ROOT / 'scripts/build_standalone.py', target)
    assert proc.returncode == 0, proc.stderr
    return proc


def execute(target, *args, stdin=None, cwd=None):
    return run_cli(target, *args, python_options=('-S',), stdin=stdin, cwd=cwd)


def test_standalone_and_package_agree_on_traceback(tmp_path):
    target = tmp_path / 'ErroRR.py'
    build(target)
    text = 'Traceback (most recent call last):\n  File "app.py", line 1, in <module>\nValueError: bad input\n'
    single = execute(target, '--paste', stdin=text, cwd=tmp_path)
    package = execute(ROOT / 'triage.py', '--paste', stdin=text, cwd=tmp_path)
    assert single.returncode == package.returncode == 0
    for expected in ('python exception type: ValueError', 'python exception message: bad input'):
        assert expected in single.stdout and expected in package.stdout


def test_standalone_supports_digest_and_sentinel(tmp_path):
    target = tmp_path / 'ErroRR.py'
    build(target)
    xml = tmp_path / 'result.xml'
    xml.write_text('<testsuite tests="1" failures="1"><testcase name="case"><failure message="bad"/></testcase></testsuite>')
    digest = execute(target, 'digest', str(xml), cwd=tmp_path)
    assert digest.returncode == 1
    assert 'case' in digest.stdout
    command = execute(target, 'sentinel', '--print-triage', '--', sys.executable, '-c', 'raise ValueError("demo")', cwd=tmp_path)
    assert command.returncode != 0
    assert 'python exception type: ValueError' in command.stdout


def test_standalone_generation_is_deterministic(tmp_path):
    first, second = tmp_path / 'first.py', tmp_path / 'second.py'
    build(first)
    build(second)
    assert first.read_bytes() == second.read_bytes()


def test_standalone_actions_log(tmp_path):
    target = tmp_path / 'ErroRR.py'
    build(target)
    log = tmp_path / 'actions.log'
    log.write_bytes((ROOT / 'tests/fixtures/actions_logs/python_fail.log').read_bytes())
    single = execute(target, '--actions-log', str(log), cwd=tmp_path)
    package = execute(ROOT / 'triage.py', '--actions-log', str(log), cwd=tmp_path)
    assert single.returncode == package.returncode == 0
    assert 'python exception type: AssertionError' in single.stdout and 'python exception type: AssertionError' in package.stdout


def test_olnm_style_exec_and_main_without_file_global(tmp_path):
    target = tmp_path / 'ErroRR.py'
    build(target)
    harness = """import contextlib, io, sys
source = open(sys.argv[1], encoding='utf-8').read()
namespace = {'__name__': '_olnm_bundled_erroroadrunner'}
exec(compile(source, '<olnm:ErroRR>', 'exec'), namespace)
output = io.StringIO()
with contextlib.redirect_stdout(output):
    result = namespace['main'](['ModuleNotFoundError:', 'No', 'module', 'named', 'example'])
assert result == 0 and 'possible Python exception' in output.getvalue()
assert namespace['main'](['--version']) == 0
"""
    proc = run_command(Command((sys.executable, '-S', '-c', harness, str(target))), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr


def test_child_process_isolated_from_host_backend_modules(tmp_path):
    target = tmp_path / 'ErroRR.py'
    build(target)
    harness = '''import subprocess, sys, types
sys.modules['triage'] = types.ModuleType('triage')
result = subprocess.run([sys.executable, '-S', sys.argv[1], 'ValueError:', 'bad'], capture_output=True, text=True)
assert result.returncode == 0, result.stderr
assert 'possible Python exception' in result.stdout
'''
    proc = run_command(Command((sys.executable, '-S', '-c', harness, str(target))), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr


# ──── uv_tool_build ───────────────────────────────────────────────

def test_common_package_preserves_bundle_and_command_names(tmp_path):
    olnm = tmp_path / "ErroRR.py"
    built = run_cli(ROOT / "scripts/build_standalone.py", olnm, python_options=("-S",))
    assert built.returncode == 0, built.stderr
    out = tmp_path / "package"
    result = run_cli(ROOT / "scripts/build_standalone.py", "--uv-tool", str(out), python_options=("-S",))
    assert result.returncode == 0, result.stderr
    assert (out / "erroroadrunner.py").read_bytes() == olnm.read_bytes()
    config = tomllib.loads((out / "pyproject.toml").read_text())
    assert config["project"]["name"] == "err-o-roadrunner"
    assert config["project"]["version"] == "0.6.0"
    assert config["project"]["requires-python"] == ">=3.11"
    assert config["project"]["dependencies"] == []
    assert config["project"]["scripts"] == {
        "erroroadrunner": "erroroadrunner:main"}
    assert config["tool"]["setuptools"]["py-modules"] == ["erroroadrunner"]
    run = run_cli(out / "erroroadrunner.py", "ValueError:", "example", python_options=("-S",), cwd=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "possible Python exception" in run.stdout
    second = run_cli(ROOT / "scripts/build_standalone.py", "--uv-tool", str(out), python_options=("-S",))
    assert second.returncode != 0
    assert (out / "erroroadrunner.py").read_bytes() == olnm.read_bytes()


def test_builder_matches_vendor_pin():
    rows = fleet.check_lock(ROOT, commits={"vendor/uv_tool_build.py": "673fc7b11d94ccad16740c0998683618f2e86254"})
    assert rows["vendor/uv_tool_build.py"]["repository"] == "myon-bioinformatics/Aoi"
