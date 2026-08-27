import { useEffect, useState } from "react";
import {
  Zap,
  ArrowRight,
  LogIn,
  Smartphone,
  Download,
  Video,
  Sparkles,
  Youtube,
  Bot,
  CheckCircle2,
  ChevronDown,
} from "lucide-react";
import { Button } from "@/components/ui";

const SSO_LOGIN_URL = "/id/login?redirect=/gpcg/dashboard";
const APP_VERSION_URL = "/gpcg/api/app/version";
const APP_DOWNLOAD_URL = "/gpcg/api/app/download";

interface AppVersionInfo {
  available: boolean;
  version: string | null;
  versionCode: number | null;
  released_at: string | null;
  changelog: string | null;
  size_bytes: number | null;
}

export function LandingPage() {
  const [appInfo, setAppInfo] = useState<AppVersionInfo | null>(null);

  useEffect(() => {
    fetch(APP_VERSION_URL)
      .then((r) => r.json())
      .then((data) => setAppInfo(data))
      .catch(() => {});
  }, []);

  const handleLogin = () => {
    window.location.href = SSO_LOGIN_URL;
  };

  const handleDownload = () => {
    window.location.href = APP_DOWNLOAD_URL;
  };

  const apkSizeMB = appInfo?.size_bytes
    ? Math.round(appInfo.size_bytes / 1048576)
    : null;

  const releasedDate = appInfo?.released_at
    ? new Date(appInfo.released_at).toLocaleDateString("pt-BR", {
        day: "2-digit",
        month: "long",
        year: "numeric",
      })
    : null;

  return (
    <div className="mesh-bg min-h-screen bg-bg">
      {/* ── Nav bar ───────────────────────────────────────────────────── */}
      <nav className="sticky top-0 z-50 border-b border-border/50 bg-bg/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="relative flex h-10 w-10 items-center justify-center rounded-xl bg-surface-elevated border border-border">
              <Zap className="h-5 w-5 text-accent" />
              <span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-accent animate-pulse-glow" />
            </div>
            <div>
              <h1 className="text-base font-bold tracking-tight">GPCG</h1>
              <p className="text-[10px] text-text-muted leading-none">
                Gameplay Content Generator
              </p>
            </div>
          </div>
          <Button variant="primary" size="sm" onClick={handleLogin}>
            <LogIn className="h-4 w-4" />
            Entrar
          </Button>
        </div>
      </nav>

      {/* ── Hero ──────────────────────────────────────────────────────── */}
      <section className="relative overflow-hidden">
        <div className="mx-auto max-w-6xl px-6 py-20 md:py-32">
          <div className="flex flex-col items-center text-center gap-6">
            <div className="inline-flex items-center gap-2 rounded-full border border-border bg-surface/50 px-4 py-1.5 text-xs text-text-secondary">
              <Sparkles className="h-3.5 w-3.5 text-accent" />
              Automação completa de conteúdo para criadores
            </div>

            <h2 className="max-w-3xl text-4xl font-bold tracking-tight md:text-6xl">
              Transforme gameplay em{" "}
              <span className="text-accent">vídeos prontos</span> para o YouTube
            </h2>

            <p className="max-w-2xl text-lg text-text-secondary">
              Geração automatizada de shorts: análise de gameplay com IA,
              roteiros editoriais, narração TTS, legendas, e publicação direto
              no YouTube. Tudo em um painel só.
            </p>

            <div className="flex flex-col sm:flex-row gap-3 mt-4">
              <Button variant="primary" size="lg" onClick={handleLogin}>
                <LogIn className="h-4 w-4" />
                Acessar painel
                <ArrowRight className="h-4 w-4" />
              </Button>
              <a href="#download-app">
                <Button variant="outline" size="lg">
                  <Smartphone className="h-4 w-4" />
                  Baixar app mobile
                </Button>
              </a>
            </div>

            <div className="mt-12 animate-bounce-slow">
              <ChevronDown className="h-6 w-6 text-text-muted" />
            </div>
          </div>
        </div>
      </section>

      {/* ── Features ──────────────────────────────────────────────────── */}
      <section className="border-t border-border/50">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <h3 className="mb-12 text-center text-2xl font-bold">
            O que o GPCG faz
          </h3>
          <div className="grid gap-6 md:grid-cols-3">
            <FeatureCard
              icon={<Video className="h-6 w-6 text-accent" />}
              title="Análise de Gameplay"
              desc="VLM + ASR mapeiam automaticamente os melhores momentos dos seus gameplays. Score de interesse, transcrição, e seleção de cenas."
            />
            <FeatureCard
              icon={<Bot className="h-6 w-6 text-accent" />}
              title="Roteiros com IA"
              desc="Pipeline editorial completo: curiosidade, story finding, creative engine, humanização, e crítica de script. Anti-plágio integrado."
            />
            <FeatureCard
              icon={<Youtube className="h-6 w-6 text-accent" />}
              title="Publicação no YouTube"
              desc="OAuth por usuário. Upload automático com título, descrição, tags e thumbnail gerados por IA. Controle total de cada publicação."
            />
          </div>
        </div>
      </section>

      {/* ── How it works ──────────────────────────────────────────────── */}
      <section className="border-t border-border/50 bg-surface/30">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <h3 className="mb-12 text-center text-2xl font-bold">
            Como funciona
          </h3>
          <div className="grid gap-8 md:grid-cols-4">
            <StepCard
              num="1"
              title="Upload"
              desc="Envie seus gameplays pelo painel web ou app mobile."
            />
            <StepCard
              num="2"
              title="Mapeamento"
              desc="O worker com GPU analisa o gameplay e encontra os melhores trechos."
            />
            <StepCard
              num="3"
              title="Geração"
              desc="Roteiro, narração, legendas, e renderização — tudo automático."
            />
            <StepCard
              num="4"
              title="Publicação"
              desc="Revise, edite metadados, e publique direto no YouTube."
            />
          </div>
        </div>
      </section>

      {/* ── App Download ──────────────────────────────────────────────── */}
      <section
        id="download-app"
        className="border-t border-border/50 scroll-mt-20"
      >
        <div className="mx-auto max-w-6xl px-6 py-20">
          <div className="grid gap-12 md:grid-cols-2 md:items-center">
            {/* Left: info */}
            <div className="flex flex-col gap-6">
              <div className="inline-flex w-fit items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-4 py-1.5 text-xs text-accent">
                <Smartphone className="h-3.5 w-3.5" />
                App Mobile
              </div>

              <h3 className="text-3xl font-bold tracking-tight">
                GPCG no seu bolso
              </h3>

              <p className="text-text-secondary">
                Baixe o app e gerencie seus vídeos de qualquer lugar. Acesse o
                dashboard, acompanhe jobs, faça upload de gameplays, edite
                metadados, e compartilhe nas redes sociais — tudo do celular.
              </p>

              <ul className="flex flex-col gap-3">
                {[
                  "Dashboard com status do worker em tempo real",
                  "Upload de gameplays direto do celular",
                  "Compartilhamento nativo (Instagram, TikTok, etc.)",
                  "Notificação automática de atualizações",
                  "Funciona offline (cache local de vídeos)",
                ].map((feat) => (
                  <li
                    key={feat}
                    className="flex items-center gap-3 text-sm text-text-secondary"
                  >
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-accent" />
                    {feat}
                  </li>
                ))}
              </ul>

              {/* Download button */}
              <div className="mt-2 flex flex-col gap-3">
                {appInfo?.available ? (
                  <Button
                    variant="primary"
                    size="lg"
                    onClick={handleDownload}
                    className="w-fit"
                  >
                    <Download className="h-5 w-5" />
                    Baixar APK
                    {appInfo.version && (
                      <span className="ml-1 text-xs opacity-80">
                        v{appInfo.version}
                      </span>
                    )}
                  </Button>
                ) : (
                  <div className="w-fit rounded-xl border border-border bg-surface px-5 py-3 text-sm text-text-muted">
                    App disponível em breve
                  </div>
                )}

                {/* App metadata */}
                {appInfo?.available && (
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
                    {apkSizeMB && <span>{apkSizeMB} MB</span>}
                    {releasedDate && <span>Atualizado em {releasedDate}</span>}
                    <span>Android 8.0+</span>
                  </div>
                )}
              </div>
            </div>

            {/* Right: phone mockup */}
            <div className="flex items-center justify-center">
              <div className="relative">
                {/* Phone frame */}
                <div className="relative h-[500px] w-[250px] rounded-[2.5rem] border-4 border-border-bright bg-surface-elevated p-3 shadow-2xl">
                  {/* Notch */}
                  <div className="absolute left-1/2 top-0 h-6 w-32 -translate-x-1/2 rounded-b-2xl bg-border-bright" />

                  {/* Screen */}
                  <div className="flex h-full w-full flex-col items-center justify-center gap-4 rounded-[2rem] bg-bg">
                    <div className="relative flex h-20 w-20 items-center justify-center rounded-2xl bg-surface-elevated border border-border">
                      <Zap className="h-10 w-10 text-accent" />
                      <span className="absolute -right-1 -top-1 h-3 w-3 rounded-full bg-accent animate-pulse-glow" />
                    </div>
                    <div className="text-center">
                      <p className="text-lg font-bold tracking-tight">GPCG</p>
                      <p className="text-xs text-text-muted">
                        Gameplay Content Generator
                      </p>
                    </div>
                    <div className="flex flex-col gap-2 w-full px-6">
                      <div className="h-8 rounded-lg bg-surface border border-border" />
                      <div className="h-8 rounded-lg bg-surface border border-border" />
                      <div className="h-8 rounded-lg bg-surface border border-border" />
                    </div>
                    <div className="mt-auto h-12 w-full rounded-t-xl bg-surface border-t border-border flex items-center justify-around">
                      <div className="h-5 w-5 rounded bg-text-muted/30" />
                      <div className="h-5 w-5 rounded bg-text-muted/30" />
                      <div className="h-5 w-5 rounded bg-accent/50" />
                      <div className="h-5 w-5 rounded bg-text-muted/30" />
                      <div className="h-5 w-5 rounded bg-text-muted/30" />
                    </div>
                  </div>
                </div>

                {/* Glow */}
                <div className="absolute -inset-4 -z-10 rounded-full bg-accent/10 blur-3xl" />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── CTA ───────────────────────────────────────────────────────── */}
      <section className="border-t border-border/50">
        <div className="mx-auto max-w-4xl px-6 py-20 text-center">
          <h3 className="mb-4 text-3xl font-bold tracking-tight">
            Pronto para automatizar?
          </h3>
          <p className="mb-8 text-text-secondary">
            Acesse o painel e comece a gerar conteúdo agora mesmo.
          </p>
          <Button variant="primary" size="lg" onClick={handleLogin}>
            <LogIn className="h-4 w-4" />
            Entrar no GPCG
            <ArrowRight className="h-4 w-4" />
          </Button>
        </div>
      </section>

      {/* ── Footer ────────────────────────────────────────────────────── */}
      <footer className="border-t border-border/50">
        <div className="mx-auto max-w-6xl px-6 py-8">
          <div className="flex flex-col items-center justify-between gap-4 sm:flex-row">
            <div className="flex items-center gap-2 text-sm text-text-muted">
              <Zap className="h-4 w-4 text-accent" />
              GPCG · Gameplay Content Generator
            </div>
            <p className="text-xs text-text-muted">
              © {new Date().getFullYear()} Bruno Integrations
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}

function FeatureCard({
  icon,
  title,
  desc,
}: {
  icon: React.ReactNode;
  title: string;
  desc: string;
}) {
  return (
    <div className="card-premium p-6 transition-all duration-300 hover:border-accent/30">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-accent/10 border border-accent/20">
        {icon}
      </div>
      <h4 className="mb-2 text-lg font-semibold">{title}</h4>
      <p className="text-sm text-text-secondary leading-relaxed">{desc}</p>
    </div>
  );
}

function StepCard({
  num,
  title,
  desc,
}: {
  num: string;
  title: string;
  desc: string;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent text-white font-bold text-lg">
        {num}
      </div>
      <h4 className="font-semibold">{title}</h4>
      <p className="text-sm text-text-secondary leading-relaxed">{desc}</p>
    </div>
  );
}
