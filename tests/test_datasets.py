import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import core
import datasets


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(core,'STATE',Path(self.tmp.name))
        self.patch.start()
        core.init()
        datasets.init()
        self.mapping = dict(merchant=0,receipt_number=1,currency=2,total=3)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def save(self, rows):
        d = datasets.save('synthetic.csv','supplier,reference,currency,amount\n'+rows)
        datasets.map_columns(d['id'],self.mapping)
        return d['id']

    def document(self, approved=True):
        path=Path(self.tmp.name)/'sample.jpg'
        path.write_bytes(b'synthetic test only')
        ident=core.add(path)[0]
        core.review(ident,dict(merchant='Example',receipt_number='A1',currency='USD',total='10',date='2026-09-01',calendar='gregorian',document_type='invoice'),approve=approved)
        return ident

    def test_persistence_identity_and_audit(self):
        ident=self.save('Example,A1,USD,10')
        duplicate=datasets.save('renamed.csv','supplier,reference,currency,amount\nExample,A1,USD,10')
        self.assertEqual(ident,duplicate['id'])
        self.assertEqual(len(datasets.listing()),1)
        self.assertEqual(len(duplicate['history']),2)
        self.assertEqual(datasets.get(ident)['content'].splitlines()[1],'Example,A1,USD,10')

    def test_matching_and_changed_approval(self):
        doc=self.document()
        ident=self.save(' example ,a1,usd,10.00')
        r=datasets.reconcile(ident)['results'][0]
        self.assertEqual(r['status'],'matched')
        self.assertEqual(r['sources'][0]['id'],doc)
        core.review(doc,core.public(core.get(doc))['fields'],exclude=True)
        self.assertEqual(datasets.reconcile(ident)['results'][0]['status'],'unmatched')

    def test_conflicts_invalid_ambiguous_and_unreviewed(self):
        self.document()
        for row,expected in [('Example,A1,EUR,10','conflict'),('Example,A1,USD,11','conflict'),('Other,X,USD,1','unmatched'),('Example,A1,USD,NaN','invalid'),('Example,A1,USD,1.234','invalid'),('Example,A1,USD,10\nExample,A1,USD,10','ambiguous')]:
            self.assertEqual(datasets.reconcile(self.save(row))['results'][0]['status'],expected)

    def test_mapping_blocks_guessing_and_malformed_tables(self):
        d=datasets.save('bad.csv','a,b\n1')
        with self.assertRaises(ValueError):datasets.map_columns(d['id'],self.mapping)
        d=datasets.save('good.csv','a,b,c,d\n1,2,3,4')
        with self.assertRaises(ValueError):datasets.reconcile(d['id'])
        with self.assertRaises(ValueError):datasets.map_columns(d['id'],dict(merchant=0,receipt_number=0,currency=2,total=3))

    def test_second_domain_table_preserved_without_finance_assumptions(self):
        d=datasets.save('service-work.csv','project,hours,person\nSite A,2,Alice\nSite B,3,Bob')
        self.assertEqual(d['report']['records'],2)
        self.assertIsNone(d['mapping'])
        with self.assertRaises(ValueError):datasets.reconcile(d['id'])

    def test_inventory_demo_and_reviewed_cleaning(self):
        demo=datasets.demo()
        before=datasets.get(demo['left'])['content']
        result=datasets.compare(demo['left'],demo['right'],0,0,1,1)
        self.assertEqual(result['counts'],dict(matched=1,conflict=1,left_only=2,right_only=2,ambiguous=1,invalid=0))
        self.assertEqual(datasets.trim_preview(demo['left'])['count'],1)
        cleaned=datasets.trim_preview(demo['left'],True)['derived']
        self.assertEqual(datasets.get(demo['left'])['content'],before)
        self.assertEqual(datasets.compare(cleaned,demo['right'],0,0,1,1)['counts']['matched'],2)
        self.assertEqual(datasets.detail(cleaned)['history'][-1]['action'],'trimmed cells')

    def test_csv_download_neutralizes_formulas_without_changing_source(self):
        content='id,value\nA, =1+1\nB,@cmd\n'
        ident=datasets.save('export.csv',content)['id']
        self.assertIn("' =1+1",datasets.export_csv(ident))
        self.assertEqual(datasets.get(ident)['content'],content)

    def test_exact_comparison_never_guesses_units_or_missing_values(self):
        a=datasets.save('a.csv','id,v\nA,1\nB,\n,3')['id']
        b=datasets.save('b.csv','id,v\nA,1.0\nB,\n,3')['id']
        counts=datasets.compare(a,b,0,0,1,1)['counts']
        self.assertEqual(counts['conflict'],1)
        self.assertEqual(counts['invalid'],2)
        with self.assertRaises(ValueError):datasets.compare(a,b,0,0,0,1)
