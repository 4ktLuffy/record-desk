import unittest
from table_quality import inspect_csv


class TableQualityTests(unittest.TestCase):
    def test_mixed_issues_preserve_record_numbers(self):
        report = inspect_csv('name,amount\n Alice ,10\nBob,\nBob,\nOnlyName\n')
        self.assertEqual(report['records'], 4)
        self.assertEqual(report['counts'], dict(whitespace=1, missing=3, duplicate=1, row_width=1))
        self.assertEqual(next(i for i in report['issues'] if i['kind']=='duplicate')['record'], 4)

    def test_quotes_bom_and_multiline(self):
        result = inspect_csv('\ufeffname,note\r\nA,"hello,\nworld"\r\n')
        self.assertEqual(result['records'], 1)
        self.assertEqual(result['total_issues'], 0)

    def test_ambiguous_headers(self):
        self.assertEqual(inspect_csv('Name, name ,\na,b,c')['counts']['header'], 2)

    def test_bad_inputs(self):
        for value in ['', '\x00', 'a\n"unterminated', 17, 'a'*2_000_001]:
            with self.assertRaises(ValueError):
                inspect_csv(value)

    def test_report_is_bounded_but_counts_complete(self):
        result = inspect_csv('a,b\n'+',\n'*150)
        self.assertEqual(len(result['issues']), 200)
        self.assertEqual(result['counts']['missing'], 300)
