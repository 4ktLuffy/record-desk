"""Explicit live benchmark: python benchmark.py --models MODEL ... --output PATH."""
import argparse
import hashlib
import json
import statistics
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from extraction_prompt import SYSTEM
from model_client import complete

AMOUNTS={'total','subtotal','tax','service_charge'}


def equal(field,actual,expected):
    if expected is None or expected=='':return actual is None or actual==''
    if field in AMOUNTS:
        try:
            a,b=Decimal(str(actual)),Decimal(str(expected))
            return a.is_finite() and b.is_finite() and a==b
        except InvalidOperation:return False
    return isinstance(actual,str) and actual.strip().casefold()==str(expected).strip().casefold()


def score(fields,expected):
    if not isinstance(fields,dict):fields={}
    correct=sum(k in fields and equal(k,fields[k],v) for k,v in expected.items())
    invented=sum(v in (None,'') and fields.get(k) not in (None,'') for k,v in expected.items())
    return dict(correct=correct,total=len(expected),unsupported_unknowns=invented)


def run(cases,models,output):
    report=dict(scope='Synthetic text extraction only; no OCR. Provisional authored labels, not independently adjudicated. Not a production accuracy estimate.',
                prompt_sha256=hashlib.sha256(SYSTEM.encode()).hexdigest(),
                cases_sha256=hashlib.sha256(json.dumps(cases,sort_keys=True).encode()).hexdigest(),runs=[],summary={})
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise ValueError('Choose a new output path to preserve earlier results.')
    for model in models:
        for case in cases:
            row=dict(model=model,case=case['id'],total=len(case['expected']),correct=0,unsupported_unknowns=0)
            started=time.monotonic()
            try:
                response=complete(SYSTEM,case['text'],model)
                if not isinstance(response['result'].get('fields'),dict):
                    raise ValueError('Extraction response needs a fields object.')
                row.update(response);row.update(score(response['result']['fields'],case['expected']));row['status']='ok'
            except ValueError as error:row.update(status='failed',error=str(error))
            row['elapsed_seconds']=round(time.monotonic()-started,3)
            report['runs'].append(row)
            output.write_text(json.dumps(report,indent=2))
            print(model,case['id'],row['status'],str(row['correct'])+'/'+str(row['total']),flush=True)
            time.sleep(2)
        rows=[r for r in report['runs'] if r['model']==model]
        report['summary'][model]=dict(correct=sum(r['correct'] for r in rows),total=sum(r['total'] for r in rows),
            failures=sum(r['status']=='failed' for r in rows),unsupported_unknowns=sum(r['unsupported_unknowns'] for r in rows),
            median_elapsed_seconds=statistics.median(r['elapsed_seconds'] for r in rows),
            prompt_tokens=sum(r.get('usage',{}).get('prompt_tokens',0) for r in rows),
            completion_tokens=sum(r.get('usage',{}).get('completion_tokens',0) for r in rows))
        output.write_text(json.dumps(report,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models',nargs='+',required=True)
    parser.add_argument('--cases',type=Path,default=Path(__file__).parent/'benchmarks/synthetic.json')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    run(json.loads(args.cases.read_text()),args.models,args.output)
