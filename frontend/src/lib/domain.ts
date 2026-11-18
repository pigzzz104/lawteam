import type {
  CaseCategory,
  CaseMemberRole,
  HolidayRollMode,
  NotifyChannel,
  NotifyTargetType,
  RiskLevel,
  SystemRole,
  TriageStatus,
} from "@/types/api";

export const STANDARD_CASE_SUBFOLDERS = [
  "01_司法与程序文书",
  "02_诉讼文书与法律意见",
  "03_证据与阅卷材料",
  "04_会见与庭审记录",
  "05_财务与发票",
] as const;

export const CASE_CATEGORY_META: Record<
  CaseCategory,
  { label: string; badgeClass: string; shortLabel: string }
> = {
  civil: {
    label: "民事诉讼",
    shortLabel: "民事",
    badgeClass: "bg-[#e3edff] text-[#0b3b8c] border-[#c4d8ff]",
  },
  criminal: {
    label: "刑事案件",
    shortLabel: "刑事",
    badgeClass: "bg-[#fde7e9] text-[#8c1d27] border-[#f8c4c9]",
  },
  administrative: {
    label: "行政诉讼与复议",
    shortLabel: "行政",
    badgeClass: "bg-[#f1e8ff] text-[#4e218a] border-[#dec9ff]",
  },
  labor_arbitration: {
    label: "劳动争议仲裁",
    shortLabel: "劳仲",
    badgeClass: "bg-[#fef1d6] text-[#7a4b00] border-[#f9dc9c]",
  },
  commercial_arb: {
    label: "商事仲裁",
    shortLabel: "商仲",
    badgeClass: "bg-[#e6e9ff] text-[#283593] border-[#c7cffc]",
  },
  enforcement: {
    label: "强制执行",
    shortLabel: "执行",
    badgeClass: "bg-[#dcf5ec] text-[#0b5742] border-[#b3e6d3]",
  },
  non_litigation: {
    label: "非诉专项/法顾",
    shortLabel: "非诉",
    badgeClass: "bg-[#eceff5] text-[#3a4252] border-[#d3dae6]",
  },
};

export const RISK_LEVEL_META: Record<
  RiskLevel,
  { label: string; badgeClass: string }
> = {
  critical: {
    label: "致命死线",
    badgeClass: "bg-[#fde7e5] text-[#8c1d18] border-[#f4b8b3]",
  },
  high: {
    label: "刚性程序",
    badgeClass: "bg-[#fef0d1] text-[#764900] border-[#f6d48b]",
  },
  normal: {
    label: "常规节点",
    badgeClass: "bg-[#e1f1ff] text-[#094a7a] border-[#b7dcfa]",
  },
  low: {
    label: "内部待办",
    badgeClass: "bg-[#eceff5] text-[#434955] border-[#d1d8e4]",
  },
};

export const HOLIDAY_ROLL_LABELS: Record<HolidayRollMode, string> = {
  roll_workday: "遇节假日顺延至首个工作日",
  no_roll_custody: "刑事在押期间不因节假日顺延",
  calendar_exact: "自然日精确到期（不顺延）",
};

export const NOTIFY_TARGET_LABELS: Record<NotifyTargetType, string> = {
  self: "仅提醒自己",
  case_team: "案件全体办案组 (主办+协办)",
  by_role: "按角色提醒 (含督办合伙人升级)",
  custom: "指定人员名单",
};

export const NOTIFY_CHANNEL_META: Record<
  NotifyChannel,
  { label: string; badge: string; targetLabel: string; targetPlaceholder: string; hint: string }
> = {
  wecom: {
    label: "企业微信自建应用 / 微信插件",
    badge: "官方企业号",
    targetLabel: "企业微信 UserID",
    targetPlaceholder: "例如 zhang_partner（开启微信插件可同步到普通微信）",
    hint: "需管理员在系统配置企业微信应用凭据；开启企业微信“微信插件”后可直接在普通微信收消息。",
  },
  serverchan: {
    label: "Server酱 Turbo（普通个人微信服务号推送）",
    badge: "零封号风险",
    targetLabel: "Server酱 SendKey",
    targetPlaceholder: "例如 SCT123456T...",
    hint: "在 sct.ftqq.com 扫码绑定个人微信后复制 SendKey，提醒直接送达普通微信服务号。",
  },
  wxpusher: {
    label: "WxPusher（普通个人微信订阅推送）",
    badge: "零封号风险",
    targetLabel: "WxPusher UID",
    targetPlaceholder: "例如 UID_xxxxxxxx",
    hint: "需管理员配置 WxPusher 应用后，微信扫码关注即可获取个人 UID。",
  },
  pushplus: {
    label: "PushPlus（普通个人微信推送）",
    badge: "零封号风险",
    targetLabel: "PushPlus Token",
    targetPlaceholder: "例如 32位个人 Token",
    hint: "关注 PushPlus 微信公众号后复制个人 Token，支持 Markdown 到期卡片直达普通微信。",
  },
  webhook: {
    label: "自定义 Webhook（钉钉 / 飞书 / 自建机器人）",
    badge: "通用回调",
    targetLabel: "Webhook URL",
    targetPlaceholder: "https://...",
    hint: "向目标 URL 发送包含 title、markdown 与 user 信息的 JSON POST 请求。",
  },
};

export const SYSTEM_ROLE_LABELS: Record<SystemRole, string> = {
  admin: "合伙人/管理员",
  lawyer: "执业律师",
  assistant: "律师助理",
};

export const CASE_MEMBER_ROLE_LABELS: Record<CaseMemberRole, string> = {
  lead: "主办律师",
  co: "协办律师/助理",
  supervisor: "督办合伙人",
};

export const TRIAGE_STATUS_META: Record<
  TriageStatus,
  { label: string; badgeClass: string }
> = {
  pending: {
    label: "待分拣确认",
    badgeClass: "bg-[#fef1d6] text-[#7a4b00] border-[#f6d48b]",
  },
  manual: {
    label: "需验证/人工取回",
    badgeClass: "bg-[var(--md-sys-color-error-container)] text-[var(--md-sys-color-on-error-container)] border-[var(--md-sys-color-error)]/30",
  },
  confirmed: {
    label: "已确认入案",
    badgeClass: "bg-[var(--md-sys-color-tertiary-container)] text-[var(--md-sys-color-on-tertiary-container)] border-[var(--md-sys-color-tertiary)]/30",
  },
  ignored: {
    label: "已忽略",
    badgeClass: "bg-[var(--md-sys-color-surface-container-high)] text-[var(--md-sys-color-on-surface-variant)] border-[var(--md-sys-color-outline-variant)]",
  },
};

export const EVENT_TYPE_PRESETS: Array<{ value: string; label: string; categories?: CaseCategory[] }> = [
  { value: "judgment_served", label: "判决书送达", categories: ["civil", "criminal", "administrative"] },
  { value: "ruling_served", label: "裁定书送达", categories: ["civil", "criminal", "administrative", "enforcement"] },
  { value: "pleading_served", label: "起诉状/仲裁申请书副本送达", categories: ["civil", "administrative", "labor_arbitration"] },
  { value: "evidence_notice_served", label: "举证通知书送达", categories: ["civil"] },
  { value: "judgment_effective", label: "终审判决生效", categories: ["civil"] },
  { value: "performance_expired", label: "履行期限届满", categories: ["civil", "enforcement"] },
  { value: "preservation_bank_done", label: "银行存款冻结完成", categories: ["civil", "enforcement"] },
  { value: "preservation_movable_done", label: "动产查封扣押完成", categories: ["civil", "enforcement"] },
  { value: "preservation_real_estate_done", label: "不动产/股权查封完成", categories: ["civil", "enforcement"] },
  { value: "detention_notified", label: "刑事拘留起始", categories: ["criminal"] },
  { value: "case_transferred_arrest", label: "公安提请批准逮捕", categories: ["criminal"] },
  { value: "arrest_executed", label: "逮捕执行", categories: ["criminal"] },
  { value: "bail_granted", label: "取保候审决定", categories: ["criminal"] },
  { value: "prosecution_review_started", label: "移送审查起诉", categories: ["criminal"] },
  { value: "admin_act_known", label: "知道/应当知道行政行为", categories: ["administrative"] },
  { value: "reconsideration_decision_served", label: "行政复议决定书送达", categories: ["administrative"] },
  { value: "arbitration_award_served", label: "仲裁裁决书送达", categories: ["labor_arbitration", "commercial_arb"] },
  { value: "enforcement_notice_served", label: "执行通知书/报告财产令送达", categories: ["enforcement"] },
  { value: "enforcement_objection_served", label: "执行异议裁定书送达", categories: ["enforcement"] },
  { value: "enforcement_settlement_signed", label: "执行和解协议签署", categories: ["enforcement"] },
  { value: "retainer_started", label: "常年法律顾问服务起算", categories: ["non_litigation"] },
  { value: "project_milestone_started", label: "非诉尽调/专项成果节点起算", categories: ["non_litigation"] },
  { value: "payment_notice_issued", label: "非诉项目阶段请款单发出", categories: ["non_litigation"] },
  { value: "contract_review_requested", label: "法顾日常合同审查收件", categories: ["non_litigation"] },
];

export const EVENT_TYPE_LABELS: Record<string, string> = {
  judgment_served: "判决书送达",
  ruling_served: "裁定书送达",
  pleading_served: "起诉状/申请书副本送达",
  evidence_notice_served: "举证通知书送达",
  judgment_effective: "判决生效",
  performance_expired: "履行期限届满",
  preservation_bank_done: "银行存款冻结完成",
  preservation_movable_done: "动产查封扣押完成",
  preservation_real_estate_done: "不动产/股权查封完成",
  detention_notified: "刑事拘留起始",
  case_transferred_arrest: "提请批准逮捕",
  arrest_executed: "执行逮捕",
  bail_granted: "取保候审决定",
  prosecution_review_started: "移送审查起诉",
  admin_act_known: "知道行政行为",
  reconsideration_decision_served: "行政复议决定书送达",
  arbitration_award_served: "仲裁裁决书送达",
  enforcement_notice_served: "执行通知书/报告财产令送达",
  enforcement_objection_served: "执行异议裁定书送达",
  enforcement_settlement_signed: "执行和解协议签署",
  retainer_started: "常年法顾服务起算",
  project_milestone_started: "非诉项目交付节点起算",
  payment_notice_issued: "非诉阶段请款单发出",
  contract_review_requested: "法顾合同审查收件",
};

export function getEventTypeLabel(eventType: string): string {
  return EVENT_TYPE_LABELS[eventType] ?? eventType;
}

export const SERVICE_METHOD_OPTIONS = [
  { value: "electronic", label: "电子送达（即日生效）" },
  { value: "direct", label: "直接送达" },
  { value: "mail", label: "邮寄送达" },
  { value: "announcement_30", label: "公告送达（30日满生效）" },
  { value: "announcement_60", label: "涉外公告送达（60日满生效）" },
] as const;

export function getServiceMethodLabel(method: string): string {
  return SERVICE_METHOD_OPTIONS.find((m) => m.value === method)?.label ?? method;
}

export const DELIVERY_STATUS_META: Record<string, { label: string; badgeClass: string }> = {
  sent: {
    label: "已推送",
    badgeClass: "bg-[var(--md-sys-color-tertiary-container)] text-[var(--md-sys-color-on-tertiary-container)] border-[var(--md-sys-color-tertiary)]/30",
  },
  skipped_completed: {
    label: "已办结跳过",
    badgeClass: "bg-[var(--md-sys-color-surface-container-high)] text-[var(--md-sys-color-on-surface-variant)] border-[var(--md-sys-color-outline-variant)]",
  },
  pending: {
    label: "待投递",
    badgeClass: "bg-[#fef1d6] text-[#7a4b00] border-[#f6d48b]",
  },
  failed: {
    label: "投递失败",
    badgeClass: "bg-[var(--md-sys-color-error-container)] text-[var(--md-sys-color-on-error-container)] border-[var(--md-sys-color-error)]/30",
  },
};

export const CRIME_TYPE_OPTIONS = [
  { value: "一般犯罪", label: "一般犯罪" },
  { value: "流窜/多次/结伙作案", label: "流窜/多次/结伙作案（适用延长拘留期限）" },
] as const;

export const CUSTODY_STATUS_OPTIONS = [
  { value: "", label: "未填写 / 不适用" },
  { value: "在押", label: "在押（看守所羁押）" },
  { value: "取保候审", label: "取保候审" },
  { value: "监视居住", label: "监视居住" },
  { value: "未羁押", label: "未羁押 / 自由身" },
] as const;

export const CASE_AUDIT_ACTION_LABELS: Record<string, string> = {
  create_case: "新建案件",
  update_case: "更新案件信息",
  replace_members: "调整办案成员",
  create_event: "录入程序事件",
  recalculate_event: "重算程序期限",
  upload_file: "上传案件卷宗",
  delete_file: "删除案件文件",
  create_deadline: "创建自定义期限",
  update_deadline: "修改法定期限",
  delete_deadline: "删除期限",
  create_schedule: "创建日程安排",
  update_schedule: "更新日程安排",
  delete_schedule: "删除日程",
  auto_archive_triage: "自动识别归档入案",
  confirm_triage: "人工确认归档入案",
  reopen_triage: "撤销归档回退",
  rule_recalculate: "期限规则重算",
};

export function getCaseAuditActionLabel(action: string): string {
  return CASE_AUDIT_ACTION_LABELS[action] ?? action;
}

export function toShanghaiIsoWithOffset(localDateTime: string): string {
  const clean = localDateTime.trim();
  if (!clean) return "";
  if (clean.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(clean)) {
    return clean;
  }
  const normalized = clean.length === 16 ? `${clean}:00` : clean;
  return `${normalized}+08:00`;
}

export function toShanghaiDateTimeLocal(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(d);
  const map: Record<string, string> = {};
  for (const p of parts) map[p.type] = p.value;
  const hh = map.hour === "24" ? "00" : map.hour;
  return `${map.year}-${map.month}-${map.day}T${hh}:${map.minute}`;
}

export const SOURCE_TYPE_LABELS: Record<string, string> = {
  deadline: "法定期限",
  schedule: "日程安排",
  triage_alert: "分拣预警",
};

export const SCHEDULE_CATEGORY_LABELS: Record<string, string> = {
  hearing: "开庭/听证",
  detention_meet: "看守所会见",
  file_review: "检察院/法院阅卷",
  meeting: "客户/内部会议",
  task: "待办事项",
};

export function formatShanghaiDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
}

export function formatShanghaiDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

export function todayShanghaiIso(): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  return parts;
}

export async function copyTextSafely(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fallback below
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    textarea.remove();
    return ok;
  } catch {
    return false;
  }
}
