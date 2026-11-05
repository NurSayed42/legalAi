import { Component, type ErrorInfo, type ReactNode } from "react";
import { ErrorState } from "@/components/ui/Display";

interface State {
  failed: boolean;
}

/** One broken tab, or a chunk that failed to download, shouldn't take the whole app down. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Tab error", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="mx-auto max-w-chat py-8">
        <ErrorState
          message="এই অংশটি দেখাতে সমস্যা হয়েছে। পেজটি আবার লোড করে দেখুন।"
          retryLabel="আবার লোড করুন"
          onRetry={() => window.location.reload()}
        />
      </div>
    );
  }
}
