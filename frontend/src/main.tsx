import React, { lazy, Suspense } from "react";
import ReactDOM from "react-dom/client";
import { MotionConfig } from "framer-motion";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AppErrorBoundary } from "./components/AppErrorBoundary";
import { AppShell } from "./components/AppShell";
import { Spinner } from "./components/Spinner";
import { localApiTokenReady } from "./api/localAuth";
import "./styles.css";
import "./design-v2.css";
import "./apple-design.css";

const HomePage = lazy(() => import("./pages/HomePage").then((module) => ({ default: module.HomePage })));
const StudioPage = lazy(() => import("./pages/StudioPage").then((module) => ({ default: module.StudioPage })));
const ModelsPage = lazy(() => import("./pages/ModelsPage").then((module) => ({ default: module.ModelsPage })));
const CustomApiPage = lazy(() => import("./pages/CustomApiPage").then((module) => ({ default: module.CustomApiPage })));
const ApprovalPage = lazy(() => import("./pages/ApprovalPage").then((module) => ({ default: module.ApprovalPage })));
const WorkspacePage = lazy(() => import("./pages/WorkspacePage").then((module) => ({ default: module.WorkspacePage })));
const ReleasePage = lazy(() => import("./pages/ReleasePage").then((module) => ({ default: module.ReleasePage })));
const ContextPage = lazy(() => import("./pages/ContextPage").then((module) => ({ default: module.ContextPage })));
const LibraryPage = lazy(() => import("./pages/LibraryPage").then((module) => ({ default: module.LibraryPage })));
const RecoveryPage = lazy(() => import("./pages/RecoveryPage").then((module) => ({ default: module.RecoveryPage })));
const AgentWorkflowPage = lazy(() => import("./pages/AgentWorkflowPage").then((module) => ({ default: module.AgentWorkflowPage })));
const ModelCenterPage = lazy(() => import("./pages/ModelCenterPage").then((module) => ({ default: module.ModelCenterPage })));

function deferred(element: React.ReactNode) {
  return <Suspense fallback={<Spinner />}>{element}</Suspense>;
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 2_000, retry: 1, refetchOnWindowFocus: false },
    mutations: { retry: 0 }
  }
});

const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { path: "/", element: deferred(<HomePage />) },
      { path: "/studio/:projectId", element: deferred(<StudioPage />) },
      { path: "/models", element: deferred(<ModelsPage />) },
      { path: "/advanced-api", element: deferred(<CustomApiPage />) },
      { path: "/approvals", element: deferred(<ApprovalPage />) },
      { path: "/workspace", element: deferred(<WorkspacePage />) },
      { path: "/release", element: deferred(<ReleasePage />) },
      { path: "/context", element: deferred(<ContextPage />) },
      { path: "/library", element: deferred(<LibraryPage />) },
      { path: "/recovery", element: deferred(<RecoveryPage />) },
      { path: "/workflows", element: deferred(<AgentWorkflowPage />) },
      { path: "/model-center", element: deferred(<ModelCenterPage />) }
    ]
  }
]);

try {
  const persisted = JSON.parse(localStorage.getItem("novel-agent-studio-ui") ?? "null") as { state?: { theme?: "light" | "dark" } } | null;
  document.documentElement.dataset.theme = persisted?.state?.theme ?? "dark";
} catch {
  document.documentElement.dataset.theme = "dark";
}

void localApiTokenReady.then(() => {
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <AppErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <MotionConfig reducedMotion="user">
            <RouterProvider router={router} />
          </MotionConfig>
        </QueryClientProvider>
      </AppErrorBoundary>
    </React.StrictMode>
  );
});
