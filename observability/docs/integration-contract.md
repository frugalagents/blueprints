# Consolidation integration contract v1

Common transport: standard OTLP HTTP protobuf → OTel Collector → existing store.
No custom intermediate telemetry format. Each source adapter owns conversion;
Collector configuration owns source semantic corrections and content policy.
The store and generic inspector receive OTel only.

Required OTLP identities: nonzero 16-byte trace ID, 8-byte span ID, timestamps.
Adapters must provide `service.name` as a resource attribute. The inspector
reports a missing-service bucket for older or incomplete telemetry rather than
inventing an agent identity. Span names and instrumentation scopes are unrestricted.
Each source owns its statuses, events, links, and optional semantic attributes.

For synchronous calls, propagate incoming W3C context and record the real parent.
For asynchronous or separate-trace relationships, emit explicit OTel links.
Shared workflow/session IDs alone do not establish a causal edge. Missing parent
records and parent cycles are exposed as coverage gaps. A root without parent
context is shown as a root, not an inferred handoff.

Optional app context: `app.workflow.id`, `app.session.id`, `app.run.id`,
`app.attempt.id`, `app.handoff.id`, `app.handoff.attempt.id`. This demo indexes
workflows through `app.workflow.id`; downstream spans without that attribute
are also included when they share an indexed trace. A trace with no workflow
attribute anywhere is stored but is not selectable in the workflow picker.
This is an explicit v1 query limitation, not a format restriction.

Usage and business evidence are optional: absence means not captured. Native
source IDs and trusted verification evidence are added at the source adapter
boundary. Source-specific normalization rules remain in Collector config.

Read-only API:
- GET `/api/observability/workflows`: stored workflow discovery.
- GET `/api/observability/workflows/{id}`: all discovered services, actual OTel
  records, recorded parents/links, cross-service boundaries, coverage gaps,
  optional source audit records and exact preservation comparison.

Audit comparison is independent of ingestion. `audit_sources.json` registers
capture globs and optional native record lookup keys. Adding a source requires
only an entry here if its original records should appear in the comparison UI.
The inspector displays native records without requiring a particular native
field layout; detailed source-field mapping remains adapter documentation.

Legacy `/api/connected` runner and its A/B response fields describe the refund
use case; they are not the generic consolidation contract. The generic Pipeline
inspection page uses only `/api/observability` and OTel record fields.

Freeze baseline files are listed in `baseline.json`. After the freeze, C may add
an implementation, adapter, and configuration. No changes to the generic store,
graph, API, or inspector are allowed for the onboarding proof. Check the hashes
before and after C; any required core modification fails that proof.

Steps 1–3 verify the existing real A/B captures and source-independent graph
behavior. They do not prove a third-source adapter yet. C onboarding is step 5.
