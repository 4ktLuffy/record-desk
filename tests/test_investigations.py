import csv
import datetime as dt
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import core
import datasets
import investigations as inv


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.state=patch.object(core,'STATE',Path(self.tmp.name));self.state.start()
        core.init();datasets.init();inv.init()
        self.rows=[]
        for day in range(65):
            self.rows.append([str(dt.date(2026,1,1)+dt.timedelta(days=day)),'P1','North',100,5,5,100])
        self.options=dict(mapping={k:i for i,k in enumerate(inv.ROLES)},train_end='2026-01-30',calibration_end='2026-02-13',test_end='2026-03-06',contract='daily-whole-units-v1')

    def tearDown(self):
        self.state.stop();self.tmp.cleanup()

    def save(self,rows):
        out=io.StringIO(newline='');w=csv.writer(out);w.writerow(inv.ROLES);w.writerows(rows)
        return datasets.save('fixture.csv',out.getvalue())['id']

    def run_rows(self,rows):
        return inv.run(self.save(rows),**self.options)

    def test_persistence_and_source_immutability(self):
        ident=self.save(self.rows);before=datasets.get(ident)
        result=inv.run(ident,**self.options)
        self.assertEqual(result,inv.get(result['id']))
        self.assertEqual(result['counts']['investigated'],21)
        self.assertEqual(result['counts']['flagged'],0)
        self.assertEqual(result['profiles'][0]['scale'],1)
        self.assertNotIn('metrics',result)
        self.assertEqual(datasets.get(ident),before)
        self.assertEqual(inv.listing()[0]['id'],result['id'])

    def test_quarantine_dates_duplicates_blanks_and_width(self):
        self.rows += [self.rows[-1].copy(),['2026-02-30','P1','North',100,5,5,100],['2026-02-18','Other','North',100,5,'',100],['short']]
        self.rows.append(['2026-02-20','Whitespace','North',' 100',5,5,100])
        r=self.run_rows(self.rows)
        self.assertEqual(r['counts']['quarantined'],6)
        self.assertEqual(r['counts']['source'],r['counts']['valid']+r['counts']['quarantined'])
        self.assertTrue(all(x['raw'] for x in r['quarantine']))

    def test_invalid_duplicate_excludes_valid_partner(self):
        bad=self.rows[-1].copy();bad[5]='';self.rows.append(bad)
        self.assertEqual(self.run_rows(self.rows)['counts']['quarantined'],2)

    def test_missing_history_unseen_series_and_arithmetic(self):
        self.rows.pop(3)
        self.rows.append(['2026-02-20','New','North',100,5,5,80])
        r=self.run_rows(self.rows)
        self.assertFalse(r['profiles'])
        self.assertTrue(all(x['score'] is None for x in r['results']))
        novel=next(x for x in r['results'] if x['product']=='New')
        self.assertIn('stock arithmetic',novel['flags'])
        self.assertIsNone(novel['continuity_delta'])
        self.assertGreater(r['series'][0]['missing_test_days'],0)

    def test_calibration_gap_disables_statistics(self):
        self.rows.pop(35)
        r=self.run_rows(self.rows)
        self.assertFalse(r['profiles'])
        self.assertIn('calibration',r['results'][0]['statistical_skip_reason'])

    def test_future_does_not_change_profiles_or_thresholds(self):
        before=self.run_rows(self.rows)
        self.rows[-1][5]=800
        self.rows.append(['2027-01-01','P1','North',100,5,999,100])
        after=self.run_rows(self.rows)
        self.assertEqual(before['profiles'],after['profiles'])
        self.assertEqual(after['counts']['outside_window'],1)
        self.assertGreater(after['counts']['flagged'],0)

    def test_promotion_can_flag_without_arithmetic_error(self):
        self.rows[-1][5]=30;self.rows[-1][6]=75
        r=self.run_rows(self.rows)['results'][-1]
        self.assertEqual(r['flags'],['unusual sales'])
        self.assertEqual(r['delta'],0)

    def test_contaminated_training_changes_profile_not_ground_truth(self):
        for row in self.rows[:30]:row[5]=50
        r=self.run_rows(self.rows)
        self.assertEqual(r['profiles'][0]['median'],50)
        self.assertNotIn('precision',r)

    def test_out_of_order_source_is_sorted_with_record_evidence(self):
        self.rows.reverse();r=self.run_rows(self.rows)
        self.assertEqual(r['results'][-1]['record'],2)
        self.assertEqual(r['results'][0]['date'],'2026-02-14')

    def test_reviews_append_and_database_blocks_mutation(self):
        r=self.run_rows(self.rows);record=r['results'][0]['record']
        inv.review(r['id'],record,'confirmed issue','Checked source')
        updated=inv.review(r['id'],record,'expected event','Reconciled after context')
        self.assertEqual(len(updated['reviews']),2)
        self.assertEqual(updated['profiles'],r['profiles'])
        with self.assertRaises(ValueError):inv.review(r['id'],1,'expected event','Header is not a record')
        with self.assertRaises(ValueError):inv.review(r['id'],record,'expected event',' ')
        with self.assertRaises(sqlite3.IntegrityError):
            with core.connection() as con:con.execute('DELETE FROM investigation_reviews')
        with self.assertRaises(sqlite3.IntegrityError):
            with core.connection() as con:con.execute("UPDATE investigations SET report='{}'")
        inv.init();self.assertEqual(len(inv.get(r['id'])['reviews']),2)

    def test_contract_mapping_and_cutoffs_rejected(self):
        ident=self.save(self.rows)
        for changes in ({'contract':'other'},{'mapping':{k:0 for k in inv.ROLES}},{'train_end':'2026-02-13'},{'train_end':'2026-02-30'},{'train_end':'0001-01-01'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):inv.run(ident,**dict(self.options,**changes))
        self.assertEqual(inv.listing(),[])

    def test_timeline_preserves_real_gaps_and_periods_without_future(self):
        self.rows.pop(50)
        self.rows.append(['2027-01-01','P1','North',100,5,5,100])
        r=self.run_rows(self.rows)
        dates={p['date'] for p in r['timeline']}
        self.assertNotIn('2026-02-20',dates)
        self.assertNotIn('2027-01-01',dates)
        self.assertEqual(len(r['timeline']),64)
        self.assertEqual({p['period'] for p in r['timeline']},{'training','calibration','investigation'})
        self.assertEqual([p['record'] for p in r['timeline'] if p['date']=='2026-01-01'],[2])

    def test_legacy_run_listing_and_report_remain_readable(self):
        r=self.run_rows(self.rows)
        r.pop('timeline');r.pop('kind');r.pop('reviews');r['id']='legacy-v1';r['version']='inventory-import-v1'
        inv.persist(r)
        saved=inv.get('legacy-v1')
        self.assertNotIn('timeline',saved)
        self.assertTrue(any(x['id']=='legacy-v1' for x in inv.listing()))

    def test_malformed_width_duplicate_excludes_valid_partner(self):
        self.rows.append(self.rows[-1][:3])
        r=self.run_rows(self.rows)
        self.assertEqual(r['counts']['quarantined'],2)
