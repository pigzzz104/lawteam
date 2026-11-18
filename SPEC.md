# Project Specification: LawTeam-OS (律所团队全案种协同管理与自动化系统)

## 1. Role & Objective
You are a Principal Full-Stack Engineer and LegalTech Architect. Your task is to build **LawTeam-OS**, a production-grade, multi-user lawyer case management system for a Chinese legal team handling **Civil (民事), Criminal (刑事), Administrative (行政诉讼与复议), Arbitration (劳动与商事仲裁), Enforcement (执行), and Non-Litigation (非诉/自定义)** cases.

Instead of inventing domain logic from scratch, you MUST clone, inspect, and port proven rules, regexes, scrapers, and patterns from the **Reference Open-Source Repositories** listed in Section 2.

---

## 2. Open-Source Reference Registry & Code Porting Map (开源项目源码对照表)

In **Phase 0**, clone the following repositories into a git-ignored `.ref_repos/` directory and reference the exact files below when implementing each module:

| Ref ID | GitHub Repository | License | Exact Files / Modules to Inspect & Port | What We Port from It |
| :--- | :--- | :--- | :--- | :--- |
| **`[REF-SKILLHUB]`** | `https://github.com/CSlawyer1985/legal-skillhub` | MIT | • `skills/litigation-hub/references/sms-patterns.json`<br>• `skills/litigation-hub/references/deadline-rules.json`<br>• `skills/litigation-hub/references/china-holidays.json`<br>• `skills/litigation-hub/references/平台下载流程.md`<br>• `skills/litigation-hub/scripts/court_full_pipeline.py`<br>• `skills/litigation-hub/scripts/holiday_utils.py`<br>• `skills/litigation-hub/scripts/court_utils.py`<br>• `skills/criminal-defense-workflow/SKILL.md`<br>• `skills/akylegal-applications/references/civil-procedure.md`<br>• `skills/akylegal-applications/references/criminal-procedure.md`<br>• `skills/akylegal-applications/references/admin-procedure.md`<br>• `skills/akylegal-applications/references/arbitration.md` | 1. 法院/检察院/仲裁短信正则库、案号提取与排除词表<br>2. 全国统一送达平台 (`zxfw.court.gov.cn`) API 直连下载全套附件逻辑<br>3. 广东/集约/湖北/成都等省市法院 Playwright 自动填码下载脚本<br>4. 民事/刑事/行政/仲裁四大程序的阶段划分、关键期限与文书归档标准 |
| **`[REF-ANQI]`** | `https://github.com/zj-ai-lab/anqi` | AGPL-3.0 *(仅参考JSON词表与架构设计，使用Python重写)* | • `docs/DESIGN.md`<br>• `rules/deadline_rules.json` (民事)<br>• `rules/deadline_rules_criminal.json` (刑事辩护)<br>• `rules/deadline_rules_complaint.json` (刑事控告)<br>• `rules/deadline_rules_admin.json` (行政诉讼与复议)<br>• `rules/stage_templates*.json`<br>• `src/lib/llm.js` & `server.js` | 1. **架构铁律**：LLM仅做结构化提取，无数据库写权限，严禁LLM计算法定期限<br>2. 民/刑/行/仲裁按程序隔离的独立可编辑期限规则表<br>3. 刑事案件专属匹配维度 (`custody_status`, `crime_type`, `case_side`, `handling_agency`, `custody_place`)<br>4. 本地案件目录映射 (`ANJIAN_FILES_ROOT`) 与收件箱人工确认流 (`inbox`) |
| **`[REF-LAWLINK]`** | `https://github.com/lawflow-boop/LawLink` | MIT | • `prisma/schema.prisma`<br>• `src/lib/holidays.ts`<br>• `src/lib/` & `src/server/` (SMS B2/B3 pipeline, ReminderDelivery, ICS feed) | 1. 案件/事件/期限/日程/提醒投递幂等台账 (`ReminderDelivery`) 数据模型<br>2. 法院短信解析→自动取件→人工接续引导→律师一键确认入库工作流 (B2/B3)<br>3. 国务院放假与周末调休补班顺延算法 (`holidays.ts`) |
| **`[REF-FACHUAN]`** | `https://github.com/Lawyer-ray/FachuanHybridSystem` | Custom *(仅参考架构)* | • Async SMS pipeline & `.ics` calendar subscription endpoint | 短信异步解析归档流水线、免 Header Token 的 `.ics` 日历只读订阅 URL 设计 |
| **`[REF-INVOICE-DL]`** | `https://github.com/EthanYoQ/Invoice-Downloader` | Apache-2.0 | • `document_types.py`<br>• `archive_service.py`<br>• `document_acceptance.py` | 电子发票短链（诺诺网/百望云/税局数电票）Playwright 网页恢复下载 PDF/OFD/XML 逻辑 |
| **`[REF-INVOICE-EX]`** | `https://github.com/NevermoreN/invoice2excel` | MIT | • Core PDF coordinate parser script | 基于 `pdfplumber` 坐标提取中国增值税发票/数电票 21 个核心字段（发票号、金额、购销方、备注等） |
| **`[REF-SMS-ANDROID]`**| `https://github.com/pppscn/SmsForwarder` | GPL-3.0 *(客户端工具)* | • Webhook JSON Payload & HMAC-SHA256 Sign Spec | Android 手机端 `SmsForwarder` Webhook 报文格式与签名校验规范 |
| **`[REF-SMS-IOS]`** | `https://github.com/wangqiang1988/smspage`<br>`https://github.com/0xYu4n/MacSMSForwarder` | MIT | • iOS Shortcuts (快捷指令) HTTP POST JSON schema<br>• macOS `~/Library/Messages/chat.db` relay | 1. iPhone 原生「快捷指令个人自动化」锁屏静默转发短信 JSON 规范<br>2. iPhone 同步 Mac 后的 `MacSMSForwarder` 桌面实时转发方案 |

---

## 3. Non-Negotiable Architectural Iron Rules (架构红线与铁律)

### Iron Rule #1: Multi-Domain Deterministic & Configurable Deadline Engine
`[Source: REF-ANQI docs/DESIGN.md, rules/deadline_rules_*.json & REF-LAWLINK src/lib/holidays.ts]`
1. **NEVER Use LLMs to Calculate Dates**: LLMs or regex parsers may ONLY extract factual events (`CaseEvent` with `event_date` and `service_method`, or `ScheduleItem` with `start_time`). All statutory deadlines MUST be computed by the pure Python function in `deadline_engine.py`.
2. **Strict Procedure Isolation (按案件程序严格隔离，绝不串用)**:
   - Every case has a `case_category` (`civil` 民事 | `criminal` 刑事 | `administrative` 行政 | `labor_arbitration` 劳动仲裁 | `commercial_arb` 商事仲裁 | `enforcement` 执行 | `non_litigation` 非诉/自定义).
   - A `judgment_served` (收到一审判决书) event on a `civil` or `administrative` case derives a **15-day** appeal deadline (`民诉法§171` / `行诉法§85`); on a `criminal` case it MUST derive a **10-day** appeal deadline (`刑诉法§230`), and **5-day** for a criminal ruling (`刑事裁定书`).
3. **Three Holiday Roll-Forward Modes (`HolidayRollMode`)**:
   - `ROLL_FORWARD_WORKDAY` (顺延至节后首个工作日): Used for Civil, Administrative, Arbitration, and Criminal Trial/Appeal filing deadlines (`民诉法§85`, `行诉法§101`, `刑诉法§105条1-2款`). Trigger/service day is excluded (starts from next day). Uses `chinese-calendar`: if the expiration date falls on a weekend or holiday, roll forward to the first workday. **State Council make-up workdays on weekends (调休补班周末) ARE working days—DO NOT roll forward on make-up workdays.**
   - `NO_ROLL_CUSTODY` (刑事在押期间节假日绝不顺延): **Mandatory per 《刑事诉讼法》第105条第3款** (“犯罪嫌疑人、被告人或者罪犯在押期间，应当在期间届满前办理完毕，不得因节假日而延长”). Used for criminal detention (`刑拘提请批捕 3日/7日/30日`), procuratorate arrest approval (`审查批捕 7日/黄金37天`), and investigative custody (`侦查羁押 2个月`).
   - `CALENDAR_EXACT` (自然日精确到期，不顺延): Used for property preservation validity end dates (`保全续封到期`), bail expiration (`取保候审12个月`), or contractually fixed dates.
4. **Editable Rules & Manual Override Lock**:
   - Rules are stored in the `DeadlineRule` database table (seeded from JSON on startup) so team admins can edit or add custom rules in the UI.
   - If a lawyer manually edits a deadline's `due_date`, set `deadline.is_manual_override = True`. Automatic recalculations MUST NEVER overwrite a manually overridden deadline.

### Iron Rule #2: Mandatory Inbox Triage Before Mutating Case/Calendar Data
`[Source: REF-ANQI docs/DESIGN.md & REF-LAWLINK SMS B3]`
- Incoming SMS or OCR results MUST NEVER silently overwrite `Case.current_stage`, `ScheduleItem`, or `Deadline`.
- They must first create an `InboxTriageItem` (`status = PENDING`). On the FullCalendar UI, pending items render with a dashed border and `[待确认]` badge.
- Only when a user clicks **Confirm** (in Web UI or WeCom card) does the system atomically update the case stage, create the `CaseEvent` / `ScheduleItem`, derive `Deadline`s, and move downloaded files into the formal case directory.

### Iron Rule #3: Safe Court Document Fetching & Human-in-the-Loop Handoff
`[Source: REF-LAWLINK SMS B2 & REF-SKILLHUB 平台下载流程.md]`
- On China's court e-service platforms, clicking "Sign/Download" records an official legal service timestamp (送达回证时间), which legally starts the appeal or defense clock.
- Automate direct PDF/OFD retrieval ONLY for Tier-1 (`zxfw.court.gov.cn` API) and Tier-2 (provincial links with explicit SMS extraction codes).
- If a link requires WeChat Mini-Program facial recognition, dynamic SMS login OTP, or slider captcha (Tier-3), immediately halt automation, set `InboxTriageItem.status = MANUAL_FETCH_REQUIRED`, and push a WeCom card to the lawyer with the link + extraction code for manual download.

### Iron Rule #4: Real Local/NAS Case Directory Tree as Single Source of Truth
`[Source: REF-ANQI ANJIAN_FILES_ROOT & REF-SKILLHUB archive-format.md]`
- Every case maps to a physical directory on disk/NAS under `{CASES_ROOT}/{YYYY}/{case_number_or_temp_id}_{client_name}/`.
- Subfolders adapt to `case_category` while keeping consistent numeric prefixes `01` to `05` (see Section 7.2).

---

## 4. Tech Stack & Directory Structure

### Tech Stack
- **Backend**: Python 3.11+, FastAPI, SQLModel (SQLAlchemy 2.0 + Pydantic v2), PostgreSQL 16, Alembic, APScheduler, Playwright (async Python), `pdfplumber`, `chinese-calendar`, `httpx`, `icalendar`, JWT Auth (`PyJWT` + `passlib[bcrypt]`).
- **Frontend**: React 18, TypeScript, Vite, TailwindCSS, shadcn/ui, `@fullcalendar/react` (`daygrid`, `timegrid`, `list`, `interaction`), TanStack Query v5, Zustand, Lucide Icons.

### Directory Structure
```text
lawteam-os/
├── .ref_repos/                     # [Git-Ignored] Cloned open-source reference repos for Agent inspection
│   ├── legal-skillhub/             # [REF-SKILLHUB]
│   ├── anqi/                       # [REF-ANQI]
│   ├── LawLink/                    # [REF-LAWLINK]
│   ├── Invoice-Downloader/         # [REF-INVOICE-DL]
│   └── invoice2excel/              # [REF-INVOICE-EX]
├── docker-compose.yml              # PostgreSQL 16 + Backend + Frontend
├── .env.example
├── backend/
│   ├── pyproject.toml
│   ├── alembic/
│   └── app/
│       ├── main.py                 # FastAPI entrypoint, seed loader & APScheduler lifespan
│       ├── config.py               # Pydantic Settings (DB, CASES_ROOT, WECOM, LLM)
│       ├── database.py             # SQLModel session management
│       ├── models/
│       │   └── entities.py         # Complete SQLModel ORM tables
│       ├── schemas/                # API Request/Response DTOs
│       ├── core/
│       │   ├── security.py         # JWT & RBAC dependencies
│       │   ├── stage_templates.json# [Ported from REF-ANQI & REF-SKILLHUB] Multi-domain stage templates
│       │   ├── deadline_rules_seed.json # [Ported from REF-ANQI, REF-LAWLINK, REF-SKILLHUB] Multi-law rules
│       │   └── sms_patterns.json   # [Ported from REF-SKILLHUB] 12368/12309/Arbitration/Invoice regexes
│       ├── services/
│       │   ├── file_bridge.py      # [Ported from REF-ANQI & REF-SKILLHUB] Case folder & safe renaming
│       │   ├── deadline_engine.py  # [Ported from REF-ANQI & REF-LAWLINK] Multi-law pure date calculator
│       │   ├── sms_normalizer.py   # [Ported from REF-SMS-IOS & REF-SMS-ANDROID] iOS/Mac/Android adapter
│       │   ├── sms_parser.py       # [Ported from REF-SKILLHUB & REF-ANQI] Regex + LLM fallback parser
│       │   ├── court_fetcher.py    # [Ported from REF-SKILLHUB & REF-LAWLINK] 3-Tier court doc downloader
│       │   ├── invoice_fetcher.py  # [Ported from REF-INVOICE-DL & REF-INVOICE-EX] Playwright + pdfplumber
│       │   ├── reminder_scheduler.py # [Ported from REF-LAWLINK] Personal/Team reminder & escalation
│       │   └── wecom_notifier.py   # [Ported from REF-ANQI] Enterprise WeChat API client
│       └── api/
│           ├── auth.py
│           ├── users.py
│           ├── cases.py
│           ├── rules.py            # CRUD for editable DeadlineRule & StageTemplates
│           ├── calendar.py         # FullCalendar JSON feed + .ics subscription feed [REF-FACHUAN]
│           ├── inbox.py            # Triage confirmation & rejection endpoints [REF-ANQI & REF-LAWLINK]
│           ├── webhooks.py         # Unified iOS Shortcuts / Mac / Android SmsForwarder webhook receiver
│           └── reminders.py
└── frontend/
    ├── package.json
    └── src/
        ├── pages/
        │   ├── CalendarWorkbench.tsx # Multi-view calendar + Team/My filter + Conflict badge
        │   ├── CaseList.tsx          # Multi-domain cases (Civil/Criminal/Admin/Arb) + Stage view
        │   ├── CaseDetail.tsx        # Case info, Criminal/Admin fields, Events, Deadlines, Folder browser
        │   ├── InboxTriage.tsx       # Pending SMS, Court Docs & Unclaimed Invoices
        │   ├── RuleManager.tsx       # Editable statutory & custom deadline rules table
        │   ├── PhoneSetupGuide.tsx   # iOS Shortcuts QR/Tutorial + Android SmsForwarder config generator
        │   ├── ReminderSettings.tsx  # Team default rules & Personal custom offsets
        │   └── TeamAdmin.tsx         # User management & SmsForwarder token setup
        └── components/
```

---

## 5. Complete Database Schema (`backend/app/models/entities.py`)
`[Source: Ported and merged from REF-LAWLINK prisma/schema.prisma & REF-ANQI docs/DESIGN.md]`

```python
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from enum import Enum
import secrets
from sqlmodel import SQLModel, Field, Column, JSON

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
    SUPERVISOR = "supervisor"# 督办合伙人（接收致命死线临期未办结升级预警）

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
    CRITICAL = "critical"    # 🔴 致命法定死线（上诉期、黄金37天批捕、再审、执行、保全续封）
    HIGH = "high"            # 🟠 刚性程序日程（开庭、举证期、答辩期、行政被告举证、会见）
    NORMAL = "normal"        # 🔵 阶段跟踪与常规节点
    LOW = "low"              # ⚪ 个人内部待办

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
    phone_os: str = Field(default="ios", description="ios | android")
    phone_last4: Optional[str] = Field(default=None, description="手机号后4位，用于法院取件回填 [REF-SKILLHUB]")
    id_card_last4: Optional[str] = Field(default=None, description="身份证后4位，用于法院取件回填 [REF-SKILLHUB]")
    calendar_color: str = Field(default="#3b82f6", description="团队日历中代表该律师的颜色")
    sms_webhook_token: str = Field(default_factory=lambda: secrets.token_urlsafe(16), unique=True, index=True)
    sms_webhook_secret: Optional[str] = Field(default=None, description="可选：Android SmsForwarder 签名密钥")
    ics_feed_token: str = Field(default_factory=lambda: secrets.token_urlsafe(24), unique=True, index=True)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class CaseMember(SQLModel, table=True):
    __tablename__ = "case_members"
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
    procedure_subtype: str = Field(default="一审", description="一审|二审|再审|侦查|审查批捕|审查起诉|行政复议|劳动仲裁|执行")
    current_stage: str = Field(default="准备阶段", index=True)
    stage_entered_at: datetime = Field(default_factory=datetime.utcnow, description="用于计算阶段停留天数 [REF-ANQI]")
    visibility: CaseVisibility = Field(default=CaseVisibility.TEAM, index=True)
    owner_id: int = Field(foreign_key="users.id", index=True)
    # 当事人与办案机关信息
    client_name: str = Field(index=True, description="委托人/犯罪嫌疑人/行政相对人名称")
    client_id_last4: Optional[str] = Field(default=None, description="委托人证件/信用代码后4位 [REF-SKILLHUB]")
    case_side: Optional[str] = Field(default=None, description="民事(原告/被告) | 刑事(辩护/被害人代理/控告) | 行政(原告相对人/被告机关)")
    opponent_name: Optional[str] = Field(default=None)
    handling_agency: Optional[str] = Field(default=None, description="当前办案机关：XX法院/XX检察院/XX公安局/XX仲裁委 [REF-ANQI]")
    judge_or_officer_name: Optional[str] = Field(default=None, description="承办法官/检察官/警官/仲裁员")
    judge_or_officer_phone: Optional[str] = Field(default=None)
    # 刑事案件专属匹配维度 [Ported from REF-ANQI docs/DESIGN.md 021/022/025]
    custody_status: Optional[str] = Field(default=None, description="在押 | 取保候审 | 监视居住 | 未羁押")
    custody_place: Optional[str] = Field(default=None, description="羁押看守所名称")
    crime_type: Optional[str] = Field(default=None, description="一般犯罪 | 流窜/多次/结伙作案")
    trial_mode: Optional[str] = Field(default=None, description="普通程序 | 简易程序 | 速裁程序")
    # 归档目录
    folder_rel_path: str = Field(description="相对于CASES_ROOT的案件目录路径 [REF-ANQI]")
    is_archived: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class DeadlineRule(SQLModel, table=True):
    """可编辑的多部门法期限规则表（启动时从 JSON 种子初始化，支持前端增删改）[REF-ANQI]"""
    __tablename__ = "deadline_rules"
    id: Optional[int] = Field(default=None, primary_key=True)
    rule_code: str = Field(unique=True, index=True)
    name: str = Field(description="期限名称，如：刑事判决上诉期(10日)、审查批捕期(7日)")
    case_category: CaseCategory = Field(index=True)
    trigger_event_type: str = Field(index=True, description="触发事件，如 judgment_served, detention_notified, case_transferred_arrest")
    condition_match: Dict[str, Any] = Field(default={}, sa_column=Column(JSON), description="可选过滤条件，如 {'custody_status': '在押', 'crime_type': '一般犯罪'}")
    duration_value: int = Field(description="时长数值")
    duration_unit: str = Field(default="days", description="days | months | years")
    holiday_roll_mode: HolidayRollMode = Field(default=HolidayRollMode.ROLL_FORWARD_WORKDAY)
    legal_basis: str = Field(description="法条依据，如《刑事诉讼法》第230条")
    risk_level: RiskLevel = Field(default=RiskLevel.CRITICAL)
    default_offsets_days: List[int] = Field(default=[7, 3, 1, 0], sa_column=Column(JSON))
    is_system_builtin: bool = Field(default=True)
    is_enabled: bool = Field(default=True)

class CaseEvent(SQLModel, table=True):
    """客观发生的程序事件（驱动阶段流转与期限计算的唯一事实源）[REF-LAWLINK & REF-ANQI]"""
    __tablename__ = "case_events"
    id: Optional[int] = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="cases.id", index=True)
    event_type: str = Field(index=True)
    event_date: date = Field(description="事件发生日/签收送达日/刑拘执行日")
    service_method: str = Field(default="electronic", description="direct(直接/电子送达) | announcement_30(公告30日) | announcement_60(涉外公告)")
    authority_specified_days: Optional[int] = Field(default=None, description="办案机关特别指定的天数（如法院指定举证期20日）")
    source_sms_id: Optional[int] = Field(default=None, foreign_key="sms_inbound.id")
    notes: Optional[str] = Field(default=None)
    created_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Deadline(SQLModel, table=True):
    """案件具体期限实例表（含法定派生期限与律师手动创建的自定义期限）[REF-LAWLINK & REF-ANQI]"""
    __tablename__ = "deadlines"
    id: Optional[int] = Field(default=None, primary_key=True)
    case_id: int = Field(foreign_key="cases.id", index=True)
    source_event_id: Optional[int] = Field(default=None, foreign_key="case_events.id")
    rule_code: str = Field(default="CUSTOM", index=True)
    title: str
    legal_basis: Optional[str] = Field(default=None)
    base_date: date
    raw_due_date: date
    due_date: date = Field(index=True)
    holiday_roll_mode: HolidayRollMode = Field(default=HolidayRollMode.ROLL_FORWARD_WORKDAY)
    roll_reason: Optional[str] = Field(default=None)
    risk_level: RiskLevel = Field(default=RiskLevel.CRITICAL)
    is_manual_override: bool = Field(default=False, description="若为True，禁止引擎自动重算覆盖 [REF-ANQI]")
    is_completed: bool = Field(default=False, index=True)
    completed_at: Optional[datetime] = Field(default=None)
    completed_by: Optional[int] = Field(default=None, foreign_key="users.id")
    # 个人与团队提醒配置
    notify_target_type: NotifyTargetType = Field(default=NotifyTargetType.CASE_TEAM)
    custom_notify_user_ids: List[int] = Field(default=[], sa_column=Column(JSON))
    escalate_to_supervisor: bool = Field(default=True, description="临期1天未完成是否自动抄送督办合伙人")
    reminder_offsets_days: List[int] = Field(default=[7, 3, 1, 0], sa_column=Column(JSON))
    personal_custom_alerts: List[Dict[str, Any]] = Field(default=[], sa_column=Column(JSON), description="[{'user_id': 1, 'fire_at': '2026-11-20T20:00:00'}]")

class ScheduleItem(SQLModel, table=True):
    """日历具体时间段日程（开庭、会见、阅卷、调解、听证、个人/团队待办）[REF-LAWLINK]"""
    __tablename__ = "schedule_items"
    id: Optional[int] = Field(default=None, primary_key=True)
    case_id: Optional[int] = Field(default=None, foreign_key="cases.id", index=True)
    owner_id: int = Field(foreign_key="users.id", index=True)
    category: str = Field(default="hearing", description="hearing(开庭/仲裁庭) | detention_meet(看守所会见) | file_review(检察院/法院阅卷) | meeting(谈话/调解/听证) | task(待办)")
    title: str
    start_time: datetime = Field(index=True)
    end_time: datetime
    location: Optional[str] = Field(default=None)
    risk_level: RiskLevel = Field(default=RiskLevel.HIGH)
    notify_target_type: NotifyTargetType = Field(default=NotifyTargetType.CASE_TEAM)
    custom_notify_user_ids: List[int] = Field(default=[], sa_column=Column(JSON))
    reminder_offsets_minutes: List[int] = Field(default=[4320, 1440, 120], sa_column=Column(JSON))
    is_completed: bool = Field(default=False)

class SmsInbound(SQLModel, table=True):
    """手机转发的原始短信记录（兼容 iOS 快捷指令 / Mac / Android SmsForwarder）"""
    __tablename__ = "sms_inbound"
    id: Optional[int] = Field(default=None, primary_key=True)
    receiver_user_id: int = Field(foreign_key="users.id", index=True)
    client_platform: str = Field(default="unknown", description="ios_shortcuts | mac_relay | android_smsforwarder")
    sender_number: str = Field(default="unknown")
    content: str
    content_hash: str = Field(unique=True, index=True, description="sha256(receiver_user_id + content.strip())")
    sms_category: str = Field(default="unknown", description="court_service | procuratorate_12309 | arbitration | invoice | other")
    received_at: datetime = Field(default_factory=datetime.utcnow)

class InboxTriageItem(SQLModel, table=True):
    """待确认收件箱（连接短信/爬虫与正式业务表的安全隔离层）[REF-ANQI & REF-LAWLINK]"""
    __tablename__ = "inbox_triage_items"
    id: Optional[int] = Field(default=None, primary_key=True)
    sms_id: Optional[int] = Field(default=None, foreign_key="sms_inbound.id")
    owner_user_id: int = Field(foreign_key="users.id", index=True)
    matched_case_id: Optional[int] = Field(default=None, foreign_key="cases.id", index=True)
    item_type: str = Field(description="judicial_stage_and_doc | invoice_archive")
    status: TriageStatus = Field(default=TriageStatus.PENDING, index=True)
    extracted_payload: Dict[str, Any] = Field(default={}, sa_column=Column(JSON))
    downloaded_files: List[str] = Field(default=[], sa_column=Column(JSON))
    error_or_manual_reason: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Document(SQLModel, table=True):
    """归档文件索引表（文书与发票）[REF-INVOICE-DL & REF-SKILLHUB]"""
    __tablename__ = "documents"
    id: Optional[int] = Field(default=None, primary_key=True)
    case_id: Optional[int] = Field(default=None, foreign_key="cases.id", index=True)
    folder_category: str = Field(description="01_司法与程序文书 | 05_财务与发票 | _待认领发票")
    file_name: str
    rel_file_path: str = Field(unique=True)
    doc_subtype: Optional[str] = Field(default=None)
    invoice_meta: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    uploaded_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

class ReminderDelivery(SQLModel, table=True):
    """微信提醒投递幂等台账 [REF-LAWLINK ReminderDelivery]"""
    __tablename__ = "reminder_deliveries"
    id: Optional[int] = Field(default=None, primary_key=True)
    idempotency_key: str = Field(unique=True, index=True)
    source_type: str = Field(description="deadline | schedule | triage_alert")
    source_id: int
    recipient_user_ids: List[int] = Field(default=[], sa_column=Column(JSON))
    scheduled_at: datetime = Field(index=True)
    sent_at: Optional[datetime] = Field(default=None)
    status: str = Field(default="pending", description="pending | sent | skipped_completed | failed")
    error_msg: Optional[str] = Field(default=None)
```

---

## 6. Cross-Platform SMS Forwarding Architecture: iOS (iPhone) & Android (`sms_normalizer.py` & `webhooks.py`)
`[Source: Ported from REF-SMS-IOS (wangqiang1988/smspage & 0xYu4n/MacSMSForwarder) and REF-SMS-ANDROID (pppscn/SmsForwarder)]`

Support 3 ingestion channels into a single unified endpoint `POST /api/webhooks/sms?token={sms_webhook_token}`:

### Channel A: iPhone Native — iOS "Shortcuts" Personal Automation (iOS 快捷指令个人自动化，主力方案)
1. On iPhone, open **快捷指令 (Shortcuts)** -> **自动化 (Automation)** -> **新建自动化** -> **信息 (Message)**.
2. Set **信息包含 (Message Contains)** keywords: `法院`, `12368`, `检察`, `12309`, `看守所`, `公安`, `仲裁`, `送达`, `传票`, `案号`, `开庭`, `判决`, `裁定`, `发票`, `诺诺`, `百望`, `数电`.
3. Choose **立即运行 (Run Immediately)** and disable **运行时通知 (Notify When Run)** (executes silently in background even when iPhone is locked).
4. Add action **获取 URL 内容 (Get Contents of URL)** -> `POST` to `https://{server}/api/webhooks/sms?token={user.sms_webhook_token}&platform=ios_shortcuts` with JSON body `{"content": "<Shortcut Input -> Content>", "sender": "<Shortcut Input -> Sender>"}`.

### Channel B: iPhone + Office Mac — iCloud SMS Relay (`MacSMSForwarder` 补充方案)
- When a lawyer enables iPhone `设置 -> 信息 -> 短信转发` to their office Mac, `MacSMSForwarder` (`[REF-SMS-IOS]`) watches `~/Library/Messages/chat.db` and POSTs matching SMS to `/api/webhooks/sms?token={user.sms_webhook_token}&platform=mac_relay`.

### Channel C: Android — `SmsForwarder` App (安卓主力/备用机方案)
- Android `SmsForwarder` (`[REF-SMS-ANDROID]`) intercepts matching SMS and POSTs `{"from": "[from]", "content": "[content]", "timestamp": "[timestamp]", "sign": "[sign]"}` to `/api/webhooks/sms?token={user.sms_webhook_token}&platform=android_smsforwarder`.

### Unified Payload Normalizer (`backend/app/services/sms_normalizer.py`)
- Accept `application/json`, `application/x-www-form-urlencoded`, or raw `text/plain` body.
- Map `content` / `msg` / `text` / `body` -> `content`, and `from` / `sender` / `handle` -> `sender_number` (defaulting to `"unknown"` if omitted by iOS Shortcuts).
- **Crucial Rule**: Never rely solely on `sender_number` to classify SMS; always classify using content regex (`sms_patterns.json`).

---

## 7. Detailed Multi-Domain Module Specifications (with Source References)

### 7.1 Multi-Domain Statutory Deadline Seed Rules (`backend/app/core/deadline_rules_seed.json`)
`[Source: Ported from REF-ANQI rules/deadline_rules*.json, REF-LAWLINK src/lib/holidays.ts, and REF-SKILLHUB akylegal-applications]`

Seed the `DeadlineRule` table with 4 comprehensive domain packs:

#### Pack A: 民事诉讼 (`CaseCategory.CIVIL` - 《民事诉讼法》)
- `CIVIL_DEFENSE_15D`: 收到起诉状/上诉状 (`pleading_served`) -> 答辩期 (15日, 民诉法§128, `ROLL_FORWARD_WORKDAY`, `HIGH`)
- `CIVIL_JURISDICTION_15D`: 收到起诉状 (`pleading_served`) -> 管辖权异议期 (15日, 民诉法§130, `ROLL_FORWARD_WORKDAY`, `HIGH`)
- `CIVIL_EVIDENCE`: 收到举证通知书 (`evidence_notice_served`) -> 举证期限 (优先取 `authority_specified_days`，默认15日并提示核对文书, `ROLL_FORWARD_WORKDAY`, `HIGH`)
- `CIVIL_APPEAL_JUDGMENT_15D`: 收到民事判决书 (`judgment_served`) -> 民事判决上诉期 (**15日**, 民诉法§171, `ROLL_FORWARD_WORKDAY`, `CRITICAL`)
- `CIVIL_APPEAL_RULING_10D`: 收到民事裁定书 (`ruling_served`) -> 民事裁定上诉期 (**10日**, 民诉法§171, `ROLL_FORWARD_WORKDAY`, `CRITICAL`)
- `CIVIL_RETRIAL_6M`: 判决/裁定生效 (`judgment_effective`) -> 申请再审期限 (6个月, 民诉法§212, `ROLL_FORWARD_WORKDAY`, `CRITICAL`)
- `CIVIL_EXECUTION_2Y`: 履行期届满 (`performance_expired`) -> 申请强制执行期限 (2年, 民诉法§250, `ROLL_FORWARD_WORKDAY`, `CRITICAL`)
- `PRESERVATION_BANK_1Y` / `MOVABLE_2Y` / `REAL_ESTATE_3Y`: 财产保全查封/冻结 (`preservation_done`) -> 续封/续冻到期日 (1年/2年/3年, 民诉法解释§487, `CALENDAR_EXACT`, 提前30/15/7/3天预警, `CRITICAL`)

#### Pack B: 刑事诉讼与刑辩全流程 (`CaseCategory.CRIMINAL` - 《刑事诉讼法》)
- `CRIM_FIRST_MEETING_48H`: 刑事拘留/接受委托 (`detention_notified`) -> 安排看守所首次会见窗口 (2日, 刑诉法§39, `CALENDAR_EXACT`, `HIGH`)
- `CRIM_DETENTION_SUBMIT_ARREST_NORMAL`: 被刑事拘留 (`detention_notified`, `condition_match: {"crime_type": "一般犯罪"}`) -> 公安提请批捕期 (3日与7日, 刑诉法§91, **`NO_ROLL_CUSTODY` 在押绝不顺延**, `HIGH`)
- `CRIM_DETENTION_SUBMIT_ARREST_30D`: 被刑事拘留 (`detention_notified`, `condition_match: {"crime_type": "流窜/多次/结伙作案"}`) -> 公安提请批捕上限 (**30日**, 刑诉法§91, **`NO_ROLL_CUSTODY` 在押绝不顺延**, `HIGH`)
- `CRIM_PROCURATORATE_ARREST_7D`: 移送检察院审查批捕 (`case_transferred_arrest`) -> 检察院审查批捕决定期 & 提交《不予批捕法律意见书》死线 (**7日/黄金37天**, 刑诉法§91, **`NO_ROLL_CUSTODY` 在押绝不顺延**, `CRITICAL`, 提前5/3/2/1天密集提醒)
- `CRIM_INVESTIGATION_CUSTODY_2M`: 宣布逮捕 (`arrest_executed`) -> 侦查羁押期限届满/申请羁押必要性审查 (**2个月**, 刑诉法§156, **`NO_ROLL_CUSTODY`**, `HIGH`)
- `CRIM_PROSECUTION_REVIEW_1M`: 移送审查起诉 (`prosecution_review_started`) -> 审查起诉1个月期限（阅卷、提交不起诉/量刑建议）(**1个月**, 刑诉法§172, `NO_ROLL_CUSTODY`, `HIGH`)
- `CRIM_BAIL_12M`: 取保候审决定 (`bail_granted`) -> 取保候审12个月到期解除/退保提醒 (12个月, 刑诉法§79, `CALENDAR_EXACT`, `NORMAL`)
- `CRIM_APPEAL_JUDGMENT_10D`: 收到刑事判决书 (`judgment_served`) -> **刑事判决上诉期** (**10日**, 刑诉法§230, `ROLL_FORWARD_WORKDAY`, `CRITICAL`)
- `CRIM_APPEAL_RULING_5D`: 收到刑事裁定书 (`ruling_served`) -> **刑事裁定上诉期** (**5日**, 刑诉法§230, `ROLL_FORWARD_WORKDAY`, `CRITICAL`)

#### Pack C: 行政诉讼与行政复议 (`CaseCategory.ADMINISTRATIVE` - 《行政诉讼法》《行政复议法》)
- `ADMIN_RECONSIDERATION_60D`: 知道具体行政行为 (`admin_act_known`) -> 申请行政复议期限 (**60日**, 行政复议法§20, `ROLL_FORWARD_WORKDAY`, `CRITICAL`)
- `ADMIN_SUIT_AFTER_RECON_15D`: 收到复议决定书 (`reconsideration_decision_served`) -> 复议后提起行政诉讼期限 (**15日**, 行诉法§45, `ROLL_FORWARD_WORKDAY`, `CRITICAL`)
- `ADMIN_DIRECT_SUIT_6M`: 知道行政行为作出 (`admin_act_known`) -> 直接提起行政诉讼期限 (**6个月**, 行诉法§46, `ROLL_FORWARD_WORKDAY`, `CRITICAL`)
- `ADMIN_DEFENDANT_EVIDENCE_15D`: 收到行政起诉状副本 (`pleading_served`) -> **被告行政机关提交证据及依据死线 / 原告核查被告逾期举证节点** (**15日**, 行诉法§67, `ROLL_FORWARD_WORKDAY`, `CRITICAL`)
- `ADMIN_APPEAL_JUDGMENT_15D`: 收到行政判决书 (`judgment_served`) -> 行政判决上诉期 (**15日**, 行诉法§85, `ROLL_FORWARD_WORKDAY`, `CRITICAL`)
- `ADMIN_APPEAL_RULING_10D`: 收到行政裁定书 (`ruling_served`) -> 行政裁定上诉期 (**10日**, 行诉法§85, `ROLL_FORWARD_WORKDAY`, `CRITICAL`)

#### Pack D: 劳动仲裁与商事仲裁 (`CaseCategory.LABOR_ARBITRATION` / `COMMERCIAL_ARBITRATION`)
- `LABOR_ARB_DEFENSE_10D`: 收到劳动仲裁申请书副本 (`pleading_served`) -> 提交劳动仲裁答辩书 (**10日**, 劳动争议调解仲裁法§30, `ROLL_FORWARD_WORKDAY`, `HIGH`)
- `LABOR_SUIT_AFTER_AWARD_15D`: 收到劳动仲裁裁决书 (`arbitration_award_served`) -> **不服劳动仲裁裁决向法院起诉期** (**15日**, 劳动争议调解仲裁法§50, `ROLL_FORWARD_WORKDAY`, `CRITICAL`)
- `ARB_SET_ASIDE_6M`: 收到商事仲裁裁决书 (`arbitration_award_served`) -> 申请撤销仲裁裁决期限 (**6个月**, 仲裁法§59, `ROLL_FORWARD_WORKDAY`, `CRITICAL`)

---

### 7.2 Multi-Agency SMS Parser (`sms_parser.py`) & Dynamic Folder Bridge (`file_bridge.py`)
`[Source: Ported from REF-SKILLHUB sms-patterns.json & REF-ANQI stage_templates*.json]`

1. **Multi-Agency SMS Classification**:
   - Recognize **Court (`12368`)**, **Procuratorate (`12309` 检察服务热线)**, **Public Security / Detention Center (公安/看守所)**, **Arbitration Commission (仲裁委)**, and **E-Invoice (`诺诺/百望/数电票`)** SMS.
   - Auto-detect `CaseCategory`:
     - `民初|民终|民再|民申|知民` -> `CIVIL`
     - `刑初|刑终|刑申|检刑诉|检刑捕|拘通字|逮捕|看守所|12309|审查逮捕|审查起诉|认罪认罚` -> `CRIMINAL`
     - `行初|行终|行再|行政复议` -> `ADMINISTRATIVE`
     - `劳人仲|劳动人事争议|仲裁委` -> `LABOR_ARBITRATION`
     - `执|执异|执恢` -> `ENFORCEMENT`
2. **Stage Templates by Category (`stage_templates.json`)**:
   - `civil`: `准备立案 -> 已立案 -> 举证答辩 -> 待开庭/审理 -> 裁判与上诉期 -> 结案归档`
   - `criminal`: `侦查阶段(刑拘) -> 审查批捕(黄金37天) -> 继续侦查(逮捕后) -> 审查起诉(阅卷/量刑) -> 刑事一审 -> 刑事二审 -> 结案归档`
   - `administrative`: `行政复议/起诉准备 -> 已立案 -> 被告举证与答辩(15日盯防) -> 开庭审理 -> 裁判与上诉期 -> 结案归档`
   - `labor_arbitration`: `仲裁申请/立案 -> 举证与答辩 -> 仲裁开庭 -> 裁决与15日起诉期 -> 结案归档`
3. **Case Subfolder Naming (`file_bridge.py`)**:
   - Create 5 standardized subdirectories under `{CASES_ROOT}/{case.folder_rel_path}/`:
     - `01_司法与程序文书/` (Stores Court 12368 docs, Procuratorate 12309 notices, Arbitration awards)
     - `02_诉讼文书与法律意见/` (Complaints, Defense briefs, Criminal Defense / Non-arrest Legal Opinions)
     - `03_证据与阅卷材料/` (Evidence, Criminal dossier transcripts)
     - `04_会见与庭审记录/` (Detention center meeting notes, Hearing transcripts)
     - `05_财务与发票/` (Auto-archived e-invoices)

---

### 7.3 Court Document Auto-Fetcher (`court_fetcher.py`) & E-Invoice Archiver (`invoice_fetcher.py`)
1. **3-Tier Court Document Fetcher** `[Source: REF-SKILLHUB court_full_pipeline.py & REF-LAWLINK SMS B2]`:
   - **Tier 1 (`zxfw.court.gov.cn` API)**: Parse `qdbh`, `sdbh`, `sdsin` parameters and download **all** PDF/OFD files in the delivery batch.
   - **Tier 2 (Provincial Playwright Scrapers)**: Support `sd.gdems.com`, `jysd.10102368.com`, `dzsd.hbfy.gov.cn`, `sfpt.cdfy12368.gov.cn`, etc., auto-filling SMS extraction codes or user/client last-4 digits.
   - **Tier 3 (Human-in-the-Loop Handoff)**: On facial recognition, WeChat Mini-Program, or dynamic SMS OTP, set `status = MANUAL_FETCH_REQUIRED` and push a WeCom card to the lawyer.
2. **E-Invoice Downloader & `pdfplumber` Archiver** `[Source: REF-INVOICE-DL & REF-INVOICE-EX]`:
   - Use Playwright to capture PDF streams from NuoNuo, Baiwang, and Chinatax links.
   - Extract 21 invoice fields via `pdfplumber` (invoice number, issue date, buyer, seller, total amount, item name, remark).
   - Match `remark` or `buyer_name` to active cases -> archive to `05_财务与发票/{YYYYMMDD}_{item_name}_{total_amount}元_{invoice_no}.pdf` (or `_待认领发票/` if unmatched).

---

### 7.4 Personal & Team Reminder Engine + WeCom Integration (`reminder_scheduler.py` & `wecom_notifier.py`)
`[Source: REF-LAWLINK ReminderDelivery & REF-ANQI WeCom integration]`
1. **4 Target Modes (`NotifyTargetType`)**:
   - `SELF`: Creator/owner only.
   - `CASE_TEAM`: All `CaseMember` users (`LEAD` + `CO_COUNSEL`) on the case.
   - `BY_ROLE` + **Partner Escalation**: Send to `LEAD` + `CO_COUNSEL` at standard offsets; if `risk_level == CRITICAL`, `days_remaining <= 1`, and `is_completed == False`, automatically append `SUPERVISOR` (partner) to recipients with `【⚠️督办合伙人升级预警】`.
   - `CUSTOM_USERS`: Explicit list of selected team members.
2. **Personal Custom Alerts (`personal_custom_alerts`)**:
   - Any lawyer can add personal custom reminder timestamps to any hearing or deadline without altering the team's shared reminder schedule.

---

## 8. Phased Execution Plan for the Coding Agent

Execute the build strictly in the following 6 sequential phases.

### Phase 0: Clone & Inspect Reference Repositories (Mandatory First Step)
1. Create `.ref_repos/` and add it to `.gitignore`.
2. Clone the reference repositories:
   ```bash
   mkdir -p .ref_repos && cd .ref_repos
   git clone --depth 1 https://github.com/CSlawyer1985/legal-skillhub.git
   git clone --depth 1 https://github.com/zj-ai-lab/anqi.git
   git clone --depth 1 https://github.com/lawflow-boop/LawLink.git
   git clone --depth 1 https://github.com/EthanYoQ/Invoice-Downloader.git
   git clone --depth 1 https://github.com/NevermoreN/invoice2excel.git
   ```
3. Inspect the target files listed in Section 2 and build `sms_patterns.json`, `deadline_rules_seed.json`, and `stage_templates.json` in `backend/app/core/`.

### Phase 1: Backend Foundation, Multi-User RBAC & Case File Bridge
- Scaffold `backend/` with FastAPI, SQLModel, PostgreSQL (`docker-compose.yml`), JWT Auth, and seed 3 demo users (`ADMIN` Partner, `LAWYER` Lead, `ASSISTANT`).
- Seed the `DeadlineRule` table with all 4 domain packs (Civil, Criminal, Administrative, Arbitration) and build `/api/rules` CRUD so admins can edit/add rules.
- Implement `file_bridge.py` (`[REF-ANQI & REF-SKILLHUB]`) and `/api/cases` CRUD supporting Civil, Criminal, Administrative, and Arbitration fields.

### Phase 2: Multi-Domain Statutory Deadline Engine & Calendar/ICS API
- Implement `deadline_engine.py` (`[REF-ANQI, REF-LAWLINK & REF-SKILLHUB]`) with `chinese-calendar`.
- Write and run `pytest` unit tests verifying:
  1. Civil/Admin 15-day judgment appeal vs. **Criminal 10-day judgment / 5-day ruling appeal** isolation.
  2. `ROLL_FORWARD_WORKDAY` over weekends & National Day/Spring Festival, AND non-roll-forward on State Council make-up workdays (`调休补班周末不顺延`).
  3. **`NO_ROLL_CUSTODY` (`刑诉法§105条3款`)**: Criminal detention & 7-day arrest approval (`审查批捕`) expiring on a holiday/weekend MUST NOT roll forward.
  4. `is_manual_override == True` prevents overwrite on recalculation.
- Build `/api/calendar/events` (`scope=my|team` + hearing conflict detection) and `/api/calendar/ics/{ics_feed_token}.ics` (`[REF-FACHUAN]`).

### Phase 3: iOS/Android SMS Webhook, Court Fetcher & Invoice Archiver
- Implement `sms_normalizer.py` and `/api/webhooks/sms` supporting **iOS Shortcuts (`快捷指令`)**, **macOS (`MacSMSForwarder`)**, and **Android (`SmsForwarder`)** (`[REF-SMS-IOS & REF-SMS-ANDROID]`).
- Implement `sms_parser.py` recognizing Court (`12368`), Procuratorate (`12309`), Detention/Police, Arbitration, and Invoice SMS.
- Port `court_fetcher.py` (`[REF-SKILLHUB court_full_pipeline.py]`) and `invoice_fetcher.py` (`[REF-INVOICE-DL & REF-INVOICE-EX]`).
- Implement `/api/inbox` confirmation workflow (`[REF-ANQI & REF-LAWLINK]`).

### Phase 4: Personal/Team Reminder Engine & WeCom Notifier
- Implement `reminder_scheduler.py` and `wecom_notifier.py` (`[REF-LAWLINK & REF-ANQI]`) supporting `SELF`, `CASE_TEAM`, `BY_ROLE` (with 1-day `SUPERVISOR` escalation), `CUSTOM_USERS`, and `personal_custom_alerts`.

### Phase 5: React + FullCalendar Multi-View Workbench & Multi-Domain UI
- Build `frontend/` with Vite + React + TypeScript + Tailwind + shadcn/ui:
  - `CalendarWorkbench.tsx`: FullCalendar (`Month`, `Week`, `Day`, `Agenda List`), `我的日历 / 团队全局日历` switch, lawyer color legend, hearing conflict warning banner, and `.ics` subscription modal.
  - `CaseList.tsx` & `CaseDetail.tsx`: Category tabs (`全部 / 民事 / 刑事 / 行政 / 仲裁`), dynamic Criminal/Admin metadata panels (`在押状态/看守所/办案机关`), stage bar, events & deadlines table (with manual override & custom reminder modal), and case folder document viewer.
  - `RuleManager.tsx`: Visual editor for `DeadlineRule` table across all legal domains.
  - `PhoneSetupGuide.tsx`: Step-by-step iOS Shortcuts (`快捷指令`) automation setup guide & Android `SmsForwarder` config generator.
  - `InboxTriage.tsx` + **"Simulate Multi-Domain SMS" Dev Drawer** pre-loaded with sample Civil `12368`, Criminal `12309`, Administrative, Arbitration, and E-Invoice SMS messages for one-click end-to-end testing.
```