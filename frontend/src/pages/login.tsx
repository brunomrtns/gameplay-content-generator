import { Zap, ArrowRight, LogIn } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui";

const SSO_LOGIN_URL = "/id/login?redirect=/gpcg/dashboard";

export function LoginPage() {
  const { t } = useTranslation();
  const handleSSO = () => {
    window.location.href = SSO_LOGIN_URL;
  };

  return (
    <div className="mesh-bg flex min-h-screen items-center justify-center bg-bg px-4">
      <div className="w-full max-w-sm animate-slide-up">
        {/* Logo */}
        <div className="mb-8 flex flex-col items-center gap-3">
          <div className="relative flex h-14 w-14 items-center justify-center rounded-2xl bg-surface-elevated border border-border">
            <Zap className="h-7 w-7 text-accent" />
            <span className="absolute -right-1 -top-1 h-3 w-3 rounded-full bg-accent animate-pulse-glow" />
          </div>
          <div className="text-center">
            <h1 className="text-2xl font-bold tracking-tight">{t("common:appName")}</h1>
            <p className="text-sm text-text-muted">{t("login:appFullName")}</p>
          </div>
        </div>

        {/* Card */}
        <div className="card-premium p-8">
          <h2 className="mb-1 text-lg font-semibold">{t("common:login")}</h2>
          <p className="mb-6 text-sm text-text-muted">
            {t("login:subtitle")}
          </p>

          <Button
            variant="primary"
            size="lg"
            className="w-full"
            onClick={handleSSO}
          >
            <span className="flex items-center gap-2">
              <LogIn className="h-4 w-4" />
              {t("login:loginViaSSO")}
              <ArrowRight className="h-4 w-4" />
            </span>
          </Button>
        </div>

        <p className="mt-6 text-center text-xs text-text-muted">
          {t("login:tagline")}
        </p>
      </div>
    </div>
  );
}
