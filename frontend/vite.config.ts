import path from "node:path";
import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const frontendRoot = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  base: "/next/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(frontendRoot, "src"),
    },
  },
  build: {
    outDir: path.resolve(frontendRoot, "../src/daily_agent/web_dist"),
    emptyOutDir: true,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "schema-vendor",
              test: /node_modules[\\/]zod/,
              priority: 30,
            },
            {
              name: "react-vendor",
              test: /node_modules[\\/](?:react|react-dom|react-router|react-router-dom|@tanstack)/,
              priority: 20,
            },
            {
              name: "ui-vendor",
              test: /node_modules[\\/](?:@base-ui|lucide-react)/,
              priority: 15,
            },
          ],
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:16666",
        changeOrigin: true,
        rewrite: (requestPath) => requestPath.replace(/^\/api/, ""),
      },
    },
  },
});
