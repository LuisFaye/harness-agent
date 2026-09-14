import { BrowserRouter } from "react-router-dom";

import { AppProviders } from "@/app/providers";
import { AppRouter } from "@/app/router";
import { ErrorBoundary } from "@/components/error-boundary";

export function App() {
  return (
    <ErrorBoundary>
      <AppProviders>
        <BrowserRouter>
          <AppRouter />
        </BrowserRouter>
      </AppProviders>
    </ErrorBoundary>
  );
}
