"""Immutable inventory runs and append-only local review history.

Contract v1: daily whole-unit stock; no returns, transfers or adjustments.
Source text remains in datasets. Statistical results are review candidates.
"""
import csv
import datetime as dt
import io
import json
import math
import re
import statistics
import uuid
from collections import Counter, defaultdict
import core
import datasets
from table_quality import inspect_csv

VERSION = 'inventory-import-v2'
ROLES = ('date', 'product', 'warehouse', 'opening', 'received', 'sold', 'closing')
DECISIONS = ('confirmed issue', 'expected event', 'needs investigation')


def init():
    with core.connection() as con:
        con.execute('CREATE TABLE IF NOT EXISTS investigations(id TEXT PRIMARY KEY, dataset_id TEXT, report TEXT NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS investigation_reviews(id INTEGER PRIMARY KEY, run_id TEXT, record INTEGER, decision TEXT, note TEXT, created TEXT)')
        for table in ('investigations', 'investigation_reviews'):
            for action in ('UPDATE', 'DELETE'):
                con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'History is append-only'); END")


def iso(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Dates must use Gregorian YYYY-MM-DD.')
    return dt.date.fromisoformat(value)


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run(dataset_id, mapping, train_end, calibration_end, test_end, contract):
    if contract != 'daily-whole-units-v1':
        raise ValueError('Confirm daily whole-unit stock and opening + received − sold = closing. Other movements need a different contract.')
    a, b, c = map(iso, (train_end, calibration_end, test_end))
    if a < dt.date(1,1,30):
        raise ValueError('Training cutoff must allow a 30-day history.')
    if not a < b < c:
        raise ValueError('Training end must precede calibration end, which must precede investigation end.')
    source = datasets.get(dataset_id)
    quality = inspect_csv(source['content'])
    if quality['counts'].get('header'):
        raise ValueError('Fix ambiguous or empty column names before mapping.')
    if not isinstance(mapping, dict) or set(mapping) != set(ROLES):
        raise ValueError('Map all seven inventory fields explicitly.')
    width = len(quality['columns'])
    if any(type(v) is not int or not 0 <= v < width for v in mapping.values()) or len(set(mapping.values())) != len(ROLES):
        raise ValueError('Choose seven distinct columns.')
    raw = list(csv.reader(io.StringIO(source['content'].lstrip('\ufeff'), newline=''), strict=True))
    candidates, quarantine = [], []
    for number, cells in enumerate(raw[1:], 2):
        reasons = []
        row = dict(record=number)
        if len(cells) != width:
            reasons.append('Column count does not match header.')
        else:
            for role, column in mapping.items():
                value = cells[column]
                if not value or value != value.strip():
                    reasons.append(role + ': blank or surrounding whitespace; clean explicitly.')
                    continue
                try:
                    if role == 'date':
                        iso(value)
                        row[role] = value
                    elif role in ('product', 'warehouse'):
                        row[role] = value
                    else:
                        # Bound integers so calculations and JSON remain interoperable.
                        if not re.fullmatch(r'[0-9]{1,9}', value):
                            raise ValueError()
                        row[role] = int(value)
                except ValueError:
                    reasons.append(role + ': expected YYYY-MM-DD date or nonnegative whole units (maximum 999999999).')
        if reasons:
            quarantine.append(dict(record=number, reasons=reasons, raw=cells))
        else:
            candidates.append(row)
    # Quarantine every occurrence of duplicate identities, including rows whose
    # quantities are invalid. Never let a bad duplicate make its partner trusted.
    keys = Counter()
    for cells in raw[1:]:
        if len(cells) > max(mapping[k] for k in ('date','product','warehouse')):
            keys[tuple(cells[mapping[k]] for k in ('date', 'product', 'warehouse'))] += 1
    valid = []
    for row in candidates:
        key = tuple(row[k] for k in ('date', 'product', 'warehouse'))
        if keys[key] > 1:
            quarantine.append(dict(record=row['record'], reasons=['Duplicate date/product/warehouse key; all occurrences excluded.'], raw=raw[row['record']-1]))
        else:
            valid.append(row)
    valid.sort(key=lambda r: (r['date'], r['product'], r['warehouse']))
    groups = defaultdict(list)
    for row in valid:
        groups[(row['product'], row['warehouse'])].append(row)
    profiles, series, results = [], [], []
    for (product, warehouse), rows in sorted(groups.items()):
        training = [r for r in rows if r['date'] <= train_end]
        calibration = [r for r in rows if train_end < r['date'] <= calibration_end]
        testing = [r for r in rows if calibration_end < r['date'] <= test_end]
        # Require a complete daily trailing window for each phase. A missing or
        # quarantined historical day disables statistical scoring for this series.
        reason = None
        train_dates = {r['date'] for r in training}
        cal_dates = {r['date'] for r in calibration}
        if any(str(a-dt.timedelta(days=n)) not in train_dates for n in range(30)):
            reason = 'Need 30 complete daily training records ending at the training cutoff.'
        elif (b-a).days < 14 or len(cal_dates) != (b-a).days:
            reason = 'Need at least 14 complete daily calibration records after training.'
        median = scale = threshold = None
        if reason is None:
            values = [r['sold'] for r in training if r['date'] >= str(a-dt.timedelta(days=29))]
            median = statistics.median(values)
            scale = max(1.0, 1.4826*statistics.median(abs(v-median) for v in values))
            scores = sorted((r['sold']-median)/scale for r in calibration)
            threshold = max(3.5, scores[math.ceil(.99*len(scores))-1])
            profiles.append(dict(product=product, warehouse=warehouse, median=median, scale=scale, threshold=threshold))
        series.append(dict(product=product, warehouse=warehouse, statistical_status=reason or 'Ready',
                           missing_test_days=(c-b).days-len(testing), training=len(training), calibration=len(calibration), investigation=len(testing)))
        previous = {r['date']: r for r in rows}
        for row in testing:
            expected = row['opening']+row['received']-row['sold']
            delta = row['closing']-expected
            prior = previous.get(str(iso(row['date'])-dt.timedelta(days=1)))
            continuity_delta = row['opening']-prior['closing'] if prior else None
            score = (row['sold']-median)/scale if reason is None else None
            flags = []
            if delta: flags.append('stock arithmetic')
            if continuity_delta: flags.append('opening differs from prior closing')
            if score is not None and score > threshold: flags.append('unusual sales')
            results.append(dict(**row, flags=flags, expected_closing=expected, delta=delta,
                                continuity_delta=continuity_delta, prior_record=prior['record'] if prior else None,
                                score=score, statistical_skip_reason=reason,
                                raw=raw[row['record']-1]))
    report = dict(id=uuid.uuid4().hex, kind='inventory', version=VERSION, created=timestamp(), dataset_id=dataset_id,
                  source_sha256=dataset_id, source_name=source['name'], headers=raw[0], mapping=mapping,
                  contract=contract, cutoffs=dict(train_end=train_end, calibration_end=calibration_end, test_end=test_end),
                  counts=dict(source=len(raw)-1, valid=len(valid), quarantined=len(quarantine),
                              outside_window=sum(r['date']>test_end for r in valid), investigated=len(results),
                              flagged=sum(bool(r['flags']) for r in results), statistical_skipped=sum(r['score'] is None for r in results)),
                  timeline=[dict(record=r['record'], date=r['date'], product=r['product'], warehouse=r['warehouse'], sold=r['sold'],
                                 period='earlier history' if r['date'] < str(a-dt.timedelta(days=29)) else
                                        'training' if r['date'] <= train_end else
                                        'calibration' if r['date'] <= calibration_end else 'investigation')
                            for r in valid if r['date'] <= test_end],
                  profiles=profiles, series=series, quarantine=sorted(quarantine,key=lambda r:r['record']), results=results,
                  note='Local deterministic checks. Whole units, daily snapshots, exact case-sensitive identities. Only the last 30 training days fit each profile; calibration sets a one-sided 99th-percentile threshold with floor 3.5. No future rows or review decisions train the detector. Missing days are not zero sales. Arithmetic assumes no transfers, returns or adjustments. Promotions may trigger alerts. Flags require review; no accuracy or financial-loss claim. Local review notes are not authenticated identities or independent ground truth.')
    return persist(report)


def persist(report):
    with core.connection() as con:
        con.execute('INSERT INTO investigations VALUES (?,?,?)', (report['id'], report['dataset_id'], json.dumps(report)))
    return get(report['id'])


def listing(kind='inventory'):
    with core.connection() as con:
        rows = con.execute('SELECT report FROM investigations ORDER BY rowid DESC').fetchall()
    return [{k:r[k] for k in ('id','source_name','created','counts')} for r in (json.loads(x['report']) for x in rows) if r.get('kind','inventory') == kind]


def get(ident):
    with core.connection() as con:
        row = con.execute('SELECT report FROM investigations WHERE id=?', (ident,)).fetchone()
        if row is None: raise ValueError('Investigation not found.')
        report = json.loads(row['report'])
        report['reviews'] = [dict(r) for r in con.execute('SELECT id,record,decision,note,created FROM investigation_reviews WHERE run_id=? ORDER BY id', (ident,))]
    return report


def review(run_id, record, decision, note):
    report = get(run_id)
    if type(record) is not int or not any(r['record']==record for r in report['results']):
        raise ValueError('Choose an investigated source record.')
    if decision not in DECISIONS or not isinstance(note,str) or not 1 <= len(note.strip()) <= 2000:
        raise ValueError('Choose a review decision and provide a note up to 2000 characters.')
    with core.connection() as con:
        con.execute('INSERT INTO investigation_reviews(run_id,record,decision,note,created) VALUES (?,?,?,?,?)',
                    (run_id,record,decision,note.strip(),timestamp()))
    return get(run_id)


def demo():
    from inventory_lab import generate
    rows, _ = generate()
    output = io.StringIO(newline='')
    writer = csv.writer(output); writer.writerow(ROLES)
    # Two series keep the starter dataset easy to inspect. No injected labels.
    for row in rows:
        if row['product'] == 'SKU-000': writer.writerow([row[k] for k in ROLES])
    writer.writerow(['2026-02-30','BAD-DATE','North',100,0,2,98])
    writer.writerow(['2026-04-20','MISSING','North',100,0,'',98])
    data = datasets.save('Synthetic daily inventory.csv', output.getvalue())
    return dict(dataset_id=data['id'], mapping={k:i for i,k in enumerate(ROLES)},
                train_end='2026-03-01', calibration_end='2026-03-31', test_end='2026-04-30')
