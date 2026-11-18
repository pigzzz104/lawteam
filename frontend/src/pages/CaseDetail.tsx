import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Archive,
  ArrowLeft,
  CalendarClock,
  CheckCircle2,
  Download,
  FileText,
  FolderOpen,
  History,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldAlert,
  Upload,
  Users,
} from "lucide-react";
import { calendarApi, casesApi, rulesApi, usersApi } from "@/api/client";
import { ModalDialog } from "@/components/ModalDialog";
import { CategoryBadge, CustodyBadge, RiskBadge } from "@/components/StatusBadges";
import {
  CASE_MEMBER_ROLE_LABELS,
  EVENT_TYPE_PRESETS,
  HOLIDAY_ROLL_LABELS,
  SCHEDULE_CATEGORY_LABELS,
  SERVICE_METHOD_OPTIONS,
  STANDARD_CASE_SUBFOLDERS,
  formatShanghaiDateTime,
  getEventTypeLabel,
  getServiceMethodLabel,
  todayShanghaiIso,
} from "@/lib/domain";
import type { CaseMemberRole, Deadline, RiskLevel } from "@/types/api";

type TabKey = "events" | "deadlines" | "schedules" | "files" | "members" | "audit";

function formatAuditDetail(detail: Record<string, unknown>): string {
  const entries = Object.entries(detail || {}).filter(
    ([, v]) => v !== null && v !== undefined && v !== ""
  );
  if (entries.length === 0) return "-";
  return entries
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" · ");
}

export function CaseDetail() {
  const { caseId } = useParams();
  const id = Number(caseId);
  const queryClient = useQueryClient();

  const [activeTab, setActiveTab] = useState<TabKey>("events");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [dlModalError, setDlModalError] = useState<string | null>(null);
  const [overrideError, setOverrideError] = useState<string | null>(null);
  const [custodyError, setCustodyError] = useState<string | null>(null);

  // Event creation form
  const [eventType, setEventType] = useState("");
  const [customEventType, setCustomEventType] = useState("");
  const [eventDate, setEventDate] = useState(todayShanghaiIso());
  const [serviceMethod, setServiceMethod] = useState("electronic");
  const [authorityDays, setAuthorityDays] = useState("");
  const [eventNotes, setEventNotes] = useState("");

  // Custom deadline modal
  const [dlModalOpen, setDlModalOpen] = useState(false);
  const [dlTitle, setDlTitle] = useState("");
  const [dlDate, setDlDate] = useState(todayShanghaiIso());
  const [dlBasis, setDlBasis] = useState("");
  const [dlRisk, setDlRisk] = useState<RiskLevel>("high");
  const [dlApplyRoll, setDlApplyRoll] = useState(true);

  // Override deadline modal
  const [overrideTarget, setOverrideTarget] = useState<Deadline | null>(null);
  const [overrideDueDate, setOverrideDueDate] = useState("");
  const [overrideReason, setOverrideReason] = useState("");

  // Quick schedule form
  const [schedTitle, setSchedTitle] = useState("");
  const [schedCat, setSchedCat] = useState("hearing");
  const [schedStart, setSchedStart] = useState(`${todayShanghaiIso()}T09:30`);
  const [schedEnd, setSchedEnd] = useState(`${todayShanghaiIso()}T11:00`);
  const [schedLoc, setSchedLoc] = useState("");

  // File upload form
  const [uploadSubfolder, setUploadSubfolder] = useState<string>(STANDARD_CASE_SUBFOLDERS[0]);
  const [uploadDocSubtype, setUploadDocSubtype] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  // Criminal custody quick edit
  const [custodyEditOpen, setCustodyEditOpen] = useState(false);
  const [editCustodyStatus, setEditCustodyStatus] = useState("");
  const [editCustodyPlace, setEditCustodyPlace] = useState("");

  // Members editor
  const [draftMembers, setDraftMembers] = useState<Record<number, CaseMemberRole | "none">>({});
  const [membersDirty, setMembersDirty] = useState(false);

  useEffect(() => {
    setActiveTab("events");
    setFeedback(null);
    setErrorMsg(null);
    setDlModalError(null);
    setOverrideError(null);
    setCustodyError(null);
    setEventType("");
    setCustomEventType("");
    setOverrideTarget(null);
    setCustodyEditOpen(false);
    setDraftMembers({});
    setMembersDirty(false);
  }, [id]);

  const detailQuery = useQuery({
    queryKey: ["case-detail", id],
    queryFn: () => casesApi.detail(id),
    enabled: Number.isFinite(id) && id > 0,
  });

  const schedulesQuery = useQuery({
    queryKey: ["case-schedules", id],
    queryFn: () => calendarApi.listSchedules(id),
    enabled: Number.isFinite(id) && id > 0,
  });

  const auditQuery = useQuery({
    queryKey: ["case-audit-logs", id],
    queryFn: () => casesApi.auditLogs(id),
    enabled: Number.isFinite(id) && id > 0 && activeTab === "audit",
  });

  const templatesQuery = useQuery({
    queryKey: ["stage-templates"],
    queryFn: rulesApi.stageTemplates,
  });

  const usersQuery = useQuery({
    queryKey: ["users", "all"],
    queryFn: usersApi.list,
  });

  const detail = detailQuery.data;
  const c = detail?.case;

  const stageChoices = useMemo(() => {
    if (!c || !templatesQuery.data) return [];
    const catTpl = templatesQuery.data.categories[c.case_category];
    if (!catTpl) return [];
    const subStages = catTpl.subtypes?.[c.procedure_subtype];
    return subStages && subStages.length > 0 ? subStages : catTpl.stages;
  }, [c, templatesQuery.data]);

  const categoryEventPresets = useMemo(() => {
    if (!c) return EVENT_TYPE_PRESETS;
    const matched = EVENT_TYPE_PRESETS.filter(
      (p) => !p.categories || p.categories.includes(c.case_category)
    );
    return matched.length > 0 ? matched : EVENT_TYPE_PRESETS;
  }, [c]);

  const refreshCase = () => {
    queryClient.invalidateQueries({ queryKey: ["case-detail", id] });
    queryClient.invalidateQueries({ queryKey: ["case-schedules", id] });
    queryClient.invalidateQueries({ queryKey: ["case-audit-logs", id] });
    queryClient.invalidateQueries({ queryKey: ["cases"] });
    queryClient.invalidateQueries({ queryKey: ["calendar"] });
  };

  const updateCaseMut = useMutation({
    mutationFn: (payload: Parameters<typeof casesApi.update>[1]) =>
      casesApi.update(id, payload),
    onSuccess: () => {
      refreshCase();
      setCustodyEditOpen(false);
      setCustodyError(null);
      setFeedback("案件状态已更新");
      setErrorMsg(null);
    },
    onError: (err: Error) => {
      if (custodyEditOpen) {
        setCustodyError(err.message);
      } else {
        setErrorMsg(err.message);
      }
    },
  });

  const createEventMut = useMutation({
    mutationFn: () => {
      const resolvedType =
        eventType === "__custom__" ? customEventType.trim() : eventType || categoryEventPresets[0]?.value;
      if (!resolvedType) throw new Error("请选择或输入事件类型");
      return casesApi.createEvent(id, {
        event_type: resolvedType,
        event_date: eventDate,
        service_method: serviceMethod,
        authority_specified_days: authorityDays ? Number(authorityDays) : null,
        notes: eventNotes.trim() || null,
      });
    },
    onSuccess: (res) => {
      refreshCase();
      setEventNotes("");
      setAuthorityDays("");
      setFeedback(
        `已记录事件「${getEventTypeLabel(res.event.event_type)}」，自动派生 ${res.derived_deadlines.length} 条法定期限`
      );
      setErrorMsg(null);
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const recalcMut = useMutation({
    mutationFn: (eventId: number) => casesApi.recalculateEvent(id, eventId),
    onSuccess: (res) => {
      refreshCase();
      setErrorMsg(null);
      setFeedback(
        `重算完成，当前匹配 ${res.derived_deadlines.length} 条规则期限（手动覆写项不受影响）`
      );
    },
    onError: (err: Error) => {
      setFeedback(null);
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
    onSuccess: () => {
      refreshCase();
      setOverrideTarget(null);
      setOverrideError(null);
      setErrorMsg(null);
      setFeedback("期限已更新");
    },
    onError: (err: Error) => {
      if (overrideTarget) {
        setOverrideError(err.message);
      } else {
        setErrorMsg(err.message);
      }
    },
  });

  const createCustomDlMut = useMutation({
    mutationFn: () =>
      calendarApi.createDeadline({
        case_id: id,
        title: dlTitle.trim(),
        due_date: dlDate,
        legal_basis: dlBasis.trim() || null,
        risk_level: dlRisk,
        apply_holiday_roll: dlApplyRoll,
      }),
    onSuccess: () => {
      refreshCase();
      setDlModalOpen(false);
      setDlModalError(null);
      setDlTitle("");
      setDlBasis("");
      setErrorMsg(null);
      setFeedback("已新增自定义期限");
    },
    onError: (err: Error) => setDlModalError(err.message),
  });

  const createSchedMut = useMutation({
    mutationFn: () => {
      const startMs = new Date(schedStart).getTime();
      const endMs = new Date(schedEnd).getTime();
      if (Number.isNaN(startMs) || Number.isNaN(endMs) || endMs <= startMs) {
        throw new Error("日程结束时间必须晚于开始时间");
      }
      return calendarApi.createSchedule({
        case_id: id,
        category: schedCat,
        title: schedTitle.trim(),
        start_time: new Date(startMs).toISOString(),
        end_time: new Date(endMs).toISOString(),
        location: schedLoc.trim() || null,
      });
    },
    onSuccess: () => {
      refreshCase();
      setSchedTitle("");
      setSchedLoc("");
      setErrorMsg(null);
      setFeedback("已添加日程安排");
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const toggleSchedMut = useMutation({
    mutationFn: ({ schedId, completed }: { schedId: number; completed: boolean }) =>
      calendarApi.updateSchedule(schedId, { is_completed: completed }),
    onSuccess: () => {
      refreshCase();
      setErrorMsg(null);
    },
    onError: (err: Error) => setErrorMsg(err.message),
  });

  const uploadMut = useMutation({
    mutationFn: () => {
      if (!uploadFile) throw new Error("请先选择文件");
      return casesApi.uploadFile(id, uploadSubfolder, uploadFile, uploadDocSubtype.trim() || undefined);
    },
    onSuccess: (doc) => {
      refreshCase();
      setUploadFile(null);
      setUploadDocSubtype("");
      setErrorMsg(null);
      setFeedback(`文件「${doc.file_name}」已归档至 ${doc.folder_category}`);
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const saveMembersMut = useMutation({
    mutationFn: () => {
      const payload = Object.entries(draftMembers)
        .filter(([, role]) => role !== "none")
        .map(([uid, role]) => ({
          user_id: Number(uid),
          role_in_case: role as CaseMemberRole,
        }));
      return casesApi.replaceMembers(id, payload);
    },
    onSuccess: () => {
      refreshCase();
      setMembersDirty(false);
      setErrorMsg(null);
      setFeedback("办案团队与督办角色已保存");
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const handleDownload = async (relPath: string, filename: string) => {
    setErrorMsg(null);
    try {
      await casesApi.downloadFile(id, relPath, filename);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "文件下载失败");
    }
  };

  if (detailQuery.isLoading) {
    return <p className="py-16 text-center text-sm text-slate-600">正在加载案件详情...</p>;
  }

  if (detailQuery.isError || !detail || !c) {
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-900 space-y-3">
        <p>未能加载案件详情（无权限或案件不存在）。</p>
        <Link to="/cases" className="inline-flex items-center gap-1 font-semibold underline">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" /> 返回案件列表
        </Link>
      </div>
    );
  }

  const effectiveMembersMap: Record<number, CaseMemberRole | "none"> = {};
  if (!membersDirty) {
    for (const u of usersQuery.data ?? []) {
      const existing = detail.members.find((m) => m.user_id === u.id);
      effectiveMembersMap[u.id] = existing ? existing.role_in_case : "none";
    }
  } else {
    Object.assign(effectiveMembersMap, draftMembers);
  }

  const tabs: Array<{ key: TabKey; label: string; count?: number }> = [
    { key: "events", label: "事件推进", count: detail.events.length },
    { key: "deadlines", label: "法定与自定义期限", count: detail.deadlines.length },
    { key: "schedules", label: "日程与开庭", count: schedulesQuery.data?.length ?? 0 },
    { key: "files", label: "归档文书树", count: detail.documents.length },
    { key: "members", label: "团队成员角色", count: detail.members.length },
    { key: "audit", label: "审计日志" },
  ];

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 text-xs text-[var(--md-sys-color-on-surface-variant)]">
        <Link
          to="/cases"
          className="inline-flex items-center gap-1 hover:text-[var(--md-sys-color-on-surface)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          <span>返回案件管理</span>
        </Link>
        <span>/</span>
        <span className="font-mono">{c.case_number || `案件#${c.id}`}</span>
      </div>

      {/* Case Header Card */}
      <section className="lt-card p-6 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <CategoryBadge category={c.case_category} />
              <span className="rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-700">
                {c.procedure_subtype}
              </span>
              {c.is_foreign_related && (
                <span className="rounded border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700">
                  涉外（30日上诉期）
                </span>
              )}
              <CustodyBadge status={c.custody_status} place={c.custody_place} />
              {detail.stale_warning && (
                <span className="inline-flex items-center gap-1 rounded border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-900 tabular-nums">
                  <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                  当前阶段已停留 {detail.stage_days_elapsed} 天
                </span>
              )}
            </div>
            <h1 className="text-2xl font-bold text-slate-900">{c.case_title}</h1>
            <p className="text-sm text-slate-600 font-mono tabular-nums">
              {c.case_number || "暂无正式案号"}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1.5">
              <label htmlFor="case-stage-select" className="text-xs font-semibold text-slate-700">
                当前阶段：
              </label>
              <select
                id="case-stage-select"
                value={c.current_stage}
                onChange={(e) => updateCaseMut.mutate({ current_stage: e.target.value })}
                className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-800"
              >
                {!stageChoices.includes(c.current_stage) && (
                  <option value={c.current_stage}>{c.current_stage}</option>
                )}
                {stageChoices.map((sg) => (
                  <option key={sg} value={sg}>
                    {sg}
                  </option>
                ))}
              </select>
            </div>

            {c.case_category === "criminal" && (
              <button
                type="button"
                onClick={() => {
                  setEditCustodyStatus(c.custody_status || "");
                  setEditCustodyPlace(c.custody_place || "");
                  setCustodyError(null);
                  setCustodyEditOpen(true);
                }}
                className="lt-btn-danger py-1.5"
              >
                更新羁押状态
              </button>
            )}

            <button
              type="button"
              onClick={() => updateCaseMut.mutate({ is_archived: !c.is_archived })}
              className="lt-btn-secondary py-1.5"
            >
              <Archive className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{c.is_archived ? "解除归档" : "结案归档"}</span>
            </button>
          </div>
        </div>

        <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 border-t border-slate-100 pt-4 text-xs">
          <div>
            <dt className="text-slate-600">委托人</dt>
            <dd className="mt-0.5 font-semibold text-slate-900">
              {c.client_name} {c.client_id_last4 ? `(*${c.client_id_last4})` : ""}
            </dd>
          </div>
          <div>
            <dt className="text-slate-600">对方当事人</dt>
            <dd className="mt-0.5 font-medium text-slate-800">{c.opponent_name || "-"}</dd>
          </div>
          <div>
            <dt className="text-slate-600">承办法院/机关</dt>
            <dd className="mt-0.5 font-medium text-slate-800">{c.handling_agency || "-"}</dd>
          </div>
          <div>
            <dt className="text-slate-600">阶段停留天数</dt>
            <dd className="mt-0.5 font-medium text-slate-800 tabular-nums">
              {detail.stage_days_elapsed} 天（{formatShanghaiDateTime(c.stage_entered_at)}）
            </dd>
          </div>
        </dl>
      </section>

      {feedback && (
        <div role="status" className="lt-alert-success">
          {feedback}
        </div>
      )}
      {errorMsg && (
        <div role="alert" className="lt-alert-error">
          {errorMsg}
        </div>
      )}

      {/* 6 Tabs */}
      <div
        className="flex overflow-x-auto whitespace-nowrap gap-1.5 rounded-2xl bg-[var(--md-sys-color-surface-container-low)] p-1.5"
        role="tablist"
      >
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={activeTab === t.key}
            onClick={() => setActiveTab(t.key)}
            className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-4 py-2 text-xs font-semibold transition-colors ${
              activeTab === t.key
                ? "bg-[var(--md-sys-color-secondary-container)] text-[var(--md-sys-color-on-secondary-container)] shadow-sm"
                : "text-[var(--md-sys-color-on-surface-variant)] hover:bg-[var(--md-sys-color-surface-container)] hover:text-[var(--md-sys-color-on-surface)]"
            }`}
          >
            <span>{t.label}</span>
            {t.count !== undefined && (
              <span className="rounded-full bg-white/80 px-2 py-0.5 text-[11px] text-[var(--md-sys-color-on-surface)] tabular-nums">
                {t.count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Tab 1: Procedural Events */}
      {activeTab === "events" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
          <div className="lg:col-span-5 lt-card p-5 space-y-4">
            <h2 className="text-base font-bold text-slate-900">录入客观程序事件</h2>
            <form
              onSubmit={(e: FormEvent) => {
                e.preventDefault();
                createEventMut.mutate();
              }}
              className="space-y-3"
            >
              <div>
                <label htmlFor="ev-type" className="block text-xs font-semibold text-slate-700 mb-1">
                  程序事件类型 *
                </label>
                <select
                  id="ev-type"
                  value={eventType || categoryEventPresets[0]?.value || ""}
                  onChange={(e) => setEventType(e.target.value)}
                  className="lt-input"
                >
                  {categoryEventPresets.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                  <option value="__custom__">自定义事件标识...</option>
                </select>
              </div>

              {eventType === "__custom__" && (
                <div>
                  <label htmlFor="ev-custom-type" className="block text-xs font-semibold text-slate-700 mb-1">
                    自定义事件标识 *
                  </label>
                  <input
                    id="ev-custom-type"
                    type="text"
                    required
                    value={customEventType}
                    onChange={(e) => setCustomEventType(e.target.value)}
                    placeholder="例如 judgment_served 或自定义名称"
                    className="lt-input"
                  />
                </div>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label htmlFor="ev-date" className="block text-xs font-semibold text-slate-700 mb-1">
                    送达 / 发生日期 *
                  </label>
                  <input
                    id="ev-date"
                    type="date"
                    required
                    value={eventDate}
                    onChange={(e) => setEventDate(e.target.value)}
                    className="lt-input"
                  />
                </div>
                <div>
                  <label htmlFor="ev-method" className="block text-xs font-semibold text-slate-700 mb-1">
                    送达方式
                  </label>
                  <select
                    id="ev-method"
                    value={serviceMethod}
                    onChange={(e) => setServiceMethod(e.target.value)}
                    className="lt-input"
                  >
                    {SERVICE_METHOD_OPTIONS.map((m) => (
                      <option key={m.value} value={m.value}>
                        {m.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <label htmlFor="ev-days" className="block text-xs font-semibold text-slate-700 mb-1">
                  办案机关指定期限天数（可选）
                </label>
                <input
                  id="ev-days"
                  type="number"
                  min={1}
                  value={authorityDays}
                  onChange={(e) => setAuthorityDays(e.target.value)}
                  placeholder="例如 15 或 30"
                  className="lt-input"
                />
              </div>

              <div>
                <label htmlFor="ev-notes" className="block text-xs font-semibold text-slate-700 mb-1">
                  备注说明
                </label>
                <textarea
                  id="ev-notes"
                  rows={2}
                  value={eventNotes}
                  onChange={(e) => setEventNotes(e.target.value)}
                  className="lt-input"
                />
              </div>

              <button
                type="submit"
                disabled={createEventMut.isPending}
                className="lt-btn-primary w-full py-2.5 text-sm"
              >
                {createEventMut.isPending ? "正在计算..." : "记录事件并推算期限"}
              </button>
            </form>
          </div>

          <div className="lg:col-span-7 lt-card p-5 space-y-3">
            <h2 className="text-base font-bold text-slate-900">案件事件时间线</h2>
            {detail.events.length === 0 ? (
              <p className="py-8 text-center text-xs text-slate-600">尚未记录任何客观程序事件。</p>
            ) : (
              <div className="space-y-3">
                {detail.events.map((ev) => (
                  <div
                    key={ev.id}
                    className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-slate-200 p-3.5"
                  >
                    <div className="space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-sm text-slate-900">
                          {getEventTypeLabel(ev.event_type)}
                        </span>
                        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-mono text-slate-700 tabular-nums">
                          {ev.event_date}
                        </span>
                      </div>
                      <p className="text-xs text-slate-600">
                        方式：{getServiceMethodLabel(ev.service_method)}
                        {ev.authority_specified_days ? ` · 指定期限 ${ev.authority_specified_days} 天` : ""}
                        {ev.notes ? ` · ${ev.notes}` : ""}
                      </p>
                    </div>
                    <button
                      type="button"
                      disabled={recalcMut.isPending}
                      onClick={() => recalcMut.mutate(ev.id)}
                      className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
                    >
                      <RefreshCw className="h-3 w-3" aria-hidden="true" />
                      <span>重算期限</span>
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tab 2: Deadlines */}
      {activeTab === "deadlines" && (
        <div className="lt-card p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-bold text-slate-900">法定与自定义期限台账</h2>
            <button
              type="button"
              onClick={() => {
                setDlModalError(null);
                setDlModalOpen(true);
              }}
              className="lt-btn-primary"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              <span>新增自定义期限</span>
            </button>
          </div>

          {detail.deadlines.length === 0 ? (
            <p className="py-8 text-center text-xs text-slate-600">
              当前无期限记录。可在“事件推进”录入送达事件自动推算，或点击右上角手动添加。
            </p>
          ) : (
            <div className="space-y-2.5">
              {detail.deadlines.map((dl) => (
                <div
                  key={dl.id}
                  className={`flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4 ${
                    dl.is_completed ? "border-slate-200 bg-slate-50" : "border-slate-200 bg-white"
                  }`}
                >
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <RiskBadge risk={dl.risk_level} />
                      <span
                        className={`font-bold text-sm ${
                          dl.is_completed ? "line-through text-slate-600" : "text-slate-900"
                        }`}
                      >
                        {dl.title}
                      </span>
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-mono text-slate-700">
                        {dl.rule_code}
                      </span>
                      {dl.is_manual_override && (
                        <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-800">
                          已人工覆写
                        </span>
                      )}
                      {dl.is_completed && (
                        <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[11px] font-medium text-emerald-800">
                          已办结
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-slate-700 tabular-nums">
                      最终到期日：<strong className="text-slate-900">{dl.due_date}</strong>（起算{" "}
                      {dl.base_date}，顺延前 {dl.raw_due_date}） ·{" "}
                      {HOLIDAY_ROLL_LABELS[dl.holiday_roll_mode]}
                    </p>
                    {(dl.roll_reason || dl.legal_basis || dl.override_reason) && (
                      <p className="text-xs text-slate-600">
                        {dl.legal_basis ? `依据：${dl.legal_basis}` : ""}
                        {dl.legal_basis && dl.roll_reason ? " · " : ""}
                        {dl.roll_reason ?? ""}
                        {dl.override_reason ? `（覆写原因：${dl.override_reason}）` : ""}
                      </p>
                    )}
                  </div>

                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        setOverrideError(null);
                        setOverrideTarget(dl);
                        setOverrideDueDate(dl.due_date);
                        setOverrideReason(dl.override_reason || "");
                      }}
                      className="lt-btn-secondary py-1.5 px-2.5"
                    >
                      调整到期日
                    </button>
                    <button
                      type="button"
                      disabled={updateDeadlineMut.isPending}
                      onClick={() =>
                        updateDeadlineMut.mutate({
                          deadlineId: dl.id,
                          payload: { is_completed: !dl.is_completed },
                        })
                      }
                      className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-semibold ${
                        dl.is_completed
                          ? "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                          : "bg-emerald-600 text-white hover:bg-emerald-700"
                      }`}
                    >
                      {dl.is_completed ? (
                        <>
                          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                          <span>重开</span>
                        </>
                      ) : (
                        <>
                          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                          <span>办结</span>
                        </>
                      )}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Tab 3: Schedules */}
      {activeTab === "schedules" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
          <div className="lg:col-span-5 lt-card p-5 space-y-3">
            <h2 className="text-base font-bold text-slate-900">添加开庭 / 会见 / 阅卷日程</h2>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                createSchedMut.mutate();
              }}
              className="space-y-3"
            >
              <div>
                <label htmlFor="cs-title" className="block text-xs font-semibold text-slate-700 mb-1">
                  日程标题 *
                </label>
                <input
                  id="cs-title"
                  type="text"
                  required
                  value={schedTitle}
                  onChange={(e) => setSchedTitle(e.target.value)}
                  placeholder="例如 一审开庭 / 看守所第2次会见"
                  className="lt-input"
                />
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label htmlFor="cs-cat" className="block text-xs font-semibold text-slate-700 mb-1">
                    类型
                  </label>
                  <select
                    id="cs-cat"
                    value={schedCat}
                    onChange={(e) => setSchedCat(e.target.value)}
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
                  <label htmlFor="cs-loc" className="block text-xs font-semibold text-slate-700 mb-1">
                    地点
                  </label>
                  <input
                    id="cs-loc"
                    type="text"
                    value={schedLoc}
                    onChange={(e) => setSchedLoc(e.target.value)}
                    className="lt-input"
                  />
                </div>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label htmlFor="cs-start" className="block text-xs font-semibold text-slate-700 mb-1">
                    开始时间 *
                  </label>
                  <input
                    id="cs-start"
                    type="datetime-local"
                    required
                    value={schedStart}
                    onChange={(e) => setSchedStart(e.target.value)}
                    className="lt-input"
                  />
                </div>
                <div>
                  <label htmlFor="cs-end" className="block text-xs font-semibold text-slate-700 mb-1">
                    结束时间 *
                  </label>
                  <input
                    id="cs-end"
                    type="datetime-local"
                    required
                    value={schedEnd}
                    onChange={(e) => setSchedEnd(e.target.value)}
                    className="lt-input"
                  />
                </div>
              </div>
              <button
                type="submit"
                disabled={createSchedMut.isPending}
                className="lt-btn-primary w-full py-2.5 text-sm"
              >
                {createSchedMut.isPending ? "保存中..." : "添加日程"}
              </button>
            </form>
          </div>

          <div className="lg:col-span-7 lt-card p-5 space-y-3">
            <h2 className="text-base font-bold text-slate-900">本案日程列表</h2>
            {schedulesQuery.isLoading ? (
              <p className="py-8 text-center text-xs text-slate-600">正在加载日程...</p>
            ) : schedulesQuery.isError ? (
              <div role="alert" className="lt-alert-error">
                日程加载失败。
              </div>
            ) : (schedulesQuery.data?.length ?? 0) === 0 ? (
              <p className="py-8 text-center text-xs text-slate-600">暂无日程记录。</p>
            ) : (
              <div className="space-y-2.5">
                {schedulesQuery.data!.map((s) => (
                  <div
                    key={s.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 p-3.5"
                  >
                    <div>
                      <div className="flex items-center gap-2">
                        <CalendarClock className="h-4 w-4 text-blue-700" aria-hidden="true" />
                        <span
                          className={`font-semibold text-sm ${
                            s.is_completed ? "line-through text-slate-500" : "text-slate-900"
                          }`}
                        >
                          {s.title}
                        </span>
                        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
                          {SCHEDULE_CATEGORY_LABELS[s.category] || s.category}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-slate-600 tabular-nums">
                        {formatShanghaiDateTime(s.start_time)} ~ {formatShanghaiDateTime(s.end_time)}
                        {s.location ? ` · ${s.location}` : ""}
                      </p>
                    </div>
                    <button
                      type="button"
                      disabled={toggleSchedMut.isPending}
                      onClick={() =>
                        toggleSchedMut.mutate({ schedId: s.id, completed: !s.is_completed })
                      }
                      className="lt-btn-secondary py-1 px-3"
                    >
                      {s.is_completed ? "标为未完成" : "标为已完成"}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tab 4: 5 Standard Folders Tree & Upload */}
      {activeTab === "files" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
          <div className="lg:col-span-4 lt-card p-5 space-y-3">
            <h2 className="text-base font-bold text-slate-900">上传文书 / 发票</h2>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                uploadMut.mutate();
              }}
              className="space-y-3"
            >
              <div>
                <label htmlFor="up-folder" className="block text-xs font-semibold text-slate-700 mb-1">
                  目标子目录 *
                </label>
                <select
                  id="up-folder"
                  value={uploadSubfolder}
                  onChange={(e) => setUploadSubfolder(e.target.value)}
                  className="lt-input"
                >
                  {STANDARD_CASE_SUBFOLDERS.map((sf) => (
                    <option key={sf} value={sf}>
                      {sf}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="up-subtype" className="block text-xs font-semibold text-slate-700 mb-1">
                  文书标签（可选）
                </label>
                <input
                  id="up-subtype"
                  type="text"
                  value={uploadDocSubtype}
                  onChange={(e) => setUploadDocSubtype(e.target.value)}
                  placeholder="例如 民事判决书 / 律师费发票"
                  className="lt-input"
                />
              </div>
              <div>
                <label htmlFor="up-file" className="block text-xs font-semibold text-slate-700 mb-1">
                  选择文件 *
                </label>
                <input
                  id="up-file"
                  type="file"
                  required
                  onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
                  className="w-full text-xs text-slate-700"
                />
              </div>
              <button
                type="submit"
                disabled={uploadMut.isPending || !uploadFile}
                className="lt-btn-primary w-full py-2.5 text-sm"
              >
                <Upload className="h-4 w-4" aria-hidden="true" />
                <span>{uploadMut.isPending ? "正在归档..." : "上传并归档"}</span>
              </button>
            </form>
          </div>

          <div className="lg:col-span-8 space-y-3">
            {STANDARD_CASE_SUBFOLDERS.map((sf) => {
              const entries = detail.files_tree[sf] ?? [];
              return (
                <div key={sf} className="lt-card p-4 space-y-2">
                  <div className="flex items-center justify-between">
                    <h3 className="flex items-center gap-2 text-sm font-bold text-slate-900">
                      <FolderOpen className="h-4 w-4 text-amber-500" aria-hidden="true" />
                      <span>{sf}</span>
                    </h3>
                    <span className="text-xs text-slate-600 tabular-nums">{entries.length} 个文件</span>
                  </div>
                  {entries.length === 0 ? (
                    <p className="text-xs text-slate-600 pl-6">目录为空</p>
                  ) : (
                    <ul className="divide-y divide-slate-100 pl-2">
                      {entries.map((entry) => (
                        <li
                          key={entry.rel_path}
                          className="flex items-center justify-between gap-3 py-2 text-xs"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <FileText className="h-3.5 w-3.5 text-slate-500 shrink-0" aria-hidden="true" />
                            <span className="truncate font-medium text-slate-800">{entry.file_name}</span>
                            <span className="text-slate-600 shrink-0 tabular-nums">
                              ({Math.max(1, Math.round(entry.size_bytes / 1024))} KB)
                            </span>
                          </div>
                          <button
                            type="button"
                            onClick={() => handleDownload(entry.rel_path, entry.file_name)}
                            className="lt-btn-secondary py-1 px-3 shrink-0"
                          >
                            <Download className="h-3 w-3" aria-hidden="true" />
                            <span>下载</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Tab 5: Team Members & Roles */}
      {activeTab === "members" && (
        <div className="lt-card p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="flex items-center gap-2 text-base font-bold text-slate-900">
                <Users className="h-4 w-4 text-blue-700" aria-hidden="true" />
                <span>办案组成员与督办角色</span>
              </h2>
              <p className="text-xs text-slate-600">
                “督办合伙人”将在致命死线临期1天仍未办结时自动收到升级微信预警。
              </p>
            </div>
            <button
              type="button"
              disabled={!membersDirty || saveMembersMut.isPending}
              onClick={() => saveMembersMut.mutate()}
              className="lt-btn-primary"
            >
              <Save className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{saveMembersMut.isPending ? "保存中..." : "保存角色分配"}</span>
            </button>
          </div>

          <div className="divide-y divide-slate-100">
            {(usersQuery.data ?? []).map((u) => {
              const currentRole = effectiveMembersMap[u.id] ?? "none";
              return (
                <div key={u.id} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
                  <div className="flex items-center gap-2.5">
                    <span
                      className="h-7 w-7 rounded-full flex items-center justify-center text-xs font-bold text-white"
                      style={{ backgroundColor: u.calendar_color || "#2563eb" }}
                    >
                      {u.full_name.slice(0, 1)}
                    </span>
                    <div>
                      <p className="font-semibold text-slate-900">{u.full_name}</p>
                      <p className="text-xs text-slate-600">{u.username}</p>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-1.5">
                    {(["none", "lead", "co", "supervisor"] as const).map((r) => (
                      <button
                        key={r}
                        type="button"
                        onClick={() => {
                          setMembersDirty(true);
                          setDraftMembers({ ...effectiveMembersMap, [u.id]: r });
                        }}
                        className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                          currentRole === r
                            ? r === "supervisor"
                              ? "border-transparent bg-[var(--md-sys-color-error)] text-[var(--md-sys-color-on-error)]"
                              : r === "none"
                              ? "border-transparent bg-[var(--md-sys-color-secondary)] text-[var(--md-sys-color-on-secondary)]"
                              : "border-transparent bg-[var(--md-sys-color-primary)] text-[var(--md-sys-color-on-primary)]"
                            : "border-[var(--md-sys-color-outline-variant)] bg-white text-[var(--md-sys-color-on-surface-variant)] hover:bg-[var(--md-sys-color-surface-container-low)]"
                        }`}
                      >
                        {r === "none" ? "未参与" : CASE_MEMBER_ROLE_LABELS[r]}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Tab 6: Audit Logs */}
      {activeTab === "audit" && (
        <div className="lt-card p-5 space-y-3">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-slate-700" aria-hidden="true" />
            <h2 className="text-base font-bold text-slate-900">关键操作审计日志</h2>
          </div>
          {auditQuery.isLoading ? (
            <p className="py-8 text-center text-xs text-slate-600">正在加载审计记录...</p>
          ) : auditQuery.isError ? (
            <div role="alert" className="lt-alert-error">
              审计日志加载失败。
            </div>
          ) : (auditQuery.data?.length ?? 0) === 0 ? (
            <p className="py-8 text-center text-xs text-slate-600">暂无审计记录。</p>
          ) : (
            <div tabIndex={0} role="region" aria-label="案件审计日志表" className="table-wrapper max-h-[520px]">
              <table className="text-left text-xs">
                <thead className="bg-slate-50 text-slate-700">
                  <tr>
                    <th className="border-b border-slate-200 px-3 py-2.5 font-semibold">时间</th>
                    <th className="border-b border-slate-200 px-3 py-2.5 font-semibold">操作人</th>
                    <th className="border-b border-slate-200 px-3 py-2.5 font-semibold">动作</th>
                    <th className="border-b border-slate-200 px-3 py-2.5 font-semibold">详情</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {auditQuery.data!.map((log) => (
                    <tr key={log.id} className="hover:bg-slate-50">
                      <td className="px-3 py-2.5 whitespace-nowrap text-slate-600 tabular-nums">
                        {formatShanghaiDateTime(log.created_at)}
                      </td>
                      <td className="px-3 py-2.5 font-medium text-slate-800 whitespace-nowrap">
                        {log.actor_name}
                      </td>
                      <td className="px-3 py-2.5 font-mono text-blue-700 whitespace-nowrap">
                        {log.action}
                      </td>
                      <td className="px-3 py-2.5 text-slate-700 break-all">
                        {formatAuditDetail(log.detail)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Custom Deadline Modal */}
      <ModalDialog
        open={dlModalOpen}
        onClose={() => setDlModalOpen(false)}
        title="新增案件自定义期限"
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            createCustomDlMut.mutate();
          }}
          className="space-y-3"
        >
          {dlModalError && (
            <div role="alert" className="lt-alert-error">
              {dlModalError}
            </div>
          )}
          <div>
            <label htmlFor="cd-title" className="block text-xs font-semibold text-slate-700 mb-1">
              期限名称 *
            </label>
            <input
              id="cd-title"
              type="text"
              required
              autoFocus
              value={dlTitle}
              onChange={(e) => setDlTitle(e.target.value)}
              className="lt-input"
            />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="cd-date" className="block text-xs font-semibold text-slate-700 mb-1">
                到期日 *
              </label>
              <input
                id="cd-date"
                type="date"
                required
                value={dlDate}
                onChange={(e) => setDlDate(e.target.value)}
                className="lt-input"
              />
            </div>
            <div>
              <label htmlFor="cd-risk" className="block text-xs font-semibold text-slate-700 mb-1">
                风险等级
              </label>
              <select
                id="cd-risk"
                value={dlRisk}
                onChange={(e) => setDlRisk(e.target.value as RiskLevel)}
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
            <label htmlFor="cd-basis" className="block text-xs font-semibold text-slate-700 mb-1">
              依据说明
            </label>
            <input
              id="cd-basis"
              type="text"
              value={dlBasis}
              onChange={(e) => setDlBasis(e.target.value)}
              className="lt-input"
            />
          </div>
          <label className="inline-flex items-center gap-2 text-xs text-slate-700 cursor-pointer">
            <input
              type="checkbox"
              checked={dlApplyRoll}
              onChange={(e) => setDlApplyRoll(e.target.checked)}
            />
            <span>遇法定节假日或周末自动顺延</span>
          </label>
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={() => setDlModalOpen(false)} className="lt-btn-secondary">
              取消
            </button>
            <button type="submit" disabled={createCustomDlMut.isPending} className="lt-btn-primary">
              {createCustomDlMut.isPending ? "保存中..." : "保存"}
            </button>
          </div>
        </form>
      </ModalDialog>

      {/* Override Deadline Modal */}
      <ModalDialog
        open={Boolean(overrideTarget)}
        onClose={() => setOverrideTarget(null)}
        title="人工覆写期限到期日"
        subtitle={overrideTarget?.title}
      >
        {overrideTarget && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              updateDeadlineMut.mutate({
                deadlineId: overrideTarget.id,
                payload: {
                  due_date: overrideDueDate,
                  override_reason: overrideReason.trim() || "律师手动调整",
                  is_manual_override: true,
                },
              });
            }}
            className="space-y-3"
          >
            {overrideError && (
              <div role="alert" className="lt-alert-error">
                {overrideError}
              </div>
            )}
            <div className="rounded-lg bg-amber-50 border border-amber-200 p-3 text-xs text-amber-900 flex gap-2">
              <ShieldAlert className="h-4 w-4 shrink-0 text-amber-700" aria-hidden="true" />
              <span>人工覆写后该期限将锁定，后续事件重算不会覆盖您修改的日期。</span>
            </div>
            <div>
              <label htmlFor="ov-date" className="block text-xs font-semibold text-slate-700 mb-1">
                新到期日 *
              </label>
              <input
                id="ov-date"
                type="date"
                required
                value={overrideDueDate}
                onChange={(e) => setOverrideDueDate(e.target.value)}
                className="lt-input"
              />
            </div>
            <div>
              <label htmlFor="ov-reason" className="block text-xs font-semibold text-slate-700 mb-1">
                修改原因 *
              </label>
              <input
                id="ov-reason"
                type="text"
                required
                autoFocus
                value={overrideReason}
                onChange={(e) => setOverrideReason(e.target.value)}
                placeholder="例如 法官延期举证通知"
                className="lt-input"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setOverrideTarget(null)} className="lt-btn-secondary">
                取消
              </button>
              <button type="submit" disabled={updateDeadlineMut.isPending} className="lt-btn-primary">
                {updateDeadlineMut.isPending ? "保存中..." : "确认覆写"}
              </button>
            </div>
          </form>
        )}
      </ModalDialog>

      {/* Criminal Custody Modal */}
      <ModalDialog
        open={custodyEditOpen}
        onClose={() => setCustodyEditOpen(false)}
        title="更新刑事羁押状态与看守所"
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            updateCaseMut.mutate({
              custody_status: editCustodyStatus.trim() || null,
              custody_place: editCustodyPlace.trim() || null,
            });
          }}
          className="space-y-3"
        >
          {custodyError && (
            <div role="alert" className="lt-alert-error">
              {custodyError}
            </div>
          )}
          <div>
            <label htmlFor="ed-cstatus" className="block text-xs font-semibold text-slate-700 mb-1">
              羁押状态
            </label>
            <input
              id="ed-cstatus"
              type="text"
              autoFocus
              value={editCustodyStatus}
              onChange={(e) => setEditCustodyStatus(e.target.value)}
              placeholder="在押 / 取保候审 / 监视居住 / 不批捕释放"
              className="lt-input"
            />
          </div>
          <div>
            <label htmlFor="ed-cplace" className="block text-xs font-semibold text-slate-700 mb-1">
              羁押场所
            </label>
            <input
              id="ed-cplace"
              type="text"
              value={editCustodyPlace}
              onChange={(e) => setEditCustodyPlace(e.target.value)}
              placeholder="例如 上海市第一看守所"
              className="lt-input"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={() => setCustodyEditOpen(false)} className="lt-btn-secondary">
              取消
            </button>
            <button
              type="submit"
              disabled={updateCaseMut.isPending}
              className="rounded-lg bg-rose-600 px-4 py-2 text-xs font-semibold text-white hover:bg-rose-700"
            >
              {updateCaseMut.isPending ? "保存中..." : "保存羁押信息"}
            </button>
          </div>
        </form>
      </ModalDialog>
    </div>
  );
}
