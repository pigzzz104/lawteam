import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  BellRing,
  Briefcase,
  CalendarDays,
  Inbox,
  LogOut,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Scale,
  ShieldCheck,
  Smartphone,
  Users,
  X,
} from "lucide-react";
import { inboxApi } from "@/api/client";
import { useAuth } from "@/context/AuthContext";
import { NOTIFY_CHANNEL_META, SYSTEM_ROLE_LABELS } from "@/lib/domain";
import { closeDialogWithExitAnimation } from "@/lib/topLayer";

const SIDEBAR_COLLAPSED_KEY = "lawteam_sidebar_collapsed";

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [desktopCollapsed, setDesktopCollapsed] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
    } catch {
      return false;
    }
  });

  const mobileDrawerRef = useRef<HTMLDialogElement>(null);
  const mobileSheetRef = useRef<HTMLDivElement>(null);
  const mobileDrawerId = useId();

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, desktopCollapsed ? "1" : "0");
    } catch {
      // ignore storage failure
    }
  }, [desktopCollapsed]);

  useEffect(() => {
    const drawer = mobileDrawerRef.current;
    if (!drawer) return;

    if (mobileNavOpen) {
      drawer.removeAttribute("data-closing");
      if (!drawer.open) {
        drawer.showModal();
      }
    } else if (drawer.open) {
      void closeDialogWithExitAnimation(drawer);
    }
  }, [mobileNavOpen]);

  const pendingInboxQuery = useQuery({
    queryKey: ["inbox", "badge-count"],
    queryFn: () => inboxApi.list(),
    refetchInterval: 30_000,
    enabled: Boolean(user),
  });

  const pendingCount = (pendingInboxQuery.data ?? []).filter(
    (item) => item.status === "pending" || item.status === "manual"
  ).length;

  const navItems = [
    { to: "/calendar", label: "日程日历", icon: CalendarDays },
    { to: "/cases", label: "案件管理", icon: Briefcase },
    { to: "/inbox", label: "收件箱", icon: Inbox, badge: pendingCount },
    { to: "/rules", label: "期限规则", icon: Scale },
    { to: "/phone-setup", label: "短信接入", icon: Smartphone },
    { to: "/reminders", label: "提醒设置", icon: BellRing },
    ...(user?.system_role === "admin"
      ? [{ to: "/team", label: "团队成员", icon: Users }]
      : []),
  ];

  const renderSidebarInner = (compact: boolean, isMobile: boolean) => (
    <div className="flex h-full flex-col bg-[var(--md-sys-color-surface-container-low)] text-[var(--md-sys-color-on-surface)] border-r border-[var(--md-sys-color-outline-variant)]/75">
      {/* Brand Header */}
      <div
        className={`flex items-center border-b border-[var(--md-sys-color-outline-variant)]/65 py-4 ${
          compact ? "justify-center px-2.5" : "justify-between px-4"
        }`}
      >
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-[var(--md-sys-color-primary)] text-[var(--md-sys-color-on-primary)] shadow-sm">
            <Scale className="h-5 w-5" aria-hidden="true" />
          </div>
          {!compact && (
            <div className="min-w-0 lt-sidebar-label">
              <p className="truncate text-sm font-bold tracking-wide text-[var(--md-sys-color-on-surface)]">
                LawTeam-OS
              </p>
              <p className="truncate text-[11px] text-[var(--md-sys-color-on-surface-variant)]">
                律所协同工作台
              </p>
            </div>
          )}
        </div>

        {isMobile ? (
          <button
            type="button"
            autoFocus
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[var(--md-sys-color-on-surface-variant)] hover:bg-[var(--md-sys-color-surface-container-high)] hover:text-[var(--md-sys-color-on-surface)]"
            onClick={() => setMobileNavOpen(false)}
            aria-label="关闭侧边导航"
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        ) : (
          !compact && (
            <button
              type="button"
              onClick={() => setDesktopCollapsed(true)}
              aria-label="收起侧边栏"
              title="收起侧边栏"
              className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[var(--md-sys-color-on-surface-variant)] transition-colors hover:bg-[var(--md-sys-color-surface-container-high)] hover:text-[var(--md-sys-color-on-surface)]"
            >
              <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
            </button>
          )
        )}
      </div>

      {/* Expand Trigger in Collapsed Rail */}
      {compact && !isMobile && (
        <div className="flex justify-center pt-3">
          <button
            type="button"
            onClick={() => setDesktopCollapsed(false)}
            aria-label="展开侧边栏"
            title="展开侧边栏"
            className="inline-flex h-9 w-12 items-center justify-center rounded-full text-[var(--md-sys-color-on-surface-variant)] transition-colors hover:bg-[var(--md-sys-color-surface-container-high)] hover:text-[var(--md-sys-color-on-surface)]"
          >
            <PanelLeftOpen className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      )}

      {/* Navigation Destinations */}
      <nav
        className={`flex-1 space-y-1.5 overflow-y-auto py-4 ${
          compact ? "px-2" : "px-3"
        }`}
        aria-label="主导航"
      >
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              title={compact ? item.label : undefined}
              onClick={() => {
                if (isMobile) setMobileNavOpen(false);
              }}
              className={({ isActive }) =>
                compact
                  ? `group relative flex flex-col items-center justify-center gap-1 rounded-2xl px-1 py-1.5 text-[11px] font-medium transition-colors ${
                      isActive
                        ? "text-[var(--md-sys-color-on-secondary-container)] font-semibold"
                        : "text-[var(--md-sys-color-on-surface-variant)] hover:text-[var(--md-sys-color-on-surface)]"
                    }`
                  : `group relative flex items-center justify-between rounded-full px-4 py-2.5 text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-[var(--md-sys-color-secondary-container)] text-[var(--md-sys-color-on-secondary-container)] font-semibold"
                        : "text-[var(--md-sys-color-on-surface-variant)] hover:bg-[var(--md-sys-color-surface-container)] hover:text-[var(--md-sys-color-on-surface)]"
                    }`
              }
            >
              {({ isActive }) => (
                <>
                  {compact ? (
                    <>
                      <span
                        className={`relative flex h-8 w-14 items-center justify-center rounded-full transition-colors ${
                          isActive
                            ? "bg-[var(--md-sys-color-secondary-container)] text-[var(--md-sys-color-on-secondary-container)]"
                            : "group-hover:bg-[var(--md-sys-color-surface-container)]"
                        }`}
                      >
                        <Icon className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
                        {typeof item.badge === "number" && item.badge > 0 && (
                          <span className="absolute -top-0.5 right-1 rounded-full bg-[var(--md-sys-color-error)] px-1.5 py-0 text-[10px] font-bold leading-4 text-[var(--md-sys-color-on-error)] tabular-nums">
                            {item.badge}
                          </span>
                        )}
                      </span>
                      <span className="max-w-full truncate text-[11px] leading-tight">
                        {item.label}
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="flex min-w-0 items-center gap-3">
                        <Icon className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
                        <span className="truncate lt-sidebar-label">{item.label}</span>
                      </span>
                      {typeof item.badge === "number" && item.badge > 0 && (
                        <span className="rounded-full bg-[var(--md-sys-color-error)] px-2 py-0.5 text-[11px] font-bold text-[var(--md-sys-color-on-error)] tabular-nums">
                          {item.badge}
                        </span>
                      )}
                    </>
                  )}
                </>
              )}
            </NavLink>
          );
        })}
      </nav>

      {/* Account Footer */}
      {user && (
        <div
          className={`border-t border-[var(--md-sys-color-outline-variant)]/65 ${
            compact ? "p-2.5 space-y-2" : "p-3.5 space-y-3"
          }`}
        >
          <div className={`flex items-center ${compact ? "justify-center" : "gap-3"}`}>
            <span
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-bold text-white shadow-sm"
              style={{ backgroundColor: user.calendar_color || "#1b56b3" }}
              title={compact ? `${user.full_name}（${SYSTEM_ROLE_LABELS[user.system_role]}）` : undefined}
            >
              {user.full_name.slice(0, 1)}
            </span>
            {!compact && (
              <div className="min-w-0 flex-1 lt-sidebar-label">
                <p className="truncate text-sm font-semibold text-[var(--md-sys-color-on-surface)]">
                  {user.full_name}
                </p>
                <p className="truncate text-xs text-[var(--md-sys-color-on-surface-variant)]">
                  {SYSTEM_ROLE_LABELS[user.system_role]}
                </p>
              </div>
            )}
          </div>

          {!compact && (
            <div className="rounded-2xl bg-[var(--md-sys-color-surface-container)] px-3 py-2 text-xs text-[var(--md-sys-color-on-surface-variant)] space-y-0.5 lt-sidebar-label">
              <p className="flex items-center gap-1.5 text-[var(--md-sys-color-tertiary)] font-semibold">
                <ShieldCheck className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span>推送：{NOTIFY_CHANNEL_META[user.notify_channel]?.badge ?? user.notify_channel}</span>
              </p>
              <p className="truncate">
                尾号：{user.phone_last4 ? `手机 *${user.phone_last4}` : "未设置手机尾号"}
              </p>
            </div>
          )}

          <button
            type="button"
            onClick={logout}
            title={compact ? "退出登录" : undefined}
            aria-label={compact ? "退出登录" : undefined}
            className="lt-btn-secondary w-full py-2"
          >
            <LogOut className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {!compact && <span>退出登录</span>}
          </button>
        </div>
      )}
    </div>
  );

  return (
    <div className="min-h-screen bg-[var(--md-sys-color-surface)] flex">
      {/* Desktop Sticky Animated Sidebar / Rail */}
      <aside
        className={`lt-sidebar hidden lg:block shrink-0 sticky top-0 h-screen overflow-hidden ${
          desktopCollapsed ? "w-[4.75rem]" : "w-64"
        }`}
      >
        {renderSidebarInner(desktopCollapsed, false)}
      </aside>

      {/* Mobile Top-Layer Navigation Drawer (<dialog> with smooth slide-in/out + scrim fade) */}
      <dialog
        ref={mobileDrawerRef}
        id={mobileDrawerId}
        aria-label="主导航抽屉"
        className="lt-drawer lg:hidden"
        onCancel={(e) => {
          e.preventDefault();
          setMobileNavOpen(false);
        }}
        onClick={(e) => {
          if (mobileSheetRef.current && !mobileSheetRef.current.contains(e.target as Node)) {
            setMobileNavOpen(false);
          }
        }}
      >
        <div ref={mobileSheetRef} className="lt-drawer-sheet">
          {renderSidebarInner(false, true)}
        </div>
      </dialog>

      {/* Content Area */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex items-center justify-between border-b border-[var(--md-sys-color-outline-variant)]/75 bg-[var(--md-sys-color-surface-container-low)]/95 backdrop-blur px-4 py-3 lg:hidden">
          <button
            type="button"
            aria-expanded={mobileNavOpen}
            aria-controls={mobileDrawerId}
            onClick={() => setMobileNavOpen(true)}
            className="lt-btn-secondary py-1.5 px-3"
          >
            <Menu className="h-4 w-4" aria-hidden="true" />
            <span>菜单</span>
          </button>
          <span className="text-sm font-bold text-[var(--md-sys-color-on-surface)]">LawTeam-OS</span>
          <span className="text-xs font-medium text-[var(--md-sys-color-on-surface-variant)]">
            {user?.full_name}
          </span>
        </header>

        <main className="flex-1 p-4 sm:p-6 lg:p-8 max-w-[1600px] w-full mx-auto">
          {children}
        </main>
      </div>
    </div>
  );
}
