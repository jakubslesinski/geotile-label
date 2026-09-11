import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // Bez jawnego hosta Vite nasłuchuje tylko na ::1. WebView2 rozwiązuje wtedy
    // `localhost` raz na IPv6, raz na IPv4 i nawigacja między stronami pomocy
    // kończy się ERR_CONNECTION_REFUSED. Trzymamy cały tryb dev na IPv4.
    host: "127.0.0.1",
    port: 3000,
    proxy: {
      "/api": {
        target: process.env.VITE_BACKEND_DEV_URL || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/data": {
        target: process.env.VITE_BACKEND_DEV_URL || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
