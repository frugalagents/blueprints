import copy
import unittest
from semantic_extensions import extend_otel


class SemanticExtensionTests(unittest.TestCase):
    def test_corrects_provider_without_mutating_raw_or_relationships(self):
        native = [{'id': 'n', 'span_data': {'type': 'generation'}}]
        raw = [{'name': 'generation', 'context': {'trace_id': 't', 'span_id': 's'},
                'parent_id': 'p', 'status': {'status_code': 'OK'}, 'start_time': 'start',
                'attributes': {'app.source.native.span_id': 'n', 'llm.system': 'openai'}}]
        original = copy.deepcopy(raw)
        result = extend_otel(raw, native, 'profile', 'us-east-1')
        self.assertEqual(raw, original)
        for key in ('context', 'parent_id', 'start_time', 'status'):
            self.assertEqual(result[0][key], raw[0][key])
        self.assertEqual(result[0]['attributes']['llm.system'], 'anthropic')
        self.assertEqual(result[0]['attributes']['cloud.provider'], 'aws')
        self.assertIn('app.telemetry.transform.version', result[0]['attributes'])

    def test_preserves_failed_verification_evidence_as_false(self):
        native = [{'id': 'v', 'span_data': {
            'type': 'custom', 'name': 'independent_eligibility_verification',
            'data': {'verified': False, 'actual': 'incomplete', 'policy_errors': 0}}}]
        raw = [{'attributes': {'app.source.native.span_id': 'v'}}]
        attrs = extend_otel(raw, native, 'profile', 'region')[0]['attributes']
        self.assertIs(attrs['app.verification.verified'], False)
        self.assertEqual(attrs['app.verification.actual'], 'incomplete')
        self.assertNotIn('llm.system', attrs)
        self.assertNotIn('app.verification.verified', raw[0]['attributes'])

    def test_does_not_create_missing_sql_or_recover_redacted_errors(self):
        raw = [{'name': 'get_refund_policy',
                'attributes': {'app.source.native.span_id': 'f'},
                'status': {'status_code': 'ERROR', 'description': 'redacted'}}]
        native = [{'id': 'f', 'span_data': {'type': 'function'}}]
        self.assertEqual(extend_otel(raw, native, 'p', 'r'), raw)


if __name__ == '__main__':
    unittest.main()
