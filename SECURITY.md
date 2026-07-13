# Security Guidelines

## Sensitive Data Protection

This repository contains example projects that interact with AWS services. To protect sensitive information:

### What's Protected

The following are automatically excluded from git via `.gitignore`:

- **Configuration files**: `**/.config/*.json` (except `*.example.json`)
- **Environment variables**: `.env`, `.env.*`
- **Credentials**: `*.pem`, `*.key`, `credentials.json`, `secrets.json`
- **Sensitive patterns**: Files matching `*secret*`, `*password*`, `*token*`

### Before Committing

Always run a security check before pushing:

```bash
# Check for exposed secrets
git diff | grep -E "(aws_access_key|aws_secret|api_key|password|secret|token|AKIA[0-9A-Z]{16})"

# Verify gitignore is working
git status --ignored
```

### Configuration Files

Each project may have a `.config/` directory with:
- `*.example.json` - Template files with placeholders (committed to git)
- `*.json` - Actual configuration with real values (excluded from git)
- `README.md` - Setup instructions (committed to git)

**Setup process:**
1. Copy `*.example.json` to `*.json`
2. Replace placeholder values with your actual AWS resources
3. Never commit the actual `*.json` files

### What to Avoid

❌ **DO NOT commit:**
- AWS Account IDs (12-digit numbers)
- AWS Access Keys (AKIA...)
- Cognito Client Secrets
- API Keys or tokens
- Real passwords (even in examples)
- Resource ARNs with account IDs
- Real user data or PII

✅ **DO commit:**
- Template/example files with `YOUR_*` placeholders
- Documentation with sanitized examples
- Code that references environment variables
- Configuration schemas without values

### If You Accidentally Commit Secrets

1. **Rotate the credentials immediately** (AWS, Cognito, API keys)
2. Remove from git history:
   ```bash
   # Use git filter-repo or BFG Repo-Cleaner
   git filter-repo --invert-paths --path <sensitive-file>
   ```
3. Force push (coordinate with team first):
   ```bash
   git push --force-with-lease
   ```
4. Notify your security team if required

### Best Practices

- Use AWS Secrets Manager or Parameter Store for production secrets
- Use environment variables for local development
- Review PRs for accidentally committed secrets
- Enable AWS CloudTrail for audit logging
- Use least-privilege IAM policies

## Reporting Security Issues

If you discover a security vulnerability, please email security@example.com instead of opening a public issue.
