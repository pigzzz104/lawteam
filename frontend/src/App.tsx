import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import { CalendarWorkbench } from "@/pages/CalendarWorkbench";
import { CaseDetail } from "@/pages/CaseDetail";
import { CaseList } from "@/pages/CaseList";
import { InboxTriage } from "@/pages/InboxTriage";
import { LoginPage } from "@/pages/LoginPage";
import { PersonalSettings } from "@/pages/PersonalSettings";
import { RuleManager } from "@/pages/RuleManager";
import { TeamAdmin } from "@/pages/TeamAdmin";

function AdminRoute({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (user?.system_role !== "admin") {
    return <Navigate to="/calendar" replace />;
  }
  return <>{children}</>;
}

function AuthenticatedRoutes() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--md-sys-color-surface)] text-sm text-[var(--md-sys-color-on-surface-variant)]">
        正在验证律所工作台会话...
      </div>
    );
  }

  if (!user) {
    return <LoginPage />;
  }

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/calendar" replace />} />
        <Route path="/calendar" element={<CalendarWorkbench />} />
        <Route path="/cases" element={<CaseList />} />
        <Route path="/cases/:caseId" element={<CaseDetail />} />
        <Route path="/inbox" element={<InboxTriage />} />
        <Route path="/rules" element={<RuleManager />} />
        <Route path="/settings" element={<PersonalSettings />} />
        <Route path="/phone-setup" element={<Navigate to="/settings?tab=sms" replace />} />
        <Route path="/reminders" element={<Navigate to="/settings" replace />} />
        <Route
          path="/team"
          element={
            <AdminRoute>
              <TeamAdmin />
            </AdminRoute>
          }
        />
        <Route path="*" element={<Navigate to="/calendar" replace />} />
      </Routes>
    </AppShell>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AuthenticatedRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
