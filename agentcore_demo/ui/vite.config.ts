import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite dev server config. The UI talks only to the FastAPI BFF (see src/api.ts).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
});
