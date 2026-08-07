import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  /** Rendered instead of the default panel when something throws. */
  fallback?: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Catches render errors and shows a recoverable panel instead of a blank page.
 *
 * Spec section 10 requires an error state for every async operation and an
 * error boundary at page level. React has no hook equivalent — only a class
 * component can implement `getDerivedStateFromError` — so this stays a class.
 *
 * Deliberately not wired to an error reporting service: spec 6.5 keeps the
 * Garmin linking form away from third-party collectors, and a boundary that
 * shipped rendered props to an external service would be a way around that.
 */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // The console is the whole reporting story for now. Logging the component
    // stack alongside the error is what makes a production stack trace from a
    // minified bundle readable at all.
    console.error('Nezachycená chyba v komponentě:', error, info.componentStack);
  }

  private readonly handleReset = (): void => {
    this.setState({ error: null });
  };

  override render(): ReactNode {
    const { error } = this.state;
    if (error === null) return this.props.children;
    if (this.props.fallback !== undefined) return this.props.fallback;

    return (
      <div role="alert" className="mx-auto max-w-lg p-6 text-center">
        <h1 className="text-xl font-semibold">Něco se pokazilo</h1>
        <p className="mt-2 text-slate-600 dark:text-slate-400">
          Stránku se nepodařilo zobrazit. Zkus to prosím znovu.
        </p>
        <button
          type="button"
          onClick={this.handleReset}
          className="bg-brand-600 hover:bg-brand-700 mt-6 rounded-md px-4 py-2 font-medium text-white"
        >
          Zkusit znovu
        </button>
      </div>
    );
  }
}
