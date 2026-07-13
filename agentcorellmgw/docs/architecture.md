# Architecture — AgentCore LLM Gateway

## System Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          DEVELOPER WORKSTATION                               │
│                                                                             │
│  ┌──────────────┐         ┌──────────────────────┐                          │
│  │  Claude Code  │────────▶│  get-token.py        │                          │
│  │  CLI          │         │  (fetches JWT from   │                          │
│  │              │         │   Cognito)           │                          │
│  └──────┬───────┘         └──────────────────────┘                          │
│         │                                                                    │
│         │  ANTHROPIC_BASE_URL = gateway/inference                            │
│         │  ANTHROPIC_AUTH_TOKEN = <cognito JWT>                              │
│         │                                                                    │
└─────────┼───────────────────────────────────────────────────────────────────┘
          │
          │  POST /inference/v1/messages
          │  Authorization: Bearer <JWT>
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS CLOUD                                        │
│                                                                             │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                    AgentCore Gateway                                    │ │
│  │                    (llm-gateway-authed)                                 │ │
│  │                                                                        │ │
│  │  ┌─────────────────┐    ┌─────────────────┐    ┌──────────────────┐   │ │
│  │  │ 1. INBOUND AUTH │    │ 2. POLICY ENGINE│    │ 3. INFERENCE     │   │ │
│  │  │                 │    │                 │    │    TARGET         │   │ │
│  │  │ CUSTOM_JWT      │    │ LOG_ONLY mode   │    │                  │   │ │
│  │  │ Validates:      │    │                 │    │ bedrock-mantle   │   │ │
│  │  │ • signature     │    │ Cedar policies  │    │ connector        │   │ │
│  │  │ • issuer        │    │ (permit-all)    │    │                  │   │ │
│  │  │ • audience      │    │                 │    │ Routes to        │   │ │
│  │  │ • expiry        │    │ Future:         │    │ Bedrock models   │   │ │
│  │  │                 │    │ guardrail       │    │                  │   │ │
│  │  │ Rejects 401 if  │    │ enforcement     │    │ 51 models        │   │ │
│  │  │ invalid         │    │                 │    │ available        │   │ │
│  │  └────────┬────────┘    └────────┬────────┘    └────────┬─────────┘   │ │
│  │           │                      │                      │             │ │
│  └───────────┼──────────────────────┼──────────────────────┼─────────────┘ │
│              │                      │                      │               │
│              │                      │                      │               │
│              ▼                      ▼                      ▼               │
│  ┌───────────────────┐  ┌──────────────────┐  ┌─────────────────────────┐ │
│  │  AWS Cognito       │  │  CloudWatch      │  │  Amazon Bedrock          │ │
│  │                   │  │                  │  │                           │ │
│  │  User Pool:       │  │  • Gateway       │  │  Model:                   │ │
│  │  llm-gateway-     │  │    metrics       │  │  anthropic.claude-        │ │
│  │  users            │  │  • Vended logs   │  │  sonnet-5                 │ │
│  │                   │  │    (per-request)  │  │                           │ │
│  │  Users:           │  │  • Token usage   │  │  IAM Role:                │ │
│  │  • developer1     │  │    per user      │  │  AgentCoreGatewayRole     │ │
│  │  • developer2     │  │    (via JWT      │  │  (bedrock:InvokeModel*    │ │
│  │                   │  │     identity)    │  │   bedrock-mantle:*)       │ │
│  │  OIDC Discovery:  │  │                  │  │                           │ │
│  │  /.well-known/    │  │  Namespace:      │  │  Guardrail:               │ │
│  │  openid-config    │  │  AWS/Bedrock-    │  │  llm-gateway-guardrail    │ │
│  │                   │  │  AgentCore       │  │  (content filtering)      │ │
│  └───────────────────┘  └──────────────────┘  └─────────────────────────┘ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Request Flow

```
Developer                Cognito              Gateway                  Bedrock
    │                       │                    │                        │
    │─── authenticate ─────▶│                    │                        │
    │◀── access_token ──────│                    │                        │
    │                       │                    │                        │
    │─── POST /v1/messages ─────────────────────▶│                        │
    │    Authorization: Bearer <token>           │                        │
    │                       │                    │                        │
    │                       │◀── fetch JWKS ─────│                        │
    │                       │─── public keys ───▶│                        │
    │                       │                    │                        │
    │                       │                    │── validate JWT          │
    │                       │                    │   (sig, iss, aud, exp)  │
    │                       │                    │                        │
    │                       │                    │── assume IAM role ─────▶│
    │                       │                    │   (GATEWAY_IAM_ROLE)    │
    │                       │                    │                        │
    │                       │                    │── InvokeModel ─────────▶│
    │                       │                    │   claude-sonnet-5       │
    │                       │                    │                        │
    │                       │                    │◀── model response ──────│
    │                       │                    │                        │
    │◀── 200 + response ────────────────────────│                        │
    │    {content, usage}   │                    │                        │
    │                       │                    │                        │
```

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Cognito over Keycloak | Publicly reachable — Gateway can fetch JWKS without tunneling |
| Separate authed gateway | Gateway auth type is immutable after creation |
| bedrock-mantle connector | Zero-config translation from Messages API to Bedrock |
| GATEWAY_IAM_ROLE outbound | Developers never hold AWS credentials |
| Policy Engine in LOG_ONLY | Inference targets don't expose action schemas for Cedar enforcement |
| Per-user identity via JWT | Every request carries user identity for attribution |

## Resources Created

| Resource | Identifier |
|----------|-----------|
| Gateway (no auth, Stage 2) | `llm-gateway-elkvo6834b` |
| Gateway (JWT auth, Stage 3) | `llm-gateway-authed-6ybw1vckbw` |
| Inference Target | `RKZUTEHMQX` / `5SVM6CBXOT` |
| IAM Role | `AgentCoreGatewayRole` |
| Guardrail | `h9wogh7sm9h7` (v2) |
| Policy Engine | `llm_gateway_policy_engine-5ib9l3xzl_` |
| Cognito Pool | `YOUR_COGNITO_POOL_ID` |
| Cognito Client | `YOUR_COGNITO_CLIENT_ID` |

## Endpoints

| Purpose | URL |
|---------|-----|
| Inference (authed) | `https://llm-gateway-authed-6ybw1vckbw.gateway.bedrock-agentcore.us-east-1.amazonaws.com/inference` |
| List models | `GET /inference/v1/models` |
| Chat completion | `POST /inference/v1/messages` |
| OIDC Discovery | `https://cognito-idp.us-east-1.amazonaws.com/YOUR_COGNITO_POOL_ID/.well-known/openid-configuration` |
