// DataClientProvider + useDataClient(): the single place the app resolves which
// DataClient to use, keyed off VITE_DATA_MODE (see mode.ts). Tabs call
// useDataClient() and never import api.ts/chat.ts/fetch directly. 'direct' mode
// is wired in FEAT-002; until then it falls back to the BFF client so the
// default local path is unaffected.

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { getDataMode } from "./mode";
import { BffDataClient } from "./bff-client";
import { DirectAwsDataClient } from "./direct-client";
import { sessionCredentialsProvider } from "./auth";
import type { DataClient } from "./types";

const DataClientContext = createContext<DataClient | null>(null);

/** Select the DataClient for the current mode. */
function createClient(): DataClient {
  switch (getDataMode()) {
    case "direct":
      // Browser-direct AWS access under the signed-in Cognito Identity Pool
      // identity. The credential provider is LAZY (sessionCredentialsProvider),
      // so the client constructs without network/credentials and only resolves
      // SigV4 creds on the first AWS call — after the login gate has set the
      // session. The login-gate UI is wired by FEAT-003 (Amplify + Cognito).
      return new DirectAwsDataClient(sessionCredentialsProvider());
    case "bff":
    default:
      return new BffDataClient();
  }
}

export function DataClientProvider({ children }: { children: ReactNode }) {
  const client = useMemo(createClient, []);
  return (
    <DataClientContext.Provider value={client}>
      {children}
    </DataClientContext.Provider>
  );
}

/** Access the active DataClient. Must be used under <DataClientProvider>. */
export function useDataClient(): DataClient {
  const client = useContext(DataClientContext);
  if (!client) {
    throw new Error("useDataClient must be used within a DataClientProvider");
  }
  return client;
}
