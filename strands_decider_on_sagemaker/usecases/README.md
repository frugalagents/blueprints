# Strands Decider use cases

Each folder has a runnable `demo.py` that calls the live SageMaker endpoint (`strands-decider`,
override with `DECIDER_ENDPOINT`). Run one with `.venv/bin/python usecases/01_ticket_triage/demo.py`,
or all with `.venv/bin/python usecases/run_all.py`.

| Folder | What it shows |
|---|---|
| 01_ticket_triage | team + refund + urgency + sentiment in one call |
| 02_intent_router | chat intent -> handler, low confidence -> human |
| 03_incident_triage | alert severity + owning team |
| 04_tool_call_gate | allow / ask / block an agent's tool call; `gated()` decorator |
| 05_llm_router | small vs large model, needs-search flag |
| 06_agent_control | done? / ask the user? / next tool |
| 07_confidence_cascade | threshold sweep; the expensive LLM is simulated |
| 08_content_screening | PII, prompt injection, abuse |
| 09_lead_scoring | Cold/Warm/Hot and ranking |
| 10_llm_judge_filter | pass / fail / escalate borderline answers |
| 11_event_driven | SQS/S3 Lambda handler, run locally with fake events |

Accuracy figures come from 10-24 hand-labeled examples per folder. They are smoke tests, not benchmarks.
