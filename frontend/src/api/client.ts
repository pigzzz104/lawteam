import type {
  AuditLogRead,
  CalendarResponse,
  CaseCategory,
  CaseDetailRead,
  CaseEventResponse,
  CaseListItem,
  CaseMemberRole,
  CaseVisibility,
  CourtAutoFetchMode,
  Deadline,
  DeadlineRule,
  DocumentRecord,
  HealthResponse,
  HolidayRollMode,
  InboxConfirmRequest,
  InboxConfirmResponse,
  InboxTriageRead,
  NotifyChannel,
  NotifyTargetType,
  ReminderDelivery,
  ReminderSweepResponse,
  RiskLevel,
  ScheduleItem,
  StageTemplatesPayload,
  SystemRole,
  TestPushResponse,
  TokenResponse,
  TriageStatus,
  UserSelfRead,
  UserSummary,
} from "@/types/api";

const TOKEN_STORAGE_KEY = "lawteam_access_token";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

export function getStoredToken(): string | null {
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setStoredToken(token: string | null): void {
  if (token) {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } else {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  }
}

let unauthorizedHandler: (() => void) | null = null;
export function registerUnauthorizedHandler(fn: () => void): void {
  unauthorizedHandler = fn;
}

async function apiFetch<T>(
  path: string,
  options: RequestInit & { skipAuth?: boolean } = {}
): Promise<T> {
  const { skipAuth = false, headers: rawHeaders, body, ...rest } = options;
  const headers = new Headers(rawHeaders);

  if (!skipAuth) {
    const token = getStoredToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  if (body && !(body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(path, {
    ...rest,
    headers,
    body,
  });

  if (res.status === 401 && !skipAuth && unauthorizedHandler) {
    unauthorizedHandler();
  }

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const parsed = await res.json();
      if (typeof parsed?.detail === "string") {
        detail = parsed.detail;
      } else if (Array.isArray(parsed?.detail)) {
        detail = parsed.detail
          .map((d: { msg?: string; loc?: unknown[] }) =>
            `${Array.isArray(d.loc) ? d.loc.join(".") + ": " : ""}${d.msg ?? "参数错误"}`
          )
          .join("; ");
      }
    } catch {
      // ignore non-json body
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export const authApi = {
  health: () => apiFetch<HealthResponse>("/api/health", { skipAuth: true }),
  login: (username: string, password: string) =>
    apiFetch<TokenResponse>("/api/auth/login", {
      method: "POST",
      skipAuth: true,
      body: JSON.stringify({ username, password }),
    }),
  me: () => apiFetch<UserSelfRead>("/api/auth/me"),
  updateMe: (payload: {
    full_name?: string;
    wecom_userid?: string | null;
    notify_channel?: NotifyChannel;
    notify_target?: string | null;
    phone_os?: string;
    phone_last4?: string | null;
    id_card_last4?: string | null;
    court_auto_fetch_mode?: CourtAutoFetchMode | null;
    calendar_color?: string;
    sms_webhook_secret?: string | null;
    rotate_sms_token?: boolean;
    rotate_ics_token?: boolean;
  }) =>
    apiFetch<UserSelfRead>("/api/auth/me", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
};

export const usersApi = {
  list: () => apiFetch<UserSummary[]>("/api/users"),
  create: (payload: {
    username: string;
    full_name: string;
    password: string;
    system_role: SystemRole;
    wecom_userid?: string | null;
    notify_channel?: NotifyChannel;
    notify_target?: string | null;
    phone_os?: string;
    phone_last4?: string | null;
    id_card_last4?: string | null;
    court_auto_fetch_mode?: CourtAutoFetchMode | null;
    calendar_color?: string;
  }) =>
    apiFetch<UserSummary>("/api/users", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  update: (
    userId: number,
    payload: Partial<{
      full_name: string;
      system_role: SystemRole;
      wecom_userid: string | null;
      notify_channel: NotifyChannel;
      notify_target: string | null;
      phone_os: string;
      phone_last4: string | null;
      id_card_last4: string | null;
      calendar_color: string;
      is_active: boolean;
      new_password: string;
    }>
  ) =>
    apiFetch<UserSummary>(`/api/users/${userId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
};

export const rulesApi = {
  stageTemplates: () => apiFetch<StageTemplatesPayload>("/api/rules/stage-templates"),
  list: (params?: { case_category?: CaseCategory; enabled_only?: boolean }) => {
    const qs = new URLSearchParams();
    if (params?.case_category) qs.set("case_category", params.case_category);
    if (params?.enabled_only) qs.set("enabled_only", "true");
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return apiFetch<DeadlineRule[]>(`/api/rules${suffix}`);
  },
  create: (payload: {
    rule_code: string;
    name: string;
    case_category: CaseCategory;
    trigger_event_type: string;
    condition_match?: Record<string, unknown>;
    duration_value: number;
    duration_unit: "days" | "months" | "years";
    holiday_roll_mode: HolidayRollMode;
    legal_basis: string;
    risk_level: RiskLevel;
    default_offsets_days: number[];
    is_enabled: boolean;
  }) =>
    apiFetch<DeadlineRule>("/api/rules", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  update: (
    ruleId: number,
    payload: Partial<{
      name: string;
      case_category: CaseCategory;
      trigger_event_type: string;
      condition_match: Record<string, unknown>;
      duration_value: number;
      duration_unit: "days" | "months" | "years";
      holiday_roll_mode: HolidayRollMode;
      legal_basis: string;
      risk_level: RiskLevel;
      default_offsets_days: number[];
      is_enabled: boolean;
    }>
  ) =>
    apiFetch<DeadlineRule>(`/api/rules/${ruleId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  remove: (ruleId: number) =>
    apiFetch<void>(`/api/rules/${ruleId}`, {
      method: "DELETE",
    }),
};

export const casesApi = {
  list: (params?: {
    case_category?: CaseCategory;
    is_archived?: boolean;
    search?: string;
  }) => {
    const qs = new URLSearchParams();
    if (params?.case_category) qs.set("case_category", params.case_category);
    if (params?.is_archived !== undefined) qs.set("is_archived", String(params.is_archived));
    if (params?.search) qs.set("search", params.search);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return apiFetch<CaseListItem[]>(`/api/cases${suffix}`);
  },
  detail: (caseId: number) => apiFetch<CaseDetailRead>(`/api/cases/${caseId}`),
  create: (payload: {
    case_title: string;
    case_number?: string | null;
    case_category: CaseCategory;
    procedure_subtype?: string | null;
    current_stage?: string | null;
    visibility: CaseVisibility;
    client_name: string;
    client_id_last4?: string | null;
    case_side?: string | null;
    opponent_name?: string | null;
    handling_agency?: string | null;
    judge_or_officer_name?: string | null;
    judge_or_officer_phone?: string | null;
    is_foreign_related?: boolean;
    custody_status?: string | null;
    custody_place?: string | null;
    crime_type?: string | null;
    trial_mode?: string | null;
    members?: Array<{ user_id: number; role_in_case: CaseMemberRole }>;
  }) =>
    apiFetch<CaseDetailRead>("/api/cases", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  update: (
    caseId: number,
    payload: Partial<{
      case_title: string;
      case_number: string | null;
      procedure_subtype: string;
      current_stage: string;
      visibility: CaseVisibility;
      client_name: string;
      client_id_last4: string | null;
      case_side: string | null;
      opponent_name: string | null;
      handling_agency: string | null;
      judge_or_officer_name: string | null;
      judge_or_officer_phone: string | null;
      is_foreign_related: boolean;
      custody_status: string | null;
      custody_place: string | null;
      crime_type: string | null;
      trial_mode: string | null;
      is_archived: boolean;
    }>
  ) =>
    apiFetch<CaseDetailRead>(`/api/cases/${caseId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  replaceMembers: (
    caseId: number,
    members: Array<{ user_id: number; role_in_case: CaseMemberRole }>
  ) =>
    apiFetch(`/api/cases/${caseId}/members`, {
      method: "PUT",
      body: JSON.stringify(members),
    }),
  createEvent: (
    caseId: number,
    payload: {
      event_type: string;
      event_date: string;
      service_method?: string;
      authority_specified_days?: number | null;
      notes?: string | null;
    }
  ) =>
    apiFetch<CaseEventResponse>(`/api/cases/${caseId}/events`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  recalculateEvent: (caseId: number, eventId: number) =>
    apiFetch<CaseEventResponse>(`/api/cases/${caseId}/events/${eventId}/recalculate`, {
      method: "POST",
    }),
  uploadFile: (caseId: number, subfolder: string, file: File, docSubtype?: string) => {
    const fd = new FormData();
    fd.append("subfolder", subfolder);
    if (docSubtype) fd.append("doc_subtype", docSubtype);
    fd.append("file", file);
    return apiFetch<DocumentRecord>(`/api/cases/${caseId}/files/upload`, {
      method: "POST",
      body: fd,
    });
  },
  downloadFile: async (caseId: number, relPath: string, filename: string) => {
    const token = getStoredToken();
    const res = await fetch(
      `/api/cases/${caseId}/files/download?rel_path=${encodeURIComponent(relPath)}`,
      {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      }
    );
    if (res.status === 401 && unauthorizedHandler) {
      unauthorizedHandler();
    }
    if (!res.ok) {
      let detail = `文件下载失败 (HTTP ${res.status})`;
      try {
        const parsed = await res.json();
        if (typeof parsed?.detail === "string") detail = parsed.detail;
      } catch {
        // ignore non-json error response
      }
      throw new ApiError(res.status, detail);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  },
  auditLogs: (caseId: number) => apiFetch<AuditLogRead[]>(`/api/cases/${caseId}/audit-logs`),
};

export const calendarApi = {
  events: (params: {
    scope: "my" | "team";
    start?: string;
    end?: string;
    case_category?: CaseCategory;
    include_completed?: boolean;
    include_tentative?: boolean;
  }) => {
    const qs = new URLSearchParams({ scope: params.scope });
    if (params.start) qs.set("start", params.start);
    if (params.end) qs.set("end", params.end);
    if (params.case_category) qs.set("case_category", params.case_category);
    if (params.include_completed !== undefined) {
      qs.set("include_completed", String(params.include_completed));
    }
    if (params.include_tentative !== undefined) {
      qs.set("include_tentative", String(params.include_tentative));
    }
    return apiFetch<CalendarResponse>(`/api/calendar/events?${qs.toString()}`);
  },
  createDeadline: (payload: {
    case_id: number;
    title: string;
    due_date: string;
    legal_basis?: string | null;
    holiday_roll_mode?: HolidayRollMode;
    apply_holiday_roll?: boolean;
    risk_level?: RiskLevel;
    override_reason?: string | null;
    notify_target_type?: NotifyTargetType;
    custom_notify_user_ids?: number[];
    escalate_to_supervisor?: boolean;
    reminder_offsets_days?: number[];
  }) =>
    apiFetch<Deadline>("/api/deadlines", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateDeadline: (
    deadlineId: number,
    payload: Partial<{
      title: string;
      due_date: string;
      legal_basis: string | null;
      holiday_roll_mode: HolidayRollMode;
      risk_level: RiskLevel;
      is_manual_override: boolean;
      override_reason: string | null;
      is_completed: boolean;
      notify_target_type: NotifyTargetType;
      custom_notify_user_ids: number[];
      escalate_to_supervisor: boolean;
      reminder_offsets_days: number[];
    }>
  ) =>
    apiFetch<Deadline>(`/api/deadlines/${deadlineId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteDeadline: (deadlineId: number) =>
    apiFetch<void>(`/api/deadlines/${deadlineId}`, { method: "DELETE" }),
  listSchedules: (caseId?: number) => {
    const qs = caseId ? `?case_id=${caseId}` : "";
    return apiFetch<ScheduleItem[]>(`/api/schedules${qs}`);
  },
  createSchedule: (payload: {
    case_id?: number | null;
    category: string;
    title: string;
    start_time: string;
    end_time: string;
    location?: string | null;
    risk_level?: RiskLevel;
    notify_target_type?: NotifyTargetType;
    custom_notify_user_ids?: number[];
    reminder_offsets_minutes?: number[];
  }) =>
    apiFetch<ScheduleItem>("/api/schedules", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateSchedule: (
    scheduleId: number,
    payload: Partial<{
      case_id: number | null;
      category: string;
      title: string;
      start_time: string;
      end_time: string;
      location: string | null;
      risk_level: RiskLevel;
      notify_target_type: NotifyTargetType;
      custom_notify_user_ids: number[];
      reminder_offsets_minutes: number[];
      is_completed: boolean;
    }>
  ) =>
    apiFetch<ScheduleItem>(`/api/schedules/${scheduleId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteSchedule: (scheduleId: number) =>
    apiFetch<void>(`/api/schedules/${scheduleId}`, { method: "DELETE" }),
};

export const inboxApi = {
  list: (params?: { status?: TriageStatus; item_type?: string; auto_archived?: boolean }) => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.item_type) qs.set("item_type", params.item_type);
    if (params?.auto_archived !== undefined) {
      qs.set("auto_archived", String(params.auto_archived));
    }
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return apiFetch<InboxTriageRead[]>(`/api/inbox${suffix}`);
  },
  simulate: (payload: {
    content: string;
    sender?: string;
    client_platform?: string;
    auto_fetch?: boolean;
    phone_last4?: string | null;
    confirm_sign?: boolean;
  }) =>
    apiFetch<InboxTriageRead>("/api/inbox/simulate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  manualUpload: (payload: {
    files: File[];
    item_type: "judicial_stage_and_doc" | "invoice_archive";
    case_id?: number | null;
    notes?: string;
    auto_archive?: boolean;
  }) => {
    const fd = new FormData();
    payload.files.forEach((f) => fd.append("files", f));
    fd.append("item_type", payload.item_type);
    if (payload.case_id) fd.append("case_id", String(payload.case_id));
    if (payload.notes) fd.append("notes", payload.notes);
    if (payload.auto_archive !== undefined) {
      fd.append("auto_archive", String(payload.auto_archive));
    }
    return apiFetch<InboxTriageRead>("/api/inbox/manual-upload", {
      method: "POST",
      body: fd,
    });
  },
  retryFetch: (
    triageId: number,
    payload: {
      phone_last4?: string | null;
      id_card_last4?: string | null;
      extraction_code?: string | null;
      confirm_sign?: boolean;
    }
  ) =>
    apiFetch<InboxTriageRead>(`/api/inbox/${triageId}/retry-fetch`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  confirm: (triageId: number, payload: InboxConfirmRequest) =>
    apiFetch<InboxConfirmResponse>(`/api/inbox/${triageId}/confirm`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  reopen: (triageId: number) =>
    apiFetch<InboxTriageRead>(`/api/inbox/${triageId}/reopen`, {
      method: "POST",
    }),
  ignore: (triageId: number) =>
    apiFetch<InboxTriageRead>(`/api/inbox/${triageId}/ignore`, {
      method: "POST",
    }),
  batchIgnore: (triageIds: number[]) =>
    apiFetch<{ ignored_count: number; ignored_ids: number[] }>("/api/inbox/batch-ignore", {
      method: "POST",
      body: JSON.stringify({ triage_ids: triageIds }),
    }),
  downloadFile: async (
    triageId: number,
    relPath: string,
    filename: string,
    inline = false
  ) => {
    const token = getStoredToken();
    const res = await fetch(
      `/api/inbox/${triageId}/files/download?rel_path=${encodeURIComponent(relPath)}&inline=${inline ? "true" : "false"}`,
      {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      }
    );
    if (res.status === 401 && unauthorizedHandler) {
      unauthorizedHandler();
    }
    if (!res.ok) {
      let detail = `文件访问失败 (HTTP ${res.status})`;
      try {
        const parsed = await res.json();
        if (typeof parsed?.detail === "string") detail = parsed.detail;
      } catch {
        // ignore non-json error response
      }
      throw new ApiError(res.status, detail);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    if (inline) {
      window.open(url, "_blank", "noopener,noreferrer");
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      return;
    }
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  },
};

export const remindersApi = {
  deliveries: (params?: { status?: string; source_type?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.source_type) qs.set("source_type", params.source_type);
    if (params?.limit) qs.set("limit", String(params.limit));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return apiFetch<ReminderDelivery[]>(`/api/reminders/deliveries${suffix}`);
  },
  runOnce: () =>
    apiFetch<ReminderSweepResponse>("/api/reminders/run-once", { method: "POST" }),
  testPush: (payload?: { title?: string; message?: string }) =>
    apiFetch<TestPushResponse>("/api/reminders/test-push", {
      method: "POST",
      body: payload ? JSON.stringify(payload) : undefined,
    }),
  addPersonalAlert: (deadlineId: number, fireAtIso: string) =>
    apiFetch<Deadline>(`/api/reminders/deadlines/${deadlineId}/personal-alerts`, {
      method: "POST",
      body: JSON.stringify({ fire_at: fireAtIso }),
    }),
};
