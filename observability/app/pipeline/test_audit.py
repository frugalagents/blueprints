import json
import tempfile
import unittest
from pathlib import Path
from audit import captures

class AuditTests(unittest.TestCase):
    def test_unknown_capture_shape_registered_by_configuration_only(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);native={'records':[{'opaque_id':'n1','custom_field':42}]}
            spans=[{'context':{'trace_id':'t','span_id':'s'},'attributes':{'app.workflow.id':'w','vendor.source.id':'n1'}}]
            (root/'telemetry.json').write_text(json.dumps(spans));(root/'raw.json').write_text(json.dumps(native))
            cfg={'sources':[{'otel_glob':'telemetry.json','native_file':'raw.json','native_records_key':'records','native_id_key':'opaque_id','otel_native_id_attribute':'vendor.source.id'}]}
            (root/'config.json').write_text(json.dumps(cfg));result=captures(root,root/'config.json','w',set())
            self.assertEqual(result['native_by_otel_identity']['t/s']['custom_field'],42)
    def test_no_registered_source_is_not_faked(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(captures(Path(d),Path(d)/'missing.json','w',set()),{'spans':[],'native_by_otel_identity':{}})
