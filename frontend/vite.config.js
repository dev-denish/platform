import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build-time only. Runtime config (API base) comes from src/config.js via
// import.meta.env.VITE_API_BASE, set as a Docker build ARG (see Dockerfile).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
