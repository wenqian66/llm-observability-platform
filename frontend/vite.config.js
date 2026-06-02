import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/metrics": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
