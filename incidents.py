"""Reproducible synthetic incident replay; evidence-based candidate repair, no model calls."""
import argparse
import copy
import csv
import hashlib
import io
import json
import random
from collections import defaultdict

import core
import datasets
import pipelines

ENGINE = 'revenue-replay-v1'
FAULTS = ('missing_region', 'duplicate_retry', 'amount_unit')
HEADERS = ['payment_id', 'order_id', 'region', 'currency', 'amount_cents']
SPEC = dict(fields=[dict(name=n, type='integer' if n == 'amount_cents' else 'text', required=True)
                         for n in HEADERS], key=['payment_id'], max_quarantine_percent=0, min_accepted_rows=1)


def init():
    with core.connection() as con:
        con.execute('CREATE TABLE IF NOT EXISTS incident_runs(id TEXT PRIMARY KEY, report TEXT NOT NULL)')
        for action in ('UPDATE', 'DELETE'):
            con.execute('CREATE TRIGGER IF NOT EXISTS incident_no_' + action.lower() +
                        ' BEFORE ' + action + " ON incident_runs BEGIN SELECT RAISE(ABORT,'Immutable incident'); END")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def encode(rows, headers=HEADERS):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=headers, lineterminator='\n')
    writer.writeheader(); writer.writerows(rows)
    return output.getvalue()


def records(source_id):
    return list(csv.DictReader(io.StringIO(datasets.get(source_id)['content'])))


def choices(value, allowed, label):
    if not isinstance(value, list) or any(not isinstance(v, str) or v not in allowed for v in value) or len(set(value)) != len(value):
        raise ValueError('Choose distinct supported ' + label + '.')
    return sorted(value)


def reconcile(reference, delivered):
    """Compare exact records by identity, without consulting injected fault labels."""
    def index(rows):
        result = defaultdict(list)
        for number, row in enumerate(rows, 2):
            result[row['payment_id']].append(dict(record=number, values=row))
        return result
    left, right = index(reference), index(delivered)
    issues = []
    for key in sorted(left.keys() | right.keys()):
        expected, actual = left.get(key, []), right.get(key, [])
        if len(expected) != 1:
            raise ValueError('Reference must contain exactly one record per payment identity.')
        if not actual:
            kind = 'missing'
        elif len(actual) > 1:
            kind = 'duplicate'
        elif expected[0]['values'] != actual[0]['values']:
            kind = 'changed'
        else:
            continue
        issues.append(dict(payment_id=key, kind=kind, reference=expected, delivered=actual,
                           changed_fields=[name for name in HEADERS if len(actual) == 1 and
                                           actual[0]['values'][name] != expected[0]['values'][name]]))
    return issues


def metric(rows, refund_rows):
    # This fixture contract allows only USD nonnegative integer cents; never float arithmetic.
    if any(r['currency'] != 'USD' or not r['amount_cents'].isdigit() for r in rows + refund_rows):
        raise ValueError('Replay metric requires USD nonnegative integer cents.')
    gross = sum(int(r['amount_cents']) for r in rows)
    refunds = sum(int(r['amount_cents']) for r in refund_rows)
    return dict(gross_cents=gross, refunds_cents=refunds, net_cents=gross-refunds,
                payment_count=len(rows), refund_count=len(refund_rows))


def get(ident):
    with core.connection() as con:
        row = con.execute('SELECT report FROM incident_runs WHERE id=?', (ident,)).fetchone()
    if row is None:
        raise ValueError('Incident not found.')
    return json.loads(row['report'])


def listing():
    with core.connection() as con:
        rows = con.execute('SELECT id,report FROM incident_runs ORDER BY rowid DESC LIMIT 50').fetchall()
    return [dict(id=r['id'], seed=json.loads(r['report'])['seed'], stage=json.loads(r['report'])['stage']) for r in rows]


def save_report(seed, faults, source_ids, parent=None, repairs=None, changes=None):
    reference = records(source_ids['reference'])
    delivered = records(source_ids['delivered'])
    refunds = records(source_ids['refunds'])
    contract = pipelines.save_contract('Replay · USD payments', SPEC)
    validation = pipelines.run(contract['id'], source_ids['delivered'])
    issues = reconcile(reference, delivered)
    expected, observed = metric(reference, refunds), metric(delivered, refunds)
    report = dict(engine=ENGINE, seed=seed, stage='candidate' if parent else 'delivery', faults=faults,
                  parent=parent, repairs=repairs or [], changes=changes or [], sources=source_ids,
                  contract_id=contract['id'], validation_run=validation['id'], schema_gate=validation['gate'],
                  expected=expected, observed=observed, delta_cents=observed['net_cents']-expected['net_cents'],
                  issues=issues, verified=validation['gate']['passed'] and not issues,
                  definition='USD cash-based demo net revenue = completed payment cents − refund cents, same synthetic snapshot. Not an accounting standard.',
                  trust='Reference is an explicitly trusted, complete synthetic ledger for this exact snapshot. In real use its authority and completeness must be established independently.',
                  limitation='Sandbox replay only. Candidate verification is exact reconciliation to that reference, not proof of real-world truth. No publication or business-data repair occurs automatically.')
    report['id'] = digest(report)
    with core.connection() as con:
        con.execute('INSERT OR IGNORE INTO incident_runs(id,report) VALUES (?,?)', (report['id'], json.dumps(report, sort_keys=True)))
    return get(report['id'])


def simulate(seed=17, faults=None):
    if type(seed) is not int or not 0 <= seed <= 999999:
        raise ValueError('Seed must be an integer from 0 to 999999.')
    faults = choices(['missing_region'] if faults is None else faults, FAULTS, 'failures')
    rng = random.Random(seed)
    reference = [dict(payment_id='PAY-%03d' % i, order_id='ORD-%03d' % i,
                      region=region, currency='USD', amount_cents=str(rng.randint(60, 180)*100))
                 for i, region in enumerate(['East']*4 + ['West']*4 + ['North']*4, 1)]
    delivered = copy.deepcopy(reference)
    if 'missing_region' in faults:
        delivered = [r for r in delivered if r['region'] != 'East']
    if 'amount_unit' in faults:
        delivered[-1]['amount_cents'] = str(int(delivered[-1]['amount_cents'])*100)
    if 'duplicate_retry' in faults:
        delivered.append(copy.deepcopy(delivered[-2]))
    refunds = [dict(refund_id='REF-001', payment_id='PAY-002', currency='USD', amount_cents='1500'),
               dict(refund_id='REF-002', payment_id='PAY-008', currency='USD', amount_cents='2500')]
    sources = dict(reference=datasets.save('Replay · trusted ledger.csv', encode(reference))['id'],
                   delivered=datasets.save('Replay · delivered payments.csv', encode(delivered))['id'],
                   refunds=datasets.save('Replay · refunds.csv', encode(refunds, ['refund_id','payment_id','currency','amount_cents']))['id'])
    return save_report(seed, faults, sources)


def repair(incident_id, payment_ids):
    parent = get(incident_id)
    if parent['stage'] != 'delivery':
        raise ValueError('Create each candidate from the original delivery, not another candidate.')
    selected = choices(payment_ids, [i['payment_id'] for i in parent['issues']], 'payment identities')
    if not selected:
        raise ValueError('Select at least one discrepancy to reconcile.')
    reference = records(parent['sources']['reference'])
    original = records(parent['sources']['delivered'])
    expected = {r['payment_id']: r for r in reference}
    # Explicitly replace ALL selected identity occurrences, retaining every unselected row.
    candidate = [r for r in original if r['payment_id'] not in selected]
    candidate += [expected[key] for key in selected]
    changes = [dict(payment_id=key, operation='replace identity from trusted reference',
                    before=[dict(record=n, values=r) for n,r in enumerate(original,2) if r['payment_id']==key],
                    after=expected[key]) for key in selected]
    source = datasets.save('Replay · candidate payments.csv', encode(candidate))['id']
    return save_report(parent['seed'], parent['faults'], dict(parent['sources'], delivered=source),
                       parent=parent['id'], repairs=selected, changes=changes)


def bundle(ident):
    report = get(ident)
    source_ids = dict(report['sources'])
    if report['parent']:
        source_ids['original_delivery'] = get(report['parent'])['sources']['delivered']
    return dict(report=report, parent_report=get(report['parent']) if report['parent'] else None,
                validation=pipelines.get_run(report['validation_run']),
                files={role: dict(sha256=source_id, csv=datasets.get(source_id)['content']) for role,source_id in source_ids.items()})


def evaluate():
    """Small exhaustive fixture verification, not a production detection benchmark."""
    cases = []
    for seed in (3, 17, 91):
        for mask in range(8):
            faults = [f for i,f in enumerate(FAULTS) if mask & (1 << i)]
            run = simulate(seed, faults)
            expected_ids = set()
            if 'missing_region' in faults: expected_ids.update('PAY-%03d' % i for i in range(1,5))
            if 'duplicate_retry' in faults: expected_ids.add('PAY-011')
            if 'amount_unit' in faults: expected_ids.add('PAY-012')
            found = {i['payment_id'] for i in run['issues']}
            fixed = repair(run['id'], sorted(found)) if found else run
            cases.append(dict(seed=seed, faults=faults, expected=len(expected_ids), detected=len(found),
                              schema_blocked=not run['schema_gate']['passed'], reconciliation_blocked=not run['verified'],
                              passed=found==expected_ids and fixed['verified'] and fixed['delta_cents']==0))
    return dict(engine=ENGINE, scope='24 deterministic synthetic reconciliation cases; no real-world accuracy claim.',
                cases=cases, passed=sum(c['passed'] for c in cases), total=len(cases),
                comparison=dict(broken_deliveries=sum(bool(c['faults']) for c in cases),
                                schema_detected=sum(c['schema_blocked'] for c in cases if c['faults']),
                                reconciliation_detected=sum(c['reconciliation_blocked'] for c in cases if c['faults']),
                                healthy_deliveries=sum(not c['faults'] for c in cases),
                                healthy_flagged=sum(c['reconciliation_blocked'] for c in cases if not c['faults'])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--fault', action='append', choices=FAULTS)
    parser.add_argument('--evaluate', action='store_true')
    args = parser.parse_args()
    core.init(); datasets.init(); pipelines.init(); init()
    result = evaluate() if args.evaluate else bundle(simulate(args.seed, args.fault)['id'])
    print(json.dumps(result, indent=2))
    if args.evaluate and result['passed'] != result['total']: raise SystemExit(1)
