import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider, Navigate, Outlet } from "react-router-dom";
import { Layout } from "@/components/layout";
import { LoginPage } from "@/pages/login";
import { DashboardPage } from "@/pages/dashboard";
import { ContentPage } from "@/pages/content";
import { JobsPage } from "@/pages/jobs";
import { AutomationPage } from "@/pages/automation";
import { VideosPage } from "@/pages/videos";
import { IdeasPage } from "@/pages/ideas";
import { AdminPage } from "@/pages/admin";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui";
import "./index.css";

const SSO_LOGIN_URL = "/id/login?redirect=/gpcg/dashboard";

/** Protected route wrapper — checks for user, calls /api/auth/me on mount.
 * If not authenticated, redirects to BI Identity login. */
function ProtectedRoute() {
  const user = useAuth((s) => s.user);
  const setUser = useAuth((s) => s.setUser);
  const logout = useAuth((s) => s.logout);
  const [checking, setChecking] = useState(!user);

  useEffect(() => {
    if (user) return; // already have user in store
    let cancelled = false;
    (async () => {
      try {
        const me = await api.getMe();
        if (!cancelled) {
          setUser(me);
          setChecking(false);
        }
      } catch {
        if (!cancelled) {
          logout();
          window.location.href = SSO_LOGIN_URL;
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user, setUser, logout]);

  if (checking) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg">
        <Spinner className="h-8 w-8" />
      </div>
    );
  }

  if (!user) return <Navigate to="/login" replace />;
  return <Outlet />;
}

/** Admin-only route wrapper — redirects to /dashboard if not admin */
function AdminRoute() {
  const user = useAuth((s) => s.user);
  if (!user?.is_admin) return <Navigate to="/dashboard" replace />;
  return <AdminPage />;
}

const basename = import.meta.env.PROD ? "/gpcg" : "/";

const router = createBrowserRouter(
  [
    {
      path: "/login",
      element: <LoginPage />,
    },
    {
      element: <ProtectedRoute />,
      children: [
        {
          element: <Layout />,
          children: [
            { path: "/dashboard", element: <DashboardPage /> },
            { path: "/content", element: <ContentPage /> },
            { path: "/jobs", element: <JobsPage /> },
            { path: "/automation", element: <AutomationPage /> },
            { path: "/videos", element: <VideosPage /> },
            { path: "/ideas", element: <IdeasPage /> },
            { path: "/admin", element: <AdminRoute /> },
          ],
        },
      ],
    },
    { path: "/", element: <Navigate to="/dashboard" replace /> },
    { path: "*", element: <Navigate to="/dashboard" replace /> },
  ],
  { basename },
);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>
);
