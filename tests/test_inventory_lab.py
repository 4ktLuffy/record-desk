import copy
import unittest
import inventory_lab as lab


class InventoryLabTests(unittest.TestCase):
    def test_reproducible_and_explicit_time_split(self):
        self.assertEqual(lab.run(),lab.run())
        self.assertEqual(lab.run()['split'],dict(training=2400,calibration=1200,test=1200))

    def test_labels_are_separate_and_input_is_unchanged(self):
        rows,truth=lab.generate();before=copy.deepcopy(rows)
        self.assertFalse(any('sales_anomaly' in r or 'reconciliation' in r for r in rows))
        profiles,threshold=lab.fit(rows[:2400]);lab.detect(rows[3600:],profiles,threshold)
        self.assertEqual(rows,before)
        self.assertTrue(any(x['sales_anomaly'] for x in truth.values()))

    def test_accounting_flags_exact_injected_differences(self):
        metric=lab.run()['metrics']['reconciliation']
        self.assertEqual(metric['fp'],0);self.assertEqual(metric['fn'],0);self.assertGreater(metric['tp'],0)

    def test_empty_alert_precision_not_invented(self):
        result=lab.metrics([dict(id='x',flag=False)],{'x':{'truth':True}},'flag','truth')
        self.assertIsNone(result['precision']);self.assertEqual(result['recall'],0)

    def test_balance_and_anomaly_are_independent(self):
        rows,_=lab.generate();profiles,threshold=lab.fit(rows[:2400])
        row=copy.deepcopy(rows[0]);row['sold']=200;row['closing']=row['opening']+row['received']-200
        result=lab.detect([row],profiles,threshold)[0]
        self.assertTrue(result['sales_anomaly']);self.assertFalse(result['reconciliation'])
