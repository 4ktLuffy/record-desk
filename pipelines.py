"""Versioned CSV contracts, replayable validation, and guarded publication.

Local snapshot batches, not streaming or incremental merge semantics.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import re
import sys
from collections import Counter
from decimal import Decimal
import core
import datasets
from table_quality import inspect_csv

ENGINE = 'csv-contracts-v1'
TYPES = ('text', 'integer', 'decimal', 'date', 'boolean')


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def init():
    with core.connection() as con:
        con.execute('CREATE TABLE IF NOT EXISTS pipelines(id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, created TEXT)')
        con.execute('CREATE TABLE IF NOT EXISTS pipeline_contracts(id TEXT PRIMARY KEY, pipeline_id TEXT, version INTEGER, spec_hash TEXT, spec TEXT, created TEXT, UNIQUE(pipeline_id,version), UNIQUE(pipeline_id,spec_hash))')
        con.execute('CREATE TABLE IF NOT EXISTS pipeline_runs(id TEXT PRIMARY KEY, pipeline_id TEXT, contract_id TEXT, dataset_id TEXT, report TEXT)')
        con.execute('CREATE TABLE IF NOT EXISTS pipeline_publications(id INTEGER PRIMARY KEY, pipeline_id TEXT, run_id TEXT, dataset_id TEXT, previous_run_id TEXT, created TEXT)')
        for table in ('pipeline_contracts','pipeline_runs','pipeline_publications'):
            for action in ('UPDATE','DELETE'):
                con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Pipeline history is append-only'); END")


def validate_spec(spec):
    if not isinstance(spec,dict) or set(spec)!= {'fields','key','max_quarantine_percent','min_accepted_rows'}:
        raise ValueError('Contract needs fields, key, max_quarantine_percent and min_accepted_rows.')
    fields=spec['fields']
    if not isinstance(fields,list) or not 1<=len(fields)<=100:
        raise ValueError('Define between 1 and 100 fields.')
    names=[]
    for f in fields:
        if not isinstance(f,dict) or set(f)!={'name','type','required'}:
            raise ValueError('Each field needs name, type and required.')
        name=f['name']
        if not isinstance(name,str) or not name or name!=name.strip() or len(name)>250:
            raise ValueError('Field names must be nonempty, trimmed and at most 250 characters.')
        if f['type'] not in TYPES or type(f['required']) is not bool:
            raise ValueError('Choose a supported type and a boolean required flag.')
        names.append(name)
    if len({n.casefold() for n in names})!=len(names):
        raise ValueError('Field names must be unambiguous.')
    key=spec['key']
    if not isinstance(key,list) or not key or any(not isinstance(k,str) or k not in names for k in key) or len(set(key))!=len(key):
        raise ValueError('Select one or more distinct fields as the batch unique key.')
    if any(not f['required'] for f in fields if f['name'] in key):
        raise ValueError('Key fields must be required.')
    if type(spec['max_quarantine_percent']) is not int or not 0<=spec['max_quarantine_percent']<=100:
        raise ValueError('Maximum quarantine percentage must be an integer from 0 through 100.')
    if type(spec['min_accepted_rows']) is not int or not 1<=spec['min_accepted_rows']<=10000:
        raise ValueError('Minimum accepted rows must be between 1 and 10000.')


def contract(ident):
    with core.connection() as con:
        row=con.execute('SELECT c.*,p.name FROM pipeline_contracts c JOIN pipelines p ON p.id=c.pipeline_id WHERE c.id=?',(ident,)).fetchone()
    if row is None:raise ValueError('Contract not found.')
    result=dict(row);result['spec']=json.loads(result['spec']);return result


def save_contract(name,spec):
    if not isinstance(name,str) or not name.strip() or len(name)>120:
        raise ValueError('Give the pipeline a name up to 120 characters.')
    name=name.strip();validate_spec(spec)
    pid=digest({'name':name});sha=digest(spec);ident=digest({'pipeline':pid,'spec':sha})
    with core.connection() as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('INSERT OR IGNORE INTO pipelines VALUES (?,?,?)',(pid,name,now()))
        old=con.execute('SELECT id FROM pipeline_contracts WHERE id=?',(ident,)).fetchone()
        if old is None:
            version=con.execute('SELECT COALESCE(MAX(version),0)+1 FROM pipeline_contracts WHERE pipeline_id=?',(pid,)).fetchone()[0]
            con.execute('INSERT INTO pipeline_contracts VALUES (?,?,?,?,?,?)',(ident,pid,version,sha,json.dumps(spec),now()))
    return contract(ident)


def listing():
    with core.connection() as con:
        return [dict(r) for r in con.execute('SELECT * FROM pipelines ORDER BY name')]


def current(con,pipeline_id):
    row=con.execute('SELECT * FROM pipeline_publications WHERE pipeline_id=? ORDER BY id DESC LIMIT 1',(pipeline_id,)).fetchone()
    return dict(row) if row else None


def detail(pipeline_id):
    with core.connection() as con:
        p=con.execute('SELECT * FROM pipelines WHERE id=?',(pipeline_id,)).fetchone()
        if p is None:raise ValueError('Pipeline not found.')
        versions=[dict(r) for r in con.execute('SELECT id,version,spec_hash,created FROM pipeline_contracts WHERE pipeline_id=? ORDER BY version DESC',(pipeline_id,))]
        runs=[json.loads(r['report']) for r in con.execute('SELECT report FROM pipeline_runs WHERE pipeline_id=? ORDER BY rowid DESC',(pipeline_id,))]
        history=[dict(r) for r in con.execute('SELECT * FROM pipeline_publications WHERE pipeline_id=? ORDER BY id DESC',(pipeline_id,))]
    return dict(**dict(p),contracts=versions,runs=[{k:r[k] for k in ('id','contract_version','source_name','created','counts','gate')} for r in runs],publications=history,current=history[0] if history else None)


def parse(value,field):
    if value=='':
        if field['required']:raise ValueError('Required value is empty.')
        return None
    if value!=value.strip():raise ValueError('Surrounding whitespace; clean explicitly.')
    kind=field['type']
    if kind=='integer':
        if not re.fullmatch(r'-?(?:0|[1-9][0-9]{0,14})',value):raise ValueError('Expected an integer with at most 15 digits; no separators or leading zeros.')
        return int(value)
    if kind=='decimal':
        if not re.fullmatch(r'-?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,9})?',value):raise ValueError('Expected exact decimal: up to 18 integer and 9 fractional digits; no exponent or separators.')
        return Decimal(value)
    if kind=='date':
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value):raise ValueError('Expected Gregorian YYYY-MM-DD.')
        try:dt.date.fromisoformat(value)
        except ValueError:raise ValueError('Invalid Gregorian date.')
    if kind=='boolean':
        if value not in ('true','false'):raise ValueError('Expected lowercase true or false.')
        return value=='true'
    return value


def get_run(ident):
    with core.connection() as con:
        row=con.execute('SELECT report FROM pipeline_runs WHERE id=?',(ident,)).fetchone()
    if row is None:raise ValueError('Pipeline run not found.')
    return json.loads(row['report'])


def run(contract_id,dataset_id):
    config=contract(contract_id);source=datasets.get(dataset_id)
    ident=digest({'engine':ENGINE,'contract':contract_id,'source':dataset_id})
    with core.connection() as con:
        old=con.execute('SELECT report FROM pipeline_runs WHERE id=?',(ident,)).fetchone()
    if old:return json.loads(old['report'])
    quality=inspect_csv(source['content']);spec=config['spec'];fields=spec['fields']
    raw=list(csv.reader(io.StringIO(source['content'].lstrip('\ufeff'),newline=''),strict=True));headers=raw[0]
    expected=[f['name'] for f in fields]
    schema_errors=[]
    if quality['counts'].get('header'):schema_errors.append('Ambiguous or empty source header.')
    missing=sorted(set(expected)-set(headers));extra=sorted(set(headers)-set(expected))
    if missing:schema_errors.append('Missing columns: '+', '.join(missing))
    if extra:schema_errors.append('Unexpected columns: '+', '.join(extra))
    candidates=[];keys=Counter()
    if not schema_errors:
        positions={name:headers.index(name) for name in expected}
        for number,cells in enumerate(raw[1:],2):
            reasons=[];values={}
            if len(cells)!=len(headers):reasons.append(dict(field=None,code='row_width',message='Column count does not match header.'))
            for field in fields:
                name=field['name'];index=positions[name]
                if index>=len(cells):continue
                try:values[name]=parse(cells[index],field)
                except ValueError as e:reasons.append(dict(field=name,code='invalid_value',message=str(e)))
            key=tuple(values[k] for k in spec['key']) if all(k in values and values[k] is not None for k in spec['key']) else None
            if key is not None:keys[key]+=1
            candidates.append(dict(record=number,raw=cells,values=values,reasons=reasons,key=key))
    else:
        candidates=[dict(record=n,raw=cells,values={},key=None,reasons=[dict(field=None,code='schema',message='Batch schema differs from pinned contract.')]) for n,cells in enumerate(raw[1:],2)]
    accepted=[];quarantine=[];errors=Counter()
    for row in candidates:
        if row['key'] is not None and keys[row['key']]>1:
            row['reasons'].append(dict(field=None,code='duplicate_key',message='Repeated typed key; all occurrences excluded.'))
        if row['reasons']:
            errors.update(x['code'] for x in row['reasons'])
            quarantine.append(dict(record=row['record'],raw=row['raw'],reasons=row['reasons']))
        else:
            # Exact decimals travel as strings in JSON; field types remain pinned
            # in the contract. Do not pass amounts through binary floating point.
            values={k:format(v,'f') if isinstance(v,Decimal) else v for k,v in row['values'].items()}
            accepted.append(dict(record=row['record'],raw=row['raw'],values=values))
    total=len(raw)-1;reasons=list(schema_errors)
    if len(accepted)<spec['min_accepted_rows']:reasons.append('Accepted rows fall below the configured minimum.')
    if total and len(quarantine)*100>spec['max_quarantine_percent']*total:
        reasons.append('Quarantine percentage exceeds the configured maximum.')
    report=dict(id=ident,engine=ENGINE,pipeline_id=config['pipeline_id'],pipeline_name=config['name'],contract_id=contract_id,
                contract_version=config['version'],contract_sha256=config['spec_hash'],spec=spec,dataset_id=dataset_id,
                source_sha256=dataset_id,source_name=source['name'],headers=headers,created=now(),schema_errors=schema_errors,
                counts=dict(source=total,accepted=len(accepted),quarantined=len(quarantine)),error_counts=dict(errors),
                gate=dict(passed=not reasons,reasons=reasons,max_quarantine_percent=spec['max_quarantine_percent'],min_accepted_rows=spec['min_accepted_rows']),
                accepted=accepted,quarantine=quarantine,
                note='Pinned CSV snapshot contract; exact case-sensitive headers, reordered columns allowed, extra/missing columns block publication. Typed composite keys are unique within this batch only. No imputation, deduplication or automatic repair. Exact decimal values remain strings in JSON. Accepted exports retain original cell spelling in contract column order. Publishing requires a passing gate and the latest contract version; it replaces the current snapshot pointer, never original data. Replays reuse the same run and do not publish automatically. Passing rules does not prove business accuracy or completeness.')
    with core.connection() as con:
        con.execute('INSERT OR IGNORE INTO pipeline_runs VALUES (?,?,?,?,?)',(ident,config['pipeline_id'],contract_id,dataset_id,json.dumps(report)))
    return get_run(ident)


def accepted_csv(report):
    if not report['gate']['passed']:raise ValueError('Quality gate blocked: accepted output cannot be published or exported.')
    out=io.StringIO(newline='');writer=csv.writer(out);names=[f['name'] for f in report['spec']['fields']]
    writer.writerow(names)
    for row in report['accepted']:writer.writerow([row['raw'][report['headers'].index(n)] for n in names])
    return out.getvalue()


def export(ident,kind):
    report=get_run(ident)
    if kind=='accepted':content=accepted_csv(report)
    elif kind=='quarantine':
        out=io.StringIO(newline='');writer=csv.writer(out);writer.writerow(['source_record','reasons_json','raw_cells_json'])
        for row in report['quarantine']:writer.writerow([row['record'],json.dumps(row['reasons']),json.dumps(row['raw'])])
        content=out.getvalue()
    else:raise ValueError('Choose accepted or quarantine export.')
    # Human-facing spreadsheet export only. Machine JSON and published datasets
    # preserve original cells without protective apostrophes.
    out=io.StringIO(newline='');writer=csv.writer(out)
    for cells in csv.reader(io.StringIO(content,newline='')):
        writer.writerow(["'"+v if v.lstrip().startswith(('=','+','-','@')) or v.startswith(('\t','\r')) else v for v in cells])
    return out.getvalue()


def publish(run_id,expected_current):
    report=get_run(run_id);content=accepted_csv(report);dataset_id=hashlib.sha256(content.encode()).hexdigest();pid=report['pipeline_id']
    with core.connection() as con:
        con.execute('BEGIN IMMEDIATE')
        previous=current(con,pid)
        if previous and previous['run_id']==run_id:return dict(previous,reused=True)
        if (previous['run_id'] if previous else None)!=expected_current:
            raise ValueError('Published snapshot changed. Refresh the pipeline before publishing.')
        latest=con.execute('SELECT id FROM pipeline_contracts WHERE pipeline_id=? ORDER BY version DESC LIMIT 1',(pid,)).fetchone()[0]
        if latest!=report['contract_id']:raise ValueError('A newer contract exists. Validate against the latest version before publishing.')
        stamp=now();name=(report['pipeline_name']+' · accepted snapshot')[:250]
        con.execute('INSERT OR IGNORE INTO datasets(id,name,content) VALUES (?,?,?)',(dataset_id,name,content))
        con.execute('INSERT INTO dataset_audit(dataset_id,action,value) VALUES (?,?,?)',(dataset_id,'pipeline publication',json.dumps(dict(run_id=run_id,source=report['dataset_id'],contract_id=report['contract_id']))))
        cursor=con.execute('INSERT INTO pipeline_publications(pipeline_id,run_id,dataset_id,previous_run_id,created) VALUES (?,?,?,?,?)',(pid,run_id,dataset_id,expected_current,stamp))
        return dict(id=cursor.lastrowid,pipeline_id=pid,run_id=run_id,dataset_id=dataset_id,previous_run_id=expected_current,created=stamp,reused=False)


def demo():
    good=datasets.save('Pipeline demo · passing.csv','id,date,amount,active\nA1,2026-09-18,12.50,true\nA2,2026-09-19,18.00,false\n')
    bad=datasets.save('Pipeline demo · failing.csv','id,date,amount,active\nA1,2026-09-18,12.50,true\nA1,2026-09-19,18.00,false\nA3,2026-02-30,unknown,true\n')
    spec=dict(fields=[dict(name=name,type=kind,required=True) for name,kind in zip(['id','date','amount','active'],['text','date','decimal','boolean'])],key=['id'],max_quarantine_percent=0,min_accepted_rows=1)
    c=save_contract('Demo · daily business records',spec)
    return dict(pipeline_id=c['pipeline_id'],contract_id=c['id'],good=good['id'],bad=bad['id'])


def main():
    parser=argparse.ArgumentParser(description='Validate local snapshot batches against pinned CSV contracts. No provider calls.')
    sub=parser.add_subparsers(dest='command',required=True)
    check=sub.add_parser('run');check.add_argument('--contract',required=True)
    source=check.add_mutually_exclusive_group(required=True);source.add_argument('--dataset');source.add_argument('--csv')
    check.add_argument('--publish',action='store_true');check.add_argument('--expected-current')
    create=sub.add_parser('contract');create.add_argument('--name',required=True);create.add_argument('--spec',required=True)
    sub.add_parser('list')
    args=parser.parse_args();core.init();datasets.init();init()
    if args.command=='list':print(json.dumps(listing(),indent=2));return 0
    from pathlib import Path
    if args.command=='contract':
        print(json.dumps(save_contract(args.name,json.loads(Path(args.spec).read_text(encoding='utf-8'))),indent=2));return 0
    source_id=args.dataset
    if args.csv:
        path=Path(args.csv)
        if path.stat().st_size>2_000_000:raise ValueError('CSV exceeds 2 MB.')
        source_id=datasets.save(path.name,path.read_bytes().decode('utf-8'))['id']
    report=run(args.contract,source_id)
    output={k:report[k] for k in ('id','engine','source_sha256','contract_id','counts','gate')}
    if args.publish and report['gate']['passed']:output['publication']=publish(report['id'],args.expected_current)
    print(json.dumps(output,indent=2))
    return 0 if report['gate']['passed'] else 2


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError) as exc:print(str(exc),file=sys.stderr);sys.exit(1)
