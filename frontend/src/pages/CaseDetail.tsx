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
  Edit3,
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
  MdCheckbox,
  MdFileDropzone,
  MdRadioGroup,
  MdSelect,
} from "@/components/ui";
import { useAuth } from "@/context/AuthContext";
import {
  CASE_MEMBER_ROLE_LABELS,
  CRIME_TYPE_OPTIONS,
  CUSTODY_STATUS_OPTIONS,
  EVENT_TYPE_PRESETS,
  HOLIDAY_ROLL_LABELS,
  NOTIFY_TARGET_LABELS,
  SCHEDULE_CATEGORY_LABELS,
  SERVICE_METHOD_OPTIONS,
  STANDARD_CASE_SUBFOLDERS,
  formatShanghaiDateTime,
  getCaseAuditActionLabel,
  getEventTypeLabel,
  getServiceMethodLabel,
  toShanghaiIsoWithOffset,
  todayShanghaiIso,
} from "@/lib/domain";
import type {
  CaseMemberRole,
  CaseVisibility,
  Deadline,
  NotifyTargetType,
  RiskLevel,
} from "@/types/api";

type TabKey = "events" | "deadlines" | "schedules" | "files" | "members" | "audit";

const AUDIT_FIELD_LABELS: Record<string, string> = {
  case_title: "案件名称",
  case_number: "案号",
  current_stage: "阶段",
  procedure_subtype: "程序子类型",
  is_archived: "是否归档",
  visibility: "可见性",
  client_name: "委托人",
  opponent_name: "对方当事人",
  handling_agency: "承办机关",
  custody_status: "羁押状态",
  custody_place: "看守所",
  crime_type: "案件情形",
  charge_name: "罪名",
  is_foreign_related: "是否涉外",
  event_type: "事件",
  event_date: "事件日期",
  due_date: "到期日",
  override_reason: "修改原因",
  file_name: "文件名",
  folder_category: "目录",
  members_count: "成员数",
};

function formatAuditValue(key: string, val: unknown): string {
  if (key === "event_type" && typeof val === "string") return getEventTypeLabel(val);
  if (val === true) return "是";
  if (val === false) return "否";
  if (typeof val === "object" && val !== null) {
    if (Array.isArray(val)) return val.join("、");
    return Object.entries(val as Record<string, unknown>)
      .map(([k, v]) => `${AUDIT_FIELD_LABELS[k] ?? k}: ${String(v)}`)
      .join(", ");
  }
  return String(val);
}

function formatAuditDetail(detail: Record<string, unknown>): string {
  const entries = Object.entries(detail || {}).filter(
    ([, v]) => v !== null && v !== undefined && v !== ""
  );
  if (entries.length === 0) return "-";
  return entries
    .map(([k, v]) => `${AUDIT_FIELD_LABELS[k] ?? k}: ${formatAuditValue(k, v)}`)
    .join(" · ");
}

export function CaseDetail() {
  const { caseId } = useParams();
  const { user } = useAuth();
  const id = Number(caseId);
  const queryClient = useQueryClient();

  const [activeTab, setActiveTab] = useState<TabKey>("events");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [dlModalError, setDlModalError] = useState<string | null>(null);
  const [overrideError, setOverrideError] = useState<string | null>(null);
  const [custodyError, setCustodyError] = useState<string | null>(null);
  const [editCaseError, setEditCaseError] = useState<string | null>(null);

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
  const [dlNotifyTarget, setDlNotifyTarget] = useState<NotifyTargetType>("case_team");
  const [dlNotifyOwnerId, setDlNotifyOwnerId] = useState<string>("");

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
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);

  // Criminal custody quick edit modal
  const [custodyEditOpen, setCustodyEditOpen] = useState(false);
  const [editCustodyStatus, setEditCustodyStatus] = useState("");
  const [editCustodyPlace, setEditCustodyPlace] = useState("");

  // Case metadata edit modal
  const [editCaseOpen, setEditCaseOpen] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editNumber, setEditNumber] = useState("");
  const [editSubtype, setEditSubtype] = useState("");
  const [editClient, setEditClient] = useState("");
  const [editClientId4, setEditClientId4] = useState("");
  const [editOpponent, setEditOpponent] = useState("");
  const [editAgency, setEditAgency] = useState("");
  const [editForeign, setEditForeign] = useState(false);
  const [editVisibility, setEditVisibility] = useState<CaseVisibility>("team");
  const [editCrimeType, setEditCrimeType] = useState("一般犯罪");
  const [editChargeName, setEditChargeName] = useState("");

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
    setEditCaseError(null);
    setEventType("");
    setCustomEventType("");
    setOverrideTarget(null);
    setCustodyEditOpen(false);
    setEditCaseOpen(false);
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

  const isManager = useMemo(() => {
    if (!user || !c || !detail) return false;
    if (user.system_role === "admin") return true;
    if (c.owner_id === user.id) return true;
    return detail.members.some(
      (m) =>
        m.user_id === user.id &&
        (m.role_in_case === "lead" || m.role_in_case === "supervisor")
    );
  }, [user, c, detail]);

  const stageChoices = useMemo(() => {
    if (!c || !templatesQuery.data) return [];
    const catTpl = templatesQuery.data.categories[c.case_category];
    if (!catTpl) return [];
    const subStages = catTpl.subtypes?.[c.procedure_subtype];
    return subStages && subStages.length > 0 ? subStages : catTpl.stages;
  }, [c, templatesQuery.data]);

  const subtypeOptions = useMemo(() => {
    if (!c || !templatesQuery.data) return [];
    const catTpl = templatesQuery.data.categories[c.case_category];
    return catTpl?.subtypes ? Object.keys(catTpl.subtypes) : [];
  }, [c, templatesQuery.data]);

  const categoryEventPresets = useMemo(() => {
    if (!c) return EVENT_TYPE_PRESETS;
    const matched = EVENT_TYPE_PRESETS.filter(
      (p) => !p.categories || p.categories.includes(c.case_category)
    );
    return matched.length > 0 ? matched : EVENT_TYPE_PRESETS;
  }, [c]);

  const activeResolvedEventType =
    eventType === "__custom__" ? customEventType.trim() : eventType || categoryEventPresets[0]?.value || "";

  const refreshCase = () => {
    queryClient.invalidateQueries({ queryKey: ["case-detail", id] });
    queryClient.invalidateQueries({ queryKey: ["case-schedules", id] });
    queryClient.invalidateQueries({ queryKey: ["case-audit-logs", id] });
    queryClient.invalidateQueries({ queryKey: ["cases"] });
    queryClient.invalidateQueries({ queryKey: ["calendar"] });
  };

  const updateCaseMut = useMutation({
    mutationFn: (payload: Parameters<typeof casesApi.update>[1]) => casesApi.update(id, payload),
    onSuccess: () => {
      refreshCase();
      setCustodyEditOpen(false);
      setCustodyError(null);
      setEditCaseOpen(false);
      setEditCaseError(null);
      setFeedback("案件信息已更新");
      setErrorMsg(null);
    },
    onError: (err: Error) => {
      if (custodyEditOpen) {
        setCustodyError(err.message);
      } else if (editCaseOpen) {
        setEditCaseError(err.message);
      } else {
        setErrorMsg(err.message);
      }
    },
  });

  const createEventMut = useMutation({
    mutationFn: () => {
      if (!activeResolvedEventType) throw new Error("请选择或输入事件类型");
      const includeAuthDays =
        activeResolvedEventType === "evidence_notice_served" && authorityDays.trim();
      return casesApi.createEvent(id, {
        event_type: activeResolvedEventType,
        event_date: eventDate,
        service_method: serviceMethod,
        authority_specified_days: includeAuthDays ? Number(authorityDays) : null,
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
      setFeedback(`重算完成，当前匹配 ${res.derived_deadlines.length} 条规则期限（已保留人工锁定）`);
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
        notify_target_type: dlNotifyTarget,
        notify_owner_id:
          dlNotifyTarget === "self" && dlNotifyOwnerId ? Number(dlNotifyOwnerId) : null,
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
      const startIso = toShanghaiIsoWithOffset(schedStart);
      const endIso = toShanghaiIsoWithOffset(schedEnd);
      const startMs = new Date(startIso).getTime();
      const endMs = new Date(endIso).getTime();
      if (Number.isNaN(startMs) || Number.isNaN(endMs) || endMs <= startMs) {
        throw new Error("日程结束时间必须晚于开始时间");
      }
      return calendarApi.createSchedule({
        case_id: id,
        category: schedCat,
        title: schedTitle.trim(),
        start_time: startIso,
        end_time: endIso,
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
      const file = uploadFiles[0];
      if (!file) throw new Error("请先选择文件");
      return casesApi.uploadFile(id, uploadSubfolder, file, uploadDocSubtype.trim() || undefined);
    },
    onSuccess: (doc) => {
      refreshCase();
      setUploadFiles([]);
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
      setErrorMsg((err as Error).message);
    }
  };

  if (detailQuery.isLoading) {
    return (
      <div className="lt-card p-12 text-center text-sm text-[var(--md-sys-color-on-surface-variant)]">
        正在加载案件档案...
      </div>
    );
  }

  if (detailQuery.isError || !detail || !c) {
    return (
      <div className="lt-card p-8 space-y-3">
        <p className="text-sm font-semibold text-[var(--md-sys-color-error)]">
          {(detailQuery.error as Error)?.message || "无权查看该案件或案件不存在。"}
        </p>
        <Link
          to="/cases"
          className="inline-flex items-center gap-1 text-sm font-semibold text-[var(--md-sys-color-primary)] hover:underline"
        >
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

  const visibleMemberUsers = (usersQuery.data ?? []).filter(
    (u) => u.is_active || detail.members.some((m) => m.user_id === u.id)
  );

  const openEditCaseModal = () => {
    setEditTitle(c.case_title);
    setEditNumber(c.case_number || "");
    setEditSubtype(c.procedure_subtype);
    setEditClient(c.client_name);
    setEditClientId4(c.client_id_last4 || "");
    setEditOpponent(c.opponent_name || "");
    setEditAgency(c.handling_agency || "");
    setEditForeign(c.is_foreign_related);
    setEditVisibility(c.visibility);
    setEditCrimeType(c.crime_type || "一般犯罪");
    setEditChargeName(c.charge_name || "");
    setEditCaseError(null);
    setEditCaseOpen(true);
  };

  const tabs: Array<{ key: TabKey; label: string; count?: number }> = [
    { key: "events", label: "事件推进", count: detail.events.length },
    { key: "deadlines", label: "法定期限", count: detail.deadlines.length },
    { key: "schedules", label: "日程开庭", count: schedulesQuery.data?.length ?? 0 },
    { key: "files", label: "归档文书", count: detail.documents.length },
    { key: "members", label: "办案成员", count: detail.members.length },
    { key: "audit", label: "操作日志" },
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
              <span className="rounded-full border border-[var(--md-sys-color-outline-variant)] bg-[var(--md-sys-color-surface-container-low)] px-2.5 py-0.5 text-xs font-medium">
                {c.procedure_subtype}
              </span>
              {c.is_foreign_related && (
                <span className="rounded-full border border-[var(--md-sys-color-primary)]/30 bg-[var(--md-sys-color-primary-container)] px-2.5 py-0.5 text-xs font-medium text-[var(--md-sys-color-on-primary-container)]">
                  涉外案件
                </span>
              )}
              {c.case_category === "criminal" && c.charge_name && (
                <span className="rounded-full border border-[var(--md-sys-color-secondary)]/30 bg-[var(--md-sys-color-secondary-container)] px-2.5 py-0.5 text-xs font-medium text-[var(--md-sys-color-on-secondary-container)]">
                  罪名：{c.charge_name}
                </span>
              )}
              <CustodyBadge status={c.custody_status} place={c.custody_place} />
              {detail.stale_warning && (
                <span className="inline-flex items-center gap-1 rounded-full border border-[#f6d48b] bg-[#fef1d6] px-2.5 py-0.5 text-xs font-semibold text-[#7a4b00] tabular-nums">
                  <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                  当前阶段停留 {detail.stage_days_elapsed} 天
                </span>
              )}
            </div>
            <h1 className="text-2xl font-bold text-[var(--md-sys-color-on-surface)]">{c.case_title}</h1>
            <p className="text-sm text-[var(--md-sys-color-on-surface-variant)] font-mono tabular-nums">
              {c.case_number || "暂无正式案号"}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1.5">
              <label htmlFor="case-stage-select" className="text-xs font-semibold">
                当前阶段：
              </label>
              <MdSelect
                id="case-stage-select"
                value={c.current_stage}
                onChange={(e) => updateCaseMut.mutate({ current_stage: e.target.value })}
                className="py-1.5 text-xs font-semibold"
              >
                {!stageChoices.includes(c.current_stage) && (
                  <option value={c.current_stage}>{c.current_stage}</option>
                )}
                {stageChoices.map((sg) => (
                  <option key={sg} value={sg}>
                    {sg}
                  </option>
                ))}
              </MdSelect>
            </div>

            <button type="button" onClick={openEditCaseModal} className="lt-btn-secondary py-1.5">
              <Edit3 className="h-3.5 w-3.5" aria-hidden="true" />
              <span>编辑基础信息</span>
            </button>

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

            {isManager && (
              <button
                type="button"
                disabled={updateCaseMut.isPending}
                onClick={() => updateCaseMut.mutate({ is_archived: !c.is_archived })}
                className="lt-btn-secondary py-1.5"
              >
                <Archive className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{c.is_archived ? "解除归档" : "结案归档"}</span>
              </button>
            )}
          </div>
        </div>

        <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 border-t border-[var(--md-sys-color-outline-variant)]/60 pt-4 text-xs">
          <div>
            <dt className="text-[var(--md-sys-color-on-surface-variant)]">委托人</dt>
            <dd className="mt-0.5 font-semibold">
              {c.client_name} {c.client_id_last4 ? `(*${c.client_id_last4})` : ""}
            </dd>
          </div>
          <div>
            <dt className="text-[var(--md-sys-color-on-surface-variant)]">对方当事人</dt>
            <dd className="mt-0.5 font-medium">{c.opponent_name || "-"}</dd>
          </div>
          <div>
            <dt className="text-[var(--md-sys-color-on-surface-variant)]">承办机关</dt>
            <dd className="mt-0.5 font-medium">{c.handling_agency || "-"}</dd>
          </div>
          <div>
            <dt className="text-[var(--md-sys-color-on-surface-variant)]">阶段停留时长</dt>
            <dd className="mt-0.5 font-medium tabular-nums">
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
              <span className="rounded-full bg-[var(--md-sys-color-surface-container-lowest)]/85 px-2 py-0.5 text-[11px] tabular-nums">
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
            <h2 className="text-base font-bold">录入客观程序事件</h2>
            <form
              onSubmit={(e: FormEvent) => {
                e.preventDefault();
                createEventMut.mutate();
              }}
              className="space-y-3"
            >
              <div>
                <label htmlFor="ev-type" className="block text-xs font-semibold mb-1">
                  程序事件类型 *
                </label>
                <MdSelect
                  id="ev-type"
                  value={eventType || categoryEventPresets[0]?.value || ""}
                  onChange={(e) => setEventType(e.target.value)}
                >
                  {categoryEventPresets.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                  <option value="__custom__">自定义事件标识...</option>
                </MdSelect>
              </div>

              {eventType === "__custom__" && (
                <div>
                  <label htmlFor="ev-custom-type" className="block text-xs font-semibold mb-1">
                    自定义事件名称 *
                  </label>
                  <input
                    id="ev-custom-type"
                    type="text"
                    required
                    value={customEventType}
                    onChange={(e) => setCustomEventType(e.target.value)}
                    placeholder="输入自定义事件名称"
                    className="lt-input"
                  />
                </div>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label htmlFor="ev-date" className="block text-xs font-semibold mb-1">
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
                  <label htmlFor="ev-method" className="block text-xs font-semibold mb-1">
                    送达方式
                  </label>
                  <MdSelect
                    id="ev-method"
                    value={serviceMethod}
                    onChange={(e) => setServiceMethod(e.target.value)}
                  >
                    {SERVICE_METHOD_OPTIONS.map((m) => (
                      <option key={m.value} value={m.value}>
                        {m.label}
                      </option>
                    ))}
                  </MdSelect>
                </div>
              </div>

              {activeResolvedEventType === "evidence_notice_served" && (
                <div>
                  <label htmlFor="ev-days" className="block text-xs font-semibold mb-1">
                    法院指定举证天数（可选）
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
              )}

              <div>
                <label htmlFor="ev-notes" className="block text-xs font-semibold mb-1">
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
            <h2 className="text-base font-bold">案件事件时间线</h2>
            {detail.events.length === 0 ? (
              <p className="py-8 text-center text-xs text-[var(--md-sys-color-on-surface-variant)]">
                尚未记录任何程序事件。
              </p>
            ) : (
              <div className="space-y-3">
                {detail.events.map((ev) => (
                  <div
                    key={ev.id}
                    className="flex flex-wrap items-start justify-between gap-3 rounded-[var(--md-sys-shape-corner-md)] border border-[var(--md-sys-color-outline-variant)] p-3.5"
                  >
                    <div className="space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-sm">{getEventTypeLabel(ev.event_type)}</span>
                        <span className="rounded-full bg-[var(--md-sys-color-surface-container)] px-2.5 py-0.5 text-xs font-mono tabular-nums">
                          {ev.event_date}
                        </span>
                      </div>
                      <p className="text-xs text-[var(--md-sys-color-on-surface-variant)]">
                        方式：{getServiceMethodLabel(ev.service_method)}
                        {ev.authority_specified_days ? ` · 指定期限 ${ev.authority_specified_days} 天` : ""}
                        {ev.notes ? ` · ${ev.notes}` : ""}
                      </p>
                    </div>
                    <button
                      type="button"
                      disabled={recalcMut.isPending}
                      onClick={() => recalcMut.mutate(ev.id)}
                      className="lt-btn-secondary py-1 px-3"
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
            <h2 className="text-base font-bold">法定期限台账</h2>
            <button
              type="button"
              onClick={() => {
                setDlModalError(null);
                setDlNotifyTarget("case_team");
                setDlNotifyOwnerId(user ? String(user.id) : "");
                setDlModalOpen(true);
              }}
              className="lt-btn-primary"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              <span>新增自定义期限</span>
            </button>
          </div>

          {detail.deadlines.length === 0 ? (
            <p className="py-8 text-center text-xs text-[var(--md-sys-color-on-surface-variant)]">
              当前无期限记录。可在「事件推进」录入送达事件自动推算，或点击右上角手动添加。
            </p>
          ) : (
            <div className="space-y-2.5">
              {detail.deadlines.map((dl) => (
                <div
                  key={dl.id}
                  className={`flex flex-wrap items-center justify-between gap-3 rounded-[var(--md-sys-shape-corner-md)] border border-[var(--md-sys-color-outline-variant)] p-4 ${
                    dl.is_completed
                      ? "bg-[var(--md-sys-color-surface-container-low)]/75"
                      : "bg-[var(--md-sys-color-surface-container-lowest)]"
                  }`}
                >
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <RiskBadge risk={dl.risk_level} />
                      <span
                        className={`font-bold text-sm ${
                          dl.is_completed
                            ? "line-through text-[var(--md-sys-color-on-surface-variant)]"
                            : "text-[var(--md-sys-color-on-surface)]"
                        }`}
                      >
                        {dl.title}
                      </span>
                      {dl.is_manual_override && (
                        <span className="rounded-full bg-[#fef1d6] px-2 py-0.5 text-[11px] font-medium text-[#7a4b00]">
                          人工覆写
                        </span>
                      )}
                      {dl.is_completed && (
                        <span className="rounded-full bg-[var(--md-sys-color-tertiary-container)] px-2 py-0.5 text-[11px] font-medium text-[var(--md-sys-color-on-tertiary-container)]">
                          已办结
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-[var(--md-sys-color-on-surface-variant)] tabular-nums">
                      最终到期日：
                      <strong className="text-[var(--md-sys-color-on-surface)]">{dl.due_date}</strong>
                      （起算 {dl.base_date}，顺延前 {dl.raw_due_date}） ·{" "}
                      {HOLIDAY_ROLL_LABELS[dl.holiday_roll_mode]}
                    </p>
                    {(dl.roll_reason || dl.legal_basis || dl.override_reason) && (
                      <p className="text-xs text-[var(--md-sys-color-on-surface-variant)]">
                        {dl.legal_basis ? `依据：${dl.legal_basis}` : ""}
                        {dl.legal_basis && dl.roll_reason ? " · " : ""}
                        {dl.roll_reason ?? ""}
                        {dl.override_reason ? `（修改原因：${dl.override_reason}）` : ""}
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
                      className="lt-btn-secondary py-1.5 px-3"
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
                      className={dl.is_completed ? "lt-btn-secondary py-1.5 px-3" : "lt-btn-primary py-1.5 px-3"}
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
            <h2 className="text-base font-bold">添加开庭 / 会见 / 阅卷日程</h2>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                createSchedMut.mutate();
              }}
              className="space-y-3"
            >
              <div>
                <label htmlFor="cs-title" className="block text-xs font-semibold mb-1">
                  日程标题 *
                </label>
                <input
                  id="cs-title"
                  type="text"
                  required
                  value={schedTitle}
                  onChange={(e) => setSchedTitle(e.target.value)}
                  placeholder="例如 一审开庭 / 看守所会见"
                  className="lt-input"
                />
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label htmlFor="cs-cat" className="block text-xs font-semibold mb-1">
                    类型
                  </label>
                  <MdSelect id="cs-cat" value={schedCat} onChange={(e) => setSchedCat(e.target.value)}>
                    {Object.entries(SCHEDULE_CATEGORY_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v}
                      </option>
                    ))}
                  </MdSelect>
                </div>
                <div>
                  <label htmlFor="cs-loc" className="block text-xs font-semibold mb-1">
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
                  <label htmlFor="cs-start" className="block text-xs font-semibold mb-1">
                    开始时间（北京时间） *
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
                  <label htmlFor="cs-end" className="block text-xs font-semibold mb-1">
                    结束时间（北京时间） *
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
              <button type="submit" disabled={createSchedMut.isPending} className="lt-btn-primary w-full py-2.5 text-sm">
                {createSchedMut.isPending ? "保存中..." : "添加日程"}
              </button>
            </form>
          </div>

          <div className="lg:col-span-7 lt-card p-5 space-y-3">
            <h2 className="text-base font-bold">本案日程列表</h2>
            {schedulesQuery.isLoading ? (
              <p className="py-8 text-center text-xs text-[var(--md-sys-color-on-surface-variant)]">
                正在加载日程...
              </p>
            ) : schedulesQuery.isError ? (
              <div role="alert" className="lt-alert-error">
                日程加载失败。
              </div>
            ) : (schedulesQuery.data?.length ?? 0) === 0 ? (
              <p className="py-8 text-center text-xs text-[var(--md-sys-color-on-surface-variant)]">
                暂无日程记录。
              </p>
            ) : (
              <div className="space-y-2.5">
                {schedulesQuery.data!.map((s) => (
                  <div
                    key={s.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-[var(--md-sys-shape-corner-md)] border border-[var(--md-sys-color-outline-variant)] p-3.5"
                  >
                    <div>
                      <div className="flex items-center gap-2">
                        <CalendarClock className="h-4 w-4 text-[var(--md-sys-color-primary)]" aria-hidden="true" />
                        <span
                          className={`font-semibold text-sm ${
                            s.is_completed
                              ? "line-through text-[var(--md-sys-color-on-surface-variant)]"
                              : "text-[var(--md-sys-color-on-surface)]"
                          }`}
                        >
                          {s.title}
                        </span>
                        <span className="rounded-full bg-[var(--md-sys-color-surface-container)] px-2 py-0.5 text-xs">
                          {SCHEDULE_CATEGORY_LABELS[s.category] || s.category}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-[var(--md-sys-color-on-surface-variant)] tabular-nums">
                        {formatShanghaiDateTime(s.start_time)} ~ {formatShanghaiDateTime(s.end_time)}
                        {s.location ? ` · ${s.location}` : ""}
                      </p>
                    </div>
                    <button
                      type="button"
                      disabled={toggleSchedMut.isPending}
                      onClick={() => toggleSchedMut.mutate({ schedId: s.id, completed: !s.is_completed })}
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
            <h2 className="text-base font-bold">上传文书 / 发票</h2>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                uploadMut.mutate();
              }}
              className="space-y-3"
            >
              <div>
                <label htmlFor="up-folder" className="block text-xs font-semibold mb-1">
                  归档目录
                </label>
                <MdSelect
                  id="up-folder"
                  value={uploadSubfolder}
                  onChange={(e) => setUploadSubfolder(e.target.value)}
                >
                  {STANDARD_CASE_SUBFOLDERS.map((sf) => (
                    <option key={sf} value={sf}>
                      {sf}
                    </option>
                  ))}
                </MdSelect>
              </div>
              <div>
                <label htmlFor="up-subtype" className="block text-xs font-semibold mb-1">
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
              <MdFileDropzone
                files={uploadFiles}
                onFilesChange={setUploadFiles}
                multiple={false}
                label="选择或拖拽文书到此上传"
                hint="支持 PDF、OFD、XML、Word 及扫描图片"
              />
              <button
                type="submit"
                disabled={uploadMut.isPending || uploadFiles.length === 0}
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
                    <h3 className="flex items-center gap-2 text-sm font-bold">
                      <FolderOpen className="h-4 w-4 text-[#b45309]" aria-hidden="true" />
                      <span>{sf}</span>
                    </h3>
                    <span className="text-xs text-[var(--md-sys-color-on-surface-variant)] tabular-nums">
                      {entries.length} 个文件
                    </span>
                  </div>
                  {entries.length === 0 ? (
                    <p className="text-xs text-[var(--md-sys-color-on-surface-variant)] pl-6">目录为空</p>
                  ) : (
                    <ul className="divide-y divide-[var(--md-sys-color-outline-variant)]/50 pl-2">
                      {entries.map((entry) => (
                        <li
                          key={entry.rel_path}
                          className="flex items-center justify-between gap-3 py-2 text-xs"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <FileText
                              className="h-3.5 w-3.5 text-[var(--md-sys-color-primary)] shrink-0"
                              aria-hidden="true"
                            />
                            <span className="truncate font-medium">{entry.file_name}</span>
                            <span className="text-[var(--md-sys-color-on-surface-variant)] shrink-0 tabular-nums">
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
              <h2 className="flex items-center gap-2 text-base font-bold">
                <Users className="h-4 w-4 text-[var(--md-sys-color-primary)]" aria-hidden="true" />
                <span>办案组成员与督办角色</span>
              </h2>
              {c.visibility === "private" && (
                <p className="mt-1 text-xs text-[#7a4b00]">
                  私密案件不开放协办成员挂载，仅办案人本人可见。
                </p>
              )}
            </div>
            {isManager && c.visibility !== "private" && (
              <button
                type="button"
                disabled={!membersDirty || saveMembersMut.isPending}
                onClick={() => saveMembersMut.mutate()}
                className="lt-btn-primary"
              >
                <Save className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{saveMembersMut.isPending ? "保存中..." : "保存角色分配"}</span>
              </button>
            )}
          </div>

          {c.visibility !== "private" && (
            <div className="divide-y divide-[var(--md-sys-color-outline-variant)]/50">
              {visibleMemberUsers.map((u) => {
                const currentRole = effectiveMembersMap[u.id] ?? "none";
                if (!isManager && currentRole === "none") return null;
                return (
                  <div
                    key={u.id}
                    className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm"
                  >
                    <div className="flex items-center gap-2.5">
                      <span
                        className="h-7 w-7 rounded-full flex items-center justify-center text-xs font-bold text-white"
                        style={{ backgroundColor: u.calendar_color || "#1b56b3" }}
                      >
                        {u.full_name.slice(0, 1)}
                      </span>
                      <div>
                        <p className="font-semibold">{u.full_name}</p>
                        <p className="text-xs text-[var(--md-sys-color-on-surface-variant)]">
                          {u.username}
                          {!u.is_active ? "（已停用）" : ""}
                        </p>
                      </div>
                    </div>

                    {isManager ? (
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
                                : "border-[var(--md-sys-color-outline-variant)] bg-[var(--md-sys-color-surface-container-lowest)] text-[var(--md-sys-color-on-surface-variant)] hover:bg-[var(--md-sys-color-surface-container-low)]"
                            }`}
                          >
                            {r === "none" ? "未参与" : CASE_MEMBER_ROLE_LABELS[r]}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <span className="rounded-full bg-[var(--md-sys-color-secondary-container)] px-3 py-1 text-xs font-semibold text-[var(--md-sys-color-on-secondary-container)]">
                        {currentRole === "none" ? "未参与" : CASE_MEMBER_ROLE_LABELS[currentRole]}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Tab 6: Audit Logs */}
      {activeTab === "audit" && (
        <div className="lt-card p-5 space-y-3">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-[var(--md-sys-color-on-surface-variant)]" aria-hidden="true" />
            <h2 className="text-base font-bold">案件操作记录</h2>
          </div>
          {auditQuery.isLoading ? (
            <p className="py-8 text-center text-xs text-[var(--md-sys-color-on-surface-variant)]">
              正在加载操作记录...
            </p>
          ) : auditQuery.isError ? (
            <div role="alert" className="lt-alert-error">
              操作记录加载失败。
            </div>
          ) : (auditQuery.data?.length ?? 0) === 0 ? (
            <p className="py-8 text-center text-xs text-[var(--md-sys-color-on-surface-variant)]">
              暂无操作记录。
            </p>
          ) : (
            <div tabIndex={0} role="region" aria-label="案件操作记录表" className="table-wrapper max-h-[520px]">
              <table className="text-left text-xs">
                <thead className="bg-[var(--md-sys-color-surface-container-low)] text-[var(--md-sys-color-on-surface-variant)]">
                  <tr>
                    <th className="border-b border-[var(--md-sys-color-outline-variant)] px-3 py-2.5 font-semibold">
                      时间
                    </th>
                    <th className="border-b border-[var(--md-sys-color-outline-variant)] px-3 py-2.5 font-semibold">
                      操作人
                    </th>
                    <th className="border-b border-[var(--md-sys-color-outline-variant)] px-3 py-2.5 font-semibold">
                      操作类型
                    </th>
                    <th className="border-b border-[var(--md-sys-color-outline-variant)] px-3 py-2.5 font-semibold">
                      详情
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--md-sys-color-outline-variant)]/50">
                  {auditQuery.data!.map((log) => (
                    <tr key={log.id} className="hover:bg-[var(--md-sys-color-surface-container-low)]/50">
                      <td className="px-3 py-2.5 whitespace-nowrap text-[var(--md-sys-color-on-surface-variant)] tabular-nums">
                        {formatShanghaiDateTime(log.created_at)}
                      </td>
                      <td className="px-3 py-2.5 font-medium whitespace-nowrap">{log.actor_name}</td>
                      <td className="px-3 py-2.5 font-semibold text-[var(--md-sys-color-primary)] whitespace-nowrap">
                        {getCaseAuditActionLabel(log.action)}
                      </td>
                      <td className="px-3 py-2.5 text-[var(--md-sys-color-on-surface-variant)] break-words">
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

      {/* Edit Case Metadata Modal */}
      <ModalDialog
        open={editCaseOpen}
        onClose={() => setEditCaseOpen(false)}
        title="编辑案件基础信息"
        maxWidthClass="max-w-2xl"
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            updateCaseMut.mutate({
              case_title: editTitle.trim(),
              case_number: editNumber.trim() || null,
              procedure_subtype: editSubtype.trim() || undefined,
              client_name: editClient.trim(),
              client_id_last4: editClientId4.trim() || null,
              opponent_name: editOpponent.trim() || null,
              handling_agency: editAgency.trim() || null,
              is_foreign_related: editForeign,
              ...(isManager ? { visibility: editVisibility } : {}),
              ...(c.case_category === "criminal"
                ? {
                    crime_type: editCrimeType || "一般犯罪",
                    charge_name: editChargeName.trim() || null,
                  }
                : {}),
            });
          }}
          className="space-y-3.5"
        >
          {editCaseError && (
            <div role="alert" className="lt-alert-error">
              {editCaseError}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="sm:col-span-2">
              <label htmlFor="ed-title" className="block text-xs font-semibold mb-1">
                案件名称 *
              </label>
              <input
                id="ed-title"
                type="text"
                required
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                className="lt-input"
              />
            </div>

            <div>
              <label htmlFor="ed-number" className="block text-xs font-semibold mb-1">
                案号
              </label>
              <input
                id="ed-number"
                type="text"
                value={editNumber}
                onChange={(e) => setEditNumber(e.target.value)}
                className="lt-input"
              />
            </div>

            <div>
              <label htmlFor="ed-subtype" className="block text-xs font-semibold mb-1">
                程序子类型
              </label>
              {subtypeOptions.length > 0 ? (
                <MdSelect
                  id="ed-subtype"
                  value={editSubtype}
                  onChange={(e) => setEditSubtype(e.target.value)}
                >
                  {!subtypeOptions.includes(editSubtype) && editSubtype && (
                    <option value={editSubtype}>{editSubtype}</option>
                  )}
                  {subtypeOptions.map((st) => (
                    <option key={st} value={st}>
                      {st}
                    </option>
                  ))}
                </MdSelect>
              ) : (
                <input
                  id="ed-subtype"
                  type="text"
                  value={editSubtype}
                  onChange={(e) => setEditSubtype(e.target.value)}
                  className="lt-input"
                />
              )}
            </div>

            <div>
              <label htmlFor="ed-client" className="block text-xs font-semibold mb-1">
                委托人 *
              </label>
              <input
                id="ed-client"
                type="text"
                required
                value={editClient}
                onChange={(e) => setEditClient(e.target.value)}
                className="lt-input"
              />
            </div>

            <div>
              <label htmlFor="ed-client4" className="block text-xs font-semibold mb-1">
                当事人证件后4位
              </label>
              <input
                id="ed-client4"
                type="text"
                maxLength={4}
                value={editClientId4}
                onChange={(e) => setEditClientId4(e.target.value)}
                className="lt-input"
              />
            </div>

            <div>
              <label htmlFor="ed-opp" className="block text-xs font-semibold mb-1">
                对方当事人 / 被害方
              </label>
              <input
                id="ed-opp"
                type="text"
                value={editOpponent}
                onChange={(e) => setEditOpponent(e.target.value)}
                className="lt-input"
              />
            </div>

            <div>
              <label htmlFor="ed-agency" className="block text-xs font-semibold mb-1">
                承办机关
              </label>
              <input
                id="ed-agency"
                type="text"
                value={editAgency}
                onChange={(e) => setEditAgency(e.target.value)}
                className="lt-input"
              />
            </div>
          </div>

          {c.case_category === "criminal" && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 lt-surface-tonal p-3">
              <div>
                <label htmlFor="ed-crime-type" className="block text-xs font-semibold mb-1">
                  案件情形（拘留计算适用）
                </label>
                <MdSelect
                  id="ed-crime-type"
                  value={editCrimeType}
                  onChange={(e) => setEditCrimeType(e.target.value)}
                >
                  {CRIME_TYPE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </MdSelect>
              </div>
              <div>
                <label htmlFor="ed-charge" className="block text-xs font-semibold mb-1">
                  涉嫌罪名
                </label>
                <input
                  id="ed-charge"
                  type="text"
                  value={editChargeName}
                  onChange={(e) => setEditChargeName(e.target.value)}
                  className="lt-input"
                />
              </div>
            </div>
          )}

          <MdCheckbox
            checked={editForeign}
            onChange={setEditForeign}
            label="涉外案件（上诉期按 30 日计算）"
          />

          {isManager && (
            <div className="space-y-1.5">
              <span className="block text-xs font-semibold">可见性权限</span>
              <MdRadioGroup<CaseVisibility>
                value={editVisibility}
                onChange={setEditVisibility}
                columns={2}
                ariaLabel="案件可见性"
                options={[
                  { value: "team", label: "团队协同" },
                  { value: "private", label: "个人私密" },
                ]}
              />
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={() => setEditCaseOpen(false)} className="lt-btn-secondary">
              取消
            </button>
            <button type="submit" disabled={updateCaseMut.isPending} className="lt-btn-primary">
              {updateCaseMut.isPending ? "保存中..." : "保存更改"}
            </button>
          </div>
        </form>
      </ModalDialog>

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
            <label htmlFor="cd-title" className="block text-xs font-semibold mb-1">
              期限名称 *
            </label>
            <input
              id="cd-title"
              type="text"
              required
              value={dlTitle}
              onChange={(e) => setDlTitle(e.target.value)}
              className="lt-input"
            />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="cd-date" className="block text-xs font-semibold mb-1">
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
              <label htmlFor="cd-risk" className="block text-xs font-semibold mb-1">
                风险等级
              </label>
              <MdSelect id="cd-risk" value={dlRisk} onChange={(e) => setDlRisk(e.target.value as RiskLevel)}>
                <option value="critical">致命死线</option>
                <option value="high">刚性程序</option>
                <option value="normal">常规节点</option>
                <option value="low">内部待办</option>
              </MdSelect>
            </div>
          </div>
          <div>
            <label htmlFor="cd-basis" className="block text-xs font-semibold mb-1">
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
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="cd-notify" className="block text-xs font-semibold mb-1">
                提醒接收范围
              </label>
              <MdSelect
                id="cd-notify"
                value={dlNotifyTarget}
                onChange={(e) => setDlNotifyTarget(e.target.value as NotifyTargetType)}
              >
                <option value="case_team">{NOTIFY_TARGET_LABELS.case_team}</option>
                <option value="by_role">{NOTIFY_TARGET_LABELS.by_role}</option>
                <option value="self">指定单人接收</option>
              </MdSelect>
            </div>
            {dlNotifyTarget === "self" && (
              <div>
                <label htmlFor="cd-owner" className="block text-xs font-semibold mb-1">
                  指定接收律师
                </label>
                <MdSelect
                  id="cd-owner"
                  value={dlNotifyOwnerId}
                  onChange={(e) => setDlNotifyOwnerId(e.target.value)}
                >
                  {visibleMemberUsers.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.full_name}
                    </option>
                  ))}
                </MdSelect>
              </div>
            )}
          </div>
          <MdCheckbox
            checked={dlApplyRoll}
            onChange={setDlApplyRoll}
            label="遇法定节假日或周末自动顺延"
          />
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
        title="人工调整期限到期日"
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
            <div className="rounded-[var(--md-sys-shape-corner-md)] bg-[#fef1d6] border border-[#f6d48b] p-3 text-xs text-[#7a4b00] flex gap-2">
              <ShieldAlert className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span>手动调整后将锁定该期限，后续重算不会覆盖您的日期。</span>
            </div>
            <div>
              <label htmlFor="ov-date" className="block text-xs font-semibold mb-1">
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
              <label htmlFor="ov-reason" className="block text-xs font-semibold mb-1">
                修改原因 *
              </label>
              <input
                id="ov-reason"
                type="text"
                required
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
                {updateDeadlineMut.isPending ? "保存中..." : "确认调整"}
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
            <label htmlFor="ed-cstatus" className="block text-xs font-semibold mb-1">
              羁押状态
            </label>
            <MdSelect
              id="ed-cstatus"
              value={editCustodyStatus}
              onChange={(e) => setEditCustodyStatus(e.target.value)}
            >
              {CUSTODY_STATUS_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </MdSelect>
          </div>
          <div>
            <label htmlFor="ed-cplace" className="block text-xs font-semibold mb-1">
              羁押看守所
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
            <button type="submit" disabled={updateCaseMut.isPending} className="lt-btn-danger">
              {updateCaseMut.isPending ? "保存中..." : "保存羁押信息"}
            </button>
          </div>
        </form>
      </ModalDialog>
    </div>
  );
}
