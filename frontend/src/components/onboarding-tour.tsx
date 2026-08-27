import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  X, ChevronRight, ChevronLeft, HelpCircle,
  Film, Settings, Lightbulb, Video, Check,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { toast } from "sonner";

interface TourStep {
  route: string;
  icon: typeof Film;
  title: string;
  description: string;
  action?: { label: string; route: string };
}

const STEPS: TourStep[] = [
  {
    route: "/content",
    icon: Film,
    title: "Conteúdo & Identidade do Canal",
    description:
      "Aqui você envia suas gravações de gameplay e configura o perfil do canal — nicho, público-alvo, tom de voz e narrativa. A IA usa essas informações para personalizar os roteiros.",
    action: { label: "Configurar canal", route: "/content" },
  },
  {
    route: "/automation",
    icon: Settings,
    title: "Automação",
    description:
      "Configure como seus vídeos serão gerados: formato (vertical/horizontal), voz da narração (TTS), legendas, transições e estilo criativo. Você só precisa fazer isso uma vez.",
    action: { label: "Configurar automação", route: "/automation" },
  },
  {
    route: "/ideas",
    icon: Lightbulb,
    title: "Ideias & Fila de Jobs",
    description:
      "A IA gera ideias de conteúdo automaticamente. Quando você aprova uma ideia, ela entra na fila de processamento (jobs) e o worker gera o vídeo.",
    action: { label: "Ver ideias", route: "/ideas" },
  },
  {
    route: "/jobs",
    icon: Lightbulb,
    title: "Jobs",
    description:
      "Aqui você acompanha o progresso de cada vídeo sendo processado — do mapeamento do gameplay até a renderização final.",
  },
  {
    route: "/videos",
    icon: Video,
    title: "Seus Vídeos",
    description:
      "Quando os vídeos estão prontos, eles aparecem aqui. Você pode publicar diretamente no YouTube, compartilhar ou baixar.",
    action: { label: "Ver vídeos", route: "/videos" },
  },
];

interface OnboardingTourProps {
  open: boolean;
  onClose: () => void;
}

export function OnboardingTour({ open, onClose }: OnboardingTourProps) {
  const navigate = useNavigate();
  const { updateUser } = useAuth();
  const [step, setStep] = useState(0);
  const [closing, setClosing] = useState(false);

  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;

  // Navigate to the step's route when step changes
  useEffect(() => {
    if (open && current) {
      navigate(current.route);
    }
  }, [step, open, current, navigate]);

  const handleClose = useCallback(async (completed: boolean) => {
    setClosing(true);
    try {
      if (completed) {
        await api.completeOnboarding();
        updateUser({} as any); // trigger re-fetch via ProtectedRoute
      }
    } catch {
      // non-critical
    }
    setTimeout(() => {
      setClosing(false);
      onClose();
    }, 200);
  }, [onClose, updateUser]);

  const handleNext = () => {
    if (isLast) {
      handleClose(true);
    } else {
      setStep(step + 1);
    }
  };

  const handlePrev = () => {
    if (step > 0) setStep(step - 1);
  };

  const handleSkip = () => handleClose(true);

  if (!open || !current) return null;

  const Icon = current.icon;

  return (
    <>
      {/* Overlay — semi-transparent, doesn't block clicks */}
      <div
        className={cn(
          "fixed inset-0 z-[60] bg-black/50 backdrop-blur-sm transition-opacity duration-200",
          closing ? "opacity-0" : "opacity-100"
        )}
        onClick={handleSkip}
      />

      {/* Tour card — centered, above overlay */}
      <div
        className={cn(
          "fixed left-1/2 top-1/2 z-[61] w-[min(520px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 transition-all duration-200",
          closing ? "scale-95 opacity-0" : "scale-100 opacity-100"
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="glass-strong rounded-2xl border border-border shadow-2xl overflow-hidden">
          {/* Header with progress */}
          <div className="flex items-center justify-between px-6 pt-5 pb-3">
            <div className="flex items-center gap-2">
              <HelpCircle className="h-5 w-5 text-accent" />
              <span className="text-sm font-semibold text-text-secondary">
                Tutorial · Passo {step + 1} de {STEPS.length}
              </span>
            </div>
            <button
              onClick={handleSkip}
              className="rounded-lg p-1.5 text-text-muted hover:bg-surface-hover hover:text-text transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Progress bar */}
          <div className="px-6 pb-3">
            <div className="h-1 w-full overflow-hidden rounded-full bg-surface-hover">
              <div
                className="h-full rounded-full bg-accent transition-all duration-300"
                style={{ width: `${((step + 1) / STEPS.length) * 100}%` }}
              />
            </div>
          </div>

          {/* Content */}
          <div className="px-6 pb-6 pt-2">
            <div className="flex items-start gap-4">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-accent/10 border border-accent/30">
                <Icon className="h-6 w-6 text-accent" />
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="text-lg font-bold text-text">
                  {current.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-text-secondary">
                  {current.description}
                </p>
              </div>
            </div>

            {/* Action button (optional) */}
            {current.action && (
              <button
                onClick={() => navigate(current.action!.route)}
                className="mt-4 w-full rounded-xl border border-accent/30 bg-accent/10 px-4 py-2.5 text-sm font-medium text-accent transition-all hover:bg-accent/20"
              >
                {current.action.label}
              </button>
            )}
          </div>

          {/* Footer with navigation */}
          <div className="flex items-center justify-between border-t border-border px-6 py-4">
            <button
              onClick={handleSkip}
              className="text-sm text-text-muted hover:text-text transition-colors"
            >
              Pular tutorial
            </button>
            <div className="flex items-center gap-2">
              {step > 0 && (
                <button
                  onClick={handlePrev}
                  className="flex items-center gap-1 rounded-lg border border-border px-3 py-2 text-sm text-text-secondary hover:bg-surface-hover transition-colors"
                >
                  <ChevronLeft className="h-4 w-4" />
                  Voltar
                </button>
              )}
              <button
                onClick={handleNext}
                className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white transition-all hover:bg-accent-bright"
              >
                {isLast ? (
                  <>
                    <Check className="h-4 w-4" />
                    Concluir
                  </>
                ) : (
                  <>
                    Próximo
                    <ChevronRight className="h-4 w-4" />
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

/** Hook to manage onboarding tour state */
export function useOnboardingTour() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);

  // Auto-open on first login (onboarding_completed = false)
  useEffect(() => {
    if (user && !user.onboarding_completed) {
      // Small delay to let the dashboard render first
      const timer = setTimeout(() => setOpen(true), 800);
      return () => clearTimeout(timer);
    }
  }, [user]);

  const close = useCallback(() => setOpen(false), []);
  const reopen = useCallback(() => {
    // Reset onboarding on the server so it shows again next time too
    api.resetOnboarding().catch(() => {});
    setOpen(true);
  }, []);

  return { open, close, reopen };
}
