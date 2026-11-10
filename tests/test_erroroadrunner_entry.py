import subprocess
import sys
from pathlib import Path


def test_entry_point_forwards_unquoted_error():
    script = Path(__file__).resolve().parents[1] / 'erroroadrunner.py'
    run = subprocess.run([sys.executable, str(script), 'ModuleNotFoundError:', 'No', 'module', 'named', 'example'], capture_output=True, text=True)
    assert run.returncode == 0
    assert 'possible Python exception' in run.stdout


def test_entry_point_without_support_modules(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'erroroadrunner.py'
    standalone = tmp_path / 'erroroadrunner.py'
    standalone.write_bytes(script.read_bytes())
    run = subprocess.run([sys.executable, '-I', '-S', str(standalone), 'error'], cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 2
    assert 'triage components are missing' in run.stderr


def test_import_entry_does_not_load_triage():
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, '-c', 'import sys, erroroadrunner; print("triage" in sys.modules)'], cwd=root, capture_output=True, text=True)
    assert run.returncode == 0
    assert run.stdout.strip() == 'False'


def test_paste_entry_forwards_to_backend():
    root = Path(__file__).resolve().parents[1]
    text = 'Traceback (most recent call last):\n  File "app.py", line 1, in <module>\nValueError: bad input\n'
    run = subprocess.run([sys.executable, str(root / 'erroroadrunner.py'), '--paste'], input=text, capture_output=True, text=True)
    assert run.returncode == 0
    assert 'python exception type: ValueError' in run.stdout
