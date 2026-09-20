"""Committed order delivery checks with explicit calendar-day semantics.

No model calls, financial inference, or silent status reconstruction.
"""
import csv
import io
import statistics
import uuid
from collections import Counter, defaultdict
import datasets
import investigations as inv
from table_quality import inspect_csv

VERSION = 'order-delivery-v1'
CONTRACT = 'committed-orders-v1'
ROLES = ('order_id', 'team', 'ordered_on', 'due_on', 'delivered_on')


def summarize(rows):
    delivered = [r for r in rows if r['delivered_on']]
    on_time = sum(r['status'] == 'delivered on time' for r in delivered)
    return dict(records=len(rows), delivered=len(delivered), delivered_on_time=on_time,
                delivered_late=len(delivered)-on_time,
                open_overdue=sum(r['status']=='open overdue' for r in rows),
                due_today=sum(r['status']=='due today' for r in rows),
                upcoming=sum(r['status']=='upcoming' for r in rows),
                on_time_rate=on_time/len(delivered) if delivered else None,
                median_lead_days=statistics.median(r['lead_days'] for r in delivered) if delivered else None)


def run(dataset_id, mapping, as_of, contract):
    if contract != CONTRACT:
        raise ValueError('Confirm one row per committed, uncancelled order; blank delivery date means not delivered as of the snapshot.')
    inv.iso(as_of)
    source = datasets.get(dataset_id)
    quality = inspect_csv(source['content'])
    if quality['counts'].get('header'):
        raise ValueError('Fix ambiguous or empty column names before mapping.')
    if not isinstance(mapping, dict) or set(mapping)!=set(ROLES):
        raise ValueError('Map all five order fields explicitly.')
    width = len(quality['columns'])
    if any(type(v) is not int or not 0 <= v < width for v in mapping.values()) or len(set(mapping.values()))!=len(ROLES):
        raise ValueError('Choose five distinct columns.')
    raw = list(csv.reader(io.StringIO(source['content'].lstrip('\ufeff'), newline=''), strict=True))
    # Count IDs even on malformed-width rows if the identity cell is present.
    keys = Counter(cells[mapping['order_id']] for cells in raw[1:] if len(cells)>mapping['order_id'])
    results, quarantine = [], []
    for number, cells in enumerate(raw[1:], 2):
        reasons = []
        row = dict(record=number, raw=cells)
        if len(cells)!=width:
            reasons.append('Column count does not match header.')
        else:
            for role, col in mapping.items():
                value = cells[col]
                if value != value.strip() or (not value and role!='delivered_on'):
                    reasons.append(role+': blank or surrounding whitespace; clean explicitly.')
                    continue
                if role.endswith('_on') and value:
                    try: inv.iso(value)
                    except ValueError:
                        reasons.append(role+': expected Gregorian YYYY-MM-DD.')
                        continue
                row[role] = value
            if keys[cells[mapping['order_id']]]>1:
                reasons.append('Repeated order ID; every occurrence is quarantined.')
            if not reasons:
                if row['due_on'] < row['ordered_on']:
                    reasons.append('Due date precedes order date.')
                if row['delivered_on'] and row['delivered_on'] < row['ordered_on']:
                    reasons.append('Delivery date precedes order date.')
                if row['ordered_on']>as_of or row['delivered_on']>as_of:
                    reasons.append('Order or delivery occurs after snapshot date; provide a matching source snapshot.')
        if reasons:
            quarantine.append(dict(record=number, reasons=reasons, raw=cells))
            continue
        end = row['delivered_on'] or as_of
        late_days = max(0, (inv.iso(end)-inv.iso(row['due_on'])).days)
        if row['delivered_on']:
            status = 'delivered late' if late_days else 'delivered on time'
        else:
            status = 'open overdue' if row['due_on']<as_of else 'due today' if row['due_on']==as_of else 'upcoming'
        row.update(status=status, late_days=late_days,
                   lead_days=(inv.iso(row['delivered_on'])-inv.iso(row['ordered_on'])).days if row['delivered_on'] else None,
                   age_days=(inv.iso(end)-inv.iso(row['ordered_on'])).days,
                   flags=[status] if late_days else [])
        results.append(row)
    results.sort(key=lambda r: (-r['late_days'], r['due_on'], r['order_id']))
    teams = defaultdict(list)
    for row in results: teams[row['team']].append(row)
    report = dict(id=uuid.uuid4().hex, kind='orders', version=VERSION, created=inv.timestamp(),
                  dataset_id=dataset_id, source_sha256=dataset_id, source_name=source['name'],
                  headers=raw[0], mapping=mapping, contract=contract, as_of=as_of,
                  counts=dict(source=len(raw)-1, valid=len(results), quarantined=len(quarantine), flagged=sum(bool(r['flags']) for r in results)),
                  summary=summarize(results), teams=[dict(team=team, **summarize(rows)) for team, rows in sorted(teams.items())],
                  results=results, quarantine=quarantine,
                  note='One committed, uncancelled order per exact case-sensitive ID. Blank delivery means undelivered at the end of the selected snapshot date. Whole calendar days, not business hours. Due-today orders are not overdue. Future order/delivery dates are quarantined, not reconstructed. Source completeness and unchanged commitments cannot be proved from this file. Summary denominators include valid records only; on-time rate uses delivered records only, so review excluded rows and open overdue counts together. A late commitment is not proof of fault. Local review notes are not authenticated identities or independent ground truth.')
    return inv.persist(report)


def demo():
    text = '''order_id,team,ordered_on,due_on,delivered_on
ORD-100,Distribution,2026-09-01,2026-09-10,2026-09-09
ORD-101,Distribution,2026-09-03,2026-09-12,2026-09-15
ORD-102,Installation,2026-09-04,2026-09-14,
ORD-103,Installation,2026-09-10,2026-09-20,
ORD-104,Distribution,2026-09-12,2026-09-25,
ORD-105,Installation,2026-09-02,2026-09-11,2026-09-11
ORD-106,Distribution,2026-09-02,2026-09-08,
ORD-107,Installation,2026-09-01,2026-09-03,2026-09-05
DUP-1,Distribution,2026-09-01,2026-09-10,
DUP-1,Distribution,2026-09-01,2026-09-10,
BAD-1,Installation,2026-09-31,2026-10-02,
'''
    saved = datasets.save('Synthetic order commitments.csv', text)
    return dict(dataset_id=saved['id'], mapping={k:i for i,k in enumerate(ROLES)}, as_of='2026-09-20')
