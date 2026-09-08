"""Rolling-origin checks on supplied synthetic histories. This is not calibration evidence."""
from __future__ import annotations
from datetime import date,timedelta
from pathlib import Path
import numpy as np
from fdt import Twin,Engine
from fdt.util import write_json
ROOT=Path(__file__).resolve().parents[1]


def actual_values(txs):
    consumption=sum(t.amount_krw for t in txs if t.active and t.kind=='expense')
    resource=sum(t.amount_krw*(1 if t.kind in ('income','reimbursement') else -1)
                 for t in txs if t.active and t.kind not in ('internal_transfer','card_settlement'))
    return consumption,resource


def main():
    records=[]
    for i,path in enumerate(sorted((ROOT/'data/demo').glob('*.csv')),1):
        whole=Twin.from_csv(path)
        for cutoff in ('2026-07-31','2026-08-15'):
            start=date.fromisoformat(cutoff)+timedelta(days=1);end=start+timedelta(days=13)
            train=Twin.from_csv(path,as_of=cutoff)
            assert all(t.date<=cutoff for t in train.transactions)
            holdout=[t for t in whole.transactions if start.isoformat()<=t.date<=end.isoformat()]
            observed_expense,observed_resource=actual_values(holdout)
            result=Engine(train).run({'mode':'forecast','horizon_days':14,'paths':400,'seed':42})
            m=result['metrics']
            predictions={key:m[key]['value'] for key in m if key.startswith(('total_expense_p','terminal_resource_change_p'))}
            # Transparent naive comparator: last 28 calendar days' mean * 14.
            recent_start=date.fromisoformat(cutoff)-timedelta(days=27)
            recent=[t for t in train.transactions if t.date>=recent_start.isoformat()]
            naive_expense,naive_resource=actual_values(recent)
            naive_expense=round(naive_expense/28*14);naive_resource=round(naive_resource/28*14)
            records.append({'consumer':f'{i:03}','cutoff':cutoff,'horizon_days':14,'training_rows':len(train.transactions),
                'excluded_future_rows':train.metadata['future_rows_excluded'],'holdout_rows':len(holdout),
                'observed_expense_krw':observed_expense,'observed_resource_change_krw':observed_resource,**predictions,
                'expense_absolute_error_krw':abs(predictions['total_expense_p50_krw']-observed_expense),
                'resource_absolute_error_krw':abs(predictions['terminal_resource_change_p50_krw']-observed_resource),
                'expense_inside_p10_p90':predictions['total_expense_p10_krw']<=observed_expense<=predictions['total_expense_p90_krw'],
                'resource_inside_p10_p90':predictions['terminal_resource_change_p10_krw']<=observed_resource<=predictions['terminal_resource_change_p90_krw'],
                'naive_expense_prediction_krw':naive_expense,'naive_resource_prediction_krw':naive_resource,
                'naive_expense_absolute_error_krw':abs(naive_expense-observed_expense),
                'naive_resource_absolute_error_krw':abs(naive_resource-observed_resource)})
    summary={
        'windows':len(records),'expense_mae_krw':round(float(np.mean([r['expense_absolute_error_krw'] for r in records]))),
        'resource_mae_krw':round(float(np.mean([r['resource_absolute_error_krw'] for r in records]))),
        'naive_expense_mae_krw':round(float(np.mean([r['naive_expense_absolute_error_krw'] for r in records]))),
        'naive_resource_mae_krw':round(float(np.mean([r['naive_resource_absolute_error_krw'] for r in records]))),
        'expense_interval_coverage':sum(r['expense_inside_p10_p90'] for r in records)/len(records),
        'resource_interval_coverage':sum(r['resource_inside_p10_p90'] for r in records)/len(records),
        'nominal_interval_mass':0.8,'real_world_calibration_validated':False,
        'note':'8 overlapping-user synthetic windows cannot establish real-world calibration. No full-dataset future leakage; no real balance backtest because no observed balances exist.'}
    write_json(ROOT/'artifacts/backtest.json',{'summary':summary,'windows':records})
    print(summary)

if __name__=='__main__':main()
