// Direct-mode (FEAT-002) AWS resource configuration.
//
// The browser-direct DataClient needs the deployed stack's ids/ARNs (Cognito
// pools, the AgentCore runtime ARN, the registry/policy ids, CloudWatch log
// groups + namespaces). These are NEVER hardcoded in tracked source — the
// redaction git-grep gate must stay clean. They resolve, in priority order:
//
//   1. Amplify build-time env vars (import.meta.env.VITE_*) — the CLOUD path.
//   2. A gitignored ui/src/data/aws-config.generated.ts default export — the
//      LOCAL-dev path (an infra script writes it; it is in .gitignore).
//
// A missing required id fails LOUDLY at use time (requireConfig) rather than
// fabricating a value, mirroring bff/aws_reads.py's _require.

/** The resolved direct-mode config. Optional fields may be absent locally. */
export interface AwsConfig {
  region: string;
  userPoolId: string;
  userPoolClientId: string;
  identityPoolId: string;
  runtimeArn: string;
  runtimeQualifier: string;
  runtimeEndpointArn?: string;
  registryId?: string;
  gatewayId?: string;
  runtimeLogGroup?: string;
  spansLogGroup: string;
  otelServiceName?: string;
  guardrailArn?: string;
  guardrailVersion?: string;
  policyEngineId?: string;
  policyId?: string;
  evalNamespace: string;
  bedrockModelId?: string;
}

/**
 * Optional gitignored local-dev config. Resolved at build time via Vite's glob
 * so a MISSING file is simply an empty result (never a build error). When the
 * file exists it must `export default` a Partial<AwsConfig>.
 */
function loadGeneratedConfig(): Partial<AwsConfig> {
  // eager:true inlines the module at build time; the glob matches 0 or 1 file.
  const mods = import.meta.glob<{ default: Partial<AwsConfig> }>(
    "./aws-config.generated.ts",
    { eager: true }
  );
  for (const key of Object.keys(mods)) {
    const mod = mods[key];
    if (mod && mod.default) return mod.default;
  }
  return {};
}

const generated = loadGeneratedConfig();

/** env var wins, else generated-config, else undefined (trimmed, non-empty). */
function pick(
  envValue: string | undefined,
  generatedValue: string | undefined
): string | undefined {
  const env = (envValue ?? "").trim();
  if (env) return env;
  const gen = (generatedValue ?? "").trim();
  return gen || undefined;
}

const env = import.meta.env;

/** The resolved direct-mode config (env override, else generated fallback). */
export const awsConfig: AwsConfig = {
  region: pick(env.VITE_AWS_REGION, generated.region) ?? "us-east-1",
  userPoolId: pick(env.VITE_COGNITO_USER_POOL_ID, generated.userPoolId) ?? "",
  userPoolClientId:
    pick(env.VITE_COGNITO_USER_POOL_CLIENT_ID, generated.userPoolClientId) ?? "",
  identityPoolId:
    pick(env.VITE_COGNITO_IDENTITY_POOL_ID, generated.identityPoolId) ?? "",
  runtimeArn: pick(env.VITE_RUNTIME_ARN, generated.runtimeArn) ?? "",
  runtimeQualifier:
    pick(env.VITE_RUNTIME_QUALIFIER, generated.runtimeQualifier) ?? "DEFAULT",
  runtimeEndpointArn: pick(
    env.VITE_RUNTIME_ENDPOINT_ARN,
    generated.runtimeEndpointArn
  ),
  registryId: pick(env.VITE_REGISTRY_ID, generated.registryId),
  gatewayId: pick(env.VITE_GATEWAY_ID, generated.gatewayId),
  runtimeLogGroup: pick(env.VITE_RUNTIME_LOG_GROUP, generated.runtimeLogGroup),
  spansLogGroup:
    pick(env.VITE_SPANS_LOG_GROUP, generated.spansLogGroup) ?? "aws/spans",
  otelServiceName: pick(env.VITE_OTEL_SERVICE_NAME, generated.otelServiceName),
  guardrailArn: pick(env.VITE_GUARDRAIL_ARN, generated.guardrailArn),
  guardrailVersion: pick(
    env.VITE_GUARDRAIL_VERSION,
    generated.guardrailVersion
  ),
  policyEngineId: pick(env.VITE_POLICY_ENGINE_ID, generated.policyEngineId),
  policyId: pick(env.VITE_POLICY_ID, generated.policyId),
  evalNamespace:
    pick(env.VITE_EVAL_NAMESPACE, generated.evalNamespace) ??
    "Bedrock-AgentCore/Evaluations",
  bedrockModelId: pick(env.VITE_BEDROCK_MODEL_ID, generated.bedrockModelId),
};

/** A required id/ARN is absent from both env and the generated config. */
export class MissingAwsConfig extends Error {
  constructor(key: string) {
    super(
      `Direct-mode AWS config '${key}' is not set. Supply it via the ` +
        `VITE_${key} Amplify env var or the gitignored ` +
        `ui/src/data/aws-config.generated.ts. (No ids are hardcoded.)`
    );
    this.name = "MissingAwsConfig";
  }
}

/** Resolve a required config value or throw a clear error (no fabrication). */
export function requireConfig<K extends keyof AwsConfig>(key: K): string {
  const val = awsConfig[key];
  if (typeof val !== "string" || !val.trim()) {
    throw new MissingAwsConfig(String(key));
  }
  return val;
}
