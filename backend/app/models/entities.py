from __future__ import annotations

import secrets
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Return timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _utc_column(nullable: bool = False, index: bool = False) -> Column:
    return Column(DateTime(timezone=True), nullable=nullable, index=index)


# --- Enums ---
class SystemRole(str, Enum):
    ADMIN = "admin"          # 合伙人/团队管理员：全局可见、管理成员与规则表
    LAWYER = "lawyer"        # 执业律师：管理自有私密案件及参与的团队案件
    ASSISTANT = "assistant"  # 律师助理/行政：处理分拣收件箱、协办团队案件


class CaseVisibility(str, Enum):
    PRIVATE = "private"      # 个人私密案件（仅创建人可见）
    TEAM = "team"            # 团队协同案件（办案组成员及ADMIN可见）


class CaseMemberRole(str, Enum):
    LEAD = "lead"            # 主办律师
    CO_COUNSEL = "co"        # 协办律师/助理
    SUPERVISOR = "supervisor"  # 督办合伙人（接收致命死线临期未办结升级预警）


class CaseCategory(str, Enum):
    CIVIL = "civil"                               # 民事诉讼
    CRIMINAL = "criminal"                         # 刑事案件（刑事辩护、刑事控告、刑附民）
    ADMINISTRATIVE = "administrative"             # 行政诉讼与行政复议
    LABOR_ARBITRATION = "labor_arbitration"       # 劳动争议仲裁
    COMMERCIAL_ARBITRATION = "commercial_arb"     # 商事仲裁
    ENFORCEMENT = "enforcement"                   # 强制执行
    NON_LITIGATION = "non_litigation"             # 非诉专项/常年法顾/自定义


class HolidayRollMode(str, Enum):
    ROLL_FORWARD_WORKDAY = "roll_workday"         # 遇法定节假日顺延至首个工作日（民/行/刑上诉等）
    NO_ROLL_CUSTODY = "no_roll_custody"           # 刑事在押期间不得因节假日顺延（刑诉法§105条3款）
    CALENDAR_EXACT = "calendar_exact"             # 自然日精确到期，不顺延（保全续封/取保/合同约定日）


class RiskLevel(str, Enum):
    CRITICAL = "critical"    # 致命法定死线（上诉期、黄金37天批捕、再审、执行、保全续封）
    HIGH = "high"            # 刚性程序日程（开庭、举证期、答辩期、行政被告举证、会见）
    NORMAL = "normal"        # 阶段跟踪与常规节点
    LOW = "low"              # 个人内部待办


class NotifyTargetType(str, Enum):
    SELF = "self"            # 仅提醒设置人自己
    CASE_TEAM = "case_team"  # 提醒该案件全体办案组成员 (LEAD + CO_COUNSEL)
    BY_ROLE = "by_role"      # 按案件角色提醒（支持向 SUPERVISOR 升级抄送）
    CUSTOM_USERS = "custom"  # 指定用户名单提醒


class TriageStatus(str, Enum):
    PENDING = "pending"              # 待律师/助理确认 [REF-ANQI inbox]
    MANUAL_FETCH_REQUIRED = "manual" # 需人工点击下载（如遇人脸识别/二次验证码）[REF-LAWLINK SMS B2]
    CONFIRMED = "confirmed"          # 已确认并写入正式业务表
    IGNORED = "ignored"              # 忽略/非办案短信


# --- Models ---
class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    full_name: str
    hashed_password: str
    system_role: SystemRole = Field(default=SystemRole.LAWYER)
    wecom_userid: Optional[str] = Field(default=None, index=True, description="企业微信成员UserID")
    notify_channel: str = Field(
        default="wecom",
        description="推送渠道: wecom(企业微信/微信插件) | serverchan(Server酱普通微信) | wxpusher(WxPusher普通微信) | pushplus(PushPlus普通微信) | webhook(自定义Webhook)",
    )
    notify_target: Optional[str] = Field(
        default=None,
        description="普通微信/Webhook目标凭据: SendKey / WxPusher UID / PushPlus Token / Webhook URL",
    )
    phone_os: str = Field(default="ios", description="ios | android")
    phone_last4: Optional[str] = Field(default=None, description="手机号后4位，用于法院取件回填")
    id_card_last4: Optional[str] = Field(default=None, description="身份证后4位，用于法院取件回填")
    court_auto_fetch_mode: Optional[str] = Field(
        default=None,
        description="Tier-2 法院自动取件策略覆盖: confirm_before_sign | auto_non_judgment | auto_all",
    )
    calendar_color: str = Field(default="#3b82f6", description="团队日历中代表该律师的颜色")
    sms_webhook_token: str = Field(
        default_factory=lambda: secrets.token_urlsafe(16), unique=True, index=True
    )
    sms_webhook_secret: Optional[str] = Field(
        default=None, description="可选：Android SmsForwarder HMAC 签名密钥"
    )
    ics_feed_token: str = Field(
        default_factory=lambda: secrets.token_urlsafe(24), unique=True, index=True
    )
    default_deadline_offsets_days: List[int] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
        description="个人追加期限提前提醒天数偏好",
    )
    default_schedule_offsets_minutes: List[int] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
        description="个人追加日程提前提醒分钟偏好",
    )
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_utc_column())


class CaseMember(SQLModel, table=True):
    __tablename__ = "case_members"
    __table_args__ = (
        UniqueConstraint("case_id", "user_id", name="uq_case_member_case_user"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="cases.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    role_in_case: CaseMemberRole = Field(default=CaseMemberRole.CO_COUNSEL)


class Case(SQLModel, table=True):
    __tablename__ = "cases"

    id: Optional[int] = Field(default=None, primary_key=True)
    case_title: str = Field(index=True, description="案件名称")
    case_number: Optional[str] = Field(default=None, index=True, description="法院/检察院/公安/仲裁案号")
    case_category: CaseCategory = Field(default=CaseCategory.CIVIL, index=True)
    procedure_subtype: str = Field(
        default="一审",
        description="一审|二审|再审|侦查|审查起诉|刑事一审|刑事二审|刑事控告|行政复议|行政一审|行政二审|劳动仲裁|商事仲裁|执行|非诉",
    )
    current_stage: str = Field(default="准备阶段", index=True)
    stage_entered_at: datetime = Field(
        default_factory=utcnow,
        sa_column=_utc_column(),
        description="用于计算阶段停留天数",
    )
    visibility: CaseVisibility = Field(default=CaseVisibility.TEAM, index=True)
    owner_id: int = Field(foreign_key="users.id", index=True)

    # 当事人与办案机关信息
    client_name: str = Field(index=True, description="委托人/犯罪嫌疑人/行政相对人名称")
    client_id_last4: Optional[str] = Field(default=None, description="委托人证件/信用代码后4位")
    case_side: Optional[str] = Field(
        default=None,
        description="民事(原告/被告) | 刑事(辩护/被害人代理/控告) | 行政(原告相对人/被告机关)",
    )
    opponent_name: Optional[str] = Field(default=None)
    handling_agency: Optional[str] = Field(
        default=None, description="当前办案机关：XX法院/XX检察院/XX公安局/XX仲裁委"
    )
    judge_or_officer_name: Optional[str] = Field(default=None, description="承办法官/检察官/警官/仲裁员")
    judge_or_officer_phone: Optional[str] = Field(default=None)
    is_foreign_related: bool = Field(
        default=False, description="是否涉外案件（触发民诉法§285/§286三十日答辩/上诉期）"
    )

    # 刑事案件专属匹配维度
    custody_status: Optional[str] = Field(
        default=None, description="在押 | 取保候审 | 监视居住 | 未羁押"
    )
    custody_place: Optional[str] = Field(default=None, description="羁押看守所名称")
    crime_type: Optional[str] = Field(default=None, description="一般犯罪 | 流窜/多次/结伙作案")
    charge_name: Optional[str] = Field(default=None, description="涉嫌具体罪名，如 职务侵占罪")
    trial_mode: Optional[str] = Field(default=None, description="普通程序 | 简易程序 | 速裁程序")

    # 归档目录
    folder_rel_path: str = Field(description="相对于 CASES_ROOT 的案件目录路径")
    is_archived: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_utc_column())
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_utc_column())


class DeadlineRule(SQLModel, table=True):
    """可编辑的多部门法期限规则表（启动时从 JSON 种子初始化，支持前端增删改）"""

    __tablename__ = "deadline_rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    rule_code: str = Field(unique=True, index=True)
    name: str = Field(description="期限名称，如：刑事判决上诉期(10日)、审查批捕期(7日)")
    case_category: CaseCategory = Field(index=True)
    trigger_event_type: str = Field(index=True, description="触发事件")
    condition_match: Dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
        description="可选过滤条件，如 {'custody_status': '在押', 'crime_type': '一般犯罪'}",
    )
    duration_value: int = Field(description="时长数值")
    duration_unit: str = Field(default="days", description="days | months | years")
    holiday_roll_mode: HolidayRollMode = Field(default=HolidayRollMode.ROLL_FORWARD_WORKDAY)
    legal_basis: str = Field(description="法条依据")
    risk_level: RiskLevel = Field(default=RiskLevel.CRITICAL)
    default_offsets_days: List[int] = Field(
        default_factory=lambda: [7, 3, 1, 0],
        sa_column=Column(JSON, nullable=False),
    )
    is_system_builtin: bool = Field(default=True)
    is_enabled: bool = Field(default=True)


class CaseEvent(SQLModel, table=True):
    """客观发生的程序事件（驱动阶段流转与期限计算的唯一事实源）"""

    __tablename__ = "case_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="cases.id", index=True)
    event_type: str = Field(index=True)
    event_date: date = Field(description="事件发生日/签收送达日/刑拘执行日 (Asia/Shanghai)")
    service_method: str = Field(
        default="electronic",
        description="direct(直接/电子送达) | announcement_30(公告30日) | announcement_60(涉外公告)",
    )
    authority_specified_days: Optional[int] = Field(
        default=None, description="办案机关特别指定的天数（如法院指定举证期20日）"
    )
    source_sms_id: Optional[int] = Field(default=None, foreign_key="sms_inbound.id")
    notes: Optional[str] = Field(default=None)
    created_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow, sa_column=_utc_column())


class Deadline(SQLModel, table=True):
    """案件具体期限实例表（含法定派生期限与律师手动创建的自定义期限）"""

    __tablename__ = "deadlines"

    id: Optional[int] = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="cases.id", index=True)
    source_event_id: Optional[int] = Field(default=None, foreign_key="case_events.id", index=True)
    rule_code: str = Field(default="CUSTOM", index=True)
    title: str
    legal_basis: Optional[str] = Field(default=None)
    base_date: date
    raw_due_date: date
    due_date: date = Field(index=True)
    holiday_roll_mode: HolidayRollMode = Field(default=HolidayRollMode.ROLL_FORWARD_WORKDAY)
    roll_reason: Optional[str] = Field(default=None)
    risk_level: RiskLevel = Field(default=RiskLevel.CRITICAL)
    is_manual_override: bool = Field(
        default=False, description="若为True，禁止引擎自动重算覆盖"
    )
    override_by: Optional[int] = Field(default=None, foreign_key="users.id")
    override_at: Optional[datetime] = Field(default=None, sa_column=_utc_column(nullable=True))
    override_reason: Optional[str] = Field(default=None)
    is_completed: bool = Field(default=False, index=True)
    completed_at: Optional[datetime] = Field(default=None, sa_column=_utc_column(nullable=True))
    completed_by: Optional[int] = Field(default=None, foreign_key="users.id")

    # 个人与团队提醒配置
    notify_target_type: NotifyTargetType = Field(default=NotifyTargetType.CASE_TEAM)
    notify_owner_id: Optional[int] = Field(
        default=None, foreign_key="users.id", description="将通知范围切换为 SELF 时的接收人"
    )
    custom_notify_user_ids: List[int] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    escalate_to_supervisor: bool = Field(
        default=True, description="临期1天未完成是否自动抄送督办合伙人"
    )
    reminder_offsets_days: List[int] = Field(
        default_factory=lambda: [7, 3, 1, 0],
        sa_column=Column(JSON, nullable=False),
    )
    personal_custom_alerts: List[Dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
        description="[{'user_id': 1, 'fire_at': '2026-11-20T20:00:00+08:00'}]",
    )
    created_at: datetime = Field(default_factory=utcnow, sa_column=_utc_column())


class ScheduleItem(SQLModel, table=True):
    """日历具体时间段日程（开庭、会见、阅卷、调解、听证、个人/团队待办）"""

    __tablename__ = "schedule_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    case_id: Optional[int] = Field(default=None, foreign_key="cases.id", index=True)
    owner_id: int = Field(foreign_key="users.id", index=True)
    category: str = Field(
        default="hearing",
        description="hearing(开庭/仲裁庭) | detention_meet(看守所会见) | file_review(检察院/法院阅卷) | meeting(谈话/调解/听证) | task(待办)",
    )
    title: str
    start_time: datetime = Field(sa_column=_utc_column(index=True))
    end_time: datetime = Field(sa_column=_utc_column())
    location: Optional[str] = Field(default=None)
    risk_level: RiskLevel = Field(default=RiskLevel.HIGH)
    notify_target_type: NotifyTargetType = Field(default=NotifyTargetType.CASE_TEAM)
    custom_notify_user_ids: List[int] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    reminder_offsets_minutes: List[int] = Field(
        default_factory=lambda: [4320, 1440, 120],
        sa_column=Column(JSON, nullable=False),
    )
    is_completed: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_utc_column())


class SmsInbound(SQLModel, table=True):
    """手机转发的原始短信记录（兼容 iOS 快捷指令 / Mac / Android SmsForwarder）"""

    __tablename__ = "sms_inbound"

    id: Optional[int] = Field(default=None, primary_key=True)
    receiver_user_id: int = Field(foreign_key="users.id", index=True)
    client_platform: str = Field(
        default="unknown", description="ios_shortcuts | mac_relay | android_smsforwarder"
    )
    sender_number: str = Field(default="unknown")
    content: str
    content_hash: str = Field(index=True, description="sha256(receiver_user_id + content.strip())")
    dedup_key: str = Field(
        unique=True,
        index=True,
        description="时间窗口去重键：sha256(receiver_user_id + content.strip() + shanghai_date)",
    )
    sms_category: str = Field(
        default="unknown",
        description="court_service | procuratorate_12309 | detention_police | arbitration | invoice | other",
    )
    received_at: datetime = Field(default_factory=utcnow, sa_column=_utc_column())


class InboxTriageItem(SQLModel, table=True):
    """待确认收件箱（连接短信/爬虫与正式业务表的安全隔离层）"""

    __tablename__ = "inbox_triage_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    sms_id: Optional[int] = Field(default=None, foreign_key="sms_inbound.id", index=True)
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    matched_case_id: Optional[int] = Field(default=None, foreign_key="cases.id", index=True)
    item_type: str = Field(description="judicial_stage_and_doc | invoice_archive")
    status: TriageStatus = Field(default=TriageStatus.PENDING, index=True)
    extracted_payload: Dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    downloaded_files: List[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    error_or_manual_reason: Optional[str] = Field(default=None)
    confirmed_by: Optional[int] = Field(default=None, foreign_key="users.id")
    confirmed_at: Optional[datetime] = Field(default=None, sa_column=_utc_column(nullable=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_utc_column())


class Document(SQLModel, table=True):
    """归档文件索引表（文书与发票）"""

    __tablename__ = "documents"

    id: Optional[int] = Field(default=None, primary_key=True)
    case_id: Optional[int] = Field(default=None, foreign_key="cases.id", index=True)
    folder_category: str = Field(description="01_司法与程序文书 | 05_财务与发票 | _待认领发票")
    file_name: str
    rel_file_path: str = Field(unique=True)
    doc_subtype: Optional[str] = Field(default=None)
    invoice_meta: Optional[Dict[str, Any]] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    uploaded_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow, sa_column=_utc_column())


class ReminderDelivery(SQLModel, table=True):
    """微信提醒投递幂等台账"""

    __tablename__ = "reminder_deliveries"

    id: Optional[int] = Field(default=None, primary_key=True)
    idempotency_key: str = Field(unique=True, index=True)
    source_type: str = Field(description="deadline | schedule | triage_alert")
    source_id: int
    recipient_user_ids: List[int] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    sent_user_ids: List[int] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    retry_count: int = Field(default=0)
    next_retry_at: Optional[datetime] = Field(
        default=None, sa_column=_utc_column(nullable=True)
    )
    scheduled_at: datetime = Field(sa_column=_utc_column(index=True))
    sent_at: Optional[datetime] = Field(default=None, sa_column=_utc_column(nullable=True))
    status: str = Field(
        default="pending", description="pending | sent | skipped_completed | failed"
    )
    error_msg: Optional[str] = Field(default=None)


class AuditLog(SQLModel, table=True):
    """关键操作审计日志（阶段流转、手动修改死线、分拣确认、规则编辑）"""

    __tablename__ = "audit_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    actor_user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    action: str = Field(index=True)
    entity_type: str = Field(index=True)
    entity_id: Optional[int] = Field(default=None, index=True)
    case_id: Optional[int] = Field(default=None, foreign_key="cases.id", index=True)
    detail: Dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    created_at: datetime = Field(default_factory=utcnow, sa_column=_utc_column(index=True))


class AutoTriagePolicy(SQLModel, table=True):
    """团队级自动分拣策略开关（按文书/短信类型配置自动归档或人工二次复核）"""

    __tablename__ = "auto_triage_policies"

    id: Optional[int] = Field(default=None, primary_key=True)
    policy_key: str = Field(unique=True, index=True)
    label: str
    description: str = Field(default="")
    mode: str = Field(
        default="auto_archive",
        description="auto_archive(默认自动归档/处理) | require_confirm(转人工二次复核)",
    )
    sort_order: int = Field(default=0)
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_utc_column())
