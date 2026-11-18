import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Eye, EyeOff, Scale, ShieldCheck } from "lucide-react";
import { ApiError, authApi } from "@/api/client";
import { useAuth } from "@/context/AuthContext";

const DEMO_ACCOUNTS = [
  { username: "partner_admin", label: "张合伙人（管理员）" },
  { username: "lead_lawyer", label: "李主办律师（执业律师）" },
  { username: "legal_assistant", label: "王法务助理（律师助理）" },
] as const;

export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: authApi.health,
    retry: false,
  });

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    setSubmitting(true);
    try {
      await login(username.trim(), password);
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMsg(err.detail);
      } else {
        setErrorMsg("无法连接后端服务，请稍后重试");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const quickLogin = async (demoUsername: string) => {
    setUsername(demoUsername);
    setPassword("LawTeam@2026");
    setErrorMsg(null);
    setSubmitting(true);
    try {
      await login(demoUsername, "LawTeam@2026");
    } catch (err) {
      setErrorMsg(err instanceof ApiError ? err.detail : "登录失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="min-h-dvh bg-gradient-to-br from-[#eef3fc] via-[#f7f9fc] to-[#e4ecfa] text-[var(--md-sys-color-on-surface)] flex items-center justify-center p-4 sm:p-8">
      <div className="w-full max-w-md rounded-[28px] border border-[var(--md-sys-color-outline-variant)]/80 bg-[var(--md-sys-color-surface-container-lowest)] text-[var(--md-sys-color-on-surface)] shadow-[0_12px_36px_rgb(15_23_42/0.09)] overflow-hidden">
        <div className="bg-[var(--md-sys-color-primary-container)] px-7 py-6 text-[var(--md-sys-color-on-primary-container)] flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[var(--md-sys-color-primary)] text-[var(--md-sys-color-on-primary)] shadow-sm">
              <Scale className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <h1 className="text-lg font-bold tracking-tight">LawTeam-OS</h1>
              <p className="text-xs opacity-85">律所协同与期限管理</p>
            </div>
          </div>
          <span
            className={`inline-flex items-center gap-1 rounded-full px-3 py-1 text-[11px] font-semibold ${
              healthQuery.isSuccess
                ? "bg-[#dcf5ec] text-[#0b5742]"
                : healthQuery.isError
                ? "bg-[#fde7e5] text-[#8c1d18]"
                : "bg-[#fef0d1] text-[#764900]"
            }`}
          >
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
            <span>
              {healthQuery.isSuccess ? "服务在线" : healthQuery.isError ? "服务离线" : "连接中"}
            </span>
          </span>
        </div>

        <div className="p-7 space-y-5">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label
                htmlFor="login-username"
                className="block text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)]"
              >
                账号用户名 <span aria-hidden="true" className="text-rose-600">*</span>
              </label>
              <input
                id="login-username"
                name="username"
                type="text"
                autoComplete="username"
                autoCorrect="off"
                autoCapitalize="none"
                spellCheck={false}
                required
                value={username}
                onChange={(e) => {
                  setUsername(e.target.value);
                  if (errorMsg) setErrorMsg(null);
                }}
                className="lt-input py-2.5 text-base"
                placeholder="输入用户名"
              />
            </div>

            <div className="space-y-1.5">
              <label
                htmlFor="login-password"
                className="block text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)]"
              >
                登录密码 <span aria-hidden="true" className="text-rose-600">*</span>
              </label>
              <div className="relative">
                <input
                  id="login-password"
                  name="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    if (errorMsg) setErrorMsg(null);
                  }}
                  className="lt-input py-2.5 pr-11 text-base"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((prev) => !prev)}
                  aria-pressed={showPassword}
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-1.5 text-[var(--md-sys-color-on-surface-variant)] hover:bg-[var(--md-sys-color-surface-container)] hover:text-[var(--md-sys-color-on-surface)]"
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            {errorMsg && (
              <div role="alert" className="lt-alert-error">
                {errorMsg}
              </div>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="lt-btn-primary w-full py-2.5 text-sm"
            >
              {submitting ? "正在验证..." : "登录"}
            </button>
          </form>

          {healthQuery.data?.env === "development" && (
            <details className="group border-t border-[var(--md-sys-color-outline-variant)]/75 pt-4">
              <summary className="flex cursor-pointer list-none items-center justify-between text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)] hover:text-[var(--md-sys-color-on-surface)]">
                <span>演示账号快速切换（默认密码 LawTeam@2026）</span>
                <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" />
              </summary>
              <div className="mt-3 grid grid-cols-1 gap-2">
                {DEMO_ACCOUNTS.map((acc) => (
                  <button
                    key={acc.username}
                    type="button"
                    disabled={submitting}
                    onClick={() => quickLogin(acc.username)}
                    className="flex items-center justify-between rounded-2xl border border-[var(--md-sys-color-outline-variant)] bg-[var(--md-sys-color-surface-container-low)] px-3.5 py-2.5 text-left text-xs transition-colors hover:border-[var(--md-sys-color-primary)] hover:bg-[var(--md-sys-color-secondary-container)]"
                  >
                    <span className="font-semibold text-[var(--md-sys-color-on-surface)]">{acc.label}</span>
                    <span className="font-mono text-[var(--md-sys-color-on-surface-variant)]">{acc.username}</span>
                  </button>
                ))}
              </div>
            </details>
          )}
        </div>
      </div>
    </main>
  );
}
