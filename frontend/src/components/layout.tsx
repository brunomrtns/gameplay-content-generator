import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import {
  LayoutDashboard, FileText, Settings, Video, Shield, LogOut, ChevronDown,
  Zap, ListChecks, Lightbulb, Baby, Film, HelpCircle, Globe, Check,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import { ssoLogout } from "@/lib/api";
import { useDomain } from "@/lib/domain-config";
import { Toaster } from "sonner";
import { UploadIndicator } from "@/components/upload-indicator";
import { OnboardingTour, useOnboardingTour } from "@/components/onboarding-tour";
import i18n from "@/i18n";

// Map icon names from domain config to lucide components
const ICON_MAP: Record<string, LucideIcon> = {
  LayoutDashboard,
  FileText,
  Settings,
  Video,
  Lightbulb,
  ListChecks,
  Baby,
  Film,
  Zap,
  Shield,
};

export function Layout() {
  const { user, logout } = useAuth();
  const { config } = useDomain();
  const { t, i18n: i18nInstance } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const tour = useOnboardingTour();

  const currentLang = i18nInstance.language;

  const handleLanguageChange = async (lng: string) => {
    await i18n.changeLanguage(lng);
    localStorage.setItem("gpcg-ui-language", lng);
    // Persist to backend
    try {
      await fetch("/api/auth/me/ui-language", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ui_language: lng }),
      });
    } catch {
      // Non-fatal — language already changed in UI
    }
  };

  const navItems = config.navigation.map((n) => ({
    ...n,
    Icon: ICON_MAP[n.icon] || LayoutDashboard,
  }));
  if (user?.is_admin) {
    navItems.push({ to: "/admin", label: "nav.admin", icon: "Shield", Icon: Shield });
  }

  const LogoIcon = ICON_MAP[config.theme.logoIcon] || Zap;

  const handleLogout = async () => {
    await ssoLogout();
    logout();
    window.location.href = "/id/login?redirect=/gpcg/dashboard";
  };

  return (
    <div className="min-h-screen bg-bg">
      {/* Fixed top header */}
      <header className="glass-strong sticky top-0 z-50 h-16 border-b border-border">
        <div className="mx-auto flex h-full max-w-7xl items-center justify-between px-6">
          {/* Logo */}
          <div className="flex items-center gap-2.5">
            <div className="relative flex h-8 w-8 items-center justify-center rounded-lg bg-surface-elevated border border-border">
              <LogoIcon className="h-4 w-4 text-accent" />
              <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-accent animate-pulse-glow" />
            </div>
            <span className="text-lg font-bold tracking-tight">
              {config.theme.appName}
            </span>
          </div>

          {/* Nav links */}
          <nav className="hidden items-center gap-1 md:flex">
            {navItems.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition-all duration-300",
                    isActive
                      ? "bg-accent/10 text-accent border border-accent/30"
                      : "text-text-secondary hover:text-text hover:bg-surface-hover border border-transparent"
                  )
                }
              >
                <n.Icon className="h-4 w-4" />
                {t(n.label)}
              </NavLink>
            ))}
          </nav>

          {/* Upload indicator (persistent across pages) */}
          <UploadIndicator />

          {/* Help button — reopens onboarding tour */}
          <button
            onClick={tour.reopen}
            title={t("tutorial")}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-surface text-text-muted hover:text-accent hover:border-accent/30 transition-all"
          >
            <HelpCircle className="h-4 w-4" />
          </button>

          {/* User menu */}
          <div className="relative">
            <button
              onClick={() => setMenuOpen(!menuOpen)}
              className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-1.5 text-sm transition-all hover:border-border-bright"
            >
              <div className="flex h-7 w-7 items-center justify-center rounded-full bg-accent/10 text-accent text-xs font-bold">
                {(user?.name || user?.email || "?").charAt(0).toUpperCase()}
              </div>
              <span className="hidden sm:block text-text-secondary max-w-[160px] truncate">
                {user?.name || user?.email}
              </span>
              <ChevronDown className="h-4 w-4 text-text-muted" />
            </button>
            {menuOpen && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setMenuOpen(false)} />
                <div className="absolute right-0 top-full mt-2 z-50 w-56 rounded-xl border border-border bg-surface-elevated shadow-2xl overflow-hidden">
                  <div className="px-4 py-3 border-b border-border">
                    <p className="text-sm font-medium truncate">{user?.name || "—"}</p>
                    <p className="text-xs text-text-muted truncate">{user?.email}</p>
                    {user?.is_admin && (
                      <span className="mt-1 inline-block rounded bg-accent-warm/10 text-accent-warm text-[10px] font-semibold px-1.5 py-0.5 border border-accent-warm/30">
                        {t("admin")}
                      </span>
                    )}
                  </div>
                  {/* Language toggle */}
                  <div className="px-4 py-3 border-b border-border">
                    <div className="flex items-center gap-2 mb-2 text-text-muted">
                      <Globe className="h-4 w-4" />
                      <span className="text-xs font-medium uppercase tracking-wide">{t("language")}</span>
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleLanguageChange("pt-BR")}
                        className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium transition-all ${
                          currentLang === "pt-BR"
                            ? "bg-accent/10 text-accent border border-accent/30"
                            : "text-text-secondary hover:text-text hover:bg-surface-hover border border-transparent"
                        }`}
                      >
                        {currentLang === "pt-BR" && <Check className="h-3 w-3" />}
                        {t("portuguese")}
                      </button>
                      <button
                        onClick={() => handleLanguageChange("en")}
                        className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium transition-all ${
                          currentLang === "en"
                            ? "bg-accent/10 text-accent border border-accent/30"
                            : "text-text-secondary hover:text-text hover:bg-surface-hover border border-transparent"
                        }`}
                      >
                        {currentLang === "en" && <Check className="h-3 w-3" />}
                        {t("english")}
                      </button>
                    </div>
                  </div>
                  <button
                    onClick={handleLogout}
                    className="flex w-full items-center gap-2 px-4 py-2.5 text-sm text-text-secondary hover:bg-surface-hover hover:text-red-400 transition-colors"
                  >
                    <LogOut className="h-4 w-4" />
                    {t("logout")}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </header>

      {/* Mobile nav */}
      <nav className="md:hidden flex items-center gap-1 overflow-x-auto border-b border-border bg-surface px-4 py-2">
        {navItems.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium whitespace-nowrap transition-all",
                isActive
                  ? "bg-accent/10 text-accent"
                  : "text-text-secondary hover:text-text hover:bg-surface-hover"
              )
            }
          >
            <n.Icon className="h-3.5 w-3.5" />
            {t(n.label)}
          </NavLink>
        ))}
      </nav>

      {/* Main content */}
      <main className="mx-auto max-w-7xl px-6 py-8">
        <Outlet />
      </main>

      <Toaster theme="dark" position="bottom-right" />

      {/* Onboarding tour overlay */}
      <OnboardingTour open={tour.open} onClose={tour.close} />
    </div>
  );
}
