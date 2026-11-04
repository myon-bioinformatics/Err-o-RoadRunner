from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import actions_log as a


class StepScanTests(unittest.TestCase):
    def test_combined_collector_matches_separate_definitions(self):
        body=['normal','##[error]message','::error file=test.py,line=12::problem',
              'FAILED tests/test_x.py::test_x - assertion','FAILED odd',
              '##[error]The operation was canceled.',
              'Process completed with exit code 1.']
        self.assertEqual(a._collect_log_wide(body),
            (a._collect_official_error_messages(body),a._collect_workflow_errors(body),a._collect_pytest_failed(body)))
        meta=a._analyze_step('job','Run test',body)
        self.assertTrue(meta.cancel_or_timeout)
        self.assertTrue(meta.is_failure_evidence())
        self.assertIs(meta.body,body)

    def test_no_error_and_exit_message_containing_timeout(self):
        self.assertFalse(a._analyze_step('','Run', ['normal']).cancel_or_timeout)
        self.assertFalse(a._analyze_step('','Run',
            ['##[error]Process completed with exit code 1 timeout expired']).cancel_or_timeout)


if __name__=='__main__':unittest.main()
