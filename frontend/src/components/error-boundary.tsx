import { Component, type ErrorInfo, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("React 工作台渲染失败", error, errorInfo);
  }

  render() {
    if (!this.state.error) {
      return this.props.children;
    }

    return (
      <main className="grid min-h-svh place-items-center bg-background p-6">
        <section
          aria-labelledby="fatal-error-title"
          className="w-full max-w-md rounded-2xl border bg-card p-6 text-card-foreground"
          role="alert"
        >
          <p className="text-sm font-medium text-destructive">页面加载失败</p>
          <h1 className="mt-2 text-xl font-semibold" id="fatal-error-title">
            React 工作台遇到了未处理错误
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            旧版工作台没有被替换。可以刷新当前预览，或者返回旧版继续使用。
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            <Button onClick={() => window.location.reload()}>刷新预览</Button>
            <Button
              onClick={() => {
                window.location.href = "/";
              }}
              variant="outline"
            >
              返回旧版
            </Button>
          </div>
        </section>
      </main>
    );
  }
}
