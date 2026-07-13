# Test-Driven Development Plan — AgentCore LLM Gateway

## IdP Choice: Keycloak

Since you don't have Entra ID, **Keycloak** is the best open-source replacement:
- Full OIDC-compliant IdP (discovery URL, JWT signing, audiences, scopes)
- Runs as a single Docker container
- Supports the same OAuth 2.0 flows (client credentials, device code, authorization code)
- Well-documented, production-grade, widely adopted
- The Gateway doesn't care — it just validates JWTs via the OIDC discovery URL

---

## Principles

1. **Each stage has a verification gate** — a concrete test that must pass before moving on.
2. **Fail fast** — if a test fails, fix it before proceeding. No skipping.
3. **Incremental** — each stage builds on the proven foundation of the previous one.
4. **Automated** — every verification is a script you can re-run, not a manual console check.

---

## Stage 0 — Local Keycloak (IdP)

**Goal:** Running OIDC provider that issues valid JWTs.

### Setup
- Run Keycloak in Docker (dev mode, no persistence needed initially)
- Create a realm (e.g., `llm-gateway`)
- Create a client (e.g., `claude-code-client`) with client credentials grant enabled
- Create a test user (e.g., `developer1`)

### Verification Tests

| # | Test | Command / Script | Pass Criteria |
|---|------|-----------------|---------------|
| 0.1 | Keycloak is running | `curl http://localhost:8080/health/ready` | HTTP 200 |
| 0.2 | OIDC discovery is exposed | `curl http://localhost:8080/realms/llm-gateway/.well-known/openid-configuration` | JSON with `issuer`, `token_endpoint`, `jwks_uri` |
| 0.3 | Can obtain access token (client credentials) | POST to token endpoint with client_id + secret | Returns `access_token` with valid JWT structure |
| 0.4 | Token is well-formed | Decode JWT, check `iss`, `aud`, `exp`, `sub` claims | All claims present and correct |
| 0.5 | JWKS endpoint returns signing keys | `curl <jwks_uri>` | JSON with RSA/EC keys |

### Artifacts
- `docker-compose.yml` (Keycloak service)
- `scripts/setup-keycloak.sh` (realm + client + user creation via Keycloak Admin REST API)
- `scripts/tests/test-stage-0.sh`

---

## Stage 1 — Bedrock Model Access + Guardrail

**Goal:** Bedrock can serve Claude requests, and a Guardrail is versioned and ready.

### Setup
- Enable Claude model access in Bedrock (manual, one-time)
- Create Guardrail with content filters
- Publish a Guardrail version
- Create/verify IAM role for the Gateway

### Verification Tests

| # | Test | Command / Script | Pass Criteria |
|---|------|-----------------|---------------|
| 1.1 | Claude model is accessible | `aws bedrock list-foundation-models --query "..." ` | Model ID appears in output |
| 1.2 | Direct Bedrock invoke works | `aws bedrock-runtime invoke-model --model-id <ID> --body <payload>` | Returns a completion |
| 1.3 | Guardrail exists | `aws bedrock get-guardrail --guardrail-identifier <ID>` | Returns guardrail config |
| 1.4 | Guardrail version is published | `aws bedrock list-guardrails` | Version ≥ 1 |
| 1.5 | Guardrail blocks harmful content | Invoke Bedrock with guardrail ID + violent prompt | Returns blocked message |
| 1.6 | IAM role exists with correct policy | `aws iam get-role` + `get-role-policy` | Role has `bedrock:InvokeModel*` |

### Artifacts
- `infra/bedrock/guardrail.json`
- `infra/iam/gateway-role.json`
- `scripts/tests/test-stage-1.sh`

---

## Stage 2 — Gateway Creation (No Auth First)

**Goal:** Gateway exists, routes to Bedrock via `bedrock-mantle`, and responds to unauthenticated requests.

### Setup
- Create Gateway with `--authorizer-type NONE` (temporary, for isolation testing)
- Register `bedrock-mantle` inference target with `GATEWAY_IAM_ROLE`
- Attach Guardrail policy and token-limit policy

### Verification Tests

| # | Test | Command / Script | Pass Criteria |
|---|------|-----------------|---------------|
| 2.1 | Gateway exists | `agentcore status` | Shows gateway ID + endpoint |
| 2.2 | Inference target is registered | `aws bedrock-agentcore-control list-gateway-targets` | `bedrock-mantle` target listed |
| 2.3 | Gateway responds to health/models | `awscurl .../inference/v1/models` | Returns model list |
| 2.4 | Chat completion works (no auth) | `awscurl POST .../inference/v1/messages` with simple prompt | Returns Claude response |
| 2.5 | Guardrail blocks via Gateway | Send harmful prompt through Gateway | Blocked response |
| 2.6 | Token limit enforced | Send request with `max_tokens` exceeding policy | Rejected or capped |

### Artifacts
- `infra/gateway/gateway.json`
- `infra/gateway/policies.json`
- `scripts/tests/test-stage-2.sh`

---

## Stage 3 — Gateway + JWT Auth (Keycloak)

**Goal:** Gateway rejects unauthenticated requests and accepts valid Keycloak JWTs.

### Setup
- Recreate (or update) Gateway with `--authorizer-type CUSTOM_JWT`
- Point discovery URL at Keycloak's OIDC endpoint
- Set allowed audience to the Keycloak client ID

### Verification Tests

| # | Test | Command / Script | Pass Criteria |
|---|------|-----------------|---------------|
| 3.1 | Unauthenticated request rejected | `curl` (no token) to Gateway | HTTP 401 |
| 3.2 | Invalid token rejected | Send request with garbage Bearer token | HTTP 401 |
| 3.3 | Expired token rejected | Send request with expired JWT | HTTP 401 |
| 3.4 | Wrong audience rejected | Get token from Keycloak with wrong `aud` | HTTP 401 or 403 |
| 3.5 | Valid token accepted | Get token from Keycloak → send with Bearer header | Claude response returned |
| 3.6 | Different users get different identities | Get tokens for user1 and user2, both succeed | Both return valid responses |

### Important Note on Keycloak Exposure
The Gateway needs to reach Keycloak's OIDC discovery URL to fetch signing keys. Options:
- **Option A:** Run Keycloak on a public-facing server (EC2/ECS with a domain) — simplest for testing.
- **Option B:** Use a tunneling tool (ngrok) to expose local Keycloak temporarily.
- **Option C:** Host Keycloak in the same VPC as the Gateway.

### Artifacts
- `infra/gateway/gateway-authed.json`
- `scripts/tests/test-stage-3.sh`

---

## Stage 4 — Claude Code Integration

**Goal:** A developer's Claude Code CLI routes through the Gateway using their Keycloak token.

### Setup
- Set `ANTHROPIC_BASE_URL` to the Gateway endpoint
- Set `ANTHROPIC_AUTH_TOKEN` to a valid Keycloak access token
- Run Claude Code

### Verification Tests

| # | Test | Command / Script | Pass Criteria |
|---|------|-----------------|---------------|
| 4.1 | Claude Code picks up base URL | `claude config get` | Shows Gateway URL |
| 4.2 | Claude Code works with valid token | Run a simple prompt | Response returned |
| 4.3 | Claude Code fails with expired token | Set an expired token, run a prompt | Auth error (not a hang) |
| 4.4 | Token refresh flow works | Script that refreshes token before expiry | Uninterrupted usage |

### Artifacts
- `client/env.example`
- `client/setup-claude-code.sh`
- `scripts/tests/test-stage-4.sh`

---

## Stage 5 — Observability + Per-User Token Attribution

**Goal:** You can see who used how many tokens, and alarms fire on errors.

### Setup
- Enable CloudWatch Transaction Search
- Create log destination for the Gateway
- Deploy CloudWatch alarms
- Write Logs Insights query for per-user usage

### Verification Tests

| # | Test | Command / Script | Pass Criteria |
|---|------|-----------------|---------------|
| 5.1 | Gateway logs appear in CloudWatch | Make a request, then query logs | Log entry found with trace_id |
| 5.2 | Logs contain user identity | Check log entry for JWT `sub`/`email` claim | User identity present |
| 5.3 | Logs contain token usage | Check response log for `usage.input_tokens` / `output_tokens` | Token counts present |
| 5.4 | Per-user query works | Run Logs Insights query | Returns rows: user, total_input_tokens, total_output_tokens |
| 5.5 | Alarm triggers on errors | Simulate errors (bad requests), wait for alarm | Alarm enters ALARM state |
| 5.6 | Different users show different usage | Two users make different-sized requests | Query shows distinct per-user totals |

### Artifacts
- `observability/alarms.json`
- `observability/dashboard.json`
- `observability/queries/tokens-per-user.sql`
- `scripts/tests/test-stage-5.sh`

---

## Execution Order Summary

```
Stage 0: Keycloak ─── "Can I issue tokens?"
    │ ✓
Stage 1: Bedrock ──── "Can I call Claude directly?"
    │ ✓
Stage 2: Gateway (no auth) ── "Does the proxy route correctly?"
    │ ✓
Stage 3: Gateway + JWT ── "Does auth gate access properly?"
    │ ✓
Stage 4: Claude Code ── "Does the end-to-end developer experience work?"
    │ ✓
Stage 5: Observability ── "Can I see who did what?"
```

Each stage is **independently verifiable** and **safe to pause at**. If anything breaks, you know exactly which layer failed because the previous layer was proven working.

---

## Tools & Dependencies

| Tool | Purpose |
|------|---------|
| Docker / Docker Compose | Run Keycloak locally |
| Keycloak 24+ | Open-source OIDC IdP |
| AWS CLI v2 | Bedrock, IAM, CloudWatch operations |
| AgentCore CLI (`@aws/agentcore`) | Gateway creation and management |
| `awscurl` | SigV4-signed HTTP requests to Gateway |
| `jq` | JSON parsing in test scripts |
| `jwt-cli` or `python-jose` | JWT decode/inspection |
| `curl` | HTTP requests for Keycloak + negative auth tests |
