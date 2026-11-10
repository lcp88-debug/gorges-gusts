import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `npm run dev` serves the app on port 5173 and passes the server's routes through to
// the Python app on port 8000, so the session cookie works as it does in production.
const python = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": python, "/login": python, "/auth": python },
  },
  test: { environment: "jsdom", globals: true },
});
