"""HTTP application boundary for isolated LangChain worker executions."""
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'agent3'))
from native_agent import parse_traceparent,POLICY
sys.path.insert(0,str(ROOT.parent/'pipeline'))
from runtime import capture_dir


def validate(data):
    required={'order','policy','model_mode','session_id','workflow_id','handoff_id','handoff_attempt_id','caller_run_id','caller_attempt_id'}
    if not isinstance(data,dict) or set(data)!=required:raise ValueError('Invalid audit request fields')
    for key in required-{'order','policy','model_mode'}:
        if not isinstance(data[key],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',data[key]):raise ValueError('Invalid audit context')
    if data['model_mode']!='bedrock':raise ValueError('LangChain demo requires bedrock mode')
    order=data['order'];policy=data['policy']
    if (not isinstance(order,dict) or set(order)!={'order_id','age_days','amount_cents'} or order['order_id']!='order-demo-1'
        or type(order['age_days']) is not int or not 0<=order['age_days']<=365
        or type(order['amount_cents']) is not int or not 0<order['amount_cents']<=100000):raise ValueError('Invalid synthetic order')
    if policy!={'policy_version':POLICY,'max_age_days':30}:raise ValueError('Invalid policy')
    return data


class Handler(BaseHTTPRequestHandler):
    seen=set();lock=threading.Lock()
    def log_message(self,*args):pass
    def reply(self,data,code=200):
        body=json.dumps(data).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.path=='/health':return self.reply({'ready':True,'framework':'langchain'})
        self.reply({'error':'Not found'},404)
    def do_POST(self):
        if self.path!='/audit' or self.headers.get('X-AgentC-Token')!=os.environ['AGENTC_TOKEN']:
            return self.reply({'error':'Invalid audit request'},403)
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:raise ValueError('Invalid request size')
            data=validate(json.loads(self.rfile.read(size)));parse_traceparent(self.headers.get('traceparent',''))
            headers={k:self.headers[k] for k in ('traceparent','tracestate') if self.headers.get(k)}
            with self.lock:
                if data['handoff_attempt_id'] in self.seen:raise ValueError('Duplicate audit handoff')
                self.seen.add(data['handoff_attempt_id'])
        except (ValueError,KeyError,TypeError) as error:return self.reply({'error':str(error)},400)
        output=capture_dir('agent3-langchain')/('lc-'+uuid.uuid4().hex[:16]);output.mkdir()
        request=output/'request.json';request.write_text(json.dumps({**data,'trace_headers':headers},indent=2))
        with (output/'worker.log').open('w') as log:
            process=subprocess.Popen([sys.executable,str(ROOT/'worker.py'),'--request',str(request),'--output',str(output)],stdout=log,stderr=subprocess.STDOUT)
            try:process.wait(timeout=95)
            except subprocess.TimeoutExpired:
                process.kill();process.wait();return self.reply({'error':'LangChain worker deadline exceeded'},504)
        try:report=json.loads((output/'run.json').read_text())
        except (OSError,ValueError):return self.reply({'error':'LangChain worker failed before final evidence'},502)
        self.reply(report,200 if report['verification_status']=='passed' else 502)


def main():
    if not os.environ.get('AGENTC_TOKEN'):raise SystemExit('Caller token required')
    server=ThreadingHTTPServer(('127.0.0.1',8786),Handler);print('LangChain Agent C ready on loopback',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
if __name__=='__main__':main()
