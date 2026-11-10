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
