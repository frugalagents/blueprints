/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_BFF_BASE_URL?: string;
  readonly VITE_DATA_MODE?: "bff" | "direct";

  // Direct-mode (FEAT-002) AWS config. Supplied by Amplify build-time env in
  // the cloud, or the gitignored ui/src/data/aws-config.generated.ts locally.
  // NO real ids live in tracked source — these are read, never hardcoded.
  readonly VITE_AWS_REGION?: string;
  readonly VITE_COGNITO_USER_POOL_ID?: string;
  readonly VITE_COGNITO_USER_POOL_CLIENT_ID?: string;
  readonly VITE_COGNITO_IDENTITY_POOL_ID?: string;
  readonly VITE_RUNTIME_ARN?: string;
  readonly VITE_RUNTIME_QUALIFIER?: string;
  readonly VITE_RUNTIME_ENDPOINT_ARN?: string;
  readonly VITE_REGISTRY_ID?: string;
  readonly VITE_GATEWAY_ID?: string;
  readonly VITE_RUNTIME_LOG_GROUP?: string;
  readonly VITE_SPANS_LOG_GROUP?: string;
  readonly VITE_OTEL_SERVICE_NAME?: string;
  readonly VITE_GUARDRAIL_ARN?: string;
  readonly VITE_GUARDRAIL_VERSION?: string;
  readonly VITE_POLICY_ENGINE_ID?: string;
  readonly VITE_POLICY_ID?: string;
  readonly VITE_EVAL_NAMESPACE?: string;
  readonly VITE_BEDROCK_MODEL_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
