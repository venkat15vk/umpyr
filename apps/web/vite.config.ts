/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite reads VITE_* variables from the repo-root .env (one env file for the whole project).
export default defineConfig({
  plugins: [react()],
  envDir: "../..",
  server: { port: 5173 },
  test: { environment: "jsdom", setupFiles: ["./src/test.setup.ts"], globals: true },
});
