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
  Ban,
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
  X,
} from "lucide-react";
import { authApi, calendarApi, casesApi, inboxApi, remindersApi, usersApi } from "@/api/client";
import { ModalDialog } from "@/components/ModalDialog";
import { CategoryBadge, RiskBadge } from "@/components/StatusBadges";
import {
  ConfirmDialog,
  MdCheckbox,
  MdSegmentedButton,
  MdSelect,
} from "@/components/ui";
import { useAuth } from "@/context/AuthContext";
import {
  CASE_CATEGORY_META,
  HOLIDAY_ROLL_LABELS,
  NOTIFY_TARGET_LABELS,
  SCHEDULE_CATEGORY_LABELS,
  copyTextSafely,
  formatShanghaiDateTime,
  toShanghaiIsoWithOffset,
  todayShanghaiIso,
} from "@/lib/domain";
import type {
  CalendarEventItem,
  CaseCategory,
  InboxTriageRead,
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
  const [icsScope, setIcsScope] = useState<"my" | "team">("my");
  const [categoryFilter, setCategoryFilter] = useState<CaseCategory | "">("");
  const [lawyerFilterId, setLawyerFilterId] = useState<number | null>(null);
  const [includeCompleted, setIncludeCompleted] = useState(false);
  const [includeTentative, setIncludeTentative] = useState(true);
  const [visibleRange, setVisibleRange] = useState<{ start: string; end: string } | null>(null);

  const [selectedEvent, setSelectedEvent] = useState<CalendarEventItem | null>(null);
  const [icsModalOpen, setIcsModalOpen] = useState(false);
  const [icsMsg, setIcsMsg] = useState<string | null>(null);
  const [icsErr, setIcsErr] = useState<string | null>(null);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createKind, setCreateKind] = useState<"schedule" | "deadline">("schedule");
  const [copiedIcs, setCopiedIcs] = useState(false);
  const [deleteScheduleTarget, setDeleteScheduleTarget] = useState<CalendarEventItem | null>(null);

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
    return deadlineDetailQuery.data?.deadlines.find((d) => d.id === selectedEvent.record_id) ?? null;
  }, [deadlineDetailQuery.data, selectedEvent]);

  useEffect(() => {
    if (!activeDeadline) return;
    setNotifyTargetType(activeDeadline.notify_target_type);
    setEscalateSupervisor(activeDeadline.escalate_to_supervisor);
    setOverrideDate(activeDeadline.due_date);
  }, [activeDeadline]);

  const ownerPills = useMemo(() => {
    if (scope !== "team") return [];
    const map = new Map<number, { id: number; name: string; color: string }>();
    for (const u of usersQuery.data ?? []) {
      if (!u.is_active) continue;
      map.set(u.id, { id: u.id, name: u.full_name, color: u.calendar_color || "#1b56b3" });
    }
    for (const ev of calendarQuery.data?.items ?? []) {
      if (ev.owner_id && ev.owner_name && !map.has(ev.owner_id)) {
        map.set(ev.owner_id, {
          id: ev.owner_id,
          name: ev.owner_name,
          color: ev.color || "#1b56b3",
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

  const fcEvents = useMemo(
    () =>
      filteredItems.map((ev) => ({
        id: ev.id,
        title: ev.case_title ? `[${ev.case_title}] ${ev.title}` : ev.title,
        start: ev.start,
        end: ev.end,
        allDay: ev.all_day,
        backgroundColor: ev.is_tentative ? "#eff3f9" : ev.color,
        borderColor: ev.is_tentative ? "#6c7484" : ev.color,
        textColor: ev.is_tentative ? "#181c23" : "#ffffff",
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
    queryClient.invalidateQueries({ queryKey: ["inbox"] });
  };

  const quickConfirmTriageMut = useMutation({
    mutationFn: async (ev: CalendarEventItem) => {
      if (!ev.case_id) throw new Error("未关联案件，请前往收件箱选择案件后确认");
      const triageItems: InboxTriageRead[] = await queryClient.fetchQuery({
        queryKey: ["inbox", "items"],
        queryFn: () => inboxApi.list({ limit: 500 }),
      });
      const triage = triageItems.find((it) => it.id === ev.record_id);
      const p = triage?.extracted_payload ?? {};
      const cleanTitle = ev.title.replace(/^\[待确认\]\s*/, "").trim() || "开庭审理";
      const extractedEventType =
        typeof p.event_type_guess === "string" && p.event_type_guess.trim()
          ? p.event_type_guess.trim()
          : ev.all_day
            ? null
            : null;
      const extractedEventDate =
        typeof p.event_date === "string" && /^\d{4}-\d{2}-\d{2}/.test(p.event_date)
          ? p.event_date.slice(0, 10)
          : extractedEventType
            ? ev.start.slice(0, 10)
            : null;
      const hasHearing = Boolean(p.hearing_time) || !ev.all_day;

      return inboxApi.confirm(ev.record_id, {
        case_id: ev.case_id,
        event_type: extractedEventType,
        event_date: extractedEventDate,
        authority_specified_days:
          typeof p.authority_specified_days === "number" ? p.authority_specified_days : null,
        new_stage:
          (typeof p.suggested_stage === "string" && p.suggested_stage) ||
          (typeof p.stage_suggestion === "string" && p.stage_suggestion) ||
          null,
        create_hearing: hasHearing
          ? {
              category: "hearing",
              title: cleanTitle,
              start_time: ev.start,
              end_time: ev.end,
              location:
                (typeof p.hearing_location === "string" && p.hearing_location) ||
                ev.location ||
                null,
              risk_level: ev.risk_level,
            }
          : null,
      });
    },
    onSuccess: (res) => {
      invalidateAllCalendar();
      setSelectedEvent(null);
      setErrorMsg(null);
      setActionMsg(`已就地确认入案（派生 ${res.generated_deadline_count} 条期限），并同步日历`);
    },
    onError: (err: Error) => {
      setActionMsg(null);
      setErrorMsg(err.message);
    },
  });

  const quickIgnoreTriageMut = useMutation({
    mutationFn: (triageId: number) => inboxApi.ignore(triageId),
    onSuccess: () => {
      invalidateAllCalendar();
      setSelectedEvent(null);
      setErrorMsg(null);
      setActionMsg("已忽略该待确认预览日程");
    },
    onError: (err: Error) => {
      setActionMsg(null);
      setErrorMsg(err.message);
    },
  });

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
      setDeleteScheduleTarget(null);
      setSelectedEvent(null);
      setErrorMsg(null);
      setActionMsg("日程已删除");
    },
    onError: (err: Error) => {
      setDeleteScheduleTarget(null);
      setActionMsg(null);
      setErrorMsg(err.message);
    },
  });

  const createItemMut = useMutation({
    mutationFn: async () => {
      if (createKind === "schedule") {
        const startIso = toShanghaiIsoWithOffset(newStartDt);
        const endIso = toShanghaiIsoWithOffset(newEndDt);
        const startMs = new Date(startIso).getTime();
        const endMs = new Date(endIso).getTime();
        if (Number.isNaN(startMs) || Number.isNaN(endMs) || endMs <= startMs) {
          throw new Error("日程结束时间必须晚于开始时间");
        }
        return calendarApi.createSchedule({
          case_id: newCaseId ? Number(newCaseId) : null,
          category: newSchedCat,
          title: newTitle.trim(),
          start_time: startIso,
          end_time: endIso,
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
      setIcsErr(null);
      setIcsMsg("已更换订阅链接，请重新复制并添加到日历订阅");
    },
    onError: (err: Error) => {
      setIcsMsg(null);
      setIcsErr(err.message);
    },
  });

  const handleEventClick = (arg: EventClickArg) => {
    const ev = arg.event.extendedProps as CalendarEventItem;
    setSelectedEvent(ev);
    setActionMsg(null);
    setErrorMsg(null);
    setOverrideDate(ev.due_date || ev.start.slice(0, 10));
    setOverrideReason("");
    setPersonalAlertAt("");
    if (window.innerWidth < 1280) {
      window.setTimeout(() => {
        detailAsideRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 60);
    }
  };

  const handleDatesSet = (arg: DatesSetArg) => {
    const start = arg.startStr.slice(0, 10);
    const end = arg.endStr.slice(0, 10);
    setVisibleRange((prev) => (prev?.start === start && prev?.end === end ? prev : { start, end }));
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
    ? `${window.location.origin}/api/calendar/ics/${user.ics_feed_token}.ics?scope=${icsScope}`
    : "";

  const copyIcsUrl = async () => {
    if (!icsUrl) return;
    const ok = await copyTextSafely(icsUrl);
    if (!ok) {
      setIcsMsg(null);
      setIcsErr("剪贴板权限受限，请手动选中复制");
      return;
    }
    setIcsErr(null);
    setCopiedIcs(true);
    window.setTimeout(() => setCopiedIcs(false), 2000);
  };

  const ownPersonalAlerts = useMemo(() => {
    if (!activeDeadline || !user) return [];
    return (activeDeadline.personal_custom_alerts ?? []).filter((a) => a.user_id === user.id);
  }, [activeDeadline, user]);

  const initialCalView =
    typeof window !== "undefined" && window.innerWidth < 768 ? "listMonth" : "dayGridMonth";

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-[var(--md-sys-color-on-surface)]">日程日历</h1>
        <div className="flex flex-wrap items-center gap-2.5">
          <MdSegmentedButton<"my" | "team">
            value={scope}
            onChange={(next) => {
              setScope(next);
              if (next === "my") setLawyerFilterId(null);
            }}
            ariaLabel="日历视图视角"
            options={[
              { value: "my", label: "我的日程" },
              { value: "team", label: "全所总览" },
            ]}
          />

          <button
            type="button"
            onClick={() => {
              setIcsScope(scope);
              setIcsMsg(null);
              setIcsErr(null);
              setIcsModalOpen(true);
            }}
            className="lt-btn-secondary"
          >
            <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
            <span>日历订阅</span>
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
        <div role="alert" className="lt-alert-error space-y-1.5">
          <div className="flex items-center gap-2 font-semibold text-sm">
            <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
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

          <div className="flex flex-wrap items-center gap-4 text-xs">
            <MdCheckbox
              checked={includeTentative}
              onChange={setIncludeTentative}
              label="显示待确认预览"
            />
            <MdCheckbox
              checked={includeCompleted}
              onChange={setIncludeCompleted}
              label="显示已办结"
            />
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
          className={`${selectedEvent ? "xl:col-span-8" : "xl:col-span-12"} lt-card p-4 overflow-x-auto`}
        >
          {calendarQuery.isError ? (
            <div role="alert" className="lt-alert-error flex items-center justify-between gap-3">
              <span>日历数据加载失败，请重试。</span>
              <button type="button" onClick={() => calendarQuery.refetch()} className="lt-btn-secondary py-1">
                重新加载
              </button>
            </div>
          ) : calendarQuery.isLoading && !calendarQuery.data ? (
            <p className="py-16 text-center text-sm text-[var(--md-sys-color-on-surface-variant)]">
              正在加载协同日历...
            </p>
          ) : (
            <FullCalendar
              ref={calendarRef}
              plugins={[dayGridPlugin, timeGridPlugin, listPlugin, interactionPlugin]}
              initialView={initialCalView}
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
            <div className="flex items-start justify-between gap-2 border-b border-[var(--md-sys-color-outline-variant)]/60 pb-3">
              <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <RiskBadge risk={selectedEvent.risk_level} />
                  {selectedEvent.case_category && <CategoryBadge category={selectedEvent.case_category} />}
                  {selectedEvent.is_tentative && (
                    <span className="rounded-full bg-[var(--md-sys-color-surface-container-high)] px-2 py-0.5 text-xs font-medium text-[var(--md-sys-color-on-surface-variant)]">
                      待确认
                    </span>
                  )}
                  {selectedEvent.is_manual_override && (
                    <span className="rounded-full bg-[#fef1d6] px-2 py-0.5 text-xs font-medium text-[#7a4b00]">
                      人工覆写
                    </span>
                  )}
                </div>
                <h2 className="text-base font-bold text-[var(--md-sys-color-on-surface)]">
                  {selectedEvent.title}
                </h2>
                {selectedEvent.case_title && (
                  <p className="text-xs text-[var(--md-sys-color-on-surface-variant)]">
                    {selectedEvent.case_title}{" "}
                    {selectedEvent.case_number ? `（${selectedEvent.case_number}）` : ""}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={() => setSelectedEvent(null)}
                className="lt-btn-secondary py-1 px-2.5 text-xs shrink-0"
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
                <dt className="text-[var(--md-sys-color-on-surface-variant)]">时间 / 到期日</dt>
                <dd className="font-semibold text-[var(--md-sys-color-on-surface)] tabular-nums">
                  {selectedEvent.all_day
                    ? selectedEvent.start
                    : `${formatShanghaiDateTime(selectedEvent.start)} ~ ${formatShanghaiDateTime(
                        selectedEvent.end
                      )}`}
                </dd>
              </div>
              {selectedEvent.raw_due_date && (
                <div className="flex justify-between gap-2">
                  <dt className="text-[var(--md-sys-color-on-surface-variant)]">顺延前原日</dt>
                  <dd className="tabular-nums">{selectedEvent.raw_due_date}</dd>
                </div>
              )}
              {selectedEvent.holiday_roll_mode && (
                <div className="flex justify-between gap-2">
                  <dt className="text-[var(--md-sys-color-on-surface-variant)]">顺延规则</dt>
                  <dd className="text-right">{HOLIDAY_ROLL_LABELS[selectedEvent.holiday_roll_mode]}</dd>
                </div>
              )}
              {selectedEvent.roll_reason && (
                <div className="lt-surface-tonal p-2.5">
                  <span className="font-semibold">顺延说明：</span>
                  {selectedEvent.roll_reason}
                </div>
              )}
              {selectedEvent.legal_basis && (
                <div className="rounded-[var(--md-sys-shape-corner-md)] bg-[var(--md-sys-color-primary-container)]/40 p-2.5 text-[var(--md-sys-color-on-primary-container)]">
                  <span className="font-semibold">法律依据：</span>
                  {selectedEvent.legal_basis}
                </div>
              )}
              {selectedEvent.location && (
                <div className="flex justify-between gap-2">
                  <dt className="text-[var(--md-sys-color-on-surface-variant)]">地点</dt>
                  <dd>{selectedEvent.location}</dd>
                </div>
              )}
              {selectedEvent.owner_name && (
                <div className="flex justify-between gap-2">
                  <dt className="text-[var(--md-sys-color-on-surface-variant)]">负责人</dt>
                  <dd>{selectedEvent.owner_name}</dd>
                </div>
              )}
            </dl>

            {selectedEvent.case_id && (
              <Link to={`/cases/${selectedEvent.case_id}`} className="lt-btn-secondary w-full">
                <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                <span>打开所属案件详情</span>
              </Link>
            )}

            {selectedEvent.source_type === "triage_tentative" && (
              <div className="space-y-2.5 rounded-[var(--md-sys-shape-corner-md)] border border-[#f6d48b] bg-[#fef1d6] p-3 text-xs text-[#7a4b00]">
                <p>该事项来自收件箱待确认短信，确认入案后将转为正式日程或法定期限。</p>
                <div className="flex flex-wrap items-center gap-2">
                  {selectedEvent.case_id && (
                    <button
                      type="button"
                      disabled={quickConfirmTriageMut.isPending}
                      onClick={() => quickConfirmTriageMut.mutate(selectedEvent)}
                      className="lt-btn-primary py-1.5 px-3"
                    >
                      <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                      <span>{quickConfirmTriageMut.isPending ? "确认中..." : "一键确认入案"}</span>
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={quickIgnoreTriageMut.isPending}
                    onClick={() => quickIgnoreTriageMut.mutate(selectedEvent.record_id)}
                    className="lt-btn-secondary py-1.5 px-3"
                  >
                    <Ban className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>忽略</span>
                  </button>
                </div>
                <Link
                  to={`/inbox?id=${selectedEvent.record_id}`}
                  className="inline-flex items-center gap-1 font-semibold underline"
                >
                  前往收件箱核对详情 →
                </Link>
              </div>
            )}

            {selectedEvent.source_type === "deadline" && (
              <div className="space-y-4 border-t border-[var(--md-sys-color-outline-variant)]/60 pt-3">
                <button
                  type="button"
                  disabled={updateDeadlineMut.isPending}
                  onClick={() =>
                    updateDeadlineMut.mutate({
                      deadlineId: selectedEvent.record_id,
                      payload: { is_completed: !selectedEvent.is_completed },
                    })
                  }
                  className={selectedEvent.is_completed ? "lt-btn-secondary w-full" : "lt-btn-primary w-full"}
                >
                  {selectedEvent.is_completed ? (
                    <>
                      <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                      <span>重新打开此期限</span>
                    </>
                  ) : (
                    <>
                      <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                      <span>标记为已办结</span>
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
                  className="lt-surface-tonal space-y-2.5 p-3"
                >
                  <p className="text-xs font-semibold">人工调整到期日（重算时不覆写）</p>
                  <div>
                    <label htmlFor="override-due-date" className="block text-[11px] mb-1">
                      调整后到期日
                    </label>
                    <input
                      id="override-due-date"
                      type="date"
                      required
                      value={overrideDate}
                      onChange={(e) => setOverrideDate(e.target.value)}
                      className="lt-input text-xs py-1.5"
                    />
                  </div>
                  <div>
                    <label htmlFor="override-reason" className="block text-[11px] mb-1">
                      调整原因说明
                    </label>
                    <input
                      id="override-reason"
                      type="text"
                      value={overrideReason}
                      onChange={(e) => setOverrideReason(e.target.value)}
                      placeholder="例如 法官批准延期"
                      className="lt-input text-xs py-1.5"
                    />
                  </div>
                  <button type="submit" disabled={updateDeadlineMut.isPending} className="lt-btn-primary w-full py-1.5">
                    保存手动调整
                  </button>
                </form>

                <div className="lt-surface-tonal space-y-3 p-3">
                  <p className="text-xs font-semibold">提醒范围 & 个人定点闹钟</p>
                  <div>
                    <label htmlFor="dl-notify-type" className="block text-[11px] mb-1">
                      接收范围
                    </label>
                    <MdSelect
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
                      className="py-1.5 text-xs"
                    >
                      {(Object.keys(NOTIFY_TARGET_LABELS) as NotifyTargetType[]).map((k) => (
                        <option key={k} value={k}>
                          {NOTIFY_TARGET_LABELS[k]}
                        </option>
                      ))}
                    </MdSelect>

                    {(activeDeadline?.notify_target_type ?? notifyTargetType) === "custom" &&
                      (usersQuery.data?.length ?? 0) > 0 && (
                        <div className="pt-2 space-y-1">
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
                                  className={`lt-chip py-0.5 text-[11px] ${checked ? "lt-chip-active" : ""}`}
                                >
                                  {u.full_name}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      )}
                  </div>

                  <MdCheckbox
                    checked={activeDeadline?.escalate_to_supervisor ?? escalateSupervisor}
                    onChange={(checked) => {
                      setEscalateSupervisor(checked);
                      updateDeadlineMut.mutate({
                        deadlineId: selectedEvent.record_id,
                        payload: { escalate_to_supervisor: checked },
                      });
                    }}
                    label={<span className="text-xs">临期 1 天未办结抄送督办合伙人</span>}
                  />

                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (!personalAlertAt) return;
                      addPersonalAlertMut.mutate({
                        deadlineId: selectedEvent.record_id,
                        fireAt: toShanghaiIsoWithOffset(personalAlertAt),
                      });
                    }}
                    className="space-y-2 border-t border-[var(--md-sys-color-outline-variant)]/55 pt-2.5"
                  >
                    <label htmlFor="personal-alert-dt" className="block text-[11px]">
                      个人定点提醒时间（北京时间）
                    </label>
                    <div className="flex gap-1.5">
                      <input
                        id="personal-alert-dt"
                        type="datetime-local"
                        value={personalAlertAt}
                        onChange={(e) => setPersonalAlertAt(e.target.value)}
                        className="lt-input flex-1 px-2 py-1 text-xs"
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

                    {ownPersonalAlerts.length > 0 && (
                      <ul className="space-y-1 pt-1">
                        {ownPersonalAlerts.map((alert) => (
                          <li
                            key={alert.fire_at}
                            className="flex items-center justify-between gap-2 rounded-[var(--md-sys-shape-corner-xs)] bg-[var(--md-sys-color-surface-container-lowest)] px-2.5 py-1 text-[11px]"
                          >
                            <span className="tabular-nums">{formatShanghaiDateTime(alert.fire_at)}</span>
                            <button
                              type="button"
                              aria-label="删除此个人提醒"
                              disabled={updateDeadlineMut.isPending}
                              onClick={() => {
                                const remaining = ownPersonalAlerts.filter(
                                  (item) => item.fire_at !== alert.fire_at
                                );
                                updateDeadlineMut.mutate({
                                  deadlineId: selectedEvent.record_id,
                                  payload: { personal_custom_alerts: remaining },
                                });
                              }}
                              className="inline-flex h-5 w-5 items-center justify-center rounded-full text-[var(--md-sys-color-on-surface-variant)] hover:bg-[var(--md-sys-color-surface-container)] hover:text-[var(--md-sys-color-error)]"
                            >
                              <X className="h-3 w-3" aria-hidden="true" />
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </form>
                </div>
              </div>
            )}

            {selectedEvent.source_type === "schedule" && (
              <div className="flex flex-col gap-2 border-t border-[var(--md-sys-color-outline-variant)]/60 pt-3">
                <button
                  type="button"
                  disabled={updateScheduleMut.isPending}
                  onClick={() =>
                    updateScheduleMut.mutate({
                      scheduleId: selectedEvent.record_id,
                      payload: { is_completed: !selectedEvent.is_completed },
                    })
                  }
                  className={selectedEvent.is_completed ? "lt-btn-secondary w-full" : "lt-btn-primary w-full"}
                >
                  <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>{selectedEvent.is_completed ? "重新打开日程" : "标记已完成"}</span>
                </button>
                <button
                  type="button"
                  disabled={deleteScheduleMut.isPending}
                  onClick={() => setDeleteScheduleTarget(selectedEvent)}
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

      {/* ICS Subscription Modal */}
      <ModalDialog
        open={icsModalOpen}
        onClose={() => setIcsModalOpen(false)}
        title="手机与电脑日历实时订阅"
        subtitle="支持 Apple 日历、Outlook 与企业微信日历"
        lightDismiss
      >
        <div className="space-y-4 text-sm">
          {icsMsg && (
            <div role="status" className="lt-alert-success">
              {icsMsg}
            </div>
          )}
          {icsErr && (
            <div role="alert" className="lt-alert-error">
              {icsErr}
            </div>
          )}

          <div className="space-y-1.5">
            <span className="block text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)]">
              选择订阅范围：
            </span>
            <MdSegmentedButton<"my" | "team">
              value={icsScope}
              onChange={setIcsScope}
              ariaLabel="日历订阅范围"
              options={[
                { value: "my", label: "仅我的案件与日程" },
                { value: "team", label: "团队全部可访问案件" },
              ]}
            />
          </div>

          <div className="lt-surface-tonal p-3.5 space-y-2">
            <div className="flex items-center gap-2">
              <input
                type="text"
                readOnly
                aria-label="日历订阅链接"
                value={icsUrl}
                className="lt-input flex-1 py-1.5 text-xs font-mono"
              />
              <button type="button" onClick={copyIcsUrl} className="lt-btn-primary py-1.5">
                {copiedIcs ? (
                  <Check className="h-3.5 w-3.5" aria-hidden="true" />
                ) : (
                  <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                <span>{copiedIcs ? "已复制" : "复制链接"}</span>
              </button>
            </div>
          </div>

          <ul className="list-disc pl-5 text-xs text-[var(--md-sys-color-on-surface-variant)] space-y-1">
            <li>iPhone / Mac 打开「日历」→ 添加订阅日历，粘贴上方地址并建议设为每 15 分钟刷新。</li>
            <li>订阅链接包含个人专属凭证，如需作废旧链接可点击下方重置。</li>
          </ul>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              disabled={rotateIcsMut.isPending}
              onClick={() => rotateIcsMut.mutate()}
              className="lt-btn-secondary"
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{rotateIcsMut.isPending ? "重置中..." : "重置订阅链接"}</span>
            </button>
            <button type="button" onClick={() => setIcsModalOpen(false)} className="lt-btn-primary">
              完成
            </button>
          </div>
        </div>
      </ModalDialog>

      {/* Create Schedule / Custom Deadline Modal */}
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

          <MdSegmentedButton<"schedule" | "deadline">
            value={createKind}
            onChange={(next) => {
              setCreateKind(next);
              if (next === "deadline" && !newCaseId && casesQuery.data?.[0]) {
                setNewCaseId(String(casesQuery.data[0].id));
              }
            }}
            ariaLabel="新建事项类型"
            options={[
              { value: "schedule", label: "具体日程（开庭 / 会见 / 阅卷）" },
              { value: "deadline", label: "自定义期限节点" },
            ]}
          />

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="cal-create-case" className="block text-xs font-semibold mb-1">
                关联案件 {createKind === "deadline" ? "*" : "（可选）"}
              </label>
              <MdSelect
                id="cal-create-case"
                value={newCaseId}
                required={createKind === "deadline"}
                onChange={(e) => setNewCaseId(e.target.value)}
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
              </MdSelect>
            </div>

            <div>
              <label htmlFor="cal-create-risk" className="block text-xs font-semibold mb-1">
                风险等级
              </label>
              <MdSelect
                id="cal-create-risk"
                value={newRiskLevel}
                onChange={(e) => setNewRiskLevel(e.target.value as RiskLevel)}
              >
                <option value="critical">致命死线</option>
                <option value="high">刚性程序</option>
                <option value="normal">常规节点</option>
                <option value="low">内部待办</option>
              </MdSelect>
            </div>
          </div>

          <div>
            <label htmlFor="cal-create-title" className="block text-xs font-semibold mb-1">
              标题 *
            </label>
            <input
              id="cal-create-title"
              type="text"
              required
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
                  <label htmlFor="cal-sched-cat" className="block text-xs font-semibold mb-1">
                    日程类型
                  </label>
                  <MdSelect
                    id="cal-sched-cat"
                    value={newSchedCat}
                    onChange={(e) => setNewSchedCat(e.target.value)}
                  >
                    {Object.entries(SCHEDULE_CATEGORY_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v}
                      </option>
                    ))}
                  </MdSelect>
                </div>
                <div>
                  <label htmlFor="cal-sched-loc" className="block text-xs font-semibold mb-1">
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
                  <label htmlFor="cal-sched-start" className="block text-xs font-semibold mb-1">
                    开始时间（北京时间） *
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
                  <label htmlFor="cal-sched-end" className="block text-xs font-semibold mb-1">
                    结束时间（北京时间） *
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
                  <label htmlFor="cal-dl-date" className="block text-xs font-semibold mb-1">
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
                  <label htmlFor="cal-dl-basis" className="block text-xs font-semibold mb-1">
                    依据或备注
                  </label>
                  <input
                    id="cal-dl-basis"
                    type="text"
                    value={newLegalBasis}
                    onChange={(e) => setNewLegalBasis(e.target.value)}
                    placeholder="例如 法官口头指定 / 双方约定"
                    className="lt-input"
                  />
                </div>
              </div>
              <MdCheckbox
                checked={newApplyRoll}
                onChange={setNewApplyRoll}
                label="如遇国家法定节假日或周末，自动顺延至首个工作日"
              />
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={() => setCreateModalOpen(false)} className="lt-btn-secondary">
              取消
            </button>
            <button type="submit" disabled={createItemMut.isPending} className="lt-btn-primary">
              {createItemMut.isPending ? "保存中..." : "确认创建"}
            </button>
          </div>
        </form>
      </ModalDialog>

      <ConfirmDialog
        open={Boolean(deleteScheduleTarget)}
        onClose={() => setDeleteScheduleTarget(null)}
        onConfirm={() => {
          if (deleteScheduleTarget) deleteScheduleMut.mutate(deleteScheduleTarget.record_id);
        }}
        loading={deleteScheduleMut.isPending}
        title="删除日程安排"
        description={`确定删除日程「${deleteScheduleTarget?.title ?? ""}」吗？删除后不可撤销。`}
        confirmLabel="确认删除"
        tone="danger"
      />
    </div>
  );
}
