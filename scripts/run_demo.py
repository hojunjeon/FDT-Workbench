"""Run the real supplied four CSVs through every mode. No network or bank calls."""
from __future__ import annotations
import argparse
import importlib.metadata
import platform
import time
from pathlib import Path
from fdt import Twin,Engine
from fdt.util import read_json,write_json,validate

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',default=str(ROOT/'artifacts/demo'))
    args=parser.parse_args();out=Path(args.out)
    summary=[]
    for i,path in enumerate(sorted((ROOT/'data/demo').glob('*.csv')),1):
        folder=out/f'{i:03}'
        snapshot=read_json(ROOT/f'examples/snapshot_{i:03}.json')
        twin=Twin.from_csv(path,snapshot=snapshot)
        write_json(folder/'twin_summary.json',twin.inspect())
        history=Twin.from_csv(path)
        write_json(folder/'history_only_goal.json',Engine(history).run({'mode':'goal','goal':{'target_krw':1000000},'paths':100,'horizon_days':30}))
        for mode in ('forecast','what_if','goal','risk','optimize'):
            request=read_json(ROOT/f'examples/requests/{mode}.json')
            start=time.perf_counter();result=Engine(twin).run(request);seconds=time.perf_counter()-start
            validate('result',result)
            write_json(folder/f'{mode}.json',result)
            summary.append({'consumer':f'{i:03}','user_id':twin.user_id,'mode':mode,'status':result['status'],
                'elapsed_seconds':round(seconds,6),'paths':request['paths'],'horizon_days':request['horizon_days'],
                'terminal_cash_p50_krw':result['metrics']['terminal_cash_p50_krw']['value'],
                'p_any_account_shortfall':result['metrics']['p_any_account_shortfall']['value'],
                'decision':result.get('decision',{}),'input_digest':result['input_digest']})
    environment={'python':platform.python_version(),'platform':platform.platform(),
                 'packages':{name:importlib.metadata.version(name) for name in ('numpy','jsonschema','pytest','pytest-cov','setuptools')}}
    write_json(out/'summary.json',{'environment':environment,'runs':summary,'total_runs':len(summary),
                               'all_status_ok':all(r['status']=='ok' for r in summary),
                               'note':'All absolute amounts depend on explicitly invented USER_ASSUMPTION demo snapshots.'})
    print(f'{len(summary)} mode runs written to {out}')

if __name__=='__main__':main()
