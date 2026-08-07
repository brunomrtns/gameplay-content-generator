import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  // Base path: /gpcg/ in production (served via nginx), / in dev
  base: process.env.NODE_ENV === "production" ? "/gpcg/" : "/",
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8787",
      "/catalog-api": {
        target: "http://127.0.0.1:8788",
        rewrite: (p) => p.replace("/catalog-api", "/api"),
      },
    },
  },
});
