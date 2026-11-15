"""Fleet contracts through the parent's shared checks (vendor/fleet_check.py): locks, pins, benchmark."""
import pytest

from vendor import fleet_check as fleet

ROOT = fleet.repo_root(__file__)
PARENT_WORKFLOW_PIN = "9e3da7ae1846e9604ebc24bb5178f7de094a4e77"
# Node sources and the shared checks stay on accepted upstream commits with exact bytes.
ACCEPTED = {
    "vendor/xprobe.py": "1ffbb7b984075c683690bbe4e9deb06199cdfe94",
    "vendor/cli_args.py": "9ff06c4326fc7ba934112abecbc59451ab07be6a",
    "vendor/fleet_check.py": "45ed741f7e1f60179934cee6fa1e16a887f97cb0",
}


def test_vendor_lock_matches_checked_in_bytes_and_accepted_commits():
    fleet.check_lock(ROOT, commits=ACCEPTED)


def test_parent_workflow_references_use_reviewed_rollout_commit():
    assert set(fleet.parent_refs(ROOT, skip=(".vendor-sync-tools",))) == {PARENT_WORKFLOW_PIN}


@pytest.mark.parametrize("optimized", [False, True], ids=["normal", "optimized"])
@pytest.mark.parametrize("mismatch", [False, True], ids=["same", "mismatch"])
def test_benchmark_parity_in_both_interpreter_modes(optimized, mismatch):
    patch = "mock.patch.object(g['subprocess'], 'check_output', return_value=open(%r, 'rb').read()).start()\n" % str(ROOT / "errlib" / "actions_log.py")
    if mismatch:
        patch += "mock.patch.object(g['actions_log'], '_analyze_step', return_value=g['actions_log']._StepMeta(name='different', body=[])).start()\n"
    result = fleet.run_main(ROOT, "scripts/benchmark_step_scan.py", patch, optimized=optimized)
    if mismatch:
        assert (result.returncode, result.stdout) == (1, ""), result.stderr
        assert "result mismatch" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("{"), result.stdout
