import concurrent.futures
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import core
import datasets
import reconciliation as r


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.state=patch.object(core,'STATE',Path(self.tmp.name));self.state.start()
        core.init();datasets.init();r.init()
        self.rules=dict(keys=['id','region'],authority='Source owner-approved CRM export',scope='All active accounts',
                        reference_date='2026-09-21',delivery_date='2026-09-21',as_of='2026-09-22',max_age_days=1,confirmed=True)
        self.reference=self.save('id,region,plan\nA,East,pro\nA,West,basic\nB,North,pro\nC,West,basic\n')
        self.delivery=self.save('plan,region,id\nbasic,East,A\nbasic,West,A\nbasic,West,A\npro,South,X\npro,South,X\n')

    def tearDown(self):self.state.stop();self.tmp.cleanup()
    def save(self,text):return datasets.save('example.csv',text)['id']
    def compare(self,**rules):return r.compare(self.reference,self.delivery,**dict(self.rules,**rules))

    def test_composite_keys_and_reordered_headers_classify_all_kinds(self):
        result=self.compare()
        self.assertEqual(sorted(i['kind'] for i in result['issues']),['changed','duplicate','missing','missing','unexpected'])
        changed=next(i for i in result['issues'] if i['kind']=='changed')
        self.assertEqual(changed['key'],['A','East']);self.assertEqual(changed['changed_fields'],['plan'])
        self.assertEqual(changed['delivery'][0]['record'],2)
        self.assertFalse(result['matched']);self.assertFalse(result['blockers'])

    def test_explicit_removal_and_partial_full_repair_preserve_sources(self):
        original=self.compare();before=datasets.get(self.delivery)['content']
        extra=next(i for i in original['issues'] if i['kind']=='unexpected')
        partial=r.repair(original['id'],[extra['id']],'Confirmed extra account is outside snapshot scope.')
        self.assertFalse(partial['matched']);self.assertEqual(len(partial['changes'][0]['before']),2)
        self.assertEqual(partial['changes'][0]['action'],'remove_unexpected')
        fixed=r.repair(original['id'],[i['id'] for i in original['issues']],'Re-export confirmed with source owner.')
        self.assertTrue(fixed['matched']);self.assertEqual(fixed['counts']['delivery_rows'],4)
        self.assertEqual(datasets.get(self.delivery)['content'],before)
        self.assertEqual(datasets.table(fixed['sources']['delivery'])[1],['plan','region','id'])
        self.assertEqual(r.get(original['id']),original)
        with self.assertRaises(ValueError):r.repair(fixed['id'],[extra['id']],'Chaining is not allowed')

    def test_reference_ambiguity_and_invalid_keys_block_repairs(self):
        for csv in ('id,region,plan\nA,East,pro\nA,East,basic\n','id,region,plan\n A,East,pro\n','id,region,plan\n,East,pro\n','id,region,plan\n'):
            result=r.compare(self.save(csv),self.delivery,**self.rules)
            self.assertTrue(result['blockers']);self.assertFalse(result['matched'])
            with self.assertRaises(ValueError):r.repair(result['id'],[i['id'] for i in result['issues']],'No guessed reference winner')
        result=r.compare(self.reference,self.save('id,region,plan\nA, ,pro\n'),**self.rules)
        self.assertEqual(result['invalid']['delivery'][0]['record'],2)
        self.assertTrue(result['blockers'])

    def test_freshness_boundary_scope_dates_and_future_sources(self):
        self.assertFalse(self.compare()['blockers'])
        for change in ({'as_of':'2026-09-23'},{'as_of':'2026-09-20'},{'delivery_date':'2026-09-20'}):
            result=self.compare(**change);self.assertTrue(result['blockers'])
            with self.assertRaises(ValueError):r.repair(result['id'],[result['issues'][0]['id']],'Dates must be resolved first')
        result=r.compare(self.reference,self.reference,**dict(self.rules,as_of='2026-09-23'))
        self.assertFalse(result['matched']) # exact agreement cannot bypass a stale-snapshot gate

    def test_exact_text_values_and_tuple_identity_avoid_silent_coercion(self):
        ref=self.save('id,region,plan\na|b,c,1.00\na,b|c, basic \n')
        delivered=self.save('id,region,plan\na,b|c, basic \na|b,c,1\n')
        result=r.compare(ref,delivered,**self.rules)
        self.assertEqual(result['counts']['matched_keys'],1)
        self.assertEqual(result['issues'][0]['key'],['a|b','c'])
        self.assertEqual(result['issues'][0]['kind'],'changed')

    def test_empty_delivery_can_be_reconstructed_but_not_empty_reference(self):
        result=r.compare(self.reference,self.save('id,region,plan\n'),**self.rules)
        fixed=r.repair(result['id'],[i['id'] for i in result['issues']],'Restore complete source export.')
        self.assertTrue(fixed['matched'])

    def test_schema_errors_rejected_without_guessing_columns(self):
        for csv in ('id,region,plan,extra\nA,East,pro,x\n','id,region,plan\nA,East\n','id,ID,plan\nA,East,pro\n'):
            with self.assertRaises(ValueError):r.compare(self.reference,self.save(csv),**self.rules)
        with self.assertRaises(ValueError):self.compare(keys=['invented'])

    def test_input_validation_and_selection_integrity(self):
        for change in ({'confirmed':False},{'authority':' '},{'scope':''},{'as_of':'2026-02-30'},
                       {'reference_date':'20260921'},{'max_age_days':True},{'max_age_days':-1},{'keys':[]},{'keys':['id','id']}):
            with self.assertRaises(ValueError):self.compare(**change)
        report=self.compare();key=report['issues'][0]['id']
        for ids,note in (([], 'why'),([key,key],'why'),(['unknown'],'why'),([key],''),('not-a-list','why')):
            with self.assertRaises(ValueError):r.repair(report['id'],ids,note)

    def test_multiline_evidence_and_formula_safe_csv_preserve_raw_bundle(self):
        ref=self.save('id,region,plan\nA,East,"=SUM(1,2)\nsecond line"\n')
        delivered=self.save('id,region,plan\nA,East,old\n')
        report=r.compare(ref,delivered,**self.rules)
        fixed=r.repair(report['id'],[report['issues'][0]['id']],'Restore exact source text.')
        bundle=r.bundle(fixed['id'])
        self.assertEqual(bundle['parent_report'],report)
        for file in bundle['files'].values():self.assertEqual(hashlib.sha256(file['csv'].encode()).hexdigest(),file['sha256'])
        self.assertIn("'=SUM",datasets.export_csv(fixed['sources']['delivery']))
        self.assertIn('"=SUM',bundle['files']['delivery']['csv'])
        self.assertEqual(report['issues'][0]['reference'][0]['record'],2)

    def test_concurrency_persistence_and_immutable_reports(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(lambda _:self.compare(),range(3)))
        self.assertTrue(all(x==results[0] for x in results));self.assertEqual(len(r.listing()),1)
        for query in ('UPDATE reconciliation_runs SET report=? WHERE id=?','DELETE FROM reconciliation_runs WHERE id=?'):
            with self.assertRaises(sqlite3.IntegrityError):
                with core.connection() as con:con.execute(query,('{}',results[0]['id']) if query.startswith('UPDATE') else (results[0]['id'],))
        self.assertEqual(r.get(results[0]['id']),results[0])

    def test_large_issue_set_keeps_all_evidence(self):
        reference=self.save('id,region,plan\n'+''.join('%s,East,pro\n'%i for i in range(120)))
        delivery=self.save('id,region,plan\n')
        report=r.compare(reference,delivery,**self.rules)
        self.assertEqual(len(report['issues']),120)
        self.assertTrue(r.repair(report['id'],[i['id'] for i in report['issues']],'Restore complete 120-record export.')['matched'])


if __name__=='__main__':unittest.main()
