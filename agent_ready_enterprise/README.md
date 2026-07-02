# Enterprise API Knowledge Graph Runtime

Standalone POC for building a domain-agnostic semantic control plane over enterprise APIs.

It ingests OpenAPI specs, builds a capability knowledge graph, retrieves relevant APIs for a question, generates a typed execution plan, validates the plan, executes it deterministically, builds evidence, and synthesizes an answer with Amazon Bedrock.

## Quickstart

```bash
cd enterprise-api-kg
pip install -r requirements.txt

python -m api_kg.cli build --specs-dir ./specs/sample_hcm
python -m api_kg.cli stats

python -m api_kg.cli mock --specs-dir ./specs/sample_hcm --fixtures-dir ./fixtures/sample_hcm --port 8080
```

In another shell:

```bash
cd enterprise-api-kg
python -m api_kg.cli ask "Why did Sarah's net pay drop this month?" --no-bedrock
```

Remove `--no-bedrock` after configuring AWS credentials:

```bash
export AWS_PROFILE=...
export AWS_REGION=us-east-1
python -m api_kg.cli ask "Why did Sarah's net pay drop this month?"
```

## Architecture

```text
OpenAPI specs
  -> normalized capabilities/entities/fields
  -> NetworkX knowledge graph
  -> lexical + graph retrieval
  -> typed plan JSON
  -> plan validation
  -> deterministic DAG execution
  -> evidence claims
  -> Bedrock answer synthesis
```

The LLM is used for planning and answer synthesis, but the trusted execution path is declarative and deterministic.

## CLI

```bash
python -m api_kg.cli build --specs-dir ./specs/sample_hcm
python -m api_kg.cli retrieve "Why did Sarah's net pay drop?"
python -m api_kg.cli plan "Why did Sarah's net pay drop?" --no-bedrock
python -m api_kg.cli ask "Why did Sarah's net pay drop?" --no-bedrock
python -m api_kg.cli inspect get_paystub
python -m api_kg.cli ui
```
