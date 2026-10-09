import json
import os
import tempfile
import unittest
from unittest.mock import patch
from opentelemetry.sdk.trace import TracerProvider
from caller import make_audit_hook
from native_agent import POLICY

class Reply:
    status=200
    def __init__(self,body):self.body=json.dumps(body).encode()
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,*args):return self.body

class CallerTests(unittest.TestCase):
    def invoke(self,tamper=False):
        ids={'session_id':'session','workflow_id':'workflow','run_id':'b-run','attempt_id':'b-attempt'}
        order={'order_id':'order-demo-1','age_days':10,'amount_cents':2500}
        policy={'policy_version':POLICY,'max_age_days':30}
        def respond(request,**kwargs):
            body=json.loads(request.data)
            result={**body,'verification_status':'passed','decision':{'verified':True,'eligible':True,'order_id':'order-demo-1','policy_version':POLICY,'max_age_days':30}}
            if tamper:result['caller_run_id']='different-run'
            return Reply(result)
        with tempfile.TemporaryDirectory() as output,patch.dict(os.environ,{'AGENTC_ENDPOINT':'http://127.0.0.1/audit','AGENTC_TOKEN':'test-only'}),patch('caller.urlopen',side_effect=respond):
            provider=TracerProvider()
            try:return make_audit_hook(provider,ids,output,'fixture')(order,policy,True)
            finally:provider.shutdown()
    def test_verified_matching_audit_is_accepted(self):
        self.assertTrue(self.invoke()['decision']['verified'])
    def test_tampered_context_is_rejected(self):
        with self.assertRaises(PermissionError):self.invoke(True)
