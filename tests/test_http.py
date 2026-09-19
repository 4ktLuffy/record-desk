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
from server import Handler, ThreadingHTTPServer


class WorkflowHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.state=patch.object(core,'STATE',Path(self.tmp.name));self.state.start()
        core.init();datasets.init();investigations.init()
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
