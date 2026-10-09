"""Start the existing app server and the official loopback Collector."""
import os
import signal
import subprocess
import sys
import time
from urllib.request import urlopen
from runtime import ROOT, RUNTIME, COLLECTOR_DATA, ingest_token, collector_health


def main():
    binary=RUNTIME/'otelcol-contrib'
    if not binary.exists():raise SystemExit('Install the pinned Collector binary first; see README.md.')
    port=int(os.environ.get('PIPELINE_APP_PORT','8765'))
    app_url=f'http://127.0.0.1:{port}'
    env={**os.environ,'PIPELINE_APP_URL':app_url,'OBSERVABILITY_OTLP_ENDPOINT':'http://127.0.0.1:4318/v1/traces',
         'PIPELINE_INGEST_TOKEN':ingest_token(),'PIPELINE_QUEUE_DIR':str(COLLECTOR_DATA/'queue')}
    (COLLECTOR_DATA/'queue').mkdir(exist_ok=True)
    server=subprocess.Popen([sys.executable,str(ROOT/'app/agent1/server.py')],env=env)
    collector=None
    try:
        for _ in range(60):
            if server.poll() is not None:raise RuntimeError('App server did not start.')
            try:
                with urlopen(app_url+'/api/pipeline/status',timeout=.3):break
            except Exception:time.sleep(.1)
        else:raise RuntimeError('App server readiness timed out.')
        with (COLLECTOR_DATA/'collector.log').open('a') as log:
            collector=subprocess.Popen([str(binary),'--config',str(ROOT/'app/pipeline/collector.yaml')],
                                        env=env,stdout=log,stderr=subprocess.STDOUT)
        for _ in range(60):
            if collector.poll() is not None:raise RuntimeError('Collector did not start; inspect collector.log.')
            if collector_health():break
            time.sleep(.1)
        else:raise RuntimeError('Collector health timed out.')
        print(f'Ready: {app_url}/overview.html · OTLP 4318 · Collector → SQLite → custom UI',flush=True)
        while server.poll() is None and collector.poll() is None:time.sleep(.5)
    except KeyboardInterrupt:pass
    finally:
        for process in (collector,server):
            if process and process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:process.wait(timeout=8)
                except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':main()
