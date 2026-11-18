import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import { CalendarWorkbench } from "@/pages/CalendarWorkbench";
import { CaseDetail } from "@/pages/CaseDetail";
import { CaseList } from "@/pages/CaseList";
import { InboxTriage } from "@/pages/InboxTriage";
import { LoginPage } from "@/pages/LoginPage";
import { PhoneSetupGuide } from "@/pages/PhoneSetupGuide";
import { ReminderSettings } from "@/pages/ReminderSettings";
import { RuleManager } from "@/pages/RuleManager";
import { TeamAdmin } from "@/pages/TeamAdmin";

function AuthenticatedRoutes() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50 text-sm text-slate-500">
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
        <Route path="/phone-setup" element={<PhoneSetupGuide />} />
        <Route path="/reminders" element={<ReminderSettings />} />
        <Route path="/team" element={<TeamAdmin />} />
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
