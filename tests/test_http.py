import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
import core
import datasets
import investigations
import pipelines
import incidents
import reconciliation
from server import Handler, ThreadingHTTPServer


class WorkflowHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.state=patch.object(core,'STATE',Path(self.tmp.name));self.state.start()
        core.init();datasets.init();investigations.init();pipelines.init();incidents.init();reconciliation.init()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.state.stop();self.tmp.cleanup()

    def post(self,path,payload,origin=None):
        headers={'Content-Type':'application/json'}
        if origin:headers['Origin']=origin
        req=urllib.request.Request(self.url+path,data=json.dumps(payload).encode(),headers=headers)
        with urllib.request.urlopen(req) as response:return json.load(response)

    def test_demo_cleanup_recompare_with_provenance(self):
        d=self.post('/api/demo',{})
        mapping=dict(left=d['left'],right=d['right'],left_key=0,right_key=0,left_value=1,right_value=1)
        self.assertEqual(self.post('/api/compare',mapping)['counts']['matched'],1)
        preview=self.post('/api/trim',dict(id=d['left']))
        self.assertEqual(preview['count'],1)
        derived=self.post('/api/trim',dict(id=d['left'],apply=True))['derived']
        mapping['left']=derived
        result=self.post('/api/compare',mapping)
        self.assertEqual(result['counts']['matched'],2)
        self.assertEqual(result['counts']['ambiguous'],1)
        with urllib.request.urlopen(self.url+'/api/dataset/'+derived) as response:
            history=json.load(response)['history']
        self.assertEqual(json.loads(history[-1]['value'])['source'],d['left'])

    def test_incident_replay_repair_and_bundle(self):
        r=self.post('/api/incident-simulate',dict(seed=17,faults=['missing_region']))
        self.assertFalse(r['verified'])
        fixed=self.post('/api/incident-repair',dict(incident_id=r['id'],payment_ids=[i['payment_id'] for i in r['issues']]))
        self.assertTrue(fixed['verified'])
        with urllib.request.urlopen(self.url+'/api/incident-bundle/'+fixed['id']) as response:
            self.assertIn('attachment;',response.headers['Content-Disposition'])
            bundle=json.load(response)
        self.assertEqual(bundle['parent_report'],r)
        self.assertIn('original_delivery',bundle['files'])
        with urllib.request.urlopen(self.url+'/api/incident/'+r['id']) as response:
            self.assertEqual(json.load(response),r)
        with urllib.request.urlopen(self.url+'/api/incidents') as response:
            self.assertEqual(len(json.load(response)),2)
        with urllib.request.urlopen(self.url+'/incidents.js') as response:
            self.assertIn(b'incidentRender',response.read())

    def test_user_snapshot_reconciliation_and_candidate(self):
        ref=self.post('/api/dataset-save',dict(name='reference.csv',content='id,plan\nA,pro\n'))['id']
        delivered=self.post('/api/dataset-save',dict(name='delivery.csv',content='id,plan\nA,basic\nX,extra\n'))['id']
        report=self.post('/api/reconciliation-compare',dict(reference_id=ref,delivery_id=delivered,keys=['id'],authority='Owner-approved export',scope='All accounts',reference_date='2026-09-21',delivery_date='2026-09-21',as_of='2026-09-22',max_age_days=1,confirmed=True))
        fixed=self.post('/api/reconciliation-repair',dict(run_id=report['id'],issue_ids=[i['id'] for i in report['issues']],note='Reference checked with owner.'))
        self.assertTrue(fixed['matched'])
        with urllib.request.urlopen(self.url+'/api/reconciliation-bundle/'+fixed['id']) as response:
            self.assertIn('attachment;',response.headers['Content-Disposition'])
            self.assertEqual(json.load(response)['parent_report'],report)
        with urllib.request.urlopen(self.url+'/api/reconciliations') as response:self.assertEqual(len(json.load(response)),2)
        with urllib.request.urlopen(self.url+'/api/reconciliation/'+report['id']) as response:self.assertEqual(json.load(response),report)

    def test_cross_origin_writes_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('/api/demo',{},'https://untrusted.example')
        self.assertEqual(error.exception.code,403)
        self.assertEqual(datasets.listing(),[])

    def test_inventory_saved_run_and_review(self):
        data=self.post('/api/investigation-demo',{})
        data['contract']='daily-whole-units-v1'
        report=self.post('/api/investigation-run',data)
        self.assertEqual(report['counts']['quarantined'],2)
        self.assertEqual(report['counts']['investigated'],60)
        self.assertEqual(report['counts']['statistical_skipped'],0)
        updated=self.post('/api/investigation-review',dict(run_id=report['id'],record=report['results'][0]['record'],decision='needs investigation',note='Check source ledger'))
        with urllib.request.urlopen(self.url+'/api/investigation/'+report['id']) as response:
            self.assertEqual(json.load(response),updated)
        with urllib.request.urlopen(self.url+'/inventory.js') as response:
            self.assertEqual(response.status,200)

    def test_order_workflow_is_saved_and_separate_from_inventory(self):
        d=self.post('/api/order-demo',{});d['contract']='committed-orders-v1'
        r=self.post('/api/order-run',d)
        self.assertEqual(r['counts']['quarantined'],3)
        self.assertEqual(r['summary']['open_overdue'],2)
        with urllib.request.urlopen(self.url+'/api/order-runs') as response:
            self.assertEqual(json.load(response)[0]['id'],r['id'])
        with urllib.request.urlopen(self.url+'/api/investigations') as response:
            self.assertEqual(json.load(response),[])
        for path in ('/orders.js','/timeline.js'):
            with urllib.request.urlopen(self.url+path) as response:self.assertEqual(response.status,200)

    def test_pipeline_gate_and_publication_over_http(self):
        demo=self.post('/api/pipeline-demo',{})
        good=self.post('/api/pipeline-run',dict(contract_id=demo['contract_id'],dataset_id=demo['good']))
        pub=self.post('/api/pipeline-publish',dict(run_id=good['id'],expected_current=None))
        bad=self.post('/api/pipeline-run',dict(contract_id=demo['contract_id'],dataset_id=demo['bad']))
        self.assertFalse(bad['gate']['passed'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('/api/pipeline-publish',dict(run_id=bad['id'],expected_current=good['id']))
        self.assertEqual(error.exception.code,400)
        with urllib.request.urlopen(self.url+'/api/pipeline/'+demo['pipeline_id']) as response:
            self.assertEqual(json.load(response)['current']['dataset_id'],pub['dataset_id'])
        with urllib.request.urlopen(self.url+'/api/pipeline-export/'+bad['id']+'/quarantine') as response:
            self.assertIn('duplicate_key',response.read().decode())
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.url+'/api/pipeline-export/'+bad['id']+'/accepted')
