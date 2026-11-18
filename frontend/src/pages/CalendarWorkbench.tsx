import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import FullCalendar from "@fullcalendar/react";
import dayGridPlugin from "@fullcalendar/daygrid";
import timeGridPlugin from "@fullcalendar/timegrid";
import listPlugin from "@fullcalendar/list";
import interactionPlugin from "@fullcalendar/interaction";
import zhCnLocale from "@fullcalendar/core/locales/zh-cn";
import type { DatesSetArg, EventClickArg } from "@fullcalendar/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BellPlus,
  CalendarPlus,
  Check,
  CheckCircle2,
  Copy,
  ExternalLink,
  Link2,
  RefreshCw,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { authApi, calendarApi, casesApi, remindersApi, usersApi } from "@/api/client";
import { ModalDialog } from "@/components/ModalDialog";
import { CategoryBadge, RiskBadge } from "@/components/StatusBadges";
import { useAuth } from "@/context/AuthContext";
import {
  CASE_CATEGORY_META,
  HOLIDAY_ROLL_LABELS,
  NOTIFY_TARGET_LABELS,
  SCHEDULE_CATEGORY_LABELS,
  copyTextSafely,
  formatShanghaiDateTime,
  todayShanghaiIso,
} from "@/lib/domain";
import type {
  CalendarEventItem,
  CaseCategory,
  NotifyTargetType,
  RiskLevel,
} from "@/types/api";

export function CalendarWorkbench() {
  const { user, refreshUser } = useAuth();
  const queryClient = useQueryClient();
  const detailAsideRef = useRef<HTMLElement>(null);
  const calendarWrapperRef = useRef<HTMLDivElement>(null);
  const calendarRef = useRef<FullCalendar>(null);

  useEffect(() => {
    const el = calendarWrapperRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let rafId = 0;
    const observer = new ResizeObserver(() => {
      if (rafId) window.cancelAnimationFrame(rafId);
      rafId = window.requestAnimationFrame(() => {
        calendarRef.current?.getApi().updateSize();
      });
    });
    observer.observe(el);
    return () => {
      if (rafId) window.cancelAnimationFrame(rafId);
      observer.disconnect();
    };
  }, []);

  const [scope, setScope] = useState<"my" | "team">("my");
  const [categoryFilter, setCategoryFilter] = useState<CaseCategory | "">("");
  const [lawyerFilterId, setLawyerFilterId] = useState<number | null>(null);
  const [includeCompleted, setIncludeCompleted] = useState(false);
  const [includeTentative, setIncludeTentative] = useState(true);
  const [visibleRange, setVisibleRange] = useState<{ start: string; end: string } | null>(null);

  const [selectedEvent, setSelectedEvent] = useState<CalendarEventItem | null>(null);
  const [icsModalOpen, setIcsModalOpen] = useState(false);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createKind, setCreateKind] = useState<"schedule" | "deadline">("schedule");
  const [copiedIcs, setCopiedIcs] = useState(false);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);

  const [overrideDate, setOverrideDate] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const [notifyTargetType, setNotifyTargetType] = useState<NotifyTargetType>("case_team");
  const [escalateSupervisor, setEscalateSupervisor] = useState(true);
  const [personalAlertAt, setPersonalAlertAt] = useState("");

  const [newCaseId, setNewCaseId] = useState<string>("");
  const [newTitle, setNewTitle] = useState("");
  const [newSchedCat, setNewSchedCat] = useState("hearing");
  const [newStartDt, setNewStartDt] = useState(`${todayShanghaiIso()}T09:30`);
  const [newEndDt, setNewEndDt] = useState(`${todayShanghaiIso()}T11:00`);
  const [newLocation, setNewLocation] = useState("");
  const [newDueDate, setNewDueDate] = useState(todayShanghaiIso());
  const [newRiskLevel, setNewRiskLevel] = useState<RiskLevel>("high");
  const [newLegalBasis, setNewLegalBasis] = useState("");
  const [newApplyRoll, setNewApplyRoll] = useState(true);

  const calendarQuery = useQuery({
    queryKey: [
      "calendar",
      scope,
      categoryFilter,
      includeCompleted,
      includeTentative,
      visibleRange?.start ?? "",
      visibleRange?.end ?? "",
    ],
    queryFn: () =>
      calendarApi.events({
        scope,
        start: visibleRange?.start,
        end: visibleRange?.end,
        case_category: categoryFilter || undefined,
        include_completed: includeCompleted,
        include_tentative: includeTentative,
      }),
    placeholderData: (prev) => prev,
  });

  const casesQuery = useQuery({
    queryKey: ["cases", "compact-for-calendar"],
    queryFn: () => casesApi.list({ is_archived: false }),
  });

  const usersQuery = useQuery({
    queryKey: ["users", "all"],
    queryFn: usersApi.list,
  });

  const deadlineDetailQuery = useQuery({
    queryKey: ["case-detail-for-cal", selectedEvent?.case_id],
    queryFn: () => casesApi.detail(selectedEvent!.case_id!),
    enabled: Boolean(selectedEvent?.source_type === "deadline" && selectedEvent?.case_id),
  });

  const activeDeadline = useMemo(() => {
    if (!selectedEvent || selectedEvent.source_type !== "deadline") return null;
    return (
      deadlineDetailQuery.data?.deadlines.find((d) => d.id === selectedEvent.record_id) ?? null
    );
  }, [deadlineDetailQuery.data, selectedEvent]);

  const ownerPills = useMemo(() => {
    const map = new Map<number, { id: number; name: string; color: string }>();
    if (scope === "team") {
      for (const u of usersQuery.data ?? []) {
        map.set(u.id, { id: u.id, name: u.full_name, color: u.calendar_color || "#2563eb" });
      }
    }
    for (const ev of calendarQuery.data?.items ?? []) {
      if (ev.owner_id && ev.owner_name && !map.has(ev.owner_id)) {
        map.set(ev.owner_id, {
          id: ev.owner_id,
          name: ev.owner_name,
          color: ev.color || "#3b82f6",
        });
      }
    }
    return Array.from(map.values());
  }, [scope, usersQuery.data, calendarQuery.data?.items]);

  const filteredItems = useMemo(() => {
    const items = calendarQuery.data?.items ?? [];
    if (lawyerFilterId === null) return items;
    return items.filter((item) => item.owner_id === lawyerFilterId);
  }, [calendarQuery.data?.items, lawyerFilterId]);

  useEffect(() => {
    if (selectedEvent && !filteredItems.some((item) => item.id === selectedEvent.id)) {
      setSelectedEvent(null);
    }
  }, [filteredItems, selectedEvent]);

  const fcEvents = useMemo(
    () =>
      filteredItems.map((ev) => ({
        id: ev.id,
        title: ev.case_title ? `[${ev.case_title}] ${ev.title}` : ev.title,
        start: ev.start,
        end: ev.end,
        allDay: ev.all_day,
        backgroundColor: ev.is_tentative ? "#f1f5f9" : ev.color,
        borderColor: ev.is_tentative ? "#64748b" : ev.color,
        textColor: ev.is_tentative ? "#334155" : "#ffffff",
        classNames: [
          ev.is_tentative ? "fc-event-tentative" : "",
          ev.is_completed ? "fc-event-completed" : "",
        ].filter(Boolean),
        extendedProps: ev,
      })),
    [filteredItems]
  );

  const invalidateAllCalendar = () => {
    queryClient.invalidateQueries({ queryKey: ["calendar"] });
    queryClient.invalidateQueries({ queryKey: ["case-detail-for-cal"] });
    queryClient.invalidateQueries({ queryKey: ["cases"] });
  };

  const updateDeadlineMut = useMutation({
    mutationFn: ({
      deadlineId,
      payload,
    }: {
      deadlineId: number;
      payload: Parameters<typeof calendarApi.updateDeadline>[1];
    }) => calendarApi.updateDeadline(deadlineId, payload),
    onSuccess: (updated) => {
      invalidateAllCalendar();
      setErrorMsg(null);
      setActionMsg("期限已更新并同步日历");
      if (selectedEvent) {
        setSelectedEvent({
          ...selectedEvent,
          is_completed: updated.is_completed,
          due_date: updated.due_date,
          raw_due_date: updated.raw_due_date,
          roll_reason: updated.roll_reason,
          legal_basis: updated.legal_basis,
          start: updated.due_date,
          end: updated.due_date,
          is_manual_override: updated.is_manual_override,
        });
      }
    },
    onError: (err: Error) => {
      setActionMsg(null);
      setErrorMsg(err.message);
    },
  });

  const addPersonalAlertMut = useMutation({
    mutationFn: ({ deadlineId, fireAt }: { deadlineId: number; fireAt: string }) =>
      remindersApi.addPersonalAlert(deadlineId, fireAt),
    onSuccess: () => {
      invalidateAllCalendar();
      setPersonalAlertAt("");
      setErrorMsg(null);
      setActionMsg("已添加个人定点提醒时间");
    },
    onError: (err: Error) => {
      setActionMsg(null);
      setErrorMsg(err.message);
    },
  });

  const updateScheduleMut = useMutation({
    mutationFn: ({
      scheduleId,
      payload,
    }: {
      scheduleId: number;
      payload: Parameters<typeof calendarApi.updateSchedule>[1];
    }) => calendarApi.updateSchedule(scheduleId, payload),
    onSuccess: (updated) => {
      invalidateAllCalendar();
      setErrorMsg(null);
      setActionMsg("日程状态已更新");
      if (selectedEvent) {
        setSelectedEvent({ ...selectedEvent, is_completed: updated.is_completed });
      }
    },
    onError: (err: Error) => {
      setActionMsg(null);
      setErrorMsg(err.message);
    },
  });

  const deleteScheduleMut = useMutation({
    mutationFn: (scheduleId: number) => calendarApi.deleteSchedule(scheduleId),
    onSuccess: () => {
      invalidateAllCalendar();
      setSelectedEvent(null);
      setErrorMsg(null);
      setActionMsg("日程已删除");
    },
    onError: (err: Error) => {
      setActionMsg(null);
      setErrorMsg(err.message);
    },
  });

  const createItemMut = useMutation({
    mutationFn: async () => {
      if (createKind === "schedule") {
        const startMs = new Date(newStartDt).getTime();
        const endMs = new Date(newEndDt).getTime();
        if (Number.isNaN(startMs) || Number.isNaN(endMs) || endMs <= startMs) {
          throw new Error("日程结束时间必须晚于开始时间");
        }
        return calendarApi.createSchedule({
          case_id: newCaseId ? Number(newCaseId) : null,
          category: newSchedCat,
          title: newTitle.trim(),
          start_time: new Date(startMs).toISOString(),
          end_time: new Date(endMs).toISOString(),
          location: newLocation.trim() || null,
          risk_level: newRiskLevel,
        });
      }
      if (!newCaseId) {
        throw new Error("创建自定义期限必须关联一个案件");
      }
      return calendarApi.createDeadline({
        case_id: Number(newCaseId),
        title: newTitle.trim(),
        due_date: newDueDate,
        legal_basis: newLegalBasis.trim() || null,
        apply_holiday_roll: newApplyRoll,
        risk_level: newRiskLevel,
      });
    },
    onSuccess: () => {
      invalidateAllCalendar();
      setCreateModalOpen(false);
      setCreateError(null);
      setNewTitle("");
      setNewLocation("");
      setNewLegalBasis("");
      setErrorMsg(null);
      setActionMsg("已新增到协同日历");
    },
    onError: (err: Error) => setCreateError(err.message),
  });

  const rotateIcsMut = useMutation({
    mutationFn: () => authApi.updateMe({ rotate_ics_token: true }),
    onSuccess: async () => {
      await refreshUser();
      setErrorMsg(null);
      setActionMsg("已重置日历订阅 Token");
    },
    onError: (err: Error) => {
      setActionMsg(null);
      setErrorMsg(err.message);
    },
  });

  const handleEventClick = (arg: EventClickArg) => {
    const ev = arg.event.extendedProps as CalendarEventItem;
    setSelectedEvent(ev);
    setActionMsg(null);
    setErrorMsg(null);
    setOverrideDate(ev.due_date || ev.start.slice(0, 10));
    setOverrideReason("");
    if (window.innerWidth < 1280) {
      window.setTimeout(() => {
        detailAsideRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 60);
    }
  };

  const handleDatesSet = (arg: DatesSetArg) => {
    const start = arg.startStr.slice(0, 10);
    const end = arg.endStr.slice(0, 10);
    setVisibleRange((prev) =>
      prev?.start === start && prev?.end === end ? prev : { start, end }
    );
  };

  const openCreateModal = () => {
    setCreateKind("schedule");
    setNewCaseId(casesQuery.data?.[0] ? String(casesQuery.data[0].id) : "");
    setNewTitle("");
    setNewSchedCat("hearing");
    setNewStartDt(`${todayShanghaiIso()}T09:30`);
    setNewEndDt(`${todayShanghaiIso()}T11:00`);
    setNewLocation("");
    setNewDueDate(todayShanghaiIso());
    setNewRiskLevel("high");
    setNewLegalBasis("");
    setNewApplyRoll(true);
    setCreateError(null);
    setCreateModalOpen(true);
  };

  const icsUrl = user
    ? `${window.location.origin}/api/calendar/ics/${user.ics_feed_token}.ics?scope=${scope}`
    : "";

  const copyIcsUrl = async () => {
    if (!icsUrl) return;
    const ok = await copyTextSafely(icsUrl);
    if (!ok) {
      setErrorMsg("浏览器剪贴板写入受限，请手动选择复制");
      return;
    }
    setCopiedIcs(true);
    window.setTimeout(() => setCopiedIcs(false), 2000);
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-[var(--md-sys-color-on-surface)]">日程日历</h1>
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="lt-segmented">
            <button
              type="button"
              onClick={() => {
                setScope("my");
                setLawyerFilterId(null);
              }}
              className={`rounded-full px-4 py-1.5 text-xs font-semibold transition-colors ${
                scope === "my"
                  ? "bg-[var(--md-sys-color-primary)] text-[var(--md-sys-color-on-primary)] shadow-sm"
                  : "text-[var(--md-sys-color-on-surface-variant)] hover:text-[var(--md-sys-color-on-surface)]"
              }`}
            >
              我的日程
            </button>
            <button
              type="button"
              onClick={() => setScope("team")}
              className={`rounded-full px-4 py-1.5 text-xs font-semibold transition-colors ${
                scope === "team"
                  ? "bg-[var(--md-sys-color-primary)] text-[var(--md-sys-color-on-primary)] shadow-sm"
                  : "text-[var(--md-sys-color-on-surface-variant)] hover:text-[var(--md-sys-color-on-surface)]"
              }`}
            >
              全所总览
            </button>
          </div>

          <button type="button" onClick={() => setIcsModalOpen(true)} className="lt-btn-secondary">
            <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
            <span>.ics 日历订阅</span>
          </button>

          <button type="button" onClick={openCreateModal} className="lt-btn-primary">
            <CalendarPlus className="h-3.5 w-3.5" aria-hidden="true" />
            <span>新建日程 / 期限</span>
          </button>
        </div>
      </div>

      {actionMsg && !selectedEvent && (
        <div role="status" className="lt-alert-success">
          {actionMsg}
        </div>
      )}
      {errorMsg && !selectedEvent && (
        <div role="alert" className="lt-alert-error">
          {errorMsg}
        </div>
      )}

      {(calendarQuery.data?.conflicts.length ?? 0) > 0 && (
        <div
          role="alert"
          className="rounded-xl border border-rose-300 bg-rose-50 px-4 py-3 text-rose-900 space-y-1.5"
        >
          <div className="flex items-center gap-2 font-semibold text-sm">
            <AlertTriangle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
            <span>检测到 {calendarQuery.data!.conflicts.length} 项同律师时间冲突：</span>
          </div>
          <ul className="list-disc pl-6 text-xs space-y-1">
            {calendarQuery.data!.conflicts.map((c, idx) => (
              <li key={`${c.schedule_id_a}-${c.schedule_id_b}-${idx}`}>
                <span className="font-semibold">{c.user_name}</span>：{c.message}（
                {formatShanghaiDateTime(c.overlap_start)} - {formatShanghaiDateTime(c.overlap_end)}）
              </li>
            ))}
          </ul>
        </div>
      )}

      <search className="lt-card p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)] mr-1">
              案种：
            </span>
            <button
              type="button"
              onClick={() => setCategoryFilter("")}
              className={`lt-chip py-1 ${categoryFilter === "" ? "lt-chip-active" : ""}`}
            >
              全部案种
            </button>
            {(Object.keys(CASE_CATEGORY_META) as CaseCategory[]).map((cat) => (
              <button
                key={cat}
                type="button"
                onClick={() => setCategoryFilter(cat)}
                className={`lt-chip py-1 ${categoryFilter === cat ? "lt-chip-active" : ""}`}
              >
                {CASE_CATEGORY_META[cat].shortLabel}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-4 text-xs text-[var(--md-sys-color-on-surface-variant)]">
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={includeTentative}
                onChange={(e) => setIncludeTentative(e.target.checked)}
                className="rounded border-slate-300"
              />
              <span>显示待确认短信 (虚线)</span>
            </label>
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={includeCompleted}
                onChange={(e) => setIncludeCompleted(e.target.checked)}
                className="rounded border-slate-300"
              />
              <span>显示已办结</span>
            </label>
          </div>
        </div>

        {ownerPills.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 border-t border-[var(--md-sys-color-outline-variant)]/55 pt-2.5">
            <span className="text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)] mr-1">
              成员筛选：
            </span>
            <button
              type="button"
              onClick={() => setLawyerFilterId(null)}
              className={`lt-chip py-0.5 ${lawyerFilterId === null ? "lt-chip-active" : ""}`}
            >
              全部成员
            </button>
            {ownerPills.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => setLawyerFilterId(lawyerFilterId === p.id ? null : p.id)}
                className={`lt-chip py-0.5 ${lawyerFilterId === p.id ? "lt-chip-active" : ""}`}
              >
                <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: p.color }} />
                <span>{p.name}</span>
              </button>
            ))}
          </div>
        )}
      </search>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-5 items-start">
        <div
          ref={calendarWrapperRef}
          className={`${
            selectedEvent ? "xl:col-span-8" : "xl:col-span-12"
          } lt-card p-4`}
        >
          {calendarQuery.isError ? (
            <div role="alert" className="lt-alert-error flex items-center justify-between gap-3">
              <span>日历数据加载失败，请检查网络连接后重试。</span>
              <button
                type="button"
                onClick={() => calendarQuery.refetch()}
                className="lt-btn-secondary py-1"
              >
                重新加载
              </button>
            </div>
          ) : calendarQuery.isLoading && !calendarQuery.data ? (
            <p className="py-16 text-center text-sm text-slate-600">正在加载协同日历...</p>
          ) : (
            <FullCalendar
              ref={calendarRef}
              plugins={[dayGridPlugin, timeGridPlugin, listPlugin, interactionPlugin]}
              initialView="dayGridMonth"
              locale={zhCnLocale}
              headerToolbar={{
                left: "prev,next today",
                center: "title",
                right: "dayGridMonth,timeGridWeek,listMonth",
              }}
              datesSet={handleDatesSet}
              events={fcEvents}
              eventClick={handleEventClick}
              height="auto"
              dayMaxEvents={4}
            />
          )}
        </div>

        {selectedEvent && (
          <aside
            ref={detailAsideRef}
            className="xl:col-span-4 lt-card p-5 space-y-4 xl:sticky xl:top-6"
          >
            <div className="flex items-start justify-between gap-2 border-b border-slate-100 pb-3">
              <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <RiskBadge risk={selectedEvent.risk_level} />
                  {selectedEvent.case_category && (
                    <CategoryBadge category={selectedEvent.case_category} />
                  )}
                  {selectedEvent.is_tentative && (
                    <span className="rounded bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-700">
                      待分拣确认
                    </span>
                  )}
                  {selectedEvent.is_manual_override && (
                    <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                      手动覆写
                    </span>
                  )}
                </div>
                <h2 className="text-base font-bold text-slate-900">{selectedEvent.title}</h2>
                {selectedEvent.case_title && (
                  <p className="text-xs text-slate-600">
                    {selectedEvent.case_title}{" "}
                    {selectedEvent.case_number ? `（${selectedEvent.case_number}）` : ""}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={() => setSelectedEvent(null)}
                className="text-xs text-slate-600 hover:text-slate-900 shrink-0"
              >
                收起
              </button>
            </div>

            {actionMsg && (
              <div role="status" className="lt-alert-success">
                {actionMsg}
              </div>
            )}
            {errorMsg && (
              <div role="alert" className="lt-alert-error">
                {errorMsg}
              </div>
            )}

            <dl className="space-y-2 text-xs">
              <div className="flex justify-between gap-2">
                <dt className="text-slate-600">时间 / 到期日</dt>
                <dd className="font-semibold text-slate-900 tabular-nums">
                  {selectedEvent.all_day
                    ? selectedEvent.start
                    : `${formatShanghaiDateTime(selectedEvent.start)} ~ ${formatShanghaiDateTime(
                        selectedEvent.end
                      )}`}
                </dd>
              </div>
              {selectedEvent.raw_due_date && (
                <div className="flex justify-between gap-2">
                  <dt className="text-slate-600">节假日顺延前原日</dt>
                  <dd className="text-slate-700 tabular-nums">{selectedEvent.raw_due_date}</dd>
                </div>
              )}
              {selectedEvent.holiday_roll_mode && (
                <div className="flex justify-between gap-2">
                  <dt className="text-slate-600">顺延规则</dt>
                  <dd className="text-right text-slate-700">
                    {HOLIDAY_ROLL_LABELS[selectedEvent.holiday_roll_mode]}
                  </dd>
                </div>
              )}
              {selectedEvent.roll_reason && (
                <div className="rounded-lg bg-slate-50 p-2.5 text-slate-700">
                  <span className="font-semibold text-slate-800">顺延说明：</span>
                  {selectedEvent.roll_reason}
                </div>
              )}
              {selectedEvent.legal_basis && (
                <div className="rounded-lg bg-blue-50/70 p-2.5 text-blue-900">
                  <span className="font-semibold">法条依据：</span>
                  {selectedEvent.legal_basis}
                </div>
              )}
              {selectedEvent.location && (
                <div className="flex justify-between gap-2">
                  <dt className="text-slate-600">地点</dt>
                  <dd className="text-slate-800">{selectedEvent.location}</dd>
                </div>
              )}
              {selectedEvent.owner_name && (
                <div className="flex justify-between gap-2">
                  <dt className="text-slate-600">负责律师</dt>
                  <dd className="text-slate-800">{selectedEvent.owner_name}</dd>
                </div>
              )}
            </dl>

            {selectedEvent.case_id && (
              <Link
                to={`/cases/${selectedEvent.case_id}`}
                className="lt-btn-secondary w-full"
              >
                <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                <span>打开所属案件详情</span>
              </Link>
            )}

            {selectedEvent.source_type === "triage_tentative" && (
              <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                <p>该事项来自未确认的短信分拣预览，确认入案后将转为正式日程/期限。</p>
                <Link to="/inbox" className="inline-flex items-center gap-1 font-semibold underline">
                  前往分拣收件箱确认 →
                </Link>
              </div>
            )}

            {selectedEvent.source_type === "deadline" && (
              <div className="space-y-4 border-t border-slate-100 pt-3">
                <button
                  type="button"
                  disabled={updateDeadlineMut.isPending}
                  onClick={() =>
                    updateDeadlineMut.mutate({
                      deadlineId: selectedEvent.record_id,
                      payload: { is_completed: !selectedEvent.is_completed },
                    })
                  }
                  className={`flex w-full items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold ${
                    selectedEvent.is_completed
                      ? "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                      : "bg-emerald-600 text-white hover:bg-emerald-700"
                  }`}
                >
                  {selectedEvent.is_completed ? (
                    <>
                      <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                      <span>重新打开此期限</span>
                    </>
                  ) : (
                    <>
                      <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                      <span>标记为已办结（停止后续预警）</span>
                    </>
                  )}
                </button>

                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    updateDeadlineMut.mutate({
                      deadlineId: selectedEvent.record_id,
                      payload: {
                        due_date: overrideDate,
                        override_reason: overrideReason.trim() || "日历侧边栏手动调整到期日",
                        is_manual_override: true,
                      },
                    });
                  }}
                  className="space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3"
                >
                  <p className="text-xs font-semibold text-slate-800">人工覆写到期日（受重算保护）</p>
                  <div className="space-y-1">
                    <label htmlFor="override-due-date" className="block text-[11px] text-slate-600">
                      调整后到期日
                    </label>
                    <input
                      id="override-due-date"
                      type="date"
                      required
                      value={overrideDate}
                      onChange={(e) => setOverrideDate(e.target.value)}
                      className="w-full rounded border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                    />
                  </div>
                  <div className="space-y-1">
                    <label htmlFor="override-reason" className="block text-[11px] text-slate-600">
                      调整原因说明
                    </label>
                    <input
                      id="override-reason"
                      type="text"
                      value={overrideReason}
                      onChange={(e) => setOverrideReason(e.target.value)}
                      placeholder="例如 法官书面批准延期举证"
                      className="w-full rounded border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={updateDeadlineMut.isPending}
                    className="lt-btn-primary w-full py-1.5"
                  >
                    保存人工调整
                  </button>
                </form>

                <div className="space-y-2 rounded-lg border border-slate-200 p-3">
                  <p className="text-xs font-semibold text-slate-800">提醒范围 & 个人加设提醒</p>
                  <div className="space-y-1.5">
                    <label htmlFor="dl-notify-type" className="block text-[11px] text-slate-600">
                      接收范围（当前：
                      {activeDeadline
                        ? NOTIFY_TARGET_LABELS[activeDeadline.notify_target_type]
                        : "加载中..."}
                      ）
                    </label>
                    <select
                      id="dl-notify-type"
                      value={activeDeadline?.notify_target_type ?? notifyTargetType}
                      onChange={(e) => {
                        const val = e.target.value as NotifyTargetType;
                        setNotifyTargetType(val);
                        updateDeadlineMut.mutate({
                          deadlineId: selectedEvent.record_id,
                          payload: { notify_target_type: val },
                        });
                      }}
                      className="lt-input py-1.5 text-xs"
                    >
                      {(Object.keys(NOTIFY_TARGET_LABELS) as NotifyTargetType[]).map((k) => (
                        <option key={k} value={k}>
                          {NOTIFY_TARGET_LABELS[k]}
                        </option>
                      ))}
                    </select>
                    {(activeDeadline?.notify_target_type ?? notifyTargetType) === "custom" &&
                      (usersQuery.data?.length ?? 0) > 0 && (
                        <div className="pt-1.5 space-y-1">
                          <span className="block text-[11px] text-[var(--md-sys-color-on-surface-variant)]">
                            点选指定接收人员：
                          </span>
                          <div className="flex flex-wrap gap-1.5">
                            {usersQuery.data!.map((u) => {
                              const currentCustomIds = activeDeadline?.custom_notify_user_ids ?? [];
                              const checked = currentCustomIds.includes(u.id);
                              return (
                                <button
                                  key={u.id}
                                  type="button"
                                  disabled={updateDeadlineMut.isPending}
                                  onClick={() => {
                                    const nextIds = checked
                                      ? currentCustomIds.filter((id) => id !== u.id)
                                      : [...currentCustomIds, u.id];
                                    updateDeadlineMut.mutate({
                                      deadlineId: selectedEvent.record_id,
                                      payload: {
                                        notify_target_type: "custom",
                                        custom_notify_user_ids: nextIds,
                                      },
                                    });
                                  }}
                                  className={`lt-chip py-0.5 text-[11px] ${
                                    checked ? "lt-chip-active" : ""
                                  }`}
                                >
                                  {u.full_name}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      )}
                  </div>
                  <label className="inline-flex items-center gap-2 text-xs text-slate-700 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={activeDeadline?.escalate_to_supervisor ?? escalateSupervisor}
                      onChange={(e) => {
                        setEscalateSupervisor(e.target.checked);
                        updateDeadlineMut.mutate({
                          deadlineId: selectedEvent.record_id,
                          payload: { escalate_to_supervisor: e.target.checked },
                        });
                      }}
                    />
                    <span>临期1天未办结升级预警督办合伙人</span>
                  </label>

                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (!personalAlertAt) return;
                      addPersonalAlertMut.mutate({
                        deadlineId: selectedEvent.record_id,
                        fireAt: new Date(personalAlertAt).toISOString(),
                      });
                    }}
                    className="space-y-1.5 border-t border-slate-100 pt-2"
                  >
                    <label htmlFor="personal-alert-dt" className="block text-[11px] text-slate-600">
                      个人自定义微信提醒时间点
                    </label>
                    <div className="flex gap-1.5">
                      <input
                        id="personal-alert-dt"
                        type="datetime-local"
                        value={personalAlertAt}
                        onChange={(e) => setPersonalAlertAt(e.target.value)}
                        className="flex-1 rounded border border-slate-300 px-2 py-1 text-xs"
                      />
                      <button
                        type="submit"
                        disabled={!personalAlertAt || addPersonalAlertMut.isPending}
                        className="lt-btn-accent px-2.5 py-1"
                      >
                        <BellPlus className="h-3.5 w-3.5" aria-hidden="true" />
                        <span>加提醒</span>
                      </button>
                    </div>
                  </form>
                </div>
              </div>
            )}

            {selectedEvent.source_type === "schedule" && (
              <div className="flex flex-col gap-2 border-t border-slate-100 pt-3">
                <button
                  type="button"
                  disabled={updateScheduleMut.isPending}
                  onClick={() =>
                    updateScheduleMut.mutate({
                      scheduleId: selectedEvent.record_id,
                      payload: { is_completed: !selectedEvent.is_completed },
                    })
                  }
                  className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-700"
                >
                  <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>{selectedEvent.is_completed ? "重新打开日程" : "标记已完成"}</span>
                </button>
                <button
                  type="button"
                  disabled={deleteScheduleMut.isPending}
                  onClick={() => {
                    if (window.confirm(`确认删除日程「${selectedEvent.title}」？`)) {
                      deleteScheduleMut.mutate(selectedEvent.record_id);
                    }
                  }}
                  className="lt-btn-danger w-full"
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>删除此日程</span>
                </button>
              </div>
            )}
          </aside>
        )}
      </div>

      <ModalDialog
        open={icsModalOpen}
        onClose={() => setIcsModalOpen(false)}
        title="手机与桌面日历实时订阅 (.ics)"
        subtitle="支持 Apple 日历、Outlook、企业微信日历导入；包含 VEVENT 与 VTODO"
        lightDismiss
      >
        <div className="space-y-4 text-sm">
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-2">
            <p className="text-xs font-semibold text-slate-700">
              当前视角订阅地址（{scope === "my" ? "仅我的案件与个人日程" : "团队全部可访问案件"}）
            </p>
            <div className="flex items-center gap-2">
              <input
                type="text"
                readOnly
                aria-label="ICS 订阅 URL"
                value={icsUrl}
                className="flex-1 rounded border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-mono text-slate-700"
              />
              <button type="button" onClick={copyIcsUrl} className="lt-btn-primary py-1.5">
                {copiedIcs ? (
                  <Check className="h-3.5 w-3.5" aria-hidden="true" />
                ) : (
                  <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                <span>{copiedIcs ? "已复制" : "复制"}</span>
              </button>
            </div>
          </div>
          <ul className="list-disc pl-5 text-xs text-slate-600 space-y-1">
            <li>
              <strong>iPhone / Mac 日历：</strong>打开“日历” → “添加日历” → “添加订阅日历”，粘贴上方地址并设置刷新频率为每15分钟。
            </li>
            <li>
              <strong>安全提醒：</strong>订阅地址内含您的专属密钥，如泄露可点击下方按钮立即作废旧链接。
            </li>
          </ul>
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              disabled={rotateIcsMut.isPending}
              onClick={() => rotateIcsMut.mutate()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-100"
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{rotateIcsMut.isPending ? "重置中..." : "重置 ICS Token"}</span>
            </button>
          </div>
        </div>
      </ModalDialog>

      <ModalDialog
        open={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        title="新建日程或自定义期限"
      >
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            createItemMut.mutate();
          }}
          className="space-y-4"
        >
          {createError && (
            <div role="alert" className="lt-alert-error">
              {createError}
            </div>
          )}

          <div className="lt-segmented">
            <button
              type="button"
              onClick={() => setCreateKind("schedule")}
              className={`rounded-full px-4 py-1.5 text-xs font-semibold transition-colors ${
                createKind === "schedule"
                  ? "bg-[var(--md-sys-color-primary)] text-[var(--md-sys-color-on-primary)] shadow-sm"
                  : "text-[var(--md-sys-color-on-surface-variant)]"
              }`}
            >
              具体日程（开庭 / 会见 / 阅卷）
            </button>
            <button
              type="button"
              onClick={() => {
                setCreateKind("deadline");
                if (!newCaseId && casesQuery.data?.[0]) {
                  setNewCaseId(String(casesQuery.data[0].id));
                }
              }}
              className={`rounded-full px-4 py-1.5 text-xs font-semibold transition-colors ${
                createKind === "deadline"
                  ? "bg-[var(--md-sys-color-primary)] text-[var(--md-sys-color-on-primary)] shadow-sm"
                  : "text-[var(--md-sys-color-on-surface-variant)]"
              }`}
            >
              自定义期限节点
            </button>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="cal-create-case" className="block text-xs font-semibold text-slate-700 mb-1">
                关联案件 {createKind === "deadline" ? "*" : "（可选）"}
              </label>
              <select
                id="cal-create-case"
                value={newCaseId}
                required={createKind === "deadline"}
                onChange={(e) => setNewCaseId(e.target.value)}
                className="lt-input"
              >
                {createKind === "schedule" ? (
                  <option value="">不关联特定案件（个人日程）</option>
                ) : (
                  <option value="">请选择关联案件...</option>
                )}
                {(casesQuery.data ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.case_title} {c.case_number ? `(${c.case_number})` : ""}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label htmlFor="cal-create-risk" className="block text-xs font-semibold text-slate-700 mb-1">
                风险等级
              </label>
              <select
                id="cal-create-risk"
                value={newRiskLevel}
                onChange={(e) => setNewRiskLevel(e.target.value as RiskLevel)}
                className="lt-input"
              >
                <option value="critical">致命死线</option>
                <option value="high">刚性程序</option>
                <option value="normal">常规节点</option>
                <option value="low">内部待办</option>
              </select>
            </div>
          </div>

          <div>
            <label htmlFor="cal-create-title" className="block text-xs font-semibold text-slate-700 mb-1">
              标题 *
            </label>
            <input
              id="cal-create-title"
              type="text"
              required
              autoFocus
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder={
                createKind === "schedule"
                  ? "例如 一审第一次开庭 / 看守所首次会见"
                  : "例如 补充提交证据截止日"
              }
              className="lt-input"
            />
          </div>

          {createKind === "schedule" ? (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label htmlFor="cal-sched-cat" className="block text-xs font-semibold text-slate-700 mb-1">
                    日程类型
                  </label>
                  <select
                    id="cal-sched-cat"
                    value={newSchedCat}
                    onChange={(e) => setNewSchedCat(e.target.value)}
                    className="lt-input"
                  >
                    {Object.entries(SCHEDULE_CATEGORY_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor="cal-sched-loc" className="block text-xs font-semibold text-slate-700 mb-1">
                    地点 / 法庭 / 看守所
                  </label>
                  <input
                    id="cal-sched-loc"
                    type="text"
                    value={newLocation}
                    onChange={(e) => setNewLocation(e.target.value)}
                    placeholder="例如 上海一中院第十二法庭"
                    className="lt-input"
                  />
                </div>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label htmlFor="cal-sched-start" className="block text-xs font-semibold text-slate-700 mb-1">
                    开始时间 *
                  </label>
                  <input
                    id="cal-sched-start"
                    type="datetime-local"
                    required
                    value={newStartDt}
                    onChange={(e) => setNewStartDt(e.target.value)}
                    className="lt-input"
                  />
                </div>
                <div>
                  <label htmlFor="cal-sched-end" className="block text-xs font-semibold text-slate-700 mb-1">
                    结束时间 *
                  </label>
                  <input
                    id="cal-sched-end"
                    type="datetime-local"
                    required
                    value={newEndDt}
                    onChange={(e) => setNewEndDt(e.target.value)}
                    className="lt-input"
                  />
                </div>
              </div>
            </>
          ) : (
            <div className="space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label htmlFor="cal-dl-date" className="block text-xs font-semibold text-slate-700 mb-1">
                    到期日 *
                  </label>
                  <input
                    id="cal-dl-date"
                    type="date"
                    required
                    value={newDueDate}
                    onChange={(e) => setNewDueDate(e.target.value)}
                    className="lt-input"
                  />
                </div>
                <div>
                  <label htmlFor="cal-dl-basis" className="block text-xs font-semibold text-slate-700 mb-1">
                    依据或备注
                  </label>
                  <input
                    id="cal-dl-basis"
                    type="text"
                    value={newLegalBasis}
                    onChange={(e) => setNewLegalBasis(e.target.value)}
                    placeholder="例如 法官口头指定/双方约定"
                    className="lt-input"
                  />
                </div>
              </div>
              <label className="inline-flex items-center gap-2 text-xs text-slate-700 cursor-pointer">
                <input
                  type="checkbox"
                  checked={newApplyRoll}
                  onChange={(e) => setNewApplyRoll(e.target.checked)}
                />
                <span>如遇国家法定节假日或周末，自动顺延至首个工作日</span>
              </label>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setCreateModalOpen(false)}
              className="lt-btn-secondary"
            >
              取消
            </button>
            <button type="submit" disabled={createItemMut.isPending} className="lt-btn-primary">
              {createItemMut.isPending ? "保存中..." : "确认创建"}
            </button>
          </div>
        </form>
      </ModalDialog>
    </div>
  );
}
