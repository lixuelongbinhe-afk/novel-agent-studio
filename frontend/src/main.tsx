import React, { lazy, Suspense } from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import "./styles.css";
import { AppErrorBoundary } from "./components/AppErrorBoundary";
import { AppShell } from "./components/AppShell";

const HomePage = lazy(() => import("./pages/HomePage").then((module) => ({ default: module.HomePage })));
const StudioPage = lazy(() => import("./pages/StudioPage").then((module) => ({ default: module.StudioPage })));
const ModelsPage = lazy(() => import("./pages/ModelsPage").then((module) => ({ default: module.ModelsPage })));
const CustomApiPage = lazy(() => import("./pages/CustomApiPage").then((module) => ({ default: module.CustomApiPage })));

function deferred(element: React.ReactNode) {
  return <Suspense fallback={<div className="route-loading">正在打开...</div>}>{element}</Suspense>;
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
      { path: "/advanced-api", element: deferred(<CustomApiPage />) }
    ]
  }
]);

document.documentElement.dataset.theme = "dark";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </AppErrorBoundary>
  </React.StrictMode>
);
