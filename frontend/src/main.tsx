import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider, Navigate, Outlet } from "react-router-dom";
import { Layout } from "@/components/layout";
import { LoginPage } from "@/pages/login";
import { DashboardPage } from "@/pages/dashboard";
import { ContentPage } from "@/pages/content";
import { AutomationPage } from "@/pages/automation";
import { VideosPage } from "@/pages/videos";
import { AdminPage } from "@/pages/admin";
import { useAuth } from "@/lib/auth";
import "./index.css";

/** Protected route wrapper — redirects to /login if no token */
function ProtectedRoute() {
  const token = useAuth((s) => s.token);
  if (!token) return <Navigate to="/login" replace />;
  return <Outlet />;
}

/** Admin-only route wrapper — redirects to /dashboard if not admin */
function AdminRoute() {
  const user = useAuth((s) => s.user);
  if (!user?.is_admin) return <Navigate to="/dashboard" replace />;
  return <AdminPage />;
}

const router = createBrowserRouter([
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
          { path: "/automation", element: <AutomationPage /> },
          { path: "/videos", element: <VideosPage /> },
          { path: "/admin", element: <AdminRoute /> },
        ],
      },
    ],
  },
  { path: "/", element: <Navigate to="/dashboard" replace /> },
  { path: "*", element: <Navigate to="/dashboard" replace /> },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>
);
