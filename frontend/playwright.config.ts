import { defineConfig } from "@playwright/test";

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
      reuseExistingServer: false,
      timeout: 120000
    },
    {
      command: "..\\backend\\.venv\\Scripts\\python.exe ..\\scripts\\e2e_server.py",
      url: "http://127.0.0.1:8010/health",
      reuseExistingServer: false,
      timeout: 120000
    },
    {
      command: "node node_modules/vite/bin/vite.js --mode e2e --host 127.0.0.1 --port 5174",
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
      timeout: 120000
    }
  ]
});
