// Registers the loader hook (sse-loader.mjs) so `node --import=./test/sse-register.mjs`
// can import the real TypeScript `src/chat.ts` in the harness.
import { register } from "node:module";
import { pathToFileURL } from "node:url";

register(new URL("./sse-loader.mjs", import.meta.url).href, pathToFileURL("./").href);
