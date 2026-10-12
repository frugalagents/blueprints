import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { DataClientProvider } from "./data/DataClientProvider";
import LoginGate from "./data/LoginGate";
import "./styles/theme.css";

// LoginGate blocks the tabs until a Cognito session exists in VITE_DATA_MODE=
// direct (the Amplify SPA); in the default 'bff' mode it renders <App/> straight
// through, so the local path is unchanged.
ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <DataClientProvider>
      <LoginGate>
        <App />
      </LoginGate>
    </DataClientProvider>
  </React.StrictMode>
);
