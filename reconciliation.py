"""Exact full-snapshot reconciliation with explicit reference authority and candidate repairs."""
import csv
import datetime as dt
import io
import json
from collections import defaultdict

import core
import datasets
from incidents import digest

ENGINE = 'snapshot-reconciliation-v1'


def init():
    with core.connection() as con:
        con.execute('CREATE TABLE IF NOT EXISTS reconciliation_runs(id TEXT PRIMARY KEY,report TEXT NOT NULL)')
        for action in ('UPDATE', 'DELETE'):
            con.execute('CREATE TRIGGER IF NOT EXISTS reconciliation_no_' + action.lower() + ' BEFORE ' + action +
                        " ON reconciliation_runs BEGIN SELECT RAISE(ABORT,'Immutable reconciliation'); END")


def get(ident):
    with core.connection() as con:
        row = con.execute('SELECT report FROM reconciliation_runs WHERE id=?', (ident,)).fetchone()
    if row is None: raise ValueError('Reconciliation not found.')
    return json.loads(row['report'])


def listing():
    with core.connection() as con:
        rows = con.execute('SELECT report FROM reconciliation_runs ORDER BY rowid DESC LIMIT 50').fetchall()
    reports = [json.loads(r['report']) for r in rows]
    return [dict(id=r['id'], stage=r['stage'], matched=r['matched'], scope=r['contract']['scope']) for r in reports]


def date(value):
    try:
        parsed = dt.date.fromisoformat(value)
        if parsed.isoformat() != value: raise ValueError()
        return parsed
    except (ValueError, TypeError): raise ValueError('Use valid YYYY-MM-DD snapshot and assessment dates.')


def contract(keys, authority, scope, reference_date, delivery_date, as_of, max_age_days, confirmed):
    if confirmed is not True: raise ValueError('Confirm that both files represent complete snapshots of the same population.')
    if not isinstance(keys, list) or not keys or any(not isinstance(k,str) or not k for k in keys) or len(set(keys))!=len(keys):
        raise ValueError('Choose distinct identity columns.')
    for value in (authority, scope):
        if not isinstance(value,str) or not value.strip() or len(value)>1000:
            raise ValueError('Provide a reference authority and snapshot scope, each up to 1000 characters.')
    for value in (reference_date, delivery_date, as_of): date(value)
    if type(max_age_days) is not int or not 0 <= max_age_days <= 3650:
        raise ValueError('Maximum age must be a whole number from 0 to 3650 days.')
    return dict(keys=keys, authority=authority.strip(), scope=scope.strip(), reference_date=reference_date,
                delivery_date=delivery_date, as_of=as_of, max_age_days=max_age_days, confirmed=True,
                comparison='Exact text across every column; no numeric conversion or inferred corrections.')


def assess(reference_id, delivery_id, rules, parent=None, changes=None, note=''):
    reference, headers, left = datasets.table(reference_id)
    delivery, delivered_headers, right = datasets.table(delivery_id)
    if set(headers)!=set(delivered_headers): raise ValueError('Both snapshots must have exactly the same column names; column order may differ.')
    if any(k not in headers for k in rules['keys']): raise ValueError('Every identity column must exist in both files.')
    blockers = []
    if not left: blockers.append('The reference is empty; completeness cannot be established.')
    if rules['reference_date']!=rules['delivery_date']: blockers.append('Snapshot dates differ; compare the same reporting snapshot.')
    for role in ('reference','delivery'):
        age = (date(rules['as_of'])-date(rules[role+'_date'])).days
        if age<0: blockers.append(role.title()+' snapshot is after the assessment date.')
        elif age>rules['max_age_days']: blockers.append(role.title()+' snapshot exceeds the declared maximum age.')
    def index(rows, columns, role):
        result = defaultdict(list)
        invalid = []
        for record, values in enumerate(rows,2):
            row = dict(zip(columns,values)); key = tuple(row[k] for k in rules['keys'])
            evidence = dict(record=record, values=row)
            if any(not v or v!=v.strip() for v in key): invalid.append(evidence)
            else: result[key].append(evidence)
        if invalid: blockers.append(role.title()+' has blank or padded identity values; correct them at the source.')
        return result, invalid
    expected, invalid_left = index(left,headers,'reference')
    actual, invalid_right = index(right,delivered_headers,'delivery')
    if any(len(v)!=1 for v in expected.values()): blockers.append('Reference identities are not unique; no reference winner can be selected.')
    issues=[]; matched_keys=0
    for key in sorted(expected.keys() | actual.keys()):
        a,b=expected.get(key,[]),actual.get(key,[])
        if len(a)>1: kind='ambiguous_reference'
        elif not a: kind='unexpected'
        elif not b: kind='missing'
        elif len(b)>1: kind='duplicate'
        elif a[0]['values']!=b[0]['values']: kind='changed'
        else:
            matched_keys+=1; continue
        issues.append(dict(id=digest(list(key)),key=list(key),kind=kind,reference=a,delivery=b,
                           changed_fields=[h for h in headers if len(a)==len(b)==1 and a[0]['values'][h]!=b[0]['values'][h]]))
    report=dict(engine=ENGINE,stage='candidate' if parent else 'comparison',parent=parent,
                sources=dict(reference=reference_id,delivery=delivery_id),names=dict(reference=reference['name'],delivery=delivery['name']),
                contract=rules,headers=headers,counts=dict(reference_rows=len(left),delivery_rows=len(right),matched_keys=matched_keys,
                discrepancies=len(issues)),blockers=blockers,invalid=dict(reference=invalid_left,delivery=invalid_right),issues=issues,
                matched=not blockers and not issues,changes=changes or [],note=note,
                limitation='Matching proves agreement with the nominated reference, not real-world correctness. Authority, completeness, scope and dates are user declarations. Candidate snapshots are never automatically published.')
    report['id']=digest(report)
    with core.connection() as con:
        con.execute('INSERT OR IGNORE INTO reconciliation_runs(id,report) VALUES (?,?)',(report['id'],json.dumps(report,sort_keys=True)))
    return get(report['id'])


def compare(reference_id, delivery_id, **kwargs):
    return assess(reference_id,delivery_id,contract(**kwargs))


def repair(run_id, issue_ids, note):
    original=get(run_id)
    if original['stage']!='comparison': raise ValueError('Build each candidate from the original comparison.')
    if original['blockers']: raise ValueError('Resolve reference, identity or snapshot blockers before proposing a repair.')
    if not isinstance(note,str) or not note.strip() or len(note)>1000: raise ValueError('Explain the candidate repair in a note up to 1000 characters.')
    lookup={i['id']:i for i in original['issues']}
    if not isinstance(issue_ids,list) or not issue_ids or any(not isinstance(i,str) or i not in lookup for i in issue_ids) or len(set(issue_ids))!=len(issue_ids):
        raise ValueError('Select distinct discrepancies from this comparison.')
    selected=[lookup[i] for i in sorted(issue_ids)]
    keys=original['contract']['keys']; selected_keys={tuple(i['key']) for i in selected}
    _,headers,rows=datasets.table(original['sources']['delivery'])
    rows=[dict(zip(headers,row)) for row in rows]
    candidate=[row for row in rows if tuple(row[k] for k in keys) not in selected_keys]
    changes=[]
    for issue in selected:
        replacement=issue['reference'][0]['values'] if issue['reference'] else None
        if replacement is not None: candidate.append(replacement)
        changes.append(dict(key=issue['key'],action='replace_from_reference' if replacement is not None else 'remove_unexpected',
                            before=issue['delivery'],after=replacement,reference=issue['reference']))
    output=io.StringIO(newline=''); writer=csv.DictWriter(output,fieldnames=headers,lineterminator='\n')
    writer.writeheader();writer.writerows(candidate)
    derived=datasets.save('Candidate · '+original['names']['delivery'][:220],output.getvalue())['id']
    return assess(original['sources']['reference'],derived,original['contract'],parent=run_id,changes=changes,note=note.strip())


def bundle(ident):
    report=get(ident); sources=dict(report['sources']);parent=get(report['parent']) if report['parent'] else None
    if parent: sources['original_delivery']=parent['sources']['delivery']
    return dict(report=report,parent_report=parent,files={role:dict(sha256=ident,csv=datasets.get(ident)['content']) for role,ident in sources.items()})
