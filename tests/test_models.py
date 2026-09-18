import io
import json
import os
import unittest
from unittest.mock import patch, Mock
import benchmark
import model_client


class ModelTests(unittest.TestCase):
    def test_scores_are_exact_and_missing_fields_do_not_pass(self):
        self.assertFalse(benchmark.equal('total','1.234','1.23'))
        self.assertFalse(benchmark.equal('total','NaN','0'))
        self.assertEqual(benchmark.score({},dict(total=None))['correct'],0)
        self.assertEqual(benchmark.score(dict(total='10'),dict(total=None))['unsupported_unknowns'],1)

    def test_unknown_representation_is_strict(self):
        self.assertFalse(benchmark.equal('calendar','','unknown'))
        self.assertTrue(benchmark.equal('total','10.00','10'))

    def test_custom_provider_never_receives_groq_key(self):
        with patch.dict(os.environ,dict(RECORD_DESK_BASE_URL='https://example.com/v1',GROQ_API_KEY='private'),clear=True):
            with self.assertRaises(ValueError):model_client.complete('system','text')

    def test_insecure_remote_endpoint_rejected(self):
        with patch.dict(os.environ,dict(RECORD_DESK_BASE_URL='http://example.com/v1'),clear=True):
            with self.assertRaises(ValueError):model_client.complete('system','text')

    def test_local_compatible_server_and_truncated_response(self):
        def response(reason):
            return io.BytesIO(json.dumps(dict(choices=[dict(finish_reason=reason,message=dict(content='{"fields":{}}'))],usage=dict(total_tokens=5))).encode())
        opener=Mock()
        with patch.dict(os.environ,dict(RECORD_DESK_BASE_URL='http://127.0.0.1:8000/v1'),clear=True),patch('urllib.request.build_opener',return_value=opener):
            opener.open.return_value=response('stop')
            self.assertEqual(model_client.complete('system','text')['result'],{'fields':{}})
            self.assertIsNone(opener.open.call_args.args[0].get_header('Authorization'))
            opener.open.return_value=response('length')
            with self.assertRaises(ValueError):model_client.complete('system','text')
