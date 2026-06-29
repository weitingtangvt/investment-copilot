import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  define: {
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
  build: {
    outDir: path.resolve(__dirname, "../static/vendor/candleview-lab"),
    emptyOutDir: false,
    lib: {
      entry: path.resolve(__dirname, "src/candleview_lab.jsx"),
      name: "CandleViewLab",
      formats: ["iife"],
      fileName: () => "candleview_lab.js",
    },
  },
});
