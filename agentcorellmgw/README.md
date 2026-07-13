# AgentCore LLM Gateway

A managed LLM proxy using Amazon Bedrock AgentCore Gateway. Developers use Claude Code through a single, governed endpoint with OAuth authentication, content guardrails, and per-user token tracking.

## What This Gives You

- **One endpoint** for all developers — no direct Bedrock/Anthropic credentials needed
- **Per-user identity** — every request carries the developer's OAuth token
- **Central governance** — content policies and token limits in one place
- **Usage attribution** — see exactly who consumed how many tokens via CloudWatch

## Architecture

```
Developer (Claude Code)
    │
    │  Bearer <Cognito JWT>
    ▼
AgentCore Gateway (CUSTOM_JWT auth)
    │
    │  IAM Role (server-side)
    ▼
Amazon Bedrock (Claude models)
```

Full architecture diagram: [docs/architecture.md](docs/architecture.md)

---

## Quick Start (Admin)

Run these stages in order. Each stage verifies itself before you proceed.

### Prerequisites

- AWS CLI configured with admin permissions (`aws sts get-caller-identity` works)
- Python 3.10+ with `boto3`, `requests`, `pyjwt` installed
- AgentCore CLI: `pip install bedrock-agentcore-starter-toolkit`

### Stage 1 — Bedrock + Guardrail + IAM Role

Enables the Claude model, creates a content guardrail, and sets up the IAM role.

```bash
cd src
python3 stage1_bedrock.py setup
python3 stage1_bedrock.py test    # All 6 must pass
```

### Stage 2 — Gateway + Inference Target

Creates the AgentCore Gateway with a `bedrock-mantle` inference target (LLM proxy).

```bash
python3 stage2_gateway.py setup
python3 stage2_gateway.py test    # All 6 must pass
```

### Stage 3 — JWT Authentication (Cognito)

Creates a Cognito User Pool and reconfigures the Gateway to require OAuth tokens.

```bash
python3 stage3_jwt_auth.py setup
python3 stage3_jwt_auth.py test   # All 6 must pass
```

### Stage 4 — Claude Code Integration

Verifies the Gateway is compatible with Claude Code's Anthropic Messages API.

```bash
python3 stage4_claude_code.py setup
python3 stage4_claude_code.py test  # All 6 must pass
```

### Stage 5 — Observability

Creates CloudWatch alarms, dashboard, and per-user usage query templates.

```bash
python3 stage5_observability.py setup
python3 stage5_observability.py test  # All 6 must pass
```

---

## Quick Start (Developer)

Once an admin has completed the setup above, developers do this:

### Option A: One-Command Wrapper (Recommended)

```bash
# Install the wrapper (admin distributes this)
cp client/claude-gateway /usr/local/bin/
chmod +x /usr/local/bin/claude-gateway

# Use it — handles auth automatically
claude-gateway
```

It prompts for your username/password on first use, caches the token, and launches Claude Code.

### Option B: Manual Token Setup

```bash
# Get your token
python3 client/get-token.py --user <your-username> --password <your-password>

# It prints export commands — paste them:
export ANTHROPIC_BASE_URL="https://llm-gateway-authed-....amazonaws.com/inference"
export ANTHROPIC_AUTH_TOKEN="eyJraWQ..."

# Run Claude Code normally
claude
```

### Option C: Environment Variables (CI/Automation)

```bash
export CLAUDE_GATEWAY_USER="your-username"
export CLAUDE_GATEWAY_PASSWORD="your-password"
claude-gateway
```

---

## Adding a New Developer

```bash
# From the src/ directory
# First, get your POOL_ID from .config/stage3.json or stage4.json

python3 -c "
import boto3
cognito = boto3.client('cognito-idp', region_name='us-east-1')
POOL_ID = 'YOUR_COGNITO_POOL_ID'  # from .config/stage4.json

cognito.admin_create_user(
    UserPoolId=POOL_ID,
    Username='newdev',
    UserAttributes=[
        {'Name': 'email', 'Value': 'newdev@yourcompany.com'},
        {'Name': 'email_verified', 'Value': 'true'},
    ],
    MessageAction='SUPPRESS',
    TemporaryPassword='TempPassword123!',
)
cognito.admin_set_user_password(
    UserPoolId=POOL_ID,
    Username='newdev',
    Password='YourSecurePassword123!',
    Permanent=True,
)
print('Done')
"
```

---

## Observability

### CloudWatch Dashboard

Open **CloudWatch → Dashboards → LLMGateway-Overview** to see:
- Request volume
- Error rates and throttles
- Latency percentiles (p50/p90/p99)
- Token consumption

### Per-User Token Usage

Run the query in `observability/queries/tokens-per-user.sql` against your Gateway's vended log group:

```
CloudWatch → Logs Insights → select log group → paste query → Run
```

### Alarms

| Alarm | Fires When |
|-------|-----------|
| LLMGateway-HighErrorRate | >5 server errors in 5 minutes |
| LLMGateway-Throttling | >10 throttles in 5 minutes |
| LLMGateway-HighLatency | p99 latency >30s for 10 minutes |

Add SNS actions to these alarms for Slack/PagerDuty notifications.

---

## Project Structure

```
src/
├── config.py               # Shared AWS/project config
├── aws_api.py              # SigV4 HTTP client for AgentCore APIs
├── stage1_bedrock.py       # Model + guardrail + IAM role
├── stage2_gateway.py       # Gateway + inference target
├── stage3_jwt_auth.py      # Cognito + JWT auth
├── stage4_claude_code.py   # Claude Code compatibility
└── stage5_observability.py # Alarms + dashboard + queries

client/
├── claude-gateway          # Developer wrapper script
├── env.example             # Env var template
└── get-token.py            # Token fetch utility

observability/
├── dashboard.json          # CloudWatch dashboard definition
└── queries/
    ├── tokens-per-user.sql
    ├── requests-over-time.sql
    └── errors-by-type.sql

docs/
└── architecture.md         # System diagrams + design decisions
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `401 Unauthorized` | Token expired or invalid | Re-run `get-token.py` or `claude-gateway` (auto-refreshes) |
| `403 Forbidden` | Wrong client ID or audience mismatch | Check Cognito client ID matches gateway config |
| `404 Model not found` | Wrong model name | Use `bedrock-mantle/anthropic.claude-sonnet-5` |
| Gateway stuck in CREATING | IAM role missing permissions | Verify `bedrock-mantle:*` in role policy |
| No per-user metrics | Haven't enabled vended logs | Enable Gateway log destination in CloudWatch |
| Claude Code hangs | `ANTHROPIC_AUTH_TOKEN` not set | Both env vars must be set |

---

## Tearing Down

```bash
# Revert gateway to no auth (for debugging)
cd src && python3 stage3_jwt_auth.py revert

# Full cleanup (deletes all resources)
# WARNING: This removes the gateway, targets, Cognito pool, alarms, etc.
# Use with caution.
python3 -c "
import aws_api, boto3
from config import REGION

# Delete gateways
for gw in aws_api.list_gateways():
    if 'llm-gateway' in gw['name']:
        targets = aws_api.list_gateway_targets(gw['gatewayId'])
        for t in targets:
            aws_api.delete_gateway_target(gw['gatewayId'], t['targetId'])
        import time; time.sleep(10)
        aws_api.delete_gateway(gw['gatewayId'])
        print(f'Deleted gateway: {gw[\"name\"]}')

# Delete Cognito pool
# Get pool ID from .config/stage3.json first
cognito = boto3.client('cognito-idp', region_name=REGION)
cognito.delete_user_pool(UserPoolId='YOUR_COGNITO_POOL_ID')
print('Deleted Cognito pool')

# Delete alarms
cw = boto3.client('cloudwatch', region_name=REGION)
cw.delete_alarms(AlarmNames=['LLMGateway-HighErrorRate','LLMGateway-Throttling','LLMGateway-HighLatency'])
cw.delete_dashboards(DashboardNames=['LLMGateway-Overview'])
print('Deleted alarms and dashboard')
"
```
