"""Reproducible synthetic investigation lab. Labels never enter detectors."""
import datetime as dt
import hashlib
import json
import math
import random
import statistics as stats

VERSION='inventory-lab-v1'


def generate(seed=42):
    rng=random.Random(seed);rows=[];truth={};stocks={}
    for day in range(120):
        for product in range(20):
            for warehouse in ('North','South'):
                key=(product,warehouse);opening=stocks.get(key,600)
                sales=max(0,round(rng.gauss(8+product*2,3+product/4)))
                promotion=day>=90 and rng.random()<.025
                if promotion:sales*=2
                injected=day>=90 and rng.random()<.04
                if injected:sales+=rng.choice([8,25,80])
                received=300 if opening<200 else 0
                closing=opening+received-sales
                stocks[key]=closing
                mismatch=day>=90 and rng.random()<.03
                observed=closing+rng.choice([-15,-1,1,25]) if mismatch else closing
                ident=f'{day:03d}-{product:02d}-{warehouse}'
                rows.append(dict(id=ident,day=day,date=str(dt.date(2026,1,1)+dt.timedelta(days=day)),product=f'SKU-{product:03d}',warehouse=warehouse,
                    opening=opening,received=received,sold=sales,closing=observed,promotion=promotion,unit_value_cents=250+product*125))
                truth[ident]=dict(reconciliation=mismatch,sales_anomaly=injected)
    return rows,truth


def fit(training):
    groups={}
    for row in training:groups.setdefault((row['product'],row['warehouse']),[]).append(row['sold'])
    profiles={}
    for key,values in groups.items():
        median=stats.median(values)
        profiles[key]=(median,max(1,1.4826*stats.median(abs(x-median) for x in values)))
    all_values=[r['sold'] for r in training]
    return profiles,stats.mean(all_values)+3*stats.pstdev(all_values)


def detect(rows,profiles,global_threshold,threshold=3.5):
    results=[]
    for row in rows:
        median,scale=profiles[(row['product'],row['warehouse'])]
        score=(row['sold']-median)/scale
        expected=row['opening']+row['received']-row['sold']
        delta=row['closing']-expected
        results.append(dict(id=row['id'],date=row['date'],product=row['product'],warehouse=row['warehouse'],
            reconciliation=delta!=0,sales_anomaly=score>threshold,global_baseline=row['sold']>global_threshold,
            score=round(score,3),delta=delta,review_value_cents=abs(delta)*row['unit_value_cents'],
            evidence=dict(opening=row['opening'],received=row['received'],sold=row['sold'],observed_closing=row['closing'],expected_closing=expected,
                          historical_median=median,robust_scale=round(scale,3),promotion=row['promotion']),
            explanation=(f'Closing quantity differs by {delta} units from opening + received − sold.' if delta else 'Stock arithmetic balances.')+
                        (' Sales exceed the historical per-product/location range; investigate context before deciding this is an error.' if score>threshold else '')))
    return results


def metrics(predictions,truth,prediction,target):
    tp=fp=fn=tn=0
    for row in predictions:
        actual=truth[row['id']][target];flag=row[prediction]
        tp+=int(flag and actual);fp+=int(flag and not actual);fn+=int(not flag and actual);tn+=int(not flag and not actual)
    return dict(tp=tp,fp=fp,fn=fn,tn=tn,precision=round(tp/(tp+fp),3) if tp+fp else None,
                recall=round(tp/(tp+fn),3) if tp+fn else None,false_alerts_per_1000=round(1000*fp/(tp+fp+fn+tn),2))


def run(seed=42):
    rows,truth=generate(seed)
    train=[r for r in rows if r['day']<60]
    calibration=[r for r in rows if 60<=r['day']<90]
    test=[r for r in rows if r['day']>=90]
    profiles,baseline=fit(train)
    # Choose threshold from unlabelled calibration scores only. Held-out labels
    # and data do not influence fitting or threshold selection.
    calibration_predictions=detect(calibration,profiles,baseline)
    scores=sorted(r['score'] for r in calibration_predictions)
    threshold=max(3.5,scores[math.ceil(.99*len(scores))-1])
    predictions=detect(test,profiles,baseline,threshold)
    alerts=[r for r in predictions if r['reconciliation'] or r['sales_anomaly']]
    alerts.sort(key=lambda r:(r['review_value_cents'],r['score']),reverse=True)
    return dict(version=VERSION,seed=seed,source_sha256=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),
        split=dict(training=len(train),calibration=len(calibration),test=len(test)),threshold=threshold,
        metrics=dict(reconciliation=metrics(predictions,truth,'reconciliation','reconciliation'),
                     global_baseline=metrics(predictions,truth,'global_baseline','sales_anomaly'),
                     per_product_robust=metrics(predictions,truth,'sales_anomaly','sales_anomaly')),
        alerts=alerts,
        note='Synthetic daily product/location records only. Train: days 0–59; calibration: 60–89; held-out test: 90–119. Detectors do not receive labels. Promotions are legitimate negative cases that may trigger false alerts. Review value is absolute quantity discrepancy × synthetic unit value, not proven financial loss. No causal diagnosis or real-world accuracy claim.')


if __name__=='__main__':
    import argparse
    from pathlib import Path
    p=argparse.ArgumentParser();p.add_argument('--seed',type=int,default=42);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():p.error('Output already exists; choose a new path.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(run(args.seed),indent=2))
