import { Component, ErrorInfo, ReactNode } from "react";

/**
 * ErrorBoundary — catches render errors (including the notorious
 * "removeChild NotFoundError" caused by browser translation extensions
 * like Google Translate that mutate the DOM out from under React).
 *
 * When an error is caught, shows a recovery UI with a "Reload" button
 * instead of crashing the whole page to a blank screen.
 */
interface State {
  hasError: boolean;
  error: Error | null;
}

interface Props {
  children: ReactNode;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("ErrorBoundary caught:", error, errorInfo);
  }

  handleReload = () => {
    this.setState({ hasError: false, error: null });
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-zinc-950 p-6">
          <div className="max-w-md space-y-4 text-center">
            <h1 className="text-xl font-bold text-zinc-100">
              Algo deu errado
            </h1>
            <p className="text-sm text-zinc-400">
              A página encontrou um erro inesperado. Isso pode ser causado por
              extensões do navegador (como tradutores). Tente recarregar.
            </p>
            {this.state.error && (
              <pre className="rounded-lg bg-zinc-900 p-3 text-left text-xs text-zinc-500 overflow-auto max-h-32">
                {this.state.error.message}
              </pre>
            )}
            <button
              onClick={this.handleReload}
              className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-white hover:bg-teal-500"
            >
              Recarregar página
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
