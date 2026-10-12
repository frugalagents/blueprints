// Minimal ESM loader hook used by the SSE parser harness: resolve extensionless
// relative specifiers to `.ts`, and provide an in-memory stub for `./api` (so
// the real `src/chat.ts` can be imported under Node without Vite's
// `import.meta.env`). Not shipped in the app bundle — test tooling only.
import { existsSync } from "node:fs";

export async function resolve(specifier, context, next) {
  if (specifier === "./api" || specifier === "./api.ts") {
    return { url: "stub:api", shortCircuit: true };
  }
  if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
    const base = new URL(specifier, context.parentURL);
    const asTs = base.href + ".ts";
    if (existsSync(new URL(asTs).pathname)) {
      return { url: asTs, shortCircuit: true };
    }
  }
  return next(specifier, context);
}

export async function load(url, context, next) {
  if (url === "stub:api") {
    return {
      format: "module",
      shortCircuit: true,
      source: 'export const BFF_BASE_URL = "http://localhost:8000";',
    };
  }
  return next(url, context);
}
