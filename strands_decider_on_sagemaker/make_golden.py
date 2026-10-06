"""Run tests/requests.json through the model locally (CPU) and save tests/golden.json."""
import json, os, sys, time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
from strands_decider.infer import EngineConfig, SystemOneEngine
from strands_decider.modeling import StrandsDeciderModel
from strands_decider.schema import SystemOneRequest

ckpt = sys.argv[1] if len(sys.argv) > 1 else "model/decider"
device = sys.argv[2] if len(sys.argv) > 2 else "cpu"
engine = SystemOneEngine(
    StrandsDeciderModel.load(ckpt),
    EngineConfig(device=device, use_prefix_cache=True, model_name="strands-decider-2B-hobson-v19"),
)
reqs = json.load(open("tests/requests.json"))
golden = {}
for name, body in reqs.items():
    t = time.perf_counter()
    resp = engine.evaluate(SystemOneRequest(**body)).model_dump()
    print(f"{name}: {(time.perf_counter()-t)*1000:.0f} ms")
    golden[name] = resp
json.dump(golden, open("tests/golden.json", "w"), indent=2)
print(json.dumps(golden, indent=1)[:1800])
