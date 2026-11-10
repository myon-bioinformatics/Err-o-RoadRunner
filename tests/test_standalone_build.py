import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(target):
    return subprocess.run([sys.executable, str(ROOT / 'scripts/build_standalone.py'), str(target)], check=True, capture_output=True, text=True)


def execute(target, *args, stdin=None, cwd=None):
    return subprocess.run([sys.executable, '-S', str(target), *args], input=stdin, cwd=cwd, capture_output=True, text=True)


def test_standalone_and_package_agree_on_traceback(tmp_path):
    target = tmp_path / 'erroroadrunner.py'
    build(target)
    text = 'Traceback (most recent call last):\n  File "app.py", line 1, in <module>\nValueError: bad input\n'
    single = execute(target, '--paste', stdin=text, cwd=tmp_path)
    package = execute(ROOT / 'erroroadrunner.py', '--paste', stdin=text, cwd=tmp_path)
    assert single.returncode == package.returncode == 0
    for expected in ('python exception type: ValueError', 'python exception message: bad input'):
        assert expected in single.stdout and expected in package.stdout


def test_standalone_supports_digest_and_sentinel(tmp_path):
    target = tmp_path / 'erroroadrunner.py'
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
    target = tmp_path / 'erroroadrunner.py'
    build(target)
    log = tmp_path / 'actions.log'
    log.write_bytes((ROOT / 'tests/fixtures/actions_logs/python_fail.log').read_bytes())
    single = execute(target, '--actions-log', str(log), cwd=tmp_path)
    package = execute(ROOT / 'erroroadrunner.py', '--actions-log', str(log), cwd=tmp_path)
    assert single.returncode == package.returncode == 0
    assert 'python exception type: AssertionError' in single.stdout and 'python exception type: AssertionError' in package.stdout
