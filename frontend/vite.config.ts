import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  build: {
    outDir: "../src/arena_intelligence/assets/app",
    emptyOutDir: true,
    target: "es2022",
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8767",
      "/fonts": "http://127.0.0.1:8767",
    },
  },
});
