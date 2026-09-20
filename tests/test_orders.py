import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import core
import datasets
import investigations as inv
import order_checks as orders


class OrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.state=patch.object(core,'STATE',Path(self.tmp.name));self.state.start()
        core.init();datasets.init();inv.init()
        self.options=dict(mapping={k:i for i,k in enumerate(orders.ROLES)},as_of='2026-09-20',contract=orders.CONTRACT)

    def tearDown(self):
        self.state.stop();self.tmp.cleanup()

    def save(self, rows, headers=None):
        out=io.StringIO(newline='');w=csv.writer(out);w.writerow(headers or orders.ROLES);w.writerows(rows)
        return datasets.save('orders.csv',out.getvalue())['id']

    def test_demo_has_explicit_denominators_and_separate_backlog(self):
        d=orders.demo();r=orders.run(**d,contract=orders.CONTRACT)
        self.assertEqual(r['counts'],dict(source=11,valid=8,quarantined=3,flagged=4))
        self.assertEqual(r['summary'],dict(records=8,delivered=4,delivered_on_time=2,delivered_late=2,open_overdue=2,due_today=1,upcoming=1,on_time_rate=.5,median_lead_days=8.5))
        self.assertEqual(sum(t['records'] for t in r['teams']),8)
        self.assertEqual(sum(t['delivered'] for t in r['teams']),4)

    def test_due_date_boundary_and_leap_day(self):
        ident=self.save([['A','Team','2024-02-28','2024-02-29',''],['B','Team','2024-02-28','2024-02-29','2024-02-29'],['C','Team','2024-02-28','2024-03-01','']])
        r=orders.run(ident,**dict(self.options,as_of='2024-03-01'))
        byid={x['order_id']:x for x in r['results']}
        self.assertEqual(byid['A']['late_days'],1)
        self.assertEqual(byid['B']['status'],'delivered on time')
        self.assertEqual(byid['B']['lead_days'],1)
        self.assertEqual(byid['C']['status'],'due today')
        self.assertFalse(byid['C']['flags'])

    def test_invalid_chronology_future_and_ambiguous_dates_quarantined(self):
        rows=[['A','T','2026-09-10','2026-09-01',''],['B','T','2026-09-10','2026-09-12','2026-09-09'],['C','T','2026-09-21','2026-09-22',''],['D','T','2026-09-01','2026-09-10','2026-09-21'],['E','T','09/01/2026','2026-09-10',''],['F','T','2026-09-01','2026-09-10',' ']]
        r=orders.run(self.save(rows),**self.options)
        self.assertEqual(r['counts']['quarantined'],6)
        self.assertEqual(r['counts']['valid'],0)
        self.assertIsNone(r['summary']['on_time_rate'])
        self.assertIsNone(r['summary']['median_lead_days'])

    def test_duplicate_with_invalid_date_or_width_excludes_both(self):
        rows=[['A','T','2026-09-01','2026-09-10',''],['A','T','bad','2026-09-10',''],['B','T','2026-09-01','2026-09-10',''],['B','T']]
        r=orders.run(self.save(rows),**self.options)
        self.assertEqual(r['counts']['quarantined'],4)
        self.assertFalse(r['results'])

    def test_no_deliveries_means_undefined_rate_not_perfect_score(self):
        r=orders.run(self.save([['A','T','2026-09-01','2026-09-10','']]),**self.options)
        self.assertEqual(r['summary']['open_overdue'],1)
        self.assertIsNone(r['summary']['on_time_rate'])

    def test_explicit_mapping_preserves_raw_values_and_source(self):
        rows=[['extra','T','2026-09-05','','ID-1','2026-09-01']]
        headers=['comment','owner','promise','actual','id','start']
        ident=self.save(rows,headers);before=datasets.get(ident)
        mapping=dict(order_id=4,team=1,ordered_on=5,due_on=2,delivered_on=3)
        r=orders.run(ident,**dict(self.options,mapping=mapping))
        self.assertEqual(r['results'][0]['raw'],rows[0])
        self.assertEqual(r['results'][0]['late_days'],15)
        self.assertEqual(datasets.get(ident),before)
        self.assertEqual(r['source_sha256'],ident)

    def test_saved_runs_and_review_history_do_not_mix_workflows(self):
        d=orders.demo();r=orders.run(**d,contract=orders.CONTRACT)
        self.assertEqual(inv.listing(),[])
        self.assertEqual(inv.listing('orders')[0]['id'],r['id'])
        reviewed=inv.review(r['id'],r['results'][0]['record'],'expected event','Customer approved delay; update the source commitment separately.')
        self.assertEqual(reviewed['summary'],r['summary'])
        self.assertEqual(inv.get(r['id'])['reviews'],reviewed['reviews'])

    def test_bad_mapping_contract_date_and_headers_do_not_save_runs(self):
        ident=self.save([['A','T','2026-09-01','2026-09-10','']])
        for change in ({'contract':'inventory'},{'mapping':dict.fromkeys(orders.ROLES,0)},{'as_of':'2026-02-30'},{'mapping':{}}):
            with self.subTest(change=change),self.assertRaises(ValueError):orders.run(ident,**dict(self.options,**change))
        bad=self.save([['A','T','2026-09-01','2026-09-10','']],['id','team','date','date','delivered'])
        with self.assertRaises(ValueError):orders.run(bad,**self.options)
        self.assertEqual(inv.listing('orders'),[])
