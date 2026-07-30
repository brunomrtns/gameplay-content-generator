import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Zap, Mail, Lock, ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button, Input, Label } from "@/components/ui";
import { toast } from "sonner";

export function LoginPage() {
  const navigate = useNavigate();
  const { setAuth } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password) return;
    setLoading(true);
    try {
      const r = await api.login(email.trim(), password);
      setAuth(r.token, r.user);
      toast.success(`Bem-vindo, ${r.user.name || r.user.email}`);
      navigate("/dashboard");
    } catch (err: any) {
      toast.error(err.message || "Falha ao entrar");
    } finally {
      setLoading(false);
    }
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
            <h1 className="text-2xl font-bold tracking-tight">GPCG</h1>
            <p className="text-sm text-text-muted">Gameplay Content Generator</p>
          </div>
        </div>

        {/* Card */}
        <div className="card-premium p-8">
          <h2 className="mb-1 text-lg font-semibold">Entrar</h2>
          <p className="mb-6 text-sm text-text-muted">Acesse o painel de automação</p>

          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <Label>Email</Label>
              <div className="relative">
                <Mail className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
                <Input
                  type="email"
                  value={email}
                  onChange={setEmail}
                  placeholder="voce@email.com"
                  className="pl-11"
                  disabled={loading}
                />
              </div>
            </div>

            <div>
              <Label>Senha</Label>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
                <Input
                  type="password"
                  value={password}
                  onChange={setPassword}
                  placeholder="••••••••"
                  className="pl-11"
                  disabled={loading}
                />
              </div>
            </div>

            <Button type="submit" variant="primary" size="lg" className="w-full" disabled={loading}>
              {loading ? (
                <span className="flex items-center gap-2">
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  Entrando...
                </span>
              ) : (
                <span className="flex items-center gap-2">
                  Entrar
                  <ArrowRight className="h-4 w-4" />
                </span>
              )}
            </Button>
          </form>
        </div>

        <p className="mt-6 text-center text-xs text-text-muted">
          GPCG · Automação de conteúdo para criadores
        </p>
      </div>
    </div>
  );
}
