# Configuration Files

This directory contains stage-specific configuration files for the AgentCore LLM Gateway.

## Setup

1. Copy the example files and rename them:
   ```bash
   cp stage1.example.json stage1.json
   cp stage2.example.json stage2.json
   cp stage3.example.json stage3.json
   cp stage4.example.json stage4.json
   cp stage5.example.json stage5.json
   ```

2. Replace all placeholder values with your actual AWS resources:
   - `YOUR_AWS_ACCOUNT_ID` - Your 12-digit AWS account ID
   - `YOUR_GATEWAY_ID` - AgentCore Gateway ID
   - `YOUR_GUARDRAIL_ID` - Bedrock Guardrail ID
   - `YOUR_COGNITO_POOL_ID` - Cognito User Pool ID
   - `YOUR_COGNITO_CLIENT_ID` - Cognito App Client ID
   - `YOUR_COGNITO_CLIENT_SECRET` - Cognito App Client Secret
   - Other resource IDs as needed

## Security

⚠️ **IMPORTANT**: The `*.json` files (without `.example`) contain sensitive credentials and are excluded from git via `.gitignore`. Never commit these files to version control.

## Files

- `stage1.json` - Basic AWS configuration and model setup
- `stage2.json` - Gateway and policy engine configuration
- `stage3.json` - JWT authentication with Cognito
- `stage4.json` - Same as stage3 (with auth)
- `stage5.json` - Observability and monitoring configuration
