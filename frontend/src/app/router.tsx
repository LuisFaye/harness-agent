import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/app-shell";
import { AuthGate, LoginPage, OwnerGate, RegisterPage, SetupPage } from "@/features/auth/auth-pages";

const AgentPage = lazy(async () => ({
  default: (await import("@/features/agent/agent-page")).AgentPage,
}));
const RunsPage = lazy(async () => ({
  default: (await import("@/features/runs/runs-page")).RunsPage,
}));
const SchedulesPage = lazy(async () => ({
  default: (await import("@/features/schedules/schedules-page")).SchedulesPage,
}));
const CapabilitiesPage = lazy(async () => ({
  default: (await import("@/features/capabilities/capabilities-page")).CapabilitiesPage,
}));
const SettingsPage = lazy(async () => ({
  default: (await import("@/features/settings/settings-page")).SettingsPage,
}));
const MembersPage = lazy(async () => ({
  default: (await import("@/features/auth/members-page")).MembersPage,
}));
const SharedSessionsPage = lazy(async () => ({
  default: (await import("@/features/sharing/shared-sessions-page")).SharedSessionsPage,
}));
const CitationDemoPage = lazy(async () => ({
  default: (await import("@/features/citations/citation-demo-page")).CitationDemoPage,
}));

export function AppRouter() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/setup" element={<SetupPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/citation-demo" element={<Suspense fallback={<PageLoading />}><CitationDemoPage /></Suspense>} />
      <Route element={<AuthGate><AppShell /></AuthGate>}>
        <Route index element={<Navigate replace to="/agent" />} />
        <Route path="/agent" element={<Suspense fallback={<PageLoading />}><AgentPage /></Suspense>} />
        <Route path="/agent/:sessionId" element={<Suspense fallback={<PageLoading />}><AgentPage /></Suspense>} />
        <Route path="/runs" element={<Suspense fallback={<PageLoading />}><RunsPage /></Suspense>} />
        <Route path="/runs/:runId" element={<Suspense fallback={<PageLoading />}><RunsPage /></Suspense>} />
        <Route path="/schedules" element={<OwnerGate><Suspense fallback={<PageLoading />}><SchedulesPage /></Suspense></OwnerGate>} />
        <Route path="/shared" element={<Suspense fallback={<PageLoading />}><SharedSessionsPage /></Suspense>} />
        <Route path="/shared/:shareId" element={<Suspense fallback={<PageLoading />}><SharedSessionsPage /></Suspense>} />
        <Route path="/capabilities" element={<OwnerGate><Suspense fallback={<PageLoading />}><CapabilitiesPage /></Suspense></OwnerGate>} />
        <Route path="/settings" element={<OwnerGate><Suspense fallback={<PageLoading />}><SettingsPage /></Suspense></OwnerGate>} />
        <Route path="/members" element={<OwnerGate><Suspense fallback={<PageLoading />}><MembersPage /></Suspense></OwnerGate>} />
        <Route path="*" element={<Navigate replace to="/agent" />} />
      </Route>
    </Routes>
  );
}

function PageLoading() {
  return (
    <div aria-label="正在加载页面" className="grid min-h-[50svh] place-items-center" role="status">
      <div className="size-8 animate-pulse rounded-full bg-primary/20" />
    </div>
  );
}
