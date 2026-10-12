// Data-access mode selection. VITE_DATA_MODE picks which DataClient the
// provider hands the app: 'bff' (DEFAULT) delegates to the FastAPI BFF via the
// existing api.ts + chat.ts — the local-no-regression path; 'direct' (FEAT-002)
// talks to AWS directly from the browser. Anything unset or unrecognized falls
// back to 'bff' so the local path never changes behavior.

export type DataMode = "bff" | "direct";

export function getDataMode(): DataMode {
  return import.meta.env.VITE_DATA_MODE === "direct" ? "direct" : "bff";
}
