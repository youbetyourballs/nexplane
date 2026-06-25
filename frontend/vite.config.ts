import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendTarget = process.env.VITE_API_URL || "http://localhost:8000";

const backendPaths = [
  "/auth",
  "/assets",
  "/connectors",
  "/projects",
  "/change-requests",
  "/runbooks",
  "/notifications",
  "/onboarding",
  "/compliance",
  "/remediation",
  "/access-reviews",
  "/scheduled-operations",
  "/maintenance-windows",
  "/backup-recovery",
  "/infrastructure-memory",
  "/impact-simulation",
  "/recommendations",
  "/demo",
  "/smoke-tests",
  "/agent",
  "/api",
];

const proxyEntries = Object.fromEntries(
  backendPaths.map((path) => [
    path,
    { target: backendTarget, changeOrigin: true },
  ])
);

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 3000,
    proxy: proxyEntries,
  },
});
