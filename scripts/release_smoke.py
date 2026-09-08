"""Install our wheel outside the source tree and exercise every public CLI operation.

Runtime dependencies are inherited from the testing interpreter. This is NOT a
fully air-gapped dependency-installation test or cross-platform validation.
"""
from __future__ import annotations
import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from fdt.util import write_json

ROOT=Path(__file__).resolve().parents[1]

def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--wheel')
    parser.add_argument('--out',default=str(ROOT/'artifacts/release_check.json'))
    args=parser.parse_args()
    candidates=sorted((ROOT/'dist').glob('*.whl'))
    if not args.wheel and not candidates:
        raise SystemExit('Build wheel first: python -m pip wheel --no-deps --no-build-isolation . --wheel-dir dist')
    wheel=Path(args.wheel).resolve() if args.wheel else candidates[-1]
    results=[]
    with tempfile.TemporaryDirectory(prefix='fdt_release_') as temp:
        cwd=Path(temp); site=cwd/'installed';env={**os.environ,'PYTHONPATH':str(site)}
        def run(label: str, argv: list[str], expected: int=0) -> dict | None:
            start=time.perf_counter()
            proc=subprocess.run(argv,cwd=cwd,env=env,text=True,capture_output=True,timeout=90)
            results.append({'check':label,'expected_exit':expected,'actual_exit':proc.returncode,
                'passed':proc.returncode==expected,'elapsed_seconds':round(time.perf_counter()-start,6),
                'stderr_tail':proc.stderr[-1200:].replace(temp,'<temporary_directory>')})
            if proc.returncode!=expected:
                raise RuntimeError(f'{label}: {proc.stdout}\n{proc.stderr}')
            try:return json.loads(proc.stdout)
            except json.JSONDecodeError:return None
        run('install_wheel_into_empty_target',[sys.executable,'-m','pip','install','--no-index','--no-deps','--target',str(site),str(wheel)])
        origin=run('import_installed_package_and_schemas',[sys.executable,'-c',
            'import json, pathlib, fdt; p=pathlib.Path(fdt.__file__); assert "installed" in str(p); '
            'assert len(list((p.parent/"schemas").glob("*.json")))==4; print(json.dumps({"installed":True,"schemas":4}))'])
        assert origin=={'installed':True,'schemas':4}
        cmd=[sys.executable,'-m','fdt']
        modes=run('list_five_modes',cmd+['list-modes']);assert len(modes['modes'])==5
        csv=ROOT/'data/demo/consumer_001.csv';snapshot=ROOT/'examples/snapshot_001.json'
        db=cwd/'demo.sqlite';history=cwd/'history.sqlite'
        built=run('build_from_csv_and_snapshot',cmd+['build','--csv',str(csv),'--snapshot',str(snapshot),'--db',str(db)])
        assert built['state']['absolute_cash_ready'] and built['revision']==0
        inspected=run('inspect_persisted_twin',cmd+['inspect','--db',str(db)])
        assert inspected['input_digest']==built['input_digest']
        for mode in ('forecast','what_if','goal','risk','optimize'):
            result=run('run_'+mode,cmd+['run','--db',str(db),'--request',str(ROOT/f'examples/requests/{mode}.json'),'--paths','20'])
            assert result['mode']==mode and result['status']=='ok'
        run('build_history_only',cmd+['build','--csv',str(csv),'--db',str(history)])
        result=run('history_only_goal_has_no_fake_balance',cmd+['run','--db',str(history),'--request',str(ROOT/'examples/requests/goal.json'),'--paths','20'])
        assert result['status']=='insufficient_data' and result['metrics']['p_goal_reached']['value'] is None
        events=ROOT/'examples/events_001.json'
        new=run('atomic_live_fixture_batch',cmd+['update','--db',str(db),'--events',str(events),'--expected-revision','0'])
        assert new['revision']==1 and new['state']['absolute_cash_ready']
        dup=run('duplicate_event_batch_is_no_op',cmd+['update','--db',str(db),'--events',str(events),'--expected-revision','1'])
        assert dup['revision']==1 and dup['input_digest']==new['input_digest']
        rejected=run('stale_revision_rejected',cmd+['update','--db',str(db),'--events',str(events),'--expected-revision','0'],expected=2)
        assert rejected['error']['code']=='REVISION_CONFLICT'
        result=run('forecast_after_event_update',cmd+['run','--db',str(db),'--mode','forecast','--paths','20','--horizon-days','14'])
        assert result['as_of']=='2026-09-04' and result['status']=='ok'
    report={'environment':{'python':platform.python_version(),'platform':platform.platform()},
        'wheel':wheel.name,'fresh_target_outside_source':True,'installed_schemas':4,
        'runtime_dependencies':'Already installed interpreter dependencies; not a clean dependency resolution test.',
        'network_for_wheel_install':False,'checks':results,'all_passed':all(r['passed'] for r in results),
        'semantic_assertions_passed':True,'windows_macos_tested':False}
    write_json(args.out,report)
    print(f'{len(results)} installed-wheel checks passed')

if __name__=='__main__':main()
