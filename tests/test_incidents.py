import concurrent.futures
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import core
import datasets
import pipelines
import incidents as replay


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = patch.object(core, 'STATE', Path(self.tmp.name)); self.state.start()
        core.init(); datasets.init(); pipelines.init(); replay.init()

    def tearDown(self):
        self.state.stop(); self.tmp.cleanup()

    def test_missing_region_passes_schema_but_blocks_trust(self):
        r = replay.simulate(17, ['missing_region'])
        self.assertTrue(r['schema_gate']['passed'])
        self.assertFalse(r['verified'])
        self.assertEqual({i['payment_id'] for i in r['issues']}, {'PAY-001','PAY-002','PAY-003','PAY-004'})
        ledger = replay.records(r['sources']['reference'])
        missing = sum(int(row['amount_cents']) for row in ledger if row['region']=='East')
        self.assertEqual(r['delta_cents'], -missing)
        self.assertEqual(r['observed']['refunds_cents'], 4000)
        self.assertTrue(all(i['reference'] and not i['delivered'] for i in r['issues']))

    def test_partial_and_full_repair_preserve_original_and_lineage(self):
        original = replay.simulate(91, list(replay.FAULTS))
        raw = datasets.get(original['sources']['delivered'])['content']
        partial = replay.repair(original['id'], ['PAY-011'])
        self.assertTrue(partial['schema_gate']['passed'])
        self.assertFalse(partial['verified'])
        self.assertEqual(len(partial['changes'][0]['before']), 2)
        self.assertEqual(len(partial['issues']), 5)
        fixed = replay.repair(original['id'], [i['payment_id'] for i in original['issues']])
        self.assertTrue(fixed['verified']); self.assertEqual(fixed['delta_cents'], 0)
        self.assertEqual(fixed['parent'], original['id'])
        self.assertEqual(datasets.get(original['sources']['delivered'])['content'], raw)
        self.assertEqual(replay.get(original['id']), original)
        self.assertEqual(replay.repair(original['id'], ['PAY-011']), partial)
        self.assertTrue(all(not p['current'] for p in (pipelines.detail(x['id']) for x in pipelines.listing())))

    def test_same_total_does_not_mean_same_records(self):
        rows = replay.records(replay.simulate(3, [])['sources']['reference'])
        changed = copy.deepcopy(rows)
        changed[0]['amount_cents'] = str(int(changed[0]['amount_cents'])+100)
        changed[1]['amount_cents'] = str(int(changed[1]['amount_cents'])-100)
        self.assertEqual(replay.metric(rows, []), replay.metric(changed, []))
        self.assertEqual(len(replay.reconcile(rows, changed)), 2)
        self.assertEqual(replay.reconcile(rows, list(reversed(rows))), [])

    def test_healthy_legitimate_seed_variation_has_no_false_alerts(self):
        a, b = replay.simulate(3, []), replay.simulate(91, [])
        self.assertNotEqual(a['expected']['net_cents'], b['expected']['net_cents'])
        self.assertTrue(a['verified'] and b['verified'])
        self.assertEqual(a['issues'], []); self.assertEqual(b['issues'], [])
        self.assertNotEqual(a['id'], b['id'])

    def test_bundle_hashes_and_record_evidence_are_recomputable(self):
        source = replay.simulate(17, list(replay.FAULTS))
        repaired = replay.repair(source['id'], ['PAY-011'])
        bundle = replay.bundle(repaired['id'])
        for role, file in bundle['files'].items():
            self.assertEqual(hashlib.sha256(file['csv'].encode()).hexdigest(), file['sha256'], role)
        self.assertEqual(bundle['parent_report'], source)
        self.assertEqual(bundle['validation']['dataset_id'], repaired['sources']['delivered'])
        changed = next(i for i in source['issues'] if i['kind']=='changed')
        self.assertEqual(changed['changed_fields'], ['amount_cents'])
        duplicate = next(i for i in source['issues'] if i['kind']=='duplicate')
        delivered = replay.records(source['sources']['delivered'])
        for evidence in duplicate['delivered']:
            self.assertEqual(delivered[evidence['record']-2], evidence['values'])

    def test_input_validation_and_immutable_reports(self):
        for seed in (True, -1, 1000000, '17', 1.5):
            with self.assertRaises(ValueError): replay.simulate(seed)
        for faults in ('missing_region', ['invented'], ['missing_region']*2):
            with self.assertRaises(ValueError): replay.simulate(faults=faults)
        r = replay.simulate()
        for selected in ([], ['PAY-009'], ['PAY-001']*2, 'PAY-001'):
            with self.assertRaises(ValueError): replay.repair(r['id'], selected)
        candidate = replay.repair(r['id'], ['PAY-001'])
        with self.assertRaises(ValueError): replay.repair(candidate['id'], ['PAY-002'])
        for query in ('UPDATE incident_runs SET report=? WHERE id=?', 'DELETE FROM incident_runs WHERE id=?'):
            with self.assertRaises(sqlite3.IntegrityError):
                with core.connection() as con: con.execute(query, ('{}',r['id']) if query.startswith('UPDATE') else (r['id'],))
        with self.assertRaises(ValueError): replay.get('not-found')

    def test_concurrent_replay_is_idempotent(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            runs = list(pool.map(lambda _: replay.simulate(17, ['duplicate_retry']), range(3)))
        self.assertTrue(all(r == runs[0] for r in runs))
        self.assertEqual(len(replay.listing()), 1)

    def test_all_24_fixture_combinations_reconcile(self):
        result = replay.evaluate()
        self.assertEqual(result['total'], 24)
        self.assertEqual(result['passed'], 24)
        self.assertEqual(result['comparison'], dict(broken_deliveries=21, schema_detected=12, reconciliation_detected=21, healthy_deliveries=3, healthy_flagged=0))
        self.assertTrue(all(c['passed'] for c in result['cases']))


if __name__ == '__main__': unittest.main()
