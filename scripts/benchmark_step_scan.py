"""Run from repository root: python -S -m scripts.benchmark_step_scan."""
from dataclasses import asdict
import json
import statistics
import subprocess
import sys
import time
import types
import actions_log


def main():
    sha='ff947a32ffdf89f69a372287adb8a9372b11b4b5'
    old=types.ModuleType('baseline_actions')
    sys.modules[old.__name__]=old
    exec(subprocess.check_output(['git','show',sha+':actions_log.py']),old.__dict__)
    records=[]
    for kind in ('ordinary','error'):
        body=['ordinary output']*20000
        if kind=='error':body+=['::error file=x.py,line=2::bad','FAILED test.py::x - fail','##[error]The operation was canceled.']
        expected=asdict(old._analyze_step('job','Run',body))
        samples={'baseline':[],'combined':[]}
        for repeat in range(8):
            for name in (list(samples) if repeat%2 else list(reversed(samples))):
                module=old if name=='baseline' else actions_log
                start=time.perf_counter_ns()
                result=module._analyze_step('job','Run',body)
                elapsed=time.perf_counter_ns()-start
                if not (asdict(result)==expected):
                    raise SystemExit('benchmark_step_scan: result mismatch')
                if repeat:samples[name].append(elapsed)
        records.append({'kind':kind,'lines':len(body),'samples_ns':samples,
                        'median_ns':{k:statistics.median(v) for k,v in samples.items()}})
    print(json.dumps({'baseline_sha':sha,'python':sys.version,'records':records},indent=2))


if __name__=='__main__':main()
