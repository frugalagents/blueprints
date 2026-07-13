# AgentCore Gateway as an LLM Gateway for Claude Code — A Novice's Step‑by‑Step Guide

**Goal:** Stand up an Amazon Bedrock AgentCore Gateway that acts as a single, governed LLM proxy. Register a Claude model (served by Amazon Bedrock) as an *inference target*, put OAuth 2.0 SSO (Entra ID / Authly) in front of it, add Guardrails, and point **Claude Code** at it. Finish with observability so you can see token usage per user.

> Written for someone new to this. Read top to bottom the first time. Every step says *why* it exists, then *what to run*.

---

## 0. The big picture (read this first)

Think of the Gateway as a **toll booth in front of your models**:

```
                         ┌──────────────────────────────────────────┐
   Claude Code           │        AgentCore Gateway (LLM proxy)      │
  (developer's laptop)   │                                          │
        │                │  1. INBOUND AUTH  →  validates OAuth JWT  │
        │  OAuth token    │     (Entra ID / Authly issues it)        │
        └───────────────►│  2. GUARDRAILS    →  content policy       │
                         │  3. INFERENCE TARGET → routes by model    │
                         │  4. OUTBOUND AUTH →  IAM role to Bedrock  │
                         └───────────────────────┬──────────────────┘
                                                 │
                                                 ▼
                                    Amazon Bedrock (Claude model)
```

Four things the Gateway gives you, all in one place:
- **One endpoint** — the client only ever talks to the Gateway, never directly to Bedrock/Anthropic.
- **Credential abstraction** — developers hold a *gateway* token; the *provider* credentials (IAM role to Bedrock) stay server‑side.
- **Central governance** — Guardrails + token‑limit policies apply to every call, no matter who made it.
- **Per‑user visibility** — because every request carries the user's OAuth identity, you can attribute token usage per user.

**Two kinds of "auth" — don't confuse them:**
| | What it does | Who provides it |
|---|---|---|
| **Inbound auth** | Checks *who is calling the Gateway* | Your IdP (Entra ID / Authly) via OAuth 2.0 JWT |
| **Outbound auth** | How the *Gateway calls Bedrock* | An AWS IAM role (`GATEWAY_IAM_ROLE`) |

---

## Prerequisites (install once)

- **AWS account** with the AWS CLI configured (`aws configure`).
- **Node.js 18+** — needed for the AgentCore CLI.
- **Python 3.10+** — for test scripts.
- Install the AgentCore CLI:
  ```bash
  npm install -g @aws/agentcore
  ```
- IAM permissions to create roles, and to use `bedrock:*` and `bedrock-agentcore:*`.
- Pick a region and stick to it (examples use `us-west-2`).

---

## Step 1 — Amazon Bedrock setup (Guardrails, Identity Center, Model Access)

### 1a. Enable model access (activate Claude)
Bedrock models are **off by default**. Turn Claude on:
1. Bedrock console → **Model access** → **Manage model access**.
2. Enable the Claude model you want (e.g. **Claude Sonnet**). Submit.
3. Note the exact **model ID** (e.g. `anthropic.claude-sonnet-4-6` — copy whatever your console shows).

CLI check that it's active:
```bash
aws bedrock list-foundation-models --region us-west-2 \
  --query "modelSummaries[?contains(modelId,'claude')].modelId"
```

### 1b. Create a Guardrail
Guardrails enforce content policy on every call routed through the Gateway.
```bash
aws bedrock create-guardrail --region us-west-2 \
  --name "llm-gateway-guardrail" \
  --description "Content policy for all gateway LLM traffic" \
  --blocked-input-messaging "This request was blocked by policy." \
  --blocked-outputs-messaging "This response was blocked by policy." \
  --content-policy-config '{
    "filtersConfig": [
      {"type": "HATE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
      {"type": "VIOLENCE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
      {"type": "SEXUAL", "inputStrength": "HIGH", "outputStrength": "HIGH"},
      {"type": "PROMPT_ATTACK", "inputStrength": "HIGH", "outputStrength": "NONE"}
    ]
  }'
```
Then publish a version so it can be referenced:
```bash
aws bedrock create-guardrail-version --region us-west-2 \
  --guardrail-identifier <GUARDRAIL_ID_FROM_ABOVE>
```
Keep the **guardrail ID** and **version** — you attach them to the Gateway as a policy later.

### 1c. IAM Identity Center (SSO backbone)
IAM Identity Center is AWS's SSO service. In this design it plays a supporting role — **your OAuth IdP for the Gateway is Entra ID / Authly** (Step 2), which is what actually issues the tokens Claude Code sends. Use IAM Identity Center to:
- Federate your workforce into AWS for console/CLI access, and
- (Optionally) act as an identity source that syncs users/groups.

For the Gateway's inbound auth, what matters is the **OIDC discovery URL + audience/client ID** from Entra ID / Authly. IAM Identity Center is not on the Gateway's request path.

> **Novice note:** You do *not* need IAM Identity Center to make the Gateway work. It's listed because it's part of the enterprise SSO story. The Gateway trusts your OAuth IdP directly via a discovery URL.

---

## Step 2 — OAuth 2.0 SSO with Entra ID / Authly (inbound auth)

The Gateway is **IdP‑agnostic**. It validates incoming JWTs using your IdP's OpenID Connect (OIDC) discovery URL. You need two values from your IdP:

1. **Discovery URL** — must end in `/.well-known/openid-configuration`.
   - Entra ID: `https://login.microsoftonline.com/<TENANT_ID>/v2.0/.well-known/openid-configuration`
   - Authly: `https://<your-authly-host>/.well-known/openid-configuration` *(exact URL comes from your workshop environment setup)*
2. **Allowed audience** (the `aud` claim) **or** **allowed client ID** (the `client_id` claim) — the app registration ID that Claude Code will authenticate as.

**In your IdP (Entra ID example):**
1. **App registrations** → **New registration** → name it (e.g. `claude-code-gateway`).
2. Expose an API / set an **Application ID URI** — this becomes your **audience**.
3. Create a client the developers' Claude Code will use (public or confidential client, per your workshop guidance).
4. Copy the **tenant ID**, **client ID**, and the **Application ID URI (audience)**.

The Gateway inbound authorizer validates: **signature, issuer, audience, expiry**, and optionally scopes or custom claims (e.g. "group must equal Developer"). At least one of *audience / client / scope / custom claim* must be configured.

You'll plug these into the Gateway in Step 3.

---

## Step 3 — Create the AgentCore Gateway and register Claude as an inference target

### 3a. Create the Gateway with JWT (OAuth) inbound auth
Using the AgentCore CLI:
```bash
agentcore add gateway --name LLMGateway \
  --authorizer-type CUSTOM_JWT \
  --discovery-url "https://login.microsoftonline.com/<TENANT_ID>/v2.0/.well-known/openid-configuration" \
  --allowed-audience "<APPLICATION_ID_URI_OR_CLIENT_ID>"
```
> To experiment first *without* auth, use `--authorizer-type NONE`, then recreate with `CUSTOM_JWT` once the model routing works. Don't ship `NONE`.

### 3b. Register the Claude model as an **inference target**

This is the new capability you referenced: **inference targets** turn the Gateway into a unified LLM proxy. For Bedrock‑hosted Claude, use the built‑in **`bedrock-mantle` connector** (zero‑config) with outbound auth via the Gateway's IAM role:

```bash
aws bedrock-agentcore-control create-gateway-target --region us-west-2 \
  --cli-input-json '{
    "gatewayIdentifier": "<GATEWAY_ID>",
    "name": "bedrock-mantle",
    "targetConfiguration": {
      "inference": {
        "connector": { "source": { "connectorId": "bedrock-mantle" } }
      }
    },
    "credentialProviderConfigurations": [
      { "credentialProviderType": "GATEWAY_IAM_ROLE" }
    ]
  }'
```

What the `bedrock-mantle` connector handles for you automatically:
- **Model ID prefix stripping** — clients can say `claude-sonnet-4-6` instead of `anthropic.claude-sonnet-4-6`.
- **Path rewriting** — maps `/inference/...` paths to Bedrock's API.
- **Operations** — exposes chat completions / messages.

> Connectors also exist for `openai` and `anthropic` (direct), which use an `API_KEY` credential provider instead of the IAM role. For your case (Claude *on Bedrock*), `bedrock-mantle` + `GATEWAY_IAM_ROLE` is the right choice.

### 3c. Attach Guardrails + a token‑limit policy
Attach the Guardrail from Step 1b and — **important** — a **token‑limit policy**. Without one, a single user's high‑`max_tokens` request can drain the shared TPM quota for everyone and hold Gateway resources open. Configure these as Gateway policies on the target (see *Gateway policies* in the docs for the exact policy JSON for your account).

### 3d. Find your Gateway's inference endpoint
```bash
agentcore status
```
Your inference base URL looks like:
```
https://<GATEWAY_ID>.gateway.bedrock-agentcore.us-west-2.amazonaws.com/inference
```

### 3e. Smoke‑test the target (Anthropic Messages format)
```bash
awscurl --service bedrock-agentcore --region us-west-2 -X POST \
  "https://<GATEWAY_ID>.gateway.bedrock-agentcore.us-west-2.amazonaws.com/inference/v1/messages" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":256,"messages":[{"role":"user","content":"Say hello in one line."}]}'
```
List everything the Gateway can route to:
```bash
awscurl --service bedrock-agentcore --region us-west-2 \
  "https://<GATEWAY_ID>.gateway.bedrock-agentcore.us-west-2.amazonaws.com/inference/v1/models"
```

---

## Step 4 — Point Claude Code at your Gateway

Claude Code can route through any gateway that speaks the Anthropic Messages API — which your inference target does at the `/inference` path.

On the developer's machine, set two environment variables:
```bash
# The Gateway's inference base URL (Anthropic Messages format lives at /inference)
export ANTHROPIC_BASE_URL="https://<GATEWAY_ID>.gateway.bedrock-agentcore.us-west-2.amazonaws.com/inference"

# The per-user OAuth token issued by Entra ID / Authly
export ANTHROPIC_AUTH_TOKEN="<USER_OAUTH_ACCESS_TOKEN>"
```
Then run `claude` as normal. Notes:
- The **`ANTHROPIC_AUTH_TOKEN`** is the developer's **gateway credential** — their OAuth access token. This replaces the claude.ai subscription for the session; usage bills to the Bedrock account behind the Gateway.
- Setting only the base URL *without* a token does **not** replace the subscription — you must supply the per‑user token for the identity (and per‑user metrics) to flow.
- For a real rollout, distribute `ANTHROPIC_BASE_URL` via a **managed settings file** and the token via your secrets tooling, so developers configure nothing manually.
- Security guard: Claude Code will only connect to a gateway on a **private address** (behind an internal load balancer / VPN). Plan network placement accordingly.

**Verify on the developer machine:**
```bash
claude config get   # confirm the base URL is picked up
# then run a trivial prompt in claude and watch it succeed
```

---

## Step 5 — Observability: metrics + token usage per user

You have **two layers** of telemetry. Use both.

### 5a. One‑time setup
1. Enable **CloudWatch Transaction Search** (one‑time, required for AgentCore spans/traces).
2. Create a **log destination** for the Gateway so request/response logs are vended.
3. Open the **CloudWatch → GenAI Observability** page to see dashboards.

### 5b. Gateway metrics (namespace `AWS/Bedrock-AgentCore`)
Batched at 1‑minute intervals. Dimensions include `Operation`, `Protocol`, `Method`, `Resource` (gateway ARN), `Name` (tool):

| Metric | Meaning |
|---|---|
| `Invocations` | Total requests to the Gateway |
| `Throttles` | 429s |
| `SystemErrors` / `UserErrors` | 5xx / 4xx (excl. 429) |
| `Latency` | Time to first response token (p50/p90/p99) |
| `Duration` | End‑to‑end request time |
| `TargetExecutionTime` | Time spent in the target |
| `TargetType` | Requests per target type |

Example alarm on error rate:
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "GatewayHighErrorRate" \
  --metric-name "SystemErrors" --namespace "AWS/Bedrock-AgentCore" \
  --statistic "Sum" --dimensions "Name=Resource,Value=<GATEWAY_ARN>" \
  --period 300 --evaluation-periods 1 --threshold 5 \
  --comparison-operator "GreaterThanThreshold" \
  --alarm-actions "arn:aws:sns:us-west-2:<ACCT>:my-topic"
```

### 5c. Token usage (namespace `AWS/Bedrock`)
Bedrock itself emits token counts:

| Metric | Meaning |
|---|---|
| `InputTokenCount` | Prompt tokens |
| `OutputTokenCount` | Completion tokens |
| `Invocations`, `InvocationLatency`, `InvocationThrottles` | Volume / latency / throttling |

Plus AgentCore session metrics on the GenAI Observability page: **session count, latency, duration, token usage, error rates**.

### 5d. Getting token usage **per user** (the key ask)
Native CloudWatch metrics are dimensioned by model/operation, **not by end user**. To attribute tokens per user you use the identity that inbound OAuth put on each request:
- Every Gateway request is validated against the user's JWT, and the Gateway **vends request/response logs** (with `trace_id` / `span_id`) to CloudWatch Logs. The response body carries token usage; the authenticated identity is on the request.
- **Do this:** run a **CloudWatch Logs Insights** query over the Gateway's vended logs, extracting the user claim (e.g. `sub` / `email` from the JWT context) and summing `usage.input_tokens` + `usage.output_tokens` from the response body, grouped by user.
- For chargeback dashboards, ship these logs to your analytics stack (S3 → Athena/QuickSight) and group by user + model.

> Rule of thumb: **CloudWatch metrics** answer "how much and how fast overall"; **vended logs (grouped by JWT identity)** answer "who consumed what."

---

## Recommended order of operations (checklist)

1. ☐ Enable Claude model access in Bedrock; note the model ID.
2. ☐ Create + version a Guardrail.
3. ☐ Register an app in Entra ID / Authly; capture discovery URL + audience/client ID.
4. ☐ `agentcore add gateway ... --authorizer-type CUSTOM_JWT` (start with `NONE` only to test).
5. ☐ Create the `bedrock-mantle` inference target with `GATEWAY_IAM_ROLE`.
6. ☐ Attach Guardrail + **token‑limit policy** to the target.
7. ☐ Smoke‑test with `awscurl` against `/inference/v1/messages`.
8. ☐ Set `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` in Claude Code; verify a prompt.
9. ☐ Enable Transaction Search + log destination; open GenAI Observability.
10. ☐ Build a Logs Insights query for **tokens per user**; add alarms.

---

## Common gotchas

- **Model not enabled** → enable it in Bedrock → Model access.
- **401 from Gateway** → JWT audience/issuer mismatch; re‑check the discovery URL and `aud`/`client_id`.
- **AccessDenied to Bedrock** → the Gateway's IAM role needs `bedrock:InvokeModel*`.
- **Unbounded/expensive responses** → you forgot the token‑limit policy; shared credentials mean one user can starve others.
- **Claude Code won't connect** → gateway must resolve to a **private** IP; and the subscription isn't replaced unless `ANTHROPIC_AUTH_TOKEN` is set.
- **No per‑user metrics** → native metrics aren't per‑user; you must derive them from vended logs keyed on the JWT identity.

---

## Sources
- AgentCore Gateway — Inference targets (unified LLM proxy), Inference connector targets (`bedrock-mantle`), model‑based routing, streaming, token‑limit policy
- AgentCore Gateway — Get started / CLI (`agentcore add gateway`, `CUSTOM_JWT`)
- AgentCore — Configure inbound JWT authorizer (IdP‑agnostic OAuth 2.0)
- AgentCore — Gateway observability (metrics, vended logs, spans); CloudWatch GenAI Observability
- Amazon Bedrock CloudWatch metrics (`InputTokenCount`, `OutputTokenCount`)
- Anthropic — Claude Code LLM gateway configuration (`ANTHROPIC_BASE_URL`, per‑user token); Claude apps gateway deployment
