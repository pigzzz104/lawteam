import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Download,
  Eye,
  FileText,
  Inbox,
  KeyRound,
  RefreshCw,
  RotateCcw,
  Send,
  Sparkles,
  Upload,
} from "lucide-react";
import { casesApi, inboxApi, rulesApi } from "@/api/client";
import { ModalDialog } from "@/components/ModalDialog";
import { TriageStatusBadge } from "@/components/StatusBadges";
import { useAuth } from "@/context/AuthContext";
import {
  CASE_CATEGORY_META,
  EVENT_TYPE_PRESETS,
  SERVICE_METHOD_OPTIONS,
  STANDARD_CASE_SUBFOLDERS,
  formatShanghaiDateTime,
  getEventTypeLabel,
  todayShanghaiIso,
} from "@/lib/domain";
import type { CaseCategory, CaseVisibility, InboxTriageRead } from "@/types/api";

const SMS_PRESETS = [
  {
    label: "法院送达（上海一中院 · 尾号8966）",
    sender: "1069012368",
    content:
      "【上海市第一中级人民法院】张晨你好，请查收（2026）沪01民终6604号案件中你的送达文书。点击链接查阅：https://zxfw.court.gov.cn/zxfw/#/pagesAjkj/app/wssd/index?qdbh=d912b9c5315f4aafa1662b2e5cbf8fe3&sdbh=2bc0bb21b92f46198b836e53f6030bf6&sdsin=77b305fb135ee6acca10ab3e138ae23c",
  },
  {
    label: "广州越秀法院（上诉缴费单）",
    sender: "1069012368",
    content:
      "【广州市越秀区人民法院】张晨你好，请查收（2025）粤0104民初27096号案件中你的送达文书（上诉缴费单）。点击链接查阅：https://zxfw.court.gov.cn/zxfw/#/pagesAjkj/app/wssd/index?qdbh=d872f6c92c934cf1b9008b4600ec761f&sdbh=dbbd740def8d4def8820d463989b1c2d&sdsin=3331de5887a4f94040efd365fef43d9f",
  },
  {
    label: "12309 检察院移送审查起诉",
    sender: "12309",
    content:
      "【12309中国检察网】张律师您好，您辩护的(2026)沪0115刑检108号案件已移送本院审查起诉，可联系案管大厅预约阅卷。",
  },
  {
    label: "公安刑事拘留家属通知",
    sender: "110",
    content:
      "【上海市公安局浦东分局】通知：嫌疑人李某涉嫌职务侵占罪已于2026-11-10被刑事拘留，现羁押于浦东新区看守所。",
  },
  {
    label: "电子发票开具提醒",
    sender: "1069800",
    content:
      "【诺诺发票】您已开具电子发票（价税合计5000.00元），备注：(2026)沪01民终6604号律师费，点击下载：https://nnfp.jss.com.cn/invoice/demo.pdf",
  },
];

function toShanghaiDatetimeLocal(iso: string, offsetMinutes = 0): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso.slice(0, 16);
  }
  const shifted = new Date(parsed.getTime() + offsetMinutes * 60_000);
  const parts = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(shifted);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "00";
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}

type InboxViewTab = "needs_action" | "auto_archived" | "confirmed" | "ignored" | "all";

export function InboxTriage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const detailPanelRef = useRef<HTMLDivElement>(null);
  const [searchParams] = useSearchParams();
  const queryIdParam = searchParams.get("id");

  const [viewTab, setViewTab] = useState<InboxViewTab>(queryIdParam ? "all" : "needs_action");
  const [selectedId, setSelectedId] = useState<number | null>(
    queryIdParam && Number.isFinite(Number(queryIdParam)) ? Number(queryIdParam) : null
  );
  const [feedback, setFeedback] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [simError, setSimError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // Retry fetch state
  const [retryPhoneLast4, setRetryPhoneLast4] = useState(user?.phone_last4 || "");
  const [retryIdLast4, setRetryIdLast4] = useState(user?.id_card_last4 || "");
  const [retryCode, setRetryCode] = useState("");

  // Confirm form state
  const [caseMode, setCaseMode] = useState<"existing" | "new">("existing");
  const [targetCaseId, setTargetCaseId] = useState<string>("");
  const [newCaseTitle, setNewCaseTitle] = useState("");
  const [newCaseNumber, setNewCaseNumber] = useState("");
  const [newCaseCategory, setNewCaseCategory] = useState<CaseCategory>("civil");
  const [newClientName, setNewClientName] = useState("");
  const [newVisibility, setNewVisibility] = useState<CaseVisibility>("team");
  const [newAgency, setNewAgency] = useState("");
  const [fileSubfolder, setFileSubfolder] = useState<string>(STANDARD_CASE_SUBFOLDERS[0]);
  const [newStage, setNewStage] = useState("");
  const [eventType, setEventType] = useState("");
  const [eventDate, setEventDate] = useState(todayShanghaiIso());
  const [serviceMethod, setServiceMethod] = useState("electronic");
  const [authorityDays, setAuthorityDays] = useState("");
  const [createHearing, setCreateHearing] = useState(false);
  const [hearingTitle, setHearingTitle] = useState("");
  const [hearingStart, setHearingStart] = useState(`${todayShanghaiIso()}T09:30`);
  const [hearingEnd, setHearingEnd] = useState(`${todayShanghaiIso()}T11:00`);
  const [hearingLoc, setHearingLoc] = useState("");

  // Modals: simulate & manual upload
  const [simOpen, setSimOpen] = useState(false);
  const [simContent, setSimContent] = useState(SMS_PRESETS[0].content);
  const [simSender, setSimSender] = useState(SMS_PRESETS[0].sender);
  const [simPhoneLast4, setSimPhoneLast4] = useState("8966");
  const [simAutoFetch, setSimAutoFetch] = useState(true);

  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadType, setUploadType] = useState<"judicial_stage_and_doc" | "invoice_archive">(
    "judicial_stage_and_doc"
  );
  const [uploadCaseId, setUploadCaseId] = useState<string>("");
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploadNotes, setUploadNotes] = useState("");

  const inboxQuery = useQuery({
    queryKey: ["inbox", "all-items"],
    queryFn: () => inboxApi.list(),
  });

  const casesQuery = useQuery({
    queryKey: ["cases", "all-for-inbox"],
    queryFn: () => casesApi.list({ is_archived: false }),
  });

  const templatesQuery = useQuery({
    queryKey: ["stage-templates"],
    queryFn: rulesApi.stageTemplates,
  });

  useEffect(() => {
    if (!user) return;
    setRetryPhoneLast4((prev) => prev || user.phone_last4 || "");
    setRetryIdLast4((prev) => prev || user.id_card_last4 || "");
  }, [user]);

  const allItems = inboxQuery.data ?? [];
  const counts = useMemo(() => {
    let needsAction = 0;
    let autoArchived = 0;
    let confirmed = 0;
    let ignored = 0;
    for (const it of allItems) {
      if (it.status === "pending" || it.status === "manual") needsAction += 1;
      if (it.status === "confirmed") {
        confirmed += 1;
        if (it.auto_archived) autoArchived += 1;
      }
      if (it.status === "ignored") ignored += 1;
    }
    return { needsAction, autoArchived, confirmed, ignored, all: allItems.length };
  }, [allItems]);

  const items = useMemo(() => {
    return allItems.filter((it) => {
      if (viewTab === "needs_action") return it.status === "pending" || it.status === "manual";
      if (viewTab === "auto_archived") return it.status === "confirmed" && it.auto_archived;
      if (viewTab === "confirmed") return it.status === "confirmed";
      if (viewTab === "ignored") return it.status === "ignored";
      return true;
    });
  }, [allItems, viewTab]);

  const selectedItem: InboxTriageRead | undefined =
    items.find((i) => i.id === selectedId) ?? items[0];

  const syncPayloadKey = selectedItem
    ? `${selectedItem.id}:${selectedItem.status}:${selectedItem.downloaded_files.length}:${String(selectedItem.extracted_payload?.event_type_guess || "")}:${String(selectedItem.extracted_payload?.hearing_time || "")}`
    : "";

  useEffect(() => {
    if (!selectedItem) return;
    const p = selectedItem.extracted_payload || {};
    if (selectedItem.matched_case_id) {
      setCaseMode("existing");
      setTargetCaseId(String(selectedItem.matched_case_id));
    } else {
      setCaseMode(casesQuery.data?.length ? "existing" : "new");
      setTargetCaseId("");
    }
    setNewCaseNumber((p.case_number as string) || "");
    setNewCaseTitle(
      (p.case_number as string)
        ? `${p.case_number} 新建协同案件`
        : ((p.doc_title_hint as string) || "短信导入新案件")
    );
    setNewCaseCategory((p.case_category_guess as CaseCategory) || "civil");
    setNewClientName(
      (p.client_hint as string) || (p.client_name_guess as string) || "待补充委托人"
    );
    setNewAgency((p.agency as string) || (p.court_or_agency as string) || "");
    setFileSubfolder(
      selectedItem.item_type === "invoice_archive"
        ? "05_财务与发票"
        : "01_司法与程序文书"
    );
    setNewStage((p.suggested_stage as string) || (p.stage_suggestion as string) || "");
    setEventType((p.event_type_guess as string) || "");
    setEventDate(
      typeof p.event_date === "string" && /^\d{4}-\d{2}-\d{2}/.test(p.event_date)
        ? p.event_date.slice(0, 10)
        : todayShanghaiIso()
    );
    setServiceMethod("electronic");
    setAuthorityDays(
      p.authority_specified_days ? String(p.authority_specified_days) : ""
    );
    setRetryCode("");
    setCreateHearing(Boolean(p.hearing_time));
    if (typeof p.hearing_time === "string") {
      setHearingStart(toShanghaiDatetimeLocal(p.hearing_time, 0));
      setHearingEnd(toShanghaiDatetimeLocal(p.hearing_time, 90));
      setHearingTitle(`${(p.case_number as string) || ""} 开庭审理`.trim());
      setHearingLoc((p.hearing_location as string) || "");
    } else {
      setHearingStart(`${todayShanghaiIso()}T09:30`);
      setHearingEnd(`${todayShanghaiIso()}T11:00`);
      setHearingTitle("");
      setHearingLoc("");
    }
  }, [syncPayloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const refreshInbox = () => {
    queryClient.invalidateQueries({ queryKey: ["inbox"] });
    queryClient.invalidateQueries({ queryKey: ["cases"] });
    queryClient.invalidateQueries({ queryKey: ["calendar"] });
  };

  const retryMut = useMutation({
    mutationFn: () => {
      if (!selectedItem) throw new Error("未选中条目");
      return inboxApi.retryFetch(selectedItem.id, {
        phone_last4: retryPhoneLast4.trim() || null,
        id_card_last4: retryIdLast4.trim() || null,
        extraction_code: retryCode.trim() || null,
        confirm_sign: true,
      });
    },
    onSuccess: (updated) => {
      refreshInbox();
      setErrorMsg(null);
      setFeedback(
        updated.status === "pending"
          ? `抓取重试成功，已获取 ${updated.downloaded_files.length} 个文件`
          : `重试完成：${updated.error_or_manual_reason || updated.status}`
      );
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const confirmMut = useMutation({
    mutationFn: () => {
      if (!selectedItem) throw new Error("未选中条目");
      if (createHearing) {
        const startMs = new Date(hearingStart).getTime();
        const endMs = new Date(hearingEnd).getTime();
        if (Number.isNaN(startMs) || Number.isNaN(endMs) || endMs <= startMs) {
          throw new Error("开庭结束时间必须晚于开始时间");
        }
      }
      return inboxApi.confirm(selectedItem.id, {
        case_id: caseMode === "existing" && targetCaseId ? Number(targetCaseId) : null,
        create_case:
          caseMode === "new"
            ? {
                case_title: newCaseTitle.trim(),
                case_number: newCaseNumber.trim() || null,
                case_category: newCaseCategory,
                visibility: newVisibility,
                client_name: newClientName.trim() || "待补充委托人",
                handling_agency: newAgency.trim() || null,
              }
            : null,
        event_type: eventType.trim() || null,
        event_date: eventType.trim() ? eventDate : null,
        service_method: serviceMethod,
        authority_specified_days: authorityDays ? Number(authorityDays) : null,
        new_stage: newStage.trim() || null,
        file_subfolder: fileSubfolder,
        create_hearing: createHearing
          ? {
              category: "hearing",
              title: hearingTitle.trim() || "开庭审理",
              start_time: new Date(hearingStart).toISOString(),
              end_time: new Date(hearingEnd).toISOString(),
              location: hearingLoc.trim() || null,
              risk_level: "high",
            }
          : null,
      });
    },
    onSuccess: (res) => {
      refreshInbox();
      setErrorMsg(null);
      setFeedback(
        `入案确认成功！关联案件 #${res.case_id}，归档 ${res.archived_documents.length} 份文件，派生 ${res.generated_deadline_count} 条期限`
      );
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const ignoreMut = useMutation({
    mutationFn: (id: number) => inboxApi.ignore(id),
    onSuccess: () => {
      refreshInbox();
      setErrorMsg(null);
      setFeedback("已忽略该条短信记录");
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const reopenMut = useMutation({
    mutationFn: (id: number) => inboxApi.reopen(id),
    onSuccess: (reopened) => {
      refreshInbox();
      setErrorMsg(null);
      setViewTab("needs_action");
      setSelectedId(reopened.id);
      setFeedback(`已撤回归档 #${reopened.id}，请重新选择归属案件并确认`);
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const batchIgnoreMut = useMutation({
    mutationFn: (ids: number[]) => inboxApi.batchIgnore(ids),
    onSuccess: (res) => {
      refreshInbox();
      setErrorMsg(null);
      setFeedback(`已批量忽略 ${res.ignored_count} 条待处理记录`);
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const simulateMut = useMutation({
    mutationFn: () =>
      inboxApi.simulate({
        content: simContent,
        sender: simSender,
        auto_fetch: simAutoFetch,
        phone_last4: simPhoneLast4.trim() || null,
        confirm_sign: true,
      }),
    onSuccess: (created) => {
      refreshInbox();
      setSimOpen(false);
      setSimError(null);
      setSelectedId(created.id);
      if (created.status === "confirmed" && created.auto_archived) {
        setViewTab("auto_archived");
        setFeedback(`已自动归档 #${created.id}：${created.triage_reason || "命中规则并自动入案"}`);
      } else if (created.status === "ignored") {
        setViewTab("ignored");
        setFeedback(`非涉案短信 #${created.id} 已自动忽略降噪`);
      } else {
        setViewTab("needs_action");
        setFeedback(`已接入并转人工确认 #${created.id}：${created.triage_reason || "请人工核对"}`);
      }
    },
    onError: (err: Error) => setSimError(err.message),
  });

  const manualUploadMut = useMutation({
    mutationFn: () =>
      inboxApi.manualUpload({
        files: uploadFiles,
        item_type: uploadType,
        case_id: uploadCaseId ? Number(uploadCaseId) : null,
        notes: uploadNotes.trim() || undefined,
      }),
    onSuccess: (created) => {
      refreshInbox();
      setUploadOpen(false);
      setUploadError(null);
      setUploadFiles([]);
      setUploadCaseId("");
      setUploadNotes("");
      setSelectedId(created.id);
      if (created.status === "confirmed" && created.auto_archived) {
        setViewTab("auto_archived");
        setFeedback(`上传文件已自动归档 #${created.id}：${created.triage_reason || "已自动归档至案件目录"}`);
      } else {
        setViewTab("needs_action");
        setFeedback(`已上传并解析文件，待人工确认 #${created.id}`);
      }
    },
    onError: (err: Error) => setUploadError(err.message),
  });

  const stageSuggestions =
    templatesQuery.data?.categories[newCaseCategory]?.stages ?? [];

  const ambiguousCandidateIds = Array.isArray(selectedItem?.extracted_payload?.ambiguous_case_ids)
    ? (selectedItem?.extracted_payload?.ambiguous_case_ids as number[])
    : [];
  const ambiguousCases = (casesQuery.data ?? []).filter((c) =>
    ambiguousCandidateIds.includes(c.id)
  );
  const needsActionIds = items
    .filter((it) => it.status === "pending" || it.status === "manual")
    .map((it) => it.id);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-[var(--md-sys-color-on-surface)]">收件箱</h1>
          <p className="mt-1 text-xs text-slate-600">
            已配置规则且唯一匹配案号的短信文书与电子发票默认自动归档；仅无覆盖规则、匹配歧义或需人机核验时提醒人工确认。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setUploadError(null);
              setUploadOpen(true);
            }}
            className="lt-btn-secondary"
          >
            <Upload className="h-3.5 w-3.5" aria-hidden="true" />
            <span>手动上传 PDF / 发票</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setSimError(null);
              setSimOpen(true);
            }}
            className="lt-btn-primary"
          >
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            <span>模拟短信接入</span>
          </button>
        </div>
      </div>

      {/* Status Filter Tabs */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-2">
          {(
            [
              { key: "needs_action", label: `需人工处理 (${counts.needsAction})` },
              { key: "auto_archived", label: `已自动归档 (${counts.autoArchived})` },
              { key: "confirmed", label: `全部已入案 (${counts.confirmed})` },
              { key: "ignored", label: `已忽略 (${counts.ignored})` },
              { key: "all", label: `全部 (${counts.all})` },
            ] as const
          ).map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => setViewTab(tab.key)}
              className={`lt-chip ${viewTab === tab.key ? "lt-chip-active" : ""}`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {viewTab === "needs_action" && needsActionIds.length > 1 && (
          <button
            type="button"
            disabled={batchIgnoreMut.isPending}
            onClick={() => batchIgnoreMut.mutate(needsActionIds)}
            className="lt-btn-secondary py-1.5 px-3 text-xs"
          >
            <Ban className="h-3.5 w-3.5" aria-hidden="true" />
            <span>一键忽略当前 {needsActionIds.length} 条待处理</span>
          </button>
        )}
      </div>

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

      {inboxQuery.isLoading ? (
        <div className="lt-card p-12 text-center text-sm text-slate-600">
          正在加载收件箱...
        </div>
      ) : inboxQuery.isError ? (
        <div role="alert" className="lt-alert-error flex items-center justify-between gap-3">
          <span>收件箱加载失败，请重试。</span>
          <button
            type="button"
            onClick={() => inboxQuery.refetch()}
            className="lt-btn-secondary py-1"
          >
            重新加载
          </button>
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white p-12 text-center space-y-2">
          <Inbox className="h-8 w-8 text-slate-500 mx-auto" aria-hidden="true" />
          <p className="text-sm font-semibold text-slate-800">
            {viewTab === "needs_action" ? "当前没有需要人工处理的异常或未匹配条目" : "当前分类下暂无记录"}
          </p>
          <p className="text-xs text-slate-600">
            有规则覆盖的短信文书与电子发票已自动归档；可切换上方「已自动归档」查看结果，或点击右上角测试接入。
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
          {/* Left Queue */}
          <div className="lg:col-span-5 space-y-2.5">
            {items.map((item) => {
              const p = item.extracted_payload || {};
              const active = selectedItem?.id === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    setSelectedId(item.id);
                    if (window.innerWidth < 1024) {
                      window.setTimeout(() => {
                        detailPanelRef.current?.scrollIntoView({
                          behavior: "smooth",
                          block: "nearest",
                        });
                      }, 60);
                    }
                  }}
                  className={`w-full text-left rounded-[20px] border p-4 transition-all space-y-2 ${
                    active
                      ? "border-[var(--md-sys-color-primary)] bg-[var(--md-sys-color-primary-container)]/35 shadow-sm ring-1 ring-[var(--md-sys-color-primary)]"
                      : "border-[var(--md-sys-color-outline-variant)]/80 bg-[var(--md-sys-color-surface-container-lowest)] hover:bg-[var(--md-sys-color-surface-container-low)]"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5">
                      <TriageStatusBadge status={item.status} />
                      {item.auto_archived && (
                        <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-800">
                          自动归档
                        </span>
                      )}
                    </div>
                    <span className="text-[11px] text-slate-600 tabular-nums">
                      {formatShanghaiDateTime(item.created_at)}
                    </span>
                  </div>
                  <p className="text-sm font-bold text-slate-900">
                    {(p.case_number as string) ||
                      (p.doc_title_hint as string) ||
                      (item.item_type === "invoice_archive" ? "电子发票归档" : `短信分拣 #${item.id}`)}
                  </p>
                  <p className="text-xs text-slate-600 line-clamp-2">
                    {item.sms_content || (p.notes as string) || "手动上传文档"}
                  </p>
                  {item.triage_reason && (
                    <p className="text-[11px] text-amber-800 line-clamp-1">
                      {item.triage_reason}
                    </p>
                  )}
                  <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-600">
                    <span>归属：{item.owner_name}</span>
                    {item.matched_case_title && (
                      <span className="rounded bg-blue-100 px-1.5 py-0.5 text-blue-800 font-medium">
                        匹配：{item.matched_case_title}
                      </span>
                    )}
                    {item.downloaded_files.length > 0 && (
                      <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-800 font-medium">
                        {item.status === "confirmed" ? "已归档" : "已暂存"} {item.downloaded_files.length} 份文件
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>

          {/* Right Detail & Confirm */}
          {selectedItem && (
            <div ref={detailPanelRef} className="lg:col-span-7 lt-card p-6 space-y-5">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 pb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <TriageStatusBadge status={selectedItem.status} />
                    {selectedItem.auto_archived && (
                      <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-800">
                        规则自动归档
                      </span>
                    )}
                    <span className="text-xs text-slate-600">
                      #{selectedItem.id} · 来源 {selectedItem.sms_platform || "manual"}
                    </span>
                  </div>
                  <h2 className="mt-1.5 text-lg font-bold text-slate-900">
                    {(selectedItem.extracted_payload?.case_number as string) ||
                      (selectedItem.extracted_payload?.doc_title_hint as string) ||
                      (selectedItem.item_type === "invoice_archive" ? "电子发票归档" : "待人工核对条目")}
                  </h2>
                </div>
                {selectedItem.status !== "confirmed" && selectedItem.status !== "ignored" && (
                  <button
                    type="button"
                    disabled={ignoreMut.isPending}
                    onClick={() => ignoreMut.mutate(selectedItem.id)}
                    className="lt-btn-secondary py-1.5 px-3"
                  >
                    <Ban className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>忽略</span>
                  </button>
                )}
              </div>

              {selectedItem.triage_reason && selectedItem.status === "pending" && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-900 flex items-start gap-2">
                  <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0 mt-0.5" aria-hidden="true" />
                  <div>
                    <span className="font-semibold">人工确认原因：</span>
                    <span>{selectedItem.triage_reason}</span>
                  </div>
                </div>
              )}

              {selectedItem.sms_content && (
                <div className="rounded-lg bg-slate-50 border border-slate-200 p-3.5 space-y-1.5">
                  <p className="text-xs font-semibold text-slate-600">
                    原始短信内容（发件号码：{selectedItem.sms_sender || "-"}）
                  </p>
                  <p className="text-xs text-slate-800 whitespace-pre-wrap break-all leading-relaxed">
                    {selectedItem.sms_content}
                  </p>
                </div>
              )}

              {selectedItem.error_or_manual_reason && (
                <div className="rounded-lg border border-rose-200 bg-rose-50 p-3.5 space-y-3">
                  <div className="flex items-start gap-2 text-xs text-rose-900">
                    <AlertTriangle className="h-4 w-4 text-rose-600 shrink-0 mt-0.5" aria-hidden="true" />
                    <div>
                      <p className="font-semibold">需要人工验证或手动取回：</p>
                      <p className="mt-0.5">{selectedItem.error_or_manual_reason}</p>
                    </div>
                  </div>
                  {selectedItem.status !== "confirmed" && selectedItem.status !== "ignored" && (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        retryMut.mutate();
                      }}
                      className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 pt-1"
                    >
                      <input
                        type="text"
                        aria-label="手机号后4位"
                        maxLength={4}
                        value={retryPhoneLast4}
                        onChange={(e) => setRetryPhoneLast4(e.target.value)}
                        placeholder="手机尾号4位(如8966)"
                        className="rounded border border-rose-300 bg-white px-2.5 py-1.5 text-xs"
                      />
                      <input
                        type="text"
                        aria-label="证件后4位"
                        maxLength={4}
                        value={retryIdLast4}
                        onChange={(e) => setRetryIdLast4(e.target.value)}
                        placeholder="证件尾号4位(可选)"
                        className="rounded border border-rose-300 bg-white px-2.5 py-1.5 text-xs"
                      />
                      <input
                        type="text"
                        aria-label="短信提取码"
                        value={retryCode}
                        onChange={(e) => setRetryCode(e.target.value)}
                        placeholder="短信提取码(可选)"
                        className="rounded border border-rose-300 bg-white px-2.5 py-1.5 text-xs"
                      />
                      <button
                        type="submit"
                        disabled={retryMut.isPending}
                        className="inline-flex items-center justify-center gap-1 rounded bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-700"
                      >
                        <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
                        <span>{retryMut.isPending ? "验证中..." : "带尾号重试取回"}</span>
                      </button>
                    </form>
                  )}
                </div>
              )}

              {selectedItem.downloaded_files.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs font-semibold text-slate-700">
                    {selectedItem.status === "confirmed" ? "已归档文件" : "已暂存文件（支持预览核对）"}
                  </p>
                  <ul className="space-y-1.5">
                    {selectedItem.downloaded_files.map((fp) => {
                      const fname = fp.split("/").pop() || "document.pdf";
                      return (
                        <li
                          key={fp}
                          className="flex items-center justify-between gap-2 rounded border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-700"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <FileText className="h-3.5 w-3.5 text-blue-700 shrink-0" aria-hidden="true" />
                            <span className="truncate font-mono">{fp}</span>
                          </div>
                          <div className="flex items-center gap-1.5 shrink-0">
                            <button
                              type="button"
                              onClick={() =>
                                void inboxApi.downloadFile(selectedItem.id, fp, fname, true).catch((err: Error) => setErrorMsg(err.message))
                              }
                              className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-700 hover:bg-blue-50"
                            >
                              <Eye className="h-3 w-3" aria-hidden="true" />
                              <span>预览</span>
                            </button>
                            <button
                              type="button"
                              onClick={() =>
                                void inboxApi.downloadFile(selectedItem.id, fp, fname, false).catch((err: Error) => setErrorMsg(err.message))
                              }
                              className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-700 hover:bg-slate-100"
                            >
                              <Download className="h-3 w-3" aria-hidden="true" />
                              <span>下载</span>
                            </button>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}

              {selectedItem.status === "confirmed" ? (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-xs text-emerald-900 space-y-2.5">
                  <p className="font-semibold flex items-center gap-1.5">
                    <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" />
                    <span>
                      {selectedItem.auto_archived ? "已按规则自动归档" : "已完成入案确认"}（
                      {formatShanghaiDateTime(selectedItem.confirmed_at)}）
                    </span>
                  </p>
                  {selectedItem.triage_reason && (
                    <p className="text-emerald-800">{selectedItem.triage_reason}</p>
                  )}
                  <div className="flex flex-wrap items-center gap-3 pt-1">
                    {selectedItem.matched_case_id && (
                      <Link
                        to={`/cases/${selectedItem.matched_case_id}`}
                        className="inline-flex items-center font-semibold underline"
                      >
                        查看归档案件 →
                      </Link>
                    )}
                    <button
                      type="button"
                      disabled={reopenMut.isPending}
                      onClick={() => reopenMut.mutate(selectedItem.id)}
                      className="inline-flex items-center gap-1 rounded border border-emerald-300 bg-white px-2.5 py-1 text-[11px] font-semibold text-emerald-900 hover:bg-emerald-100"
                    >
                      <RotateCcw className="h-3 w-3" aria-hidden="true" />
                      <span>{reopenMut.isPending ? "撤回中..." : "撤回归档 / 改派案件"}</span>
                    </button>
                  </div>
                </div>
              ) : selectedItem.status === "ignored" ? (
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-xs text-slate-700 flex items-center gap-2">
                  <Ban className="h-4 w-4 text-slate-500 shrink-0" aria-hidden="true" />
                  <span>
                    {selectedItem.triage_reason || "该条记录已标记为忽略。"}
                  </span>
                </div>
              ) : (
                <form
                  onSubmit={(e: FormEvent) => {
                    e.preventDefault();
                    confirmMut.mutate();
                  }}
                  className="space-y-4 border-t border-slate-100 pt-4"
                >
                  <div className="lt-segmented">
                    <button
                      type="button"
                      onClick={() => setCaseMode("existing")}
                      className={`rounded-full px-4 py-1.5 text-xs font-semibold transition-colors ${
                        caseMode === "existing"
                          ? "bg-[var(--md-sys-color-primary)] text-[var(--md-sys-color-on-primary)] shadow-sm"
                          : "text-[var(--md-sys-color-on-surface-variant)]"
                      }`}
                    >
                      归入已有案件
                    </button>
                    <button
                      type="button"
                      onClick={() => setCaseMode("new")}
                      className={`rounded-full px-4 py-1.5 text-xs font-semibold transition-colors ${
                        caseMode === "new"
                          ? "bg-[var(--md-sys-color-primary)] text-[var(--md-sys-color-on-primary)] shadow-sm"
                          : "text-[var(--md-sys-color-on-surface-variant)]"
                      }`}
                    >
                      确认时同时新建案件
                    </button>
                  </div>

                  {caseMode === "existing" ? (
                    <div className="space-y-2">
                      {ambiguousCases.length > 0 && (
                        <div className="space-y-1">
                          <span className="block text-[11px] font-semibold text-amber-800">
                            候选同委托人/同案号案件（点击快捷选中）：
                          </span>
                          <div className="flex flex-wrap gap-1.5">
                            {ambiguousCases.map((c) => (
                              <button
                                key={c.id}
                                type="button"
                                onClick={() => setTargetCaseId(String(c.id))}
                                className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
                                  targetCaseId === String(c.id)
                                    ? "border-blue-600 bg-blue-50 text-blue-800"
                                    : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                                }`}
                              >
                                {c.case_title} {c.case_number ? `(${c.case_number})` : ""}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                      <div>
                        <label htmlFor="cf-case" className="block text-xs font-semibold text-slate-700 mb-1">
                          目标归档案件 *
                        </label>
                        <select
                          id="cf-case"
                          required
                          value={targetCaseId}
                          onChange={(e) => setTargetCaseId(e.target.value)}
                          className="lt-input"
                        >
                          <option value="">请选择案件...</option>
                          {(casesQuery.data ?? []).map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.case_title} {c.case_number ? `(${c.case_number})` : ""}
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                  ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3.5">
                      <div className="sm:col-span-2">
                        <label htmlFor="cf-ntitle" className="block text-xs font-semibold text-slate-700 mb-1">
                          新案件名称 *
                        </label>
                        <input
                          id="cf-ntitle"
                          type="text"
                          required
                          value={newCaseTitle}
                          onChange={(e) => setNewCaseTitle(e.target.value)}
                          className="w-full rounded border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                        />
                      </div>
                      <div>
                        <label htmlFor="cf-ncat" className="block text-xs font-semibold text-slate-700 mb-1">
                          案件大类
                        </label>
                        <select
                          id="cf-ncat"
                          value={newCaseCategory}
                          onChange={(e) => setNewCaseCategory(e.target.value as CaseCategory)}
                          className="w-full rounded border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                        >
                          {(Object.keys(CASE_CATEGORY_META) as CaseCategory[]).map((cat) => (
                            <option key={cat} value={cat}>
                              {CASE_CATEGORY_META[cat].label}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label htmlFor="cf-nnum" className="block text-xs font-semibold text-slate-700 mb-1">
                          案号
                        </label>
                        <input
                          id="cf-nnum"
                          type="text"
                          value={newCaseNumber}
                          onChange={(e) => setNewCaseNumber(e.target.value)}
                          className="w-full rounded border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                        />
                      </div>
                      <div>
                        <label htmlFor="cf-nclient" className="block text-xs font-semibold text-slate-700 mb-1">
                          委托人 *
                        </label>
                        <input
                          id="cf-nclient"
                          type="text"
                          required
                          value={newClientName}
                          onChange={(e) => setNewClientName(e.target.value)}
                          className="w-full rounded border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                        />
                      </div>
                      <div>
                        <label htmlFor="cf-nvis" className="block text-xs font-semibold text-slate-700 mb-1">
                          可见性
                        </label>
                        <select
                          id="cf-nvis"
                          value={newVisibility}
                          onChange={(e) => setNewVisibility(e.target.value as CaseVisibility)}
                          className="w-full rounded border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                        >
                          <option value="team">团队协同</option>
                          <option value="private">个人私密</option>
                        </select>
                      </div>
                    </div>
                  )}

                  {selectedItem.item_type === "invoice_archive" ? (
                    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-xs text-slate-700">
                      <span className="font-semibold">默认归档目录：</span>
                      <span className="font-mono">{fileSubfolder}</span>
                      {Boolean(selectedItem.extracted_payload?.invoice_meta) && (
                        <span className="ml-2 text-slate-600">
                          · 金额{" "}
                          {String(
                            (selectedItem.extracted_payload?.invoice_meta as Record<string, unknown>)
                              ?.total_amount ?? "待核"
                          )}{" "}
                          元
                        </span>
                      )}
                    </div>
                  ) : (
                    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-xs text-slate-700 space-y-1">
                      <div>
                        <span className="font-semibold">识别处理动作：</span>
                        <span>
                          {eventType ? `${getEventTypeLabel(eventType)}（${eventDate}）` : "仅归档至「01_司法与程序文书」"}
                          {authorityDays ? ` · 指定 ${authorityDays} 日` : ""}
                          {createHearing ? ` · 写入开庭日程 ${hearingStart.replace("T", " ")}` : ""}
                        </span>
                      </div>
                    </div>
                  )}

                  <details
                    open={selectedItem.item_type !== "invoice_archive" && !eventType}
                    className="rounded-lg border border-slate-200 bg-white"
                  >
                    <summary className="cursor-pointer select-none px-3.5 py-2.5 text-xs font-semibold text-slate-700 hover:bg-slate-50">
                      高级选项（调整归档子目录 / 程序事件 / 阶段 / 开庭时间）
                    </summary>
                    <div className="space-y-3 border-t border-slate-100 p-3.5">
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                          <label htmlFor="cf-folder" className="block text-xs font-semibold text-slate-700 mb-1">
                            文书归档目录
                          </label>
                          <select
                            id="cf-folder"
                            value={fileSubfolder}
                            onChange={(e) => setFileSubfolder(e.target.value)}
                            className="lt-input text-xs"
                          >
                            {STANDARD_CASE_SUBFOLDERS.map((sf) => (
                              <option key={sf} value={sf}>
                                {sf}
                              </option>
                            ))}
                          </select>
                        </div>
                        <div>
                          <label htmlFor="cf-stage" className="block text-xs font-semibold text-slate-700 mb-1">
                            同步推进阶段（可选）
                          </label>
                          <input
                            id="cf-stage"
                            list="cf-stage-list"
                            value={newStage}
                            onChange={(e) => setNewStage(e.target.value)}
                            placeholder="留空则按事件自动推进"
                            className="lt-input text-xs"
                          />
                          <datalist id="cf-stage-list">
                            {stageSuggestions.map((sg) => (
                              <option key={sg} value={sg} />
                            ))}
                          </datalist>
                        </div>
                      </div>

                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 rounded-lg border border-slate-200 p-3">
                        <div className="sm:col-span-2 text-xs font-semibold text-slate-700">
                          触发程序事件并推算期限（留空则仅归档文件）
                        </div>
                        <div>
                          <label htmlFor="cf-evtype" className="block text-[11px] text-slate-600 mb-1">
                            事件标识（可选预设）
                          </label>
                          <input
                            id="cf-evtype"
                            list="cf-ev-presets"
                            value={eventType}
                            onChange={(e) => setEventType(e.target.value)}
                            placeholder="如 judgment_served"
                            className="w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs font-mono"
                          />
                          <datalist id="cf-ev-presets">
                            {EVENT_TYPE_PRESETS.map((p) => (
                              <option key={p.value} value={p.value}>
                                {p.label}
                              </option>
                            ))}
                          </datalist>
                        </div>
                        <div>
                          <label htmlFor="cf-evdate" className="block text-[11px] text-slate-600 mb-1">
                            送达日期
                          </label>
                          <input
                            id="cf-evdate"
                            type="date"
                            value={eventDate}
                            onChange={(e) => setEventDate(e.target.value)}
                            className="w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                          />
                        </div>
                        <div>
                          <label htmlFor="cf-evmethod" className="block text-[11px] text-slate-600 mb-1">
                            送达方式
                          </label>
                          <select
                            id="cf-evmethod"
                            value={serviceMethod}
                            onChange={(e) => setServiceMethod(e.target.value)}
                            className="w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                          >
                            {SERVICE_METHOD_OPTIONS.map((m) => (
                              <option key={m.value} value={m.value}>
                                {m.label}
                              </option>
                            ))}
                          </select>
                        </div>
                        <div>
                          <label htmlFor="cf-evdays" className="block text-[11px] text-slate-600 mb-1">
                            指定期限天数（可选）
                          </label>
                          <input
                            id="cf-evdays"
                            type="number"
                            min={1}
                            value={authorityDays}
                            onChange={(e) => setAuthorityDays(e.target.value)}
                            placeholder="如 15"
                            className="w-full rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                          />
                        </div>
                      </div>

                      <div className="rounded-lg border border-slate-200 p-3 space-y-2">
                        <label className="inline-flex items-center gap-2 text-xs font-semibold text-slate-700 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={createHearing}
                            onChange={(e) => setCreateHearing(e.target.checked)}
                          />
                          <span>同时写入正式开庭 / 听证日程</span>
                        </label>
                        {createHearing && (
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1">
                            <input
                              type="text"
                              value={hearingTitle}
                              onChange={(e) => setHearingTitle(e.target.value)}
                              placeholder="开庭标题"
                              className="rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                            />
                            <input
                              type="text"
                              value={hearingLoc}
                              onChange={(e) => setHearingLoc(e.target.value)}
                              placeholder="法庭地点"
                              className="rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                            />
                            <input
                              type="datetime-local"
                              value={hearingStart}
                              onChange={(e) => setHearingStart(e.target.value)}
                              className="rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                            />
                            <input
                              type="datetime-local"
                              value={hearingEnd}
                              onChange={(e) => setHearingEnd(e.target.value)}
                              className="rounded border border-slate-300 px-2.5 py-1.5 text-xs"
                            />
                          </div>
                        )}
                      </div>
                    </div>
                  </details>

                  <button
                    type="submit"
                    disabled={confirmMut.isPending}
                    className="lt-btn-primary w-full py-2.5 text-sm"
                  >
                    <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                    <span>
                      {confirmMut.isPending
                        ? "正在归档..."
                        : selectedItem.item_type === "invoice_archive"
                          ? "确认归档至财务目录"
                          : "确认入案并归档"}
                    </span>
                  </button>
                </form>
              )}
            </div>
          )}
        </div>
      )}

      {/* Simulate SMS Modal */}
      <ModalDialog
        open={simOpen}
        onClose={() => setSimOpen(false)}
        title="模拟司法 / 发票短信接入"
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            simulateMut.mutate();
          }}
          className="space-y-4"
        >
          {simError && (
            <div role="alert" className="lt-alert-error">
              {simError}
            </div>
          )}

          <div className="space-y-1.5">
            <span className="block text-xs font-semibold text-slate-700">选择短信样本：</span>
            <div className="flex flex-wrap gap-1.5">
              {SMS_PRESETS.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => {
                    setSimContent(p.content);
                    setSimSender(p.sender);
                  }}
                  className="rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-blue-50 hover:border-blue-300"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label htmlFor="sim-sender" className="block text-xs font-semibold text-slate-700 mb-1">
              发件号码
            </label>
            <input
              id="sim-sender"
              type="text"
              value={simSender}
              onChange={(e) => setSimSender(e.target.value)}
              className="lt-input"
            />
          </div>

          <div>
            <label htmlFor="sim-content" className="block text-xs font-semibold text-slate-700 mb-1">
              短信正文 *
            </label>
            <textarea
              id="sim-content"
              rows={4}
              required
              autoFocus
              value={simContent}
              onChange={(e) => setSimContent(e.target.value)}
              className="lt-input font-mono text-xs"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-center">
            <div>
              <label htmlFor="sim-phone4" className="block text-xs font-semibold text-slate-700 mb-1">
                手机号后4位（送达校验用，可选）
              </label>
              <input
                id="sim-phone4"
                type="text"
                maxLength={4}
                value={simPhoneLast4}
                onChange={(e) => setSimPhoneLast4(e.target.value)}
                placeholder="例如 8966"
                className="lt-input"
              />
            </div>
            <label className="inline-flex items-center gap-2 text-xs text-slate-700 sm:pt-5 cursor-pointer">
              <input
                type="checkbox"
                checked={simAutoFetch}
                onChange={(e) => setSimAutoFetch(e.target.checked)}
              />
              <span>自动尝试抓取送达文书</span>
            </label>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={() => setSimOpen(false)} className="lt-btn-secondary">
              取消
            </button>
            <button type="submit" disabled={simulateMut.isPending} className="lt-btn-primary">
              <Send className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{simulateMut.isPending ? "处理中..." : "注入收件箱"}</span>
            </button>
          </div>
        </form>
      </ModalDialog>

      {/* Manual PDF / Invoice Upload Modal */}
      <ModalDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        title="手动上传文书 PDF 或电子发票"
        subtitle="自动提取 PDF 文本中的案号、开庭时间或发票金额与抬头"
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            manualUploadMut.mutate();
          }}
          className="space-y-4"
        >
          {uploadError && (
            <div role="alert" className="lt-alert-error">
              {uploadError}
            </div>
          )}

          <div className="flex flex-wrap gap-4">
            <label className="inline-flex items-center gap-1.5 text-xs font-semibold cursor-pointer">
              <input
                type="radio"
                name="up-kind"
                checked={uploadType === "judicial_stage_and_doc"}
                onChange={() => setUploadType("judicial_stage_and_doc")}
              />
              <span>司法送达文书 PDF</span>
            </label>
            <label className="inline-flex items-center gap-1.5 text-xs font-semibold cursor-pointer">
              <input
                type="radio"
                name="up-kind"
                checked={uploadType === "invoice_archive"}
                onChange={() => setUploadType("invoice_archive")}
              />
              <span>电子发票（自动识别金额重命名）</span>
            </label>
          </div>

          <div>
            <label htmlFor="m-up-case" className="block text-xs font-semibold text-slate-700 mb-1">
              预关联案件（可选，留空自动按案号匹配）
            </label>
            <select
              id="m-up-case"
              value={uploadCaseId}
              onChange={(e) => setUploadCaseId(e.target.value)}
              className="lt-input"
            >
              <option value="">自动识别匹配</option>
              {(casesQuery.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.case_title} {c.case_number ? `(${c.case_number})` : ""}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="m-up-files" className="block text-xs font-semibold text-slate-700 mb-1">
              选择文件 *
            </label>
            <input
              id="m-up-files"
              type="file"
              multiple
              required
              onChange={(e) => setUploadFiles(Array.from(e.target.files ?? []))}
              className="w-full text-xs text-slate-700"
            />
          </div>
          <div>
            <label htmlFor="m-up-notes" className="block text-xs font-semibold text-slate-700 mb-1">
              备注 / 案号提示
            </label>
            <input
              id="m-up-notes"
              type="text"
              value={uploadNotes}
              onChange={(e) => setUploadNotes(e.target.value)}
              className="lt-input"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={() => setUploadOpen(false)} className="lt-btn-secondary">
              取消
            </button>
            <button
              type="submit"
              disabled={manualUploadMut.isPending || uploadFiles.length === 0}
              className="lt-btn-primary"
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{manualUploadMut.isPending ? "上传解析中..." : "上传进入分拣队列"}</span>
            </button>
          </div>
        </form>
      </ModalDialog>
    </div>
  );
}
