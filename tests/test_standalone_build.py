import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(target):
    return subprocess.run([sys.executable, str(ROOT / 'scripts/build_standalone.py'), str(target)], check=True, capture_output=True, text=True)


def execute(target, *args, stdin=None, cwd=None):
    return subprocess.run([sys.executable, '-S', str(target), *args], input=stdin, cwd=cwd, capture_output=True, text=True)


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
    proc = subprocess.run([sys.executable, '-S', '-c', harness, str(target)], cwd=tmp_path, capture_output=True, text=True)
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
    proc = subprocess.run([sys.executable, '-S', '-c', harness, str(target)], cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
