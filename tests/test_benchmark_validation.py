"""Exercise benchmark success and failure in normal and optimized subprocesses."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

class BenchmarkValidationTests(unittest.TestCase):
    def test_parity_in_both_interpreter_modes(self):
        for optimized in (False, True):
            for mismatch in (False, True):
                with self.subTest(optimized=optimized, mismatch=mismatch):
                    code = """
from contextlib import nullcontext
from pathlib import Path
import runpy
from unittest.mock import patch
ROOT = Path(%r)
g = runpy.run_path(str(ROOT / 'scripts/benchmark_step_scan.py'))['main'].__globals__
with patch.object(g['subprocess'], 'check_output', return_value=(ROOT / 'errlib' / 'actions_log.py').read_bytes()):
    with %s:
        g['main']()
""" % (str(ROOT), "patch.object(g['actions_log'], '_analyze_step', return_value=g['actions_log']._StepMeta(name='different', body=[]))" if mismatch else 'nullcontext()')
                    result = subprocess.run(
                        [sys.executable, '-S', *(['-O'] if optimized else []), '-c', code],
                        cwd=ROOT, capture_output=True, text=True, timeout=60,
                    )
                    if mismatch:
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertEqual(result.stdout, '')
                        self.assertIn('result mismatch', result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertTrue(result.stdout.startswith('{'), result.stdout)

if __name__ == '__main__':
    unittest.main()
