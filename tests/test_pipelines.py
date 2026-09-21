import concurrent.futures
import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import core
import datasets
import pipelines as p


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.state=patch.object(core,'STATE',Path(self.tmp.name));self.state.start()
        core.init();datasets.init();p.init()
        self.spec=dict(fields=[dict(name='id',type='text',required=True),dict(name='amount',type='decimal',required=True)],key=['id'],max_quarantine_percent=0,min_accepted_rows=1)
        self.c=p.save_contract('sales',self.spec)

    def tearDown(self):
        self.state.stop();self.tmp.cleanup()

    def source(self,text):return datasets.save('test.csv',text)['id']

    def test_failed_batch_never_replaces_published_snapshot(self):
        good=self.source('id,amount\nA,12.00\n');bad=self.source('id,amount\nA,unknown\n')
        first=p.run(self.c['id'],good);publication=p.publish(first['id'],None)
        before=datasets.get(good)
        failed=p.run(self.c['id'],bad)
        self.assertFalse(failed['gate']['passed'])
        with self.assertRaises(ValueError):p.publish(failed['id'],first['id'])
        with self.assertRaises(ValueError):p.export(failed['id'],'accepted')
        self.assertEqual(p.detail(self.c['pipeline_id'])['current'],{k:v for k,v in publication.items() if k!='reused'})
        self.assertEqual(datasets.get(good),before)

    def test_replay_is_idempotent_and_does_not_republish_old_run(self):
        a=self.source('id,amount\nA,1\n');b=self.source('id,amount\nB,2\n')
        r1=p.run(self.c['id'],a);p.publish(r1['id'],None)
        r2=p.run(self.c['id'],b);p.publish(r2['id'],r1['id'])
        self.assertEqual(p.run(self.c['id'],a),r1)
        d=p.detail(self.c['pipeline_id']);self.assertEqual(len(d['runs']),2)
        self.assertEqual(d['current']['run_id'],r2['id']);self.assertEqual(len(d['publications']),2)
        self.assertTrue(p.publish(r2['id'],r1['id'])['reused'])
        self.assertEqual(len(p.detail(self.c['pipeline_id'])['publications']),2)

    def test_concurrent_runs_share_identity_and_only_one_publish_wins(self):
        a=self.source('id,amount\nA,1\n');b=self.source('id,amount\nB,2\n')
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:p.run(self.c['id'],a),range(4)))
        self.assertTrue(all(r==results[0] for r in results))
        self.assertEqual(len(p.detail(self.c['pipeline_id'])['runs']),1)
        other=p.run(self.c['id'],b)
        def attempt(ident):
            try:p.publish(ident,None);return True
            except ValueError:return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(attempt,[results[0]['id'],other['id']]))
        self.assertEqual(sum(outcomes),1)
        self.assertEqual(len(p.detail(self.c['pipeline_id'])['publications']),1)

    def test_contract_versions_are_immutable_and_stale_cannot_publish(self):
        r=p.run(self.c['id'],self.source('id,amount\nA,1\n'))
        spec=dict(self.spec,min_accepted_rows=2);new=p.save_contract('sales',spec)
        self.assertEqual(new['version'],2);self.assertEqual(p.save_contract('sales',spec)['id'],new['id'])
        self.assertEqual(p.contract(self.c['id'])['spec'],self.spec)
        with self.assertRaises(ValueError):p.publish(r['id'],None)
        with self.assertRaises(sqlite3.IntegrityError):
            with core.connection() as con:con.execute('DELETE FROM pipeline_contracts')

    def test_schema_drift_blocks_even_when_row_threshold_allows_errors(self):
        c=p.save_contract('lenient',dict(self.spec,max_quarantine_percent=100))
        r=p.run(c['id'],self.source('id,amount,unapproved\nA,1,secret\n'))
        self.assertFalse(r['gate']['passed']);self.assertIn('Unexpected columns',r['schema_errors'][0])
        self.assertEqual(r['counts'],dict(source=1,accepted=0,quarantined=1))

    def test_reordered_headers_are_mapped_by_exact_names(self):
        r=p.run(self.c['id'],self.source('amount,id\n1.2300,A\n'))
        self.assertTrue(r['gate']['passed'])
        self.assertEqual(r['accepted'][0]['values'],dict(id='A',amount='1.2300'))
        self.assertEqual(list(csv.reader(io.StringIO(p.accepted_csv(r)))),[['id','amount'],['A','1.2300']])

    def test_typed_duplicate_keys_exclude_invalid_partners(self):
        spec=dict(self.spec,key=['amount'])
        c=p.save_contract('amount-keys',spec)
        r=p.run(c['id'],self.source('id,amount\nA,1.00\n,1.0\nB,2\nC,2,extra\n'))
        self.assertEqual(r['counts']['quarantined'],4)
        self.assertEqual(r['error_counts']['duplicate_key'],4)

    def test_threshold_boundary_exact_and_no_empty_batch_publication(self):
        c=p.save_contract('tolerant',dict(self.spec,max_quarantine_percent=25))
        r=p.run(c['id'],self.source('id,amount\nA,1\nB,2\nC,3\nD,bad\n'))
        self.assertTrue(r['gate']['passed']);self.assertEqual(r['counts']['quarantined'],1)
        pub=p.publish(r['id'],None);self.assertNotIn('bad',datasets.get(pub['dataset_id'])['content'])
        smaller=p.run(c['id'],self.source('id,amount\nA,1\nB,2\nC,bad\n'))
        self.assertFalse(smaller['gate']['passed'])
        empty=p.run(c['id'],self.source('id,amount\n'));self.assertFalse(empty['gate']['passed'])

    def test_strict_types_nulls_and_precision(self):
        fields=[dict(name=n,type=t,required=required) for n,t,required in [('id','integer',True),('date','date',True),('amount','decimal',True),('active','boolean',True),('memo','text',False)]]
        c=p.save_contract('typed',dict(self.spec,fields=fields))
        r=p.run(c['id'],self.source('id,date,amount,active,memo\n1,2024-02-29,123456789012345678.123456789,false,\n2,2026-02-30,NaN,1, spaced \n003,2026-09-20,1e2,true,x\n'))
        self.assertEqual(r['counts']['accepted'],1)
        self.assertEqual(r['accepted'][0]['values'],dict(id=1,date='2024-02-29',amount='123456789012345678.123456789',active=False,memo=None))
        self.assertEqual(r['counts']['quarantined'],2)

    def test_machine_output_preserves_cells_spreadsheet_export_protects(self):
        r=p.run(self.c['id'],self.source('id,amount\n=1+1,-3.20\n'))
        self.assertEqual(r['accepted'][0]['raw'],['=1+1','-3.20'])
        csv_rows=list(csv.reader(io.StringIO(p.export(r['id'],'accepted'))))
        self.assertEqual(csv_rows[1],["'=1+1","'-3.20"])
        pub=p.publish(r['id'],None)
        self.assertIn('=1+1,-3.20',datasets.get(pub['dataset_id'])['content'])
        history=datasets.detail(pub['dataset_id'])['history']
        self.assertEqual(json.loads(history[-1]['value'])['run_id'],r['id'])

    def test_cli_returns_gate_status_and_reuses_run(self):
        source=Path(self.tmp.name)/'batch.csv';source.write_text('id,amount\nA,unknown\n')
        env=dict(os.environ,RECEIPT_DESK_STATE=self.tmp.name)
        command=[sys.executable,str(core.ROOT/'pipelines.py'),'run','--contract',self.c['id'],'--csv',str(source)]
        first=subprocess.run(command,env=env,capture_output=True,text=True,timeout=20)
        second=subprocess.run(command,env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(first.returncode,2,first.stderr);self.assertEqual(json.loads(first.stdout),json.loads(second.stdout))
        self.assertIsNone(p.detail(self.c['pipeline_id'])['current'])

    def test_bad_specs_and_ambiguous_header_rejected(self):
        for changes in ({'key':[]},{'max_quarantine_percent':True},{'min_accepted_rows':0},{'key':['missing']}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):p.save_contract('bad',dict(self.spec,**changes))
        r=p.run(self.c['id'],self.source('id,id\nA,B\n'))
        self.assertFalse(r['gate']['passed']);self.assertTrue(r['schema_errors'])
