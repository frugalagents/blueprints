"""Start LangChain C and the frozen app/Collector with application-side integration configured."""
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[2]


def main():
    env={**os.environ,'PIPELINE_APP_PORT':os.environ.get('PIPELINE_APP_PORT','8775'),
         'AGENTC_PORT':'8786','AGENTC_ENDPOINT':'http://127.0.0.1:8786/audit',
         'AGENTC_TOKEN':secrets.token_hex(24),'OBSERVABILITY_OTLP_ENDPOINT':'http://127.0.0.1:4318/v1/traces'}
    c=subprocess.Popen([str(ROOT/'.runtime/agent3-langchain/.venv/bin/python'),str(ROOT/'app/agent3-langchain/server.py')],env=env,start_new_session=True);pipeline=None
    try:
        for _ in range(60):
            if c.poll() is not None:raise RuntimeError('Agent C did not start')
            try:
                with urlopen('http://127.0.0.1:8786/health',timeout=.3):break
            except Exception:time.sleep(.1)
        else:raise RuntimeError('Agent C readiness timed out')
        pipeline=subprocess.Popen([sys.executable,str(ROOT/'app/pipeline/start.py')],env=env,start_new_session=True)
        print('Three-agent demo configured. Open the existing Pipeline inspection tab.',flush=True)
        while c.poll() is None and pipeline.poll() is None:time.sleep(.5)
    except KeyboardInterrupt:pass
    finally:
        for process in (pipeline,c):
            if process and process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:process.wait(timeout=12)
                except subprocess.TimeoutExpired:process.kill();process.wait()

if __name__=='__main__':main()
