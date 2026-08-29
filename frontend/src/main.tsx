import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider, Navigate, Outlet } from "react-router-dom";
import { Layout } from "@/components/layout";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { LoginPage } from "@/pages/login";
import { LandingPage } from "@/pages/landing";
import { DashboardPage } from "@/pages/dashboard";
import { ContentPage } from "@/pages/content";
import { JobsPage } from "@/pages/jobs";
import { AutomationPage } from "@/pages/automation";
import { VideosPage } from "@/pages/videos";
import { IdeasPage } from "@/pages/ideas";
import { AdminPage } from "@/pages/admin";
import { KidsPage } from "@/pages/kids";
import { KidsIdeasPage } from "@/pages/kids-ideas";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { DomainProvider } from "@/lib/domain-config";
import { QueryProvider } from "@/lib/query-provider";
import { EventsProvider } from "@/hooks/useEvents";
import { Spinner } from "@/components/ui";
import "./index.css";

const SSO_LOGIN_URL = "/id/login?redirect=/gpcg/dashboard";

/** Landing page redirect — if user is logged in, go to dashboard.
 * Otherwise show the public landing page. */
function LandingRedirect() {
  const user = useAuth((s) => s.user);
  if (user) return <Navigate to="/dashboard" replace />;
  return <LandingPage />;
}

/** Protected route wrapper — validates session on every mount/refresh.
 * Calls /api/auth/me to verify the SSO cookie is still valid, even if
 * the user is in localStorage. This prevents stale sessions after refresh
 * when the bi_auth cookie has expired (15min) but localStorage still has
 * the user object. */
function ProtectedRoute() {
  const user = useAuth((s) => s.user);
  const setUser = useAuth((s) => s.setUser);
  const logout = useAuth((s) => s.logout);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
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
  }, [setUser, logout]);

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
          element: <DomainProvider><Layout /></DomainProvider>,
          children: [
            { path: "/dashboard", element: <DashboardPage /> },
            { path: "/content", element: <ContentPage /> },
            { path: "/kids", element: <KidsPage /> },
            { path: "/kids-ideas", element: <KidsIdeasPage /> },
            { path: "/jobs", element: <JobsPage /> },
            { path: "/automation", element: <AutomationPage /> },
            { path: "/videos", element: <VideosPage /> },
            { path: "/ideas", element: <IdeasPage /> },
            { path: "/admin", element: <AdminRoute /> },
          ],
        },
      ],
    },
    { path: "/", element: <LandingRedirect /> },
    { path: "*", element: <Navigate to="/" replace /> },
  ],
  { basename },
);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryProvider>
        <EventsProvider>
          <RouterProvider router={router} />
        </EventsProvider>
      </QueryProvider>
    </ErrorBoundary>
  </StrictMode>
);
