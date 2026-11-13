# aws-agent-scanner

Catalogs AI agents, MCP servers and tools from your AWS organization into one
**AWS Agent Registry**. Everything runs locally from your terminal. Nothing is deployed.

| Source | How it gets into the registry |
|---|---|
| AgentCore Runtime agents | **Auto-detection** (managed by AWS, no code needed) |
| AgentCore Gateways | **Auto-detection** |
| Targets behind a Gateway (MCP, Lambda, OpenAPI, ...) | `agent_registry_sync` scanner (this repo) |
| Agents / MCP servers on EKS | `agent_registry_sync` scanner (this repo) |

## Prerequisites

- Python 3.10+ and `pip`
- AWS CLI v2 configured (`aws sts get-caller-identity` must work). The `aws agent-registry-control`
  commands used in Steps 3 and 5 need a recent CLI (2.40+; 2.37 does not include them). Upgrade it, or use boto3 instead.
- **boto3 >= 1.43.108** (older versions do not include the `agent-registry-control` API)
- An AWS Organization with *all features* enabled (needed for auto-detection)
- Permissions (broad admin is simplest for the one-time setup):
  - Step 1, management account: `organizations:EnableAWSServiceAccess`, `organizations:RegisterDelegatedAdministrator`,
    `organizations:ListAWSServiceAccessForOrganization`, `organizations:ListDelegatedAdministrators`,
    `iam:CreateServiceLinkedRole`, `iam:GetRole`
  - Steps 2 to 4: `agent-registry:*` and read access to AgentCore (`bedrock-agentcore:List*`, `bedrock-agentcore:Get*`),
    `eks:ListClusters`, `eks:DescribeCluster`, `ec2:DescribeRegions`

## Setup

```bash
pip install -r requirements.txt
export PYTHONPATH=src           # lets Python find the agent_registry_sync package
export AWS_PROFILE=<your-profile>   # optional
```

## Step 1: One-time organization setup (management account)

Enables trusted access for Agent Registry, makes sure the service-linked role exists,
and registers a delegated administrator.

AWS recommends a separate **delegated administrator** account own the registry rather than the management account.

```bash
# Preview, changes nothing
python3 scripts/bootstrap_org.py --delegated-admin <ADMIN_ACCOUNT_ID> --dry-run

# Apply
python3 scripts/bootstrap_org.py --delegated-admin <ADMIN_ACCOUNT_ID>
```

If you pass the management account's own ID, the delegated-admin step is skipped and
the management account will own the registry (works, but not recommended).

## Step 2: Create the registry (registry-owner account)

Switch to the account that will own the registry (the delegated admin, or the management account), then:

```bash
python3 scripts/create_registry.py --name org-agent-registry --region us-east-1
```

Note the **registry ID** it prints. You will use it below. Re-running is safe: an existing registry with that name is reused.

> The registry is **regional**. An organization can have one organization-scoped registry per Region.
> Auto-detection only discovers resources in the registry's own Region. Resources in other Regions
> need a registry in that Region.

> Auto-detection can take **up to 20 minutes** after creation to populate. Check `autoDetection.status` is `ACTIVE`.

## Step 3: Verify auto-detection

```bash
aws agent-registry-control list-registry-records --registry-id <REGISTRY_ID> --region us-east-1
```

You should see records named `aws-autodetected-<account>-<region>-<id>`: one per AgentCore runtime (type `AGENT`)
and gateway (type `GATEWAY`). They start in `DRAFT`.

## Step 4: Run the scanner

Always start with a dry run. It writes nothing and prints what it would do.

```bash
# Preview
python3 -m agent_registry_sync.cli --registry-id <REGISTRY_ID> --regions all --dry-run

# Apply (leave records in DRAFT so a curator can review before approval)
python3 -m agent_registry_sync.cli --registry-id <REGISTRY_ID> --regions all --no-submit
```

Options:

| Flag | Meaning |
|---|---|
| `--registry-id` | (required) ID from Step 2 |
| `--registry-region` | Region of the registry (default `us-east-1`) |
| `--regions` | Comma list, or `all` for every enabled Region. Unreachable Regions are skipped |
| `--sources` | `gateway`, `eks`, or `gateway,eks` (default both) |
| `--eks-clusters` | Comma list of cluster names (default: all clusters in each Region) |
| `--profile` | AWS CLI profile to use |
| `--no-submit` | Do not submit new records for approval |
| `--prune` | Deprecate scanner-created records whose source no longer exists |
| `--dry-run` | Show actions only |

The scanner is **idempotent**: re-running updates existing records instead of duplicating them.
It only manages records whose names start with `gw/` or `eks/`. It never touches auto-detected records.

The output ends with a `coverage` section listing any runtimes or gateways that exist but have no
auto-detected record yet. Anything listed there (for example a gateway in a Region other than the registry's)
is not cataloged.

### Gateway targets

Each target behind a gateway becomes a record named `gw/<region>-<gateway>/<target>`:

- MCP server target with an HTTPS endpoint: `MCP` record (metadata synced from the URL)
- Pass-through A2A target with an HTTPS endpoint: `AGENT` record
- Lambda, OpenAPI, Smithy, API Gateway and other targets: `CUSTOM` record holding the gateway and target details
- Targets that point at an AgentCore runtime are skipped (already covered by auto-detection)

### EKS agents

The scanner reads Kubernetes Services from each cluster. Label and annotate the Service of every agent or MCP server you want cataloged:

```yaml
metadata:
  labels:
    agent-registry/enabled: "true"
  annotations:
    agent-registry/url: "https://agents.example.com/orders"   # required, HTTPS
    agent-registry/protocol: "a2a"                            # a2a (default) or mcp
    agent-registry/description: "Order management agent"      # optional
```

Give the identity that runs the scanner read access to the cluster by creating an EKS access entry
for your IAM role/user with the `AmazonEKSViewPolicy`:

```bash
aws eks create-access-entry --cluster-name <CLUSTER> --principal-arn <YOUR_IAM_ARN> --region <REGION>
aws eks associate-access-policy --cluster-name <CLUSTER> --principal-arn <YOUR_IAM_ARN> --region <REGION> \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSViewPolicy --access-scope type=cluster
```

Clusters that cannot be read are logged and skipped. The Kubernetes API endpoint must be reachable from where you run the scanner.

The registry fetches the URL itself to read the agent card or MCP tool list. If that URL is private,
the record may end in `CREATE_FAILED`, and the failure reason is shown on the record.

## Step 5: Review and approve

New records are `DRAFT`. A record is only discoverable once it is `APPROVED`.

```bash
# List drafts
aws agent-registry-control list-registry-records --registry-id <REGISTRY_ID> \
  --filters '[{"name":"status","values":["DRAFT"]}]' --region us-east-1

# Submit for approval
aws agent-registry-control submit-registry-record-for-approval --registry-id <REGISTRY_ID> --record-id <RECORD_ID>

# Approve (curator)
aws agent-registry-control update-registry-record-status --registry-id <REGISTRY_ID> \
  --record-id <RECORD_ID> --status APPROVED --status-reason "Reviewed"
```

To submit everything the scanner created in one go, run Step 4 without `--no-submit`.

## Keeping it current

Re-run Step 4 whenever agents change. Auto-detection keeps runtimes and gateways current by itself.

## Troubleshooting

- `Unknown service: agent-registry-control` or `AttributeError`: upgrade boto3 (`pip install -U boto3`).
- `Organization is not onboarded to auto-detection`: Step 1 has not propagated yet. Wait a minute and retry Step 2.
- No auto-detected records: wait up to 20 minutes and confirm `autoDetection.status` is `ACTIVE`.
- A record is `CREATE_FAILED`: open it and read the status reason (usually the URL is unreachable or needs auth).
- A Region is skipped with `EndpointConnectionError`: AgentCore is not available in that Region. This is expected.
