# Lambda Web Function → AgentCore POC

A learning proof-of-concept: an **AWS Lambda Web Function** (plain Express, Node.js)
that serves a small chat **UI** and invokes an agent running in **Amazon Bedrock
AgentCore Runtime**, streaming the reply back to the browser.

> **Is the Web Function the UI?** No. A Web Function is a backend HTTP server. It
> can *serve* a UI (as this POC does — it returns `chat.html`) and expose API
> routes, but it is not itself a UI. The browser is the UI tier; the Web Function
> is the backend tier; AgentCore is the agent tier.

## Architecture

```
Browser (chat UI)
   │  POST /chat { message, sessionId }
   ▼
Lambda Web Function (Node.js + Express)      ← one deployable
   │  GET  /        → serves chat.html (the UI)
   │  POST /chat    → InvokeAgentRuntimeCommand, streams SSE back
   │  GET  /health
   ▼
Amazon Bedrock AgentCore Runtime (your agent, via AGENT_RUNTIME_ARN)
```

Credentials never touch the browser — the Lambda execution role (locally, your AWS
profile) holds `bedrock-agentcore:InvokeAgentRuntime`.

## Prerequisites

- Node.js 20+ (Lambda Web Functions run Node.js 24 on Arm64).
- An agent already deployed in AgentCore Runtime, and its **runtime ARN**.
- AWS credentials with `bedrock-agentcore:InvokeAgentRuntime` permission.

## Configuration

| Env var                   | Required | Purpose                                                        |
| ------------------------- | -------- | -------------------------------------------------------------- |
| `AGENT_RUNTIME_ARN`       | yes      | ARN of your AgentCore Runtime agent.                           |
| `AWS_REGION`              | no       | Region for the AgentCore client (default `us-east-1`).         |
| `AWS_LAMBDA_HTTP_ENDPOINT`| set by Lambda | `host:port` the server listens on. Set it yourself locally. |

## Run locally

```bash
npm install

# Point at your agent + region (loaded from .env, which is gitignored).
cp .env.example .env   # then edit AGENT_RUNTIME_ARN etc.

# `dev` sets AWS_LAMBDA_HTTP_ENDPOINT=127.0.0.1:3000 and loads .env for you.
npm run dev
```

Open http://127.0.0.1:3000 and chat. `GET /health` reports whether the agent ARN
is configured.

Local AWS credentials are picked up the usual way (`AWS_PROFILE`, env vars, or
`~/.aws/credentials`).

## IAM policy (for the Lambda role, or your local user)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock-agentcore:InvokeAgentRuntime",
      "Resource": "arn:aws:bedrock-agentcore:*:*:runtime/*"
    }
  ]
}
```

Scope `Resource` down to your specific runtime ARN for anything beyond a POC.

## Deploy as a Lambda Web Function

Build an isolated deploy package (source + production dependencies), then deploy it:

```bash
rm -rf webfn && mkdir webfn
cp index.js package.json package-lock.json webfn/ && cp -r public webfn/
(cd webfn && npm ci --omit=dev)

set -a; source .env; set +a
aws lambda-web deploy --name "$WEB_FUNCTION_NAME" --code ./webfn --region "$AWS_REGION"
```

The CLI bundles the source, creates the Web Function, publishes a revision, and
returns a live HTTPS URL. Set `AGENT_RUNTIME_ARN` / `AWS_REGION` as function
environment variables, and attach the IAM policy above to the function's role
(see `webfn-invoke-policy.example.json`).

> **The Web Function URL is public and unauthenticated.** Anyone with the URL can
> invoke your agent and incur model costs. Add auth (e.g. CloudFront + Cognito/JWT)
> or delete the function when you're done.

> Requires a recent AWS CLI with the `lambda-web` command. See the
> [announcement](https://aws.amazon.com/blogs/aws/announcing-aws-lambda-web-functions-for-fast-consistent-web-app-performance-anywhere/)
> and the Lambda Web Functions developer guide.

## Deploy the agent

The agent lives in `webfnpoc/` (an AgentCore CLI project deployed via CDK).

```bash
cd webfnpoc/agentcore
cp aws-targets.example.json aws-targets.json   # set your account + region
cd .. && agentcore deploy --yes
```

The deploy output includes the runtime ARN to use for `AGENT_RUNTIME_ARN`. The
agent uses the `us.anthropic.claude-sonnet-5-5` inference profile (see
`webfnpoc/app/webfnpocagent/model/load.py`).

## Tear down (stop billing)

```bash
# Agent + its stack (ECR, runtime, roles, CodeBuild): delete the CloudFormation
# stack AgentCore-webfnpoc-default, or run `agentcore remove agent webfnpocagent`
# followed by `agentcore deploy`.

# Web Function:
aws lambda-web delete-web-function --name "$WEB_FUNCTION_NAME" --region "$AWS_REGION"
aws iam delete-role-policy --role-name awscli-lambdaweb-web-fn-agentcore-poc --policy-name invoke-agentcore-runtime
```

## Notes & caveats

- **Node.js only.** At launch, Lambda Web Functions support only the Node.js
  runtime. Python is not supported for this form factor yet (it remains a managed
  runtime for regular "Event Functions").
- **Streaming.** `POST /chat` forwards AgentCore's streaming response to the
  browser as SSE. If your agent returns `application/json` instead of
  `text/event-stream`, the server collects it and sends it as a single token.
- **Session continuity.** The browser stores a `sessionId` (≥33 chars, as
  AgentCore requires) and reuses it so the agent keeps conversation context.
- **POC scope.** No auth, no CloudFront, no multi-region, no tests, and IaC only
  for the agent (CDK). Add
  those before anything resembling production.

## License

MIT, same as the rest of this repository.
