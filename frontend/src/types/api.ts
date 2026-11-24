export type SystemRole = "admin" | "lawyer" | "assistant";
export type CaseVisibility = "private" | "team";
export type CaseMemberRole = "lead" | "co" | "supervisor";
export type CaseCategory =
  | "civil"
  | "criminal"
  | "administrative"
  | "labor_arbitration"
  | "commercial_arb"
  | "enforcement"
  | "non_litigation";

export type HolidayRollMode = "roll_workday" | "no_roll_custody" | "calendar_exact";
export type RiskLevel = "critical" | "high" | "normal" | "low";
export type NotifyTargetType = "self" | "case_team" | "by_role" | "custom";
export type TriageStatus = "pending" | "manual" | "confirmed" | "ignored";
export type NotifyChannel = "wecom" | "serverchan" | "wxpusher" | "pushplus" | "webhook";
export type CourtAutoFetchMode = "confirm_before_sign" | "auto_non_judgment" | "auto_all";

export interface HealthResponse {
  status: string;
  database: string;
  env: string;
  timezone: string;
  llm_provider: string;
  court_auto_fetch_mode: string;
}

export interface UserSelfRead {
  id: number;
  username: string;
  full_name: string;
  system_role: SystemRole;
  wecom_userid: string | null;
  notify_channel: NotifyChannel;
  notify_target: string | null;
  phone_os: string;
  phone_last4: string | null;
  id_card_last4: string | null;
  court_auto_fetch_mode: CourtAutoFetchMode;
  calendar_color: string;
  sms_webhook_token: string;
  has_sms_webhook_secret: boolean;
  ics_feed_token: string;
  default_deadline_offsets_days: number[];
  default_schedule_offsets_minutes: number[];
  is_active: boolean;
  created_at: string;
}

export interface UserSummary {
  id: number;
  username: string;
  full_name: string;
  system_role: SystemRole;
  wecom_userid: string | null;
  notify_channel: NotifyChannel;
  notify_target: string | null;
  phone_os: string;
  phone_last4: string | null;
  id_card_last4: string | null;
  court_auto_fetch_mode: CourtAutoFetchMode | null;
  calendar_color: string;
  is_active: boolean;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: UserSelfRead;
}

export interface CaseMemberRead {
  id: number;
  user_id: number;
  username: string;
  full_name: string;
  calendar_color: string;
  role_in_case: CaseMemberRole;
}

export interface CaseRecord {
  id: number;
  case_title: string;
  case_number: string | null;
  case_category: CaseCategory;
  procedure_subtype: string;
  current_stage: string;
  stage_entered_at: string;
  visibility: CaseVisibility;
  owner_id: number;
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
  charge_name: string | null;
  trial_mode: string | null;
  folder_rel_path: string;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface CaseListItem {
  id: number;
  case_title: string;
  case_number: string | null;
  case_category: CaseCategory;
  procedure_subtype: string;
  current_stage: string;
  stage_entered_at: string;
  stage_days_elapsed: number;
  stale_warning: boolean;
  visibility: CaseVisibility;
  owner_id: number;
  client_name: string;
  opponent_name: string | null;
  handling_agency: string | null;
  is_foreign_related: boolean;
  custody_status: string | null;
  custody_place: string | null;
  folder_rel_path: string;
  is_archived: boolean;
  created_at: string;
  members: CaseMemberRead[];
  open_deadlines_count: number;
  next_deadline_date: string | null;
}

export interface CaseEvent {
  id: number;
  case_id: number;
  event_type: string;
  event_date: string;
  service_method: string;
  authority_specified_days: number | null;
  source_sms_id: number | null;
  notes: string | null;
  created_by: number;
  created_at: string;
}

export interface PersonalAlertItem {
  user_id: number;
  fire_at: string;
}

export interface Deadline {
  id: number;
  case_id: number;
  source_event_id: number | null;
  rule_code: string;
  title: string;
  legal_basis: string | null;
  base_date: string;
  raw_due_date: string;
  due_date: string;
  holiday_roll_mode: HolidayRollMode;
  roll_reason: string | null;
  risk_level: RiskLevel;
  is_manual_override: boolean;
  override_by: number | null;
  override_at: string | null;
  override_reason: string | null;
  is_completed: boolean;
  completed_at: string | null;
  completed_by: number | null;
  notify_target_type: NotifyTargetType;
  notify_owner_id: number | null;
  custom_notify_user_ids: number[];
  escalate_to_supervisor: boolean;
  reminder_offsets_days: number[];
  personal_custom_alerts: PersonalAlertItem[];
  created_at: string;
}

export interface ScheduleItem {
  id: number;
  case_id: number | null;
  owner_id: number;
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
  created_at: string;
}

export interface DocumentRecord {
  id: number;
  case_id: number | null;
  folder_category: string;
  file_name: string;
  rel_file_path: string;
  doc_subtype: string | null;
  invoice_meta: Record<string, unknown> | null;
  uploaded_by: number;
  created_at: string;
}

export interface CaseFileEntry {
  file_name: string;
  rel_path: string;
  size_bytes: number;
  modified_at: string;
}

export interface CaseDetailRead {
  case: CaseRecord;
  stage_days_elapsed: number;
  stale_warning: boolean;
  members: CaseMemberRead[];
  events: CaseEvent[];
  deadlines: Deadline[];
  documents: DocumentRecord[];
  files_tree: Record<string, CaseFileEntry[]>;
}

export interface CaseEventResponse {
  event: CaseEvent;
  derived_deadlines: Deadline[];
}

export interface AuditLogRead {
  id: number;
  actor_user_id: number | null;
  actor_name: string;
  action: string;
  entity_type: string;
  entity_id: number | null;
  case_id: number | null;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface CalendarEventItem {
  id: string;
  record_id: number;
  source_type: "deadline" | "schedule" | "triage_tentative";
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  is_tentative: boolean;
  case_id: number | null;
  case_title: string | null;
  case_number: string | null;
  case_category: CaseCategory | null;
  category: string | null;
  risk_level: RiskLevel;
  rule_code: string | null;
  legal_basis: string | null;
  raw_due_date: string | null;
  due_date: string | null;
  holiday_roll_mode: HolidayRollMode | null;
  roll_reason: string | null;
  is_manual_override: boolean;
  is_completed: boolean;
  location: string | null;
  owner_id: number | null;
  owner_name: string | null;
  color: string;
  status: string | null;
}

export interface CalendarConflict {
  user_id: number;
  user_name: string;
  schedule_id_a: number;
  schedule_id_b: number;
  title_a: string;
  title_b: string;
  overlap_start: string;
  overlap_end: string;
  message: string;
}

export interface CalendarResponse {
  scope: string;
  items: CalendarEventItem[];
  conflicts: CalendarConflict[];
}

export interface DeadlineRule {
  id: number;
  rule_code: string;
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
  is_system_builtin: boolean;
  is_enabled: boolean;
}

export interface RuleRecalculateResponse {
  rule_id: number;
  rule_code: string;
  affected_cases_count: number;
  affected_events_count: number;
  updated_deadlines_count: number;
  created_deadlines_count: number;
  skipped_manual_override_count: number;
  skipped_completed_count: number;
}

export type AutoTriagePolicyMode = "auto_archive" | "require_confirm";

export interface AutoTriagePolicy {
  id: number;
  policy_key: string;
  label: string;
  description: string;
  mode: AutoTriagePolicyMode;
  sort_order: number;
  updated_by: number | null;
  updated_at: string;
}

export interface StageCategoryTemplate {
  label: string;
  default_subtype: string;
  stages: string[];
  subtypes?: Record<string, string[]>;
}

export interface StageTemplatesPayload {
  stale_days_default: number;
  categories: Record<CaseCategory, StageCategoryTemplate>;
}

export interface InboxTriageRead {
  id: number;
  sms_id: number | null;
  owner_user_id: number;
  owner_name: string;
  matched_case_id: number | null;
  matched_case_title: string | null;
  matched_case_number: string | null;
  item_type: "judicial_stage_and_doc" | "invoice_archive";
  status: TriageStatus;
  auto_archived: boolean;
  triage_reason: string | null;
  extracted_payload: Record<string, unknown>;
  downloaded_files: string[];
  error_or_manual_reason: string | null;
  sms_content: string | null;
  sms_sender: string | null;
  sms_platform: string | null;
  sms_category: string | null;
  confirmed_by: number | null;
  confirmed_at: string | null;
  created_at: string;
}

export interface InboxConfirmRequest {
  case_id?: number | null;
  create_case?: {
    case_title: string;
    case_number?: string | null;
    case_category: CaseCategory;
    procedure_subtype?: string | null;
    current_stage?: string | null;
    visibility: CaseVisibility;
    client_name: string;
    handling_agency?: string | null;
    crime_type?: string | null;
    charge_name?: string | null;
  } | null;
  event_type?: string | null;
  event_date?: string | null;
  service_method?: string;
  authority_specified_days?: number | null;
  event_notes?: string | null;
  new_stage?: string | null;
  create_hearing?: {
    category: string;
    title: string;
    start_time: string;
    end_time: string;
    location?: string | null;
    risk_level: RiskLevel;
  } | null;
  file_subfolder?: string | null;
}

export interface InboxConfirmResponse {
  triage_item: InboxTriageRead;
  case_id: number;
  created_case: boolean;
  created_event_id: number | null;
  generated_deadline_count: number;
  created_schedule_id: number | null;
  archived_documents: string[];
}

export interface ReminderDelivery {
  id: number;
  idempotency_key: string;
  source_type: string;
  source_id: number;
  recipient_user_ids: number[];
  sent_user_ids: number[];
  retry_count: number;
  next_retry_at: string | null;
  scheduled_at: string;
  sent_at: string | null;
  status: "pending" | "sent" | "skipped_completed" | "failed";
  error_msg: string | null;
  case_id: number | null;
  case_title: string | null;
  case_number: string | null;
  source_title: string | null;
  recipient_names: string[];
}

export interface ReminderSweepResponse {
  evaluated_at: string;
  sent_count: number;
  skipped_completed_count: number;
  failed_count: number;
  already_processed_count: number;
  delivery_keys: string[];
}

export interface TestPushResponse {
  ok: boolean;
  dry_run: boolean;
  channel: string;
  summary: string;
  error_msg: string | null;
  attempts: Array<Record<string, unknown>>;
}
