import { defineConfig } from "@playwright/test";

const runtimeProcess = (globalThis as {
  process?: { env?: Record<string, string | undefined> };
}).process;
const reuseExistingServer = runtimeProcess?.env?.PLAYWRIGHT_REUSE_SERVERS === "1";

export default defineConfig({
  testDir: "./src/test/e2e",
  fullyParallel: false,
  use: {
    baseURL: "http://127.0.0.1:5174",
    trace: "retain-on-failure",
    screenshot: "only-on-failure"
  },
  webServer: [
    {
      command: "..\\backend\\.venv\\Scripts\\python.exe ..\\scripts\\fake_provider_server.py",
      url: "http://127.0.0.1:8020/health",
      reuseExistingServer,
      timeout: 120000
    },
    {
      command: "..\\backend\\.venv\\Scripts\\python.exe ..\\scripts\\e2e_server.py",
      url: "http://127.0.0.1:8010/health",
      reuseExistingServer,
      timeout: 120000
    },
    {
      command: "node node_modules/vite/bin/vite.js --mode e2e --host 127.0.0.1 --port 5174",
      url: "http://127.0.0.1:5174",
      reuseExistingServer,
      timeout: 120000
    }
  ]
});
