"""Saved source tables and explicit document reconciliation; no model-written joins."""
import csv
import hashlib
import io
import json
import re
import core
from table_quality import inspect_csv

ROLES = ('merchant', 'receipt_number', 'currency', 'total')


def init():
    with core.connection() as con:
        con.execute('CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY, name TEXT, content TEXT, mapping TEXT DEFAULT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS dataset_audit(id INTEGER PRIMARY KEY, dataset_id TEXT, action TEXT, value TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP)')


def get(ident):
    with core.connection() as con:
        row = con.execute('SELECT * FROM datasets WHERE id=?', (ident,)).fetchone()
    if row is None:
        raise ValueError('Dataset not found.')
    return dict(row)


def listing():
    with core.connection() as con:
        return [dict(r) for r in con.execute('SELECT id,name FROM datasets ORDER BY rowid DESC')]


def table(ident):
    source = get(ident)
    report = inspect_csv(source['content'])
    if report['counts'].get('header') or report['counts'].get('row_width'):
        raise ValueError('Correct column names and row lengths before comparing or cleaning.')
    rows = list(csv.reader(io.StringIO(source['content'].lstrip('\ufeff'), newline='')))
    return source, rows[0], rows[1:]


def export_csv(ident):
    source=get(ident)
    rows=csv.reader(io.StringIO(source['content'].lstrip('\ufeff'),newline=''))
    output=io.StringIO(newline='');writer=csv.writer(output)
    for row in rows:
        writer.writerow(["'"+v if v.lstrip().startswith(('=','+','-','@')) or v.startswith(('\t','\r')) else v for v in row])
    return output.getvalue()


def trim_preview(ident, apply=False):
    source, headers, rows = table(ident)
    changes = [dict(record=n, column=i+1, before=v, after=v.strip())
               for n,row in enumerate(rows,2) for i,v in enumerate(row) if v != v.strip()]
    result = dict(changes=changes, count=len(changes), source=ident)
    if apply and changes:
        stream=io.StringIO(newline=''); writer=csv.writer(stream)
        writer.writerow(headers); writer.writerows([[v.strip() for v in row] for row in rows])
        derived=save(('Trimmed · '+source['name'])[:250],stream.getvalue())
        with core.connection() as con:
            con.execute('INSERT INTO dataset_audit(dataset_id,action,value) VALUES (?,?,?)',
                        (derived['id'],'trimmed cells',json.dumps(dict(source=ident,changed_cells=len(changes)))))
        result['derived']=derived['id']
    return result


def compare(left, right, left_key, right_key, left_value, right_value):
    _, lh, lr = table(left); _, rh, rr = table(right)
    for k,v,headers in [(left_key,left_value,lh),(right_key,right_value,rh)]:
        if any(type(x) is not int or not 0 <= x < len(headers) for x in (k,v)) or k==v:
            raise ValueError('Choose distinct key and value columns on each side.')
    def index(rows,key,value):
        result={}
        for n,row in enumerate(rows,2):
            result.setdefault(row[key],[]).append(dict(record=n,value=row[value]))
        return result
    li=index(lr,left_key,left_value); ri=index(rr,right_key,right_value)
    results=[]
    for key in sorted(li.keys()|ri.keys()):
        a,b=li.get(key,[]),ri.get(key,[])
        if not key.strip():status='invalid'
        elif len(a)>1 or len(b)>1:status='ambiguous'
        elif not a:status='right_only'
        elif not b:status='left_only'
        elif not a[0]['value'].strip() or not b[0]['value'].strip():status='invalid'
        elif a[0]['value']==b[0]['value']:status='matched'
        else:status='conflict'
        results.append(dict(key=key,status=status,left=a,right=b))
    return dict(left=left,right=right,results=results,
                counts={s:sum(r['status']==s for r in results) for s in ('matched','conflict','left_only','right_only','ambiguous','invalid')},
                note='Exact, case-sensitive text comparison. No numeric conversion, trimming, deduplication or unit conversion. Preview and apply cleaning separately. One-sided records mean absent from the other uploaded dataset only. Counts describe key groups, not individual rows.')


def demo():
    left=save('Demo · warehouse.csv','sku,quantity\nW-100,12\nW-200,8\nW-300,4\nW-300,4\n W-400 ,7\nW-500,3\n')
    right=save('Demo · stock-system.csv','product_code,on_hand\nW-100,12\nW-200,6\nW-300,4\nW-400,7\nW-600,2\n')
    return dict(left=left['id'],right=right['id'])


def detail(ident):
    row = get(ident)
    report = inspect_csv(row['content'])
    with core.connection() as con:
        history = [dict(r) for r in con.execute('SELECT action,value,created FROM dataset_audit WHERE dataset_id=? ORDER BY id', (ident,))]
    return dict(id=ident, name=row['name'], report=report, mapping=json.loads(row['mapping']) if row['mapping'] else None, history=history)


def save(name, content):
    inspect_csv(content)
    if not isinstance(name, str) or not name.strip() or len(name)>250:
        raise ValueError('Provide a filename up to 250 characters.')
    ident = hashlib.sha256(content.encode('utf-8')).hexdigest()
    with core.connection() as con:
        inserted = con.execute('INSERT OR IGNORE INTO datasets(id,name,content) VALUES (?,?,?)', (ident,name,content)).rowcount
        if inserted:
            con.execute('INSERT INTO dataset_audit(dataset_id,action,value) VALUES (?,?,?)', (ident,'import',json.dumps({'name':name,'sha256':ident})))
    return detail(ident)


def map_columns(ident, mapping):
    row = get(ident)
    report = inspect_csv(row['content'])
    if report['counts'].get('header') or report['counts'].get('row_width'):
        raise ValueError('Correct ambiguous headers or inconsistent row lengths in the source and import again.')
    if not isinstance(mapping, dict) or set(mapping)!=set(ROLES):
        raise ValueError('Map seller, document number, currency, and document total.')
    if any(type(v) is not int or not 0 <= v < len(report['columns']) for v in mapping.values()) or len(set(mapping.values()))!=4:
        raise ValueError('Choose four distinct source columns.')
    with core.connection() as con:
        con.execute('UPDATE datasets SET mapping=? WHERE id=?', (json.dumps(mapping),ident))
        con.execute('INSERT INTO dataset_audit(dataset_id,action,value) VALUES (?,?,?)', (ident,'confirmed mapping',json.dumps(mapping)))
    return detail(ident)


def reconcile(ident):
    source = get(ident)
    if not source['mapping']:
        raise ValueError('Confirm a column mapping first.')
    mapping = json.loads(source['mapping'])
    rows = list(csv.reader(io.StringIO(source['content'].lstrip('\ufeff'), newline='')))[1:]
    records = [{k:row[v].strip() for k,v in mapping.items()} for row in rows]
    def key(f):
        return (f['merchant'].casefold(), f['receipt_number'].casefold())
    frequencies = {}
    for f in records:
        frequencies[key(f)] = frequencies.get(key(f),0)+1
    approved = {}
    for d in core.documents():
        if d['status']=='approved' and not core.validate(d['fields'],d['id']):
            approved.setdefault(key(d['fields']),[]).append(d)
    results = []
    for number, f in enumerate(records,2):
        candidates = approved.get(key(f),[])
        currency = f['currency'].upper()
        if any(not v for v in f.values()) or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', f['total']) or not re.fullmatch(r'[A-Z]{3}', currency):
            status = 'invalid'
        elif frequencies[key(f)]>1 or len(candidates)>1:
            status = 'ambiguous'
        elif not candidates:
            status = 'unmatched'
        else:
            d = candidates[0]['fields']
            # Decimal syntax was validated above; no rounding or locale guessing.
            from decimal import Decimal
            status = 'matched' if currency==d['currency'] and Decimal(f['total'])==Decimal(d['total']) else 'conflict'
        results.append(dict(record=number, fields=f, status=status, sources=[dict(id=d['id'],name=d['name'],currency=d['fields']['currency'],total=d['fields']['total']) for d in candidates]))
    return dict(results=results, counts={s:sum(r['status']==s for r in results) for s in ('matched','conflict','unmatched','ambiguous','invalid')},
                note='Compared with currently approved, valid documents using seller and document number (trimmed, case-insensitive), then exact currency and total. Matched does not prove payment. Unmatched means absent from this approved collection, not missing in the real world. Repeated seller/document keys need review. Use document totals, not partial payments. Amounts require a dot decimal without thousands separators.')
