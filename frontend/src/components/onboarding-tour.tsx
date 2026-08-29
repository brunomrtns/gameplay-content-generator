import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
  X, ChevronRight, ChevronLeft, HelpCircle,
  Film, Settings, Lightbulb, Video, Check,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

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
  const [step, setStep] = useState(0);

  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;

  // Reset step to 0 whenever the tour opens
  useEffect(() => {
    if (open) {
      setStep(0);
    }
  }, [open]);

  // Navigate to the step's route when step changes
  useEffect(() => {
    if (open && current) {
      navigate(current.route);
    }
  }, [step, open, current, navigate]);

  // Close immediately and fire the API call in the background.
  // MUST call onClose() before any async work — onClose() sets
  // userDismissed.current=true in the hook, which prevents the
  // auto-reopen effect from re-triggering when user data refreshes.
  // Previously, updateUser({}) replaced the user with an empty object
  // (onboarding_completed=undefined → falsy), which triggered the
  // auto-reopen race before userDismissed was set. That's why
  // "Pular tutorial" and "Concluir" didn't close the modal.
  const handleClose = useCallback((completed: boolean) => {
    onClose();
    if (completed) {
      api.completeOnboarding().catch(() => {});
    }
  }, [onClose]);

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
        className="fixed inset-0 z-[60] bg-black/50 backdrop-blur-sm"
        onClick={handleSkip}
      />

      {/* Tour card — centered, above overlay */}
      <div
        className="fixed left-1/2 top-1/2 z-[61] w-[min(520px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2"
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
  // Prevents auto-reopen after user dismisses — survives user re-fetches
  // that still show onboarding_completed=false (race with backend update)
  const userDismissed = useRef(false);

  // Auto-open on first login (onboarding_completed = false)
  useEffect(() => {
    if (user && !user.onboarding_completed && !userDismissed.current) {
      // Small delay to let the dashboard render first
      const timer = setTimeout(() => setOpen(true), 800);
      return () => clearTimeout(timer);
    }
    // If onboarding is now completed, clear the dismissed flag
    // so it can auto-open again on a future session if reset
    if (user?.onboarding_completed) {
      userDismissed.current = false;
    }
  }, [user]);

  const close = useCallback(() => {
    userDismissed.current = true;
    setOpen(false);
  }, []);

  const reopen = useCallback(() => {
    // Reset onboarding on the server so it shows again next time too
    api.resetOnboarding().catch(() => {});
    userDismissed.current = false;
    setOpen(true);
  }, []);

  return { open, close, reopen };
}
