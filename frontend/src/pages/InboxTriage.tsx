import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronDown,
  Download,
  Eye,
  FileText,
  Inbox,
  KeyRound,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  Settings2,
  Sparkles,
  Upload,
} from "lucide-react";
import { casesApi, inboxApi, rulesApi } from "@/api/client";
import { ModalDialog } from "@/components/ModalDialog";
import { TriageStatusBadge } from "@/components/StatusBadges";
import {
  ConfirmDialog,
  MdCheckbox,
  MdFileDropzone,
  MdRadioGroup,
  MdSegmentedButton,
  MdSelect,
  MdSwitch,
} from "@/components/ui";
import { useAuth } from "@/context/AuthContext";
import {
  CASE_CATEGORY_META,
  CRIME_TYPE_OPTIONS,
  EVENT_TYPE_PRESETS,
  SERVICE_METHOD_OPTIONS,
  STANDARD_CASE_SUBFOLDERS,
  formatShanghaiDateTime,
  getEventTypeLabel,
  toShanghaiDateTimeLocal,
  toShanghaiIsoWithOffset,
  todayShanghaiIso,
} from "@/lib/domain";
import type {
  AutoTriagePolicy,
  CaseCategory,
  CaseVisibility,
  InboxTriageRead,
} from "@/types/api";

const SMS_PRESETS = [
  {
    label: "法院送达（上海一中院 · 尾号8966）",
    sender: "1069012368",
    content:
      "【上海市第一中级人民法院】张晨你好，请查收（2026）沪01民终6604号案件中你的送达文书。点击链接查阅：https://zxfw.court.gov.cn/zxfw/#/pagesAjkj/app/wssd/index?qdbh=d912b9c5315f4aafa1662b2e5cbf8fe3&sdbh=2bc0bb21b92f46198b836e138ae23c&sdsin=77b305fb135ee6acca10ab3e138ae23c",
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

type InboxViewTab = "needs_action" | "auto_archived" | "manual_confirmed" | "ignored" | "all";

function addMinutesLocal(isoLocal: string, minutes: number): string {
  const isoWithOffset = toShanghaiIsoWithOffset(isoLocal);
  const parsed = new Date(isoWithOffset);
  if (Number.isNaN(parsed.getTime())) return isoLocal;
  const shifted = new Date(parsed.getTime() + minutes * 60_000);
  return toShanghaiDateTimeLocal(shifted.toISOString());
}

function tabForItem(item: InboxTriageRead): InboxViewTab {
  if (item.status === "pending" || item.status === "manual") return "needs_action";
  if (item.status === "confirmed") return item.auto_archived ? "auto_archived" : "manual_confirmed";
  if (item.status === "ignored") return "ignored";
  return "all";
}

export function InboxTriage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryIdParam = searchParams.get("id");

  const [viewTab, setViewTab] = useState<InboxViewTab>(queryIdParam ? "all" : "needs_action");
  const [searchKw, setSearchKw] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(
    queryIdParam ? Number(queryIdParam) : null
  );
  const detailPanelRef = useRef<HTMLDivElement>(null);

  const [feedback, setFeedback] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [simError, setSimError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [batchIgnoreConfirmOpen, setBatchIgnoreConfirmOpen] = useState(false);
  const [policyOpen, setPolicyOpen] = useState(false);

  // Verification retry inputs
  const [retryPhoneLast4, setRetryPhoneLast4] = useState(user?.phone_last4 || "");
  const [retryIdLast4, setRetryIdLast4] = useState(user?.id_card_last4 || "");
  const [retryCode, setRetryCode] = useState("");

  // Confirm form state
  const [caseMode, setCaseMode] = useState<"existing" | "new">("existing");
  const [caseModeTouched, setCaseModeTouched] = useState(false);
  const [targetCaseId, setTargetCaseId] = useState<string>("");
  const [newCaseTitle, setNewCaseTitle] = useState("");
  const [newCaseNumber, setNewCaseNumber] = useState("");
  const [newCaseCategory, setNewCaseCategory] = useState<CaseCategory>("civil");
  const [newClientName, setNewClientName] = useState("");
  const [newVisibility, setNewVisibility] = useState<CaseVisibility>("team");
  const [newAgency, setNewAgency] = useState("");
  const [newCrimeType, setNewCrimeType] = useState("一般犯罪");
  const [newChargeName, setNewChargeName] = useState("");

  const [advancedOpen, setAdvancedOpen] = useState(false);
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

  // Simulate SMS modal
  const [simOpen, setSimOpen] = useState(false);
  const [simContent, setSimContent] = useState(SMS_PRESETS[0].content);
  const [simSender, setSimSender] = useState(SMS_PRESETS[0].sender);
  const [simPhoneLast4, setSimPhoneLast4] = useState(user?.phone_last4 || "8966");
  const [simAutoFetch, setSimAutoFetch] = useState(true);

  // Manual upload modal
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadType, setUploadType] = useState<"judicial_stage_and_doc" | "invoice_archive">(
    "judicial_stage_and_doc"
  );
  const [uploadCaseId, setUploadCaseId] = useState<string>("");
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploadNotes, setUploadNotes] = useState("");
  const [uploadAutoArchive, setUploadAutoArchive] = useState(true);

  const inboxQuery = useQuery({
    queryKey: ["inbox", "items"],
    queryFn: () => inboxApi.list({ limit: 500 }),
  });

  const casesQuery = useQuery({
    queryKey: ["cases", "all-for-inbox"],
    queryFn: () => casesApi.list({ is_archived: false }),
  });

  const templatesQuery = useQuery({
    queryKey: ["stage-templates"],
    queryFn: rulesApi.stageTemplates,
  });

  const policiesQuery = useQuery({
    queryKey: ["auto-triage-policies"],
    queryFn: rulesApi.autoTriagePolicies,
    enabled: policyOpen,
  });

  useEffect(() => {
    if (!user) return;
    setRetryPhoneLast4((prev) => prev || user.phone_last4 || "");
    setRetryIdLast4((prev) => prev || user.id_card_last4 || "");
  }, [user]);

  const allItems = inboxQuery.data ?? [];

  // Deep-link sync `/inbox?id=123` even when already mounted
  useEffect(() => {
    if (!queryIdParam) return;
    const idNum = Number(queryIdParam);
    if (!Number.isFinite(idNum)) return;
    setSelectedId(idNum);
    const match = allItems.find((it) => it.id === idNum);
    if (match) {
      setViewTab(tabForItem(match));
    } else {
      setViewTab("all");
    }
  }, [queryIdParam, allItems]);

  const counts = useMemo(() => {
    let needsAction = 0;
    let autoArchived = 0;
    let manualConfirmed = 0;
    let ignored = 0;
    for (const it of allItems) {
      if (it.status === "pending" || it.status === "manual") needsAction += 1;
      else if (it.status === "confirmed") {
        if (it.auto_archived) autoArchived += 1;
        else manualConfirmed += 1;
      } else if (it.status === "ignored") ignored += 1;
    }
    return {
      needsAction,
      autoArchived,
      manualConfirmed,
      ignored,
      all: allItems.length,
    };
  }, [allItems]);

  const items = useMemo(() => {
    const kw = searchKw.trim().toLowerCase();
    return allItems.filter((it) => {
      if (viewTab === "needs_action" && it.status !== "pending" && it.status !== "manual") {
        return false;
      }
      if (viewTab === "auto_archived" && !(it.status === "confirmed" && it.auto_archived)) {
        return false;
      }
      if (viewTab === "manual_confirmed" && !(it.status === "confirmed" && !it.auto_archived)) {
        return false;
      }
      if (viewTab === "ignored" && it.status !== "ignored") {
        return false;
      }
      if (!kw) return true;
      const p = it.extracted_payload || {};
      const haystack = [
        String(p.case_number || ""),
        String(p.doc_title_hint || ""),
        String(p.agency || ""),
        it.matched_case_title || "",
        it.matched_case_number || "",
        it.sms_content || "",
        it.sms_sender || "",
        it.triage_reason || "",
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(kw);
    });
  }, [allItems, viewTab, searchKw]);

  const selectedItem: InboxTriageRead | undefined =
    items.find((i) => i.id === selectedId) ?? items[0];

  const syncPayloadKey = selectedItem
    ? `${selectedItem.id}:${selectedItem.status}:${selectedItem.downloaded_files.length}:${String(selectedItem.extracted_payload?.event_type_guess || "")}:${String(selectedItem.extracted_payload?.hearing_time || "")}`
    : "";

  useEffect(() => {
    if (!selectedItem) return;
    const p = selectedItem.extracted_payload || {};
    setCaseModeTouched(false);
    if (selectedItem.matched_case_id) {
      setCaseMode("existing");
      setTargetCaseId(String(selectedItem.matched_case_id));
    } else {
      setCaseMode((casesQuery.data?.length ?? 0) > 0 ? "existing" : "new");
      setTargetCaseId("");
    }
    setNewCaseNumber((p.case_number as string) || "");
    setNewCaseTitle(
      (p.case_number as string)
        ? `${p.case_number} 新建协同案件`
        : (p.doc_title_hint as string) || "短信导入新案件"
    );
    setNewCaseCategory((p.case_category_guess as CaseCategory) || "civil");
    setNewClientName(
      (p.client_hint as string) || (p.client_name_guess as string) || "待补充委托人"
    );
    setNewAgency((p.agency as string) || (p.court_or_agency as string) || "");
    setNewCrimeType((p.crime_type as string) || "一般犯罪");
    setNewChargeName((p.charge_name as string) || "");
    setFileSubfolder(
      selectedItem.item_type === "invoice_archive" ? "05_财务与发票" : "01_司法与程序文书"
    );
    setNewStage((p.suggested_stage as string) || (p.stage_suggestion as string) || "");
    const evGuess = (p.event_type_guess as string) || "";
    setEventType(evGuess);
    setAdvancedOpen(selectedItem.item_type !== "invoice_archive" && !evGuess);
    setEventDate(
      typeof p.event_date === "string" && /^\d{4}-\d{2}-\d{2}/.test(p.event_date)
        ? p.event_date.slice(0, 10)
        : todayShanghaiIso()
    );
    setServiceMethod("electronic");
    setAuthorityDays(p.authority_specified_days ? String(p.authority_specified_days) : "");
    setRetryCode("");
    setCreateHearing(Boolean(p.hearing_time));
    if (typeof p.hearing_time === "string") {
      const startLocal = toShanghaiDateTimeLocal(p.hearing_time) || `${todayShanghaiIso()}T09:30`;
      setHearingStart(startLocal);
      setHearingEnd(addMinutesLocal(startLocal, 90));
      setHearingTitle(`${(p.case_number as string) || ""} 开庭审理`.trim());
      setHearingLoc((p.hearing_location as string) || "");
    } else {
      setHearingStart(`${todayShanghaiIso()}T09:30`);
      setHearingEnd(`${todayShanghaiIso()}T11:00`);
      setHearingTitle("");
      setHearingLoc("");
    }
  }, [syncPayloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Resolve async casesQuery race so default caseMode updates if user hasn't manually toggled
  useEffect(() => {
    if (!selectedItem || caseModeTouched || selectedItem.matched_case_id) return;
    if (!casesQuery.isSuccess) return;
    setCaseMode((casesQuery.data?.length ?? 0) > 0 ? "existing" : "new");
  }, [casesQuery.isSuccess, casesQuery.data?.length, selectedItem, caseModeTouched]);

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
          ? `取回成功，已获取 ${updated.downloaded_files.length} 份文书`
          : updated.status === "confirmed"
            ? "验证取回后已自动归档"
            : `重试完成：${updated.error_or_manual_reason || updated.triage_reason || "请继续确认"}`
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
        const startIso = toShanghaiIsoWithOffset(hearingStart);
        const endIso = toShanghaiIsoWithOffset(hearingEnd);
        const startMs = new Date(startIso).getTime();
        const endMs = new Date(endIso).getTime();
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
                ...(newCaseCategory === "criminal"
                  ? {
                      crime_type: newCrimeType,
                      charge_name: newChargeName.trim() || null,
                    }
                  : {}),
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
              start_time: toShanghaiIsoWithOffset(hearingStart),
              end_time: toShanghaiIsoWithOffset(hearingEnd),
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
        `入案确认成功！归档 ${res.archived_documents.length} 份文件，派生 ${res.generated_deadline_count} 条期限`
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
      setSearchParams({ id: String(reopened.id) }, { replace: true });
      setFeedback(`已恢复 #${reopened.id} 到待处理队列`);
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
      setBatchIgnoreConfirmOpen(false);
      setErrorMsg(null);
      setFeedback(`已批量忽略 ${res.ignored_count} 条待处理记录`);
    },
    onError: (err: Error) => {
      setBatchIgnoreConfirmOpen(false);
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
      setSearchParams({ id: String(created.id) }, { replace: true });
      setViewTab(tabForItem(created));
      if (created.status === "confirmed" && created.auto_archived) {
        setFeedback(`已自动归档 #${created.id}：${created.triage_reason || "命中规则并自动入案"}`);
      } else if (created.status === "ignored") {
        setFeedback(`非涉案短信 #${created.id} 已自动忽略`);
      } else {
        setFeedback(`已接入并转待处理 #${created.id}：${created.triage_reason || "请确认入案"}`);
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
        auto_archive: uploadAutoArchive,
      }),
    onSuccess: (created) => {
      refreshInbox();
      setUploadOpen(false);
      setUploadError(null);
      setUploadFiles([]);
      setUploadCaseId("");
      setUploadNotes("");
      setSelectedId(created.id);
      setSearchParams({ id: String(created.id) }, { replace: true });
      setViewTab(tabForItem(created));
      if (created.status === "confirmed" && created.auto_archived) {
        setFeedback(`上传文件已自动归档 #${created.id}：${created.triage_reason || "已入案"}`);
      } else {
        setFeedback(`已上传并解析文件，请确认入案 #${created.id}`);
      }
    },
    onError: (err: Error) => setUploadError(err.message),
  });

  const updatePolicyMut = useMutation({
    mutationFn: ({ key, mode }: { key: string; mode: "auto_archive" | "require_confirm" }) =>
      rulesApi.updateAutoTriagePolicy(key, mode),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["auto-triage-policies"] });
    },
    onError: (err: Error) => {
      setErrorMsg(err.message);
    },
  });

  // Match stageSuggestions with selected case's category + procedure_subtype
  const existingSelectedCase = useMemo(
    () => (casesQuery.data ?? []).find((c) => String(c.id) === targetCaseId),
    [casesQuery.data, targetCaseId]
  );
  const effectiveCategory: CaseCategory =
    caseMode === "existing" && existingSelectedCase
      ? existingSelectedCase.case_category
      : newCaseCategory;
  const effectiveSubtype =
    caseMode === "existing" && existingSelectedCase ? existingSelectedCase.procedure_subtype : undefined;

  const stageSuggestions = useMemo(() => {
    const catTpl = templatesQuery.data?.categories?.[effectiveCategory];
    if (!catTpl) return [];
    if (effectiveSubtype && catTpl.subtypes?.[effectiveSubtype]) {
      return catTpl.subtypes[effectiveSubtype];
    }
    return catTpl.stages ?? [];
  }, [templatesQuery.data, effectiveCategory, effectiveSubtype]);

  const filteredEventPresets = useMemo(() => {
    const matching = EVENT_TYPE_PRESETS.filter(
      (p) => !p.categories || p.categories.includes(effectiveCategory)
    );
    return matching.length > 0 ? matching : EVENT_TYPE_PRESETS;
  }, [effectiveCategory]);

  const ambiguousCandidateIds = Array.isArray(selectedItem?.extracted_payload?.ambiguous_case_ids)
    ? (selectedItem?.extracted_payload?.ambiguous_case_ids as number[])
    : [];
  const ambiguousCases = (casesQuery.data ?? []).filter((c) => ambiguousCandidateIds.includes(c.id));
  const needsActionIds = items
    .filter((it) => it.status === "pending" || it.status === "manual")
    .map((it) => it.id);

  const selectedPayload = selectedItem?.extracted_payload || {};
  const isAutoCreatedCase = Boolean(selectedPayload.auto_created_case);
  const relatedCaseHint =
    typeof selectedPayload.related_case_hint === "string"
      ? selectedPayload.related_case_hint
      : null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-[var(--md-sys-color-on-surface)]">收件箱</h1>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setPolicyOpen(true)}
            className="lt-btn-secondary"
          >
            <Settings2 className="h-3.5 w-3.5" aria-hidden="true" />
            <span>自动规则设置</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setUploadError(null);
              setUploadOpen(true);
            }}
            className="lt-btn-secondary"
          >
            <Upload className="h-3.5 w-3.5" aria-hidden="true" />
            <span>手动上传文件</span>
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

      {/* Status Filter Tabs & Search */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <MdSegmentedButton<InboxViewTab>
          value={viewTab}
          onChange={setViewTab}
          ariaLabel="收件箱状态筛选"
          options={[
            { value: "needs_action", label: "待处理", count: counts.needsAction },
            { value: "auto_archived", label: "已自动归档", count: counts.autoArchived },
            { value: "manual_confirmed", label: "人工已归档", count: counts.manualConfirmed },
            { value: "ignored", label: "已忽略", count: counts.ignored },
            { value: "all", label: "全部", count: counts.all },
          ]}
        />

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--md-sys-color-on-surface-variant)]"
            />
            <input
              type="search"
              value={searchKw}
              onChange={(e) => setSearchKw(e.target.value)}
              placeholder="搜案号、文书名或短信内容..."
              aria-label="搜索收件箱"
              className="lt-input pl-8 py-1.5 text-xs w-56"
            />
          </div>

          {viewTab === "needs_action" && needsActionIds.length > 1 && (
            <button
              type="button"
              disabled={batchIgnoreMut.isPending}
              onClick={() => setBatchIgnoreConfirmOpen(true)}
              className="lt-btn-secondary py-1.5 px-3 text-xs"
            >
              <Ban className="h-3.5 w-3.5" aria-hidden="true" />
              <span>批量忽略 ({needsActionIds.length})</span>
            </button>
          )}
        </div>
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
        <div className="lt-card p-12 text-center text-sm text-[var(--md-sys-color-on-surface-variant)]">
          正在加载收件箱...
        </div>
      ) : inboxQuery.isError ? (
        <div role="alert" className="lt-alert-error flex items-center justify-between gap-3">
          <span>收件箱加载失败，请重试。</span>
          <button type="button" onClick={() => inboxQuery.refetch()} className="lt-btn-secondary py-1">
            重新加载
          </button>
        </div>
      ) : items.length === 0 ? (
        <div className="lt-card p-12 text-center space-y-2 border-dashed">
          <Inbox className="h-8 w-8 text-[var(--md-sys-color-on-surface-variant)] mx-auto" aria-hidden="true" />
          <p className="text-sm font-semibold text-[var(--md-sys-color-on-surface)]">
            {viewTab === "needs_action" ? "当前没有待处理的短信或文书" : "当前分类下暂无记录"}
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
                    setSearchParams({ id: String(item.id) }, { replace: true });
                    if (window.innerWidth < 1024) {
                      window.setTimeout(() => {
                        detailPanelRef.current?.scrollIntoView({
                          behavior: "smooth",
                          block: "nearest",
                        });
                      }, 60);
                    }
                  }}
                  className={`w-full text-left rounded-[var(--md-sys-shape-corner-lg)] border p-4 transition-all space-y-2 ${
                    active
                      ? "border-[var(--md-sys-color-primary)] bg-[var(--md-sys-color-primary-container)]/35 shadow-sm ring-1 ring-[var(--md-sys-color-primary)]"
                      : "border-[var(--md-sys-color-outline-variant)]/80 bg-[var(--md-sys-color-surface-container-lowest)] hover:bg-[var(--md-sys-color-surface-container-low)]"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <TriageStatusBadge status={item.status} />
                      {item.auto_archived && (
                        <span className="rounded-full bg-[var(--md-sys-color-tertiary-container)] px-2 py-0.5 text-[10px] font-semibold text-[var(--md-sys-color-on-tertiary-container)]">
                          自动归档
                        </span>
                      )}
                      {Boolean(p.auto_created_case) && (
                        <span className="rounded-full bg-[var(--md-sys-color-secondary-container)] px-2 py-0.5 text-[10px] font-semibold text-[var(--md-sys-color-on-secondary-container)]">
                          自动建案
                        </span>
                      )}
                    </div>
                    <span className="text-[11px] text-[var(--md-sys-color-on-surface-variant)] tabular-nums">
                      {formatShanghaiDateTime(item.created_at)}
                    </span>
                  </div>
                  <p className="text-sm font-bold text-[var(--md-sys-color-on-surface)]">
                    {(p.case_number as string) ||
                      (p.doc_title_hint as string) ||
                      (item.item_type === "invoice_archive" ? "电子发票归档" : `待确认事项 #${item.id}`)}
                  </p>
                  <p className="text-xs text-[var(--md-sys-color-on-surface-variant)] line-clamp-2">
                    {item.sms_content || (p.notes as string) || "手动上传文档"}
                  </p>
                  {item.triage_reason && (
                    <p className="text-[11px] text-[#7a4b00] line-clamp-1">{item.triage_reason}</p>
                  )}
                  <div className="flex flex-wrap items-center gap-2 text-[11px] text-[var(--md-sys-color-on-surface-variant)]">
                    <span>接收：{item.owner_name}</span>
                    {item.matched_case_title && (
                      <span className="rounded-full bg-[var(--md-sys-color-primary-container)] px-2 py-0.5 text-[var(--md-sys-color-on-primary-container)] font-medium">
                        {item.matched_case_title}
                      </span>
                    )}
                    {item.downloaded_files.length > 0 && (
                      <span className="rounded-full bg-[var(--md-sys-color-tertiary-container)] px-2 py-0.5 text-[var(--md-sys-color-on-tertiary-container)] font-medium">
                        {item.status === "confirmed" ? "已归档" : "已暂存"} {item.downloaded_files.length} 份
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
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--md-sys-color-outline-variant)]/60 pb-4">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <TriageStatusBadge status={selectedItem.status} />
                    {selectedItem.auto_archived && (
                      <span className="rounded-full bg-[var(--md-sys-color-tertiary-container)] px-2.5 py-0.5 text-[11px] font-semibold text-[var(--md-sys-color-on-tertiary-container)]">
                        规则自动归档
                      </span>
                    )}
                    {isAutoCreatedCase && (
                      <span className="rounded-full bg-[var(--md-sys-color-secondary-container)] px-2.5 py-0.5 text-[11px] font-semibold text-[var(--md-sys-color-on-secondary-container)]">
                        已自动新建案件
                      </span>
                    )}
                    <span className="text-xs text-[var(--md-sys-color-on-surface-variant)]">
                      #{selectedItem.id} · {selectedItem.sms_sender || "手动上传"}
                    </span>
                  </div>
                  <h2 className="mt-1.5 text-lg font-bold text-[var(--md-sys-color-on-surface)]">
                    {(selectedPayload.case_number as string) ||
                      (selectedPayload.doc_title_hint as string) ||
                      (selectedItem.item_type === "invoice_archive" ? "电子发票归档" : "待确认事项")}
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

              {selectedItem.triage_reason &&
                (selectedItem.status === "pending" || selectedItem.status === "manual") && (
                  <div className="rounded-[var(--md-sys-shape-corner-md)] border border-[#f6d48b] bg-[#fef1d6] px-3.5 py-2.5 text-xs text-[#7a4b00] flex items-start gap-2">
                    <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" aria-hidden="true" />
                    <div>
                      <span className="font-semibold">人工处理原因：</span>
                      <span>{selectedItem.triage_reason}</span>
                    </div>
                  </div>
                )}

              {relatedCaseHint && (
                <div className="rounded-[var(--md-sys-shape-corner-md)] border border-[var(--md-sys-color-primary)]/30 bg-[var(--md-sys-color-primary-container)]/40 px-3.5 py-2.5 text-xs text-[var(--md-sys-color-on-primary-container)]">
                  {relatedCaseHint}
                </div>
              )}

              {selectedItem.sms_content && (
                <div className="lt-surface-tonal p-3.5 space-y-1.5">
                  <p className="text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)]">
                    原始短信内容（发件号码：{selectedItem.sms_sender || "-"}）
                  </p>
                  <p className="text-xs text-[var(--md-sys-color-on-surface)] whitespace-pre-wrap break-all leading-relaxed">
                    {selectedItem.sms_content}
                  </p>
                </div>
              )}

              {selectedItem.error_or_manual_reason && (
                <div className="rounded-[var(--md-sys-shape-corner-md)] border border-[var(--md-sys-color-error)]/30 bg-[var(--md-sys-color-error-container)]/60 p-3.5 space-y-3">
                  <div className="flex items-start gap-2 text-xs text-[var(--md-sys-color-on-error-container)]">
                    <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" aria-hidden="true" />
                    <div>
                      <p className="font-semibold">需补充验证信息或人工打开链接取回：</p>
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
                        placeholder="手机尾号4位"
                        className="lt-input text-xs py-1.5"
                      />
                      <input
                        type="text"
                        aria-label="证件后4位"
                        maxLength={4}
                        value={retryIdLast4}
                        onChange={(e) => setRetryIdLast4(e.target.value)}
                        placeholder="证件尾号4位（可选）"
                        className="lt-input text-xs py-1.5"
                      />
                      <input
                        type="text"
                        aria-label="短信提取码"
                        value={retryCode}
                        onChange={(e) => setRetryCode(e.target.value)}
                        placeholder="提取码（可选）"
                        className="lt-input text-xs py-1.5"
                      />
                      <button
                        type="submit"
                        disabled={retryMut.isPending}
                        className="lt-btn-danger py-1.5 px-3"
                      >
                        <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
                        <span>{retryMut.isPending ? "取回中..." : "补充重试"}</span>
                      </button>
                    </form>
                  )}
                </div>
              )}

              {selectedItem.downloaded_files.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs font-semibold text-[var(--md-sys-color-on-surface)]">
                    {selectedItem.status === "confirmed" ? "已归档文件" : "已暂存文件"}
                  </p>
                  <ul className="space-y-1.5">
                    {selectedItem.downloaded_files.map((fp) => {
                      const fname = fp.split("/").pop() || "document.pdf";
                      return (
                        <li
                          key={fp}
                          className="flex items-center justify-between gap-2 rounded-[var(--md-sys-shape-corner-sm)] border border-[var(--md-sys-color-outline-variant)] bg-[var(--md-sys-color-surface-container-low)] px-3 py-2 text-xs text-[var(--md-sys-color-on-surface)]"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <FileText className="h-3.5 w-3.5 text-[var(--md-sys-color-primary)] shrink-0" aria-hidden="true" />
                            <span className="truncate font-medium">{fname}</span>
                          </div>
                          <div className="flex items-center gap-1.5 shrink-0">
                            <button
                              type="button"
                              onClick={() =>
                                void inboxApi
                                  .downloadFile(selectedItem.id, fp, fname, true)
                                  .catch((err: Error) => setErrorMsg(err.message))
                              }
                              className="lt-btn-secondary py-1 px-2.5 text-[11px]"
                            >
                              <Eye className="h-3 w-3" aria-hidden="true" />
                              <span>预览</span>
                            </button>
                            <button
                              type="button"
                              onClick={() =>
                                void inboxApi
                                  .downloadFile(selectedItem.id, fp, fname, false)
                                  .catch((err: Error) => setErrorMsg(err.message))
                              }
                              className="lt-btn-secondary py-1 px-2.5 text-[11px]"
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
                <div className="lt-alert-success p-4 text-xs space-y-2.5">
                  <p className="font-semibold flex items-center gap-1.5">
                    <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                    <span>
                      {selectedItem.auto_archived ? "已自动归档" : "已确认归档"}（
                      {formatShanghaiDateTime(selectedItem.confirmed_at)}）
                    </span>
                  </p>
                  {selectedItem.triage_reason && <p>{selectedItem.triage_reason}</p>}
                  <div className="flex flex-wrap items-center gap-3 pt-1">
                    {selectedItem.matched_case_id && (
                      <Link
                        to={`/cases/${selectedItem.matched_case_id}`}
                        className="lt-btn-accent py-1.5 px-3"
                      >
                        查看归档案件 →
                      </Link>
                    )}
                    <button
                      type="button"
                      disabled={reopenMut.isPending}
                      onClick={() => reopenMut.mutate(selectedItem.id)}
                      className="lt-btn-secondary py-1.5 px-3"
                    >
                      <RotateCcw className="h-3 w-3" aria-hidden="true" />
                      <span>{reopenMut.isPending ? "撤回中..." : "撤回归档 / 改派案件"}</span>
                    </button>
                  </div>
                </div>
              ) : selectedItem.status === "ignored" ? (
                <div className="lt-surface-tonal p-4 text-xs flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-2 text-[var(--md-sys-color-on-surface-variant)]">
                    <Ban className="h-4 w-4 shrink-0" aria-hidden="true" />
                    <span>{selectedItem.triage_reason || "该条记录已忽略。"}</span>
                  </div>
                  <button
                    type="button"
                    disabled={reopenMut.isPending}
                    onClick={() => reopenMut.mutate(selectedItem.id)}
                    className="lt-btn-secondary py-1.5 px-3"
                  >
                    <RotateCcw className="h-3 w-3" aria-hidden="true" />
                    <span>{reopenMut.isPending ? "恢复中..." : "恢复到待处理"}</span>
                  </button>
                </div>
              ) : (
                <form
                  onSubmit={(e: FormEvent) => {
                    e.preventDefault();
                    confirmMut.mutate();
                  }}
                  className="space-y-4 border-t border-[var(--md-sys-color-outline-variant)]/60 pt-4"
                >
                  <MdSegmentedButton<"existing" | "new">
                    value={caseMode}
                    onChange={(v) => {
                      setCaseModeTouched(true);
                      setCaseMode(v);
                    }}
                    ariaLabel="案件归属方式"
                    options={[
                      { value: "existing", label: "归入已有案件" },
                      { value: "new", label: "确认时新建案件" },
                    ]}
                  />

                  {caseMode === "existing" ? (
                    <div className="space-y-2">
                      {ambiguousCases.length > 0 && (
                        <div className="space-y-1.5">
                          <span className="block text-[11px] font-semibold text-[#7a4b00]">
                            检测到多个同委托人/同案号案件，请点击选择：
                          </span>
                          <div className="flex flex-wrap gap-1.5">
                            {ambiguousCases.map((c) => (
                              <button
                                key={c.id}
                                type="button"
                                onClick={() => setTargetCaseId(String(c.id))}
                                className={`lt-chip ${targetCaseId === String(c.id) ? "lt-chip-active" : ""}`}
                              >
                                {c.case_title} {c.case_number ? `(${c.case_number})` : ""}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                      <div>
                        <label
                          htmlFor="cf-case"
                          className="block text-xs font-semibold text-[var(--md-sys-color-on-surface)] mb-1"
                        >
                          目标归档案件 *
                        </label>
                        <MdSelect
                          id="cf-case"
                          required
                          value={targetCaseId}
                          onChange={(e) => setTargetCaseId(e.target.value)}
                        >
                          <option value="">请选择案件...</option>
                          {(casesQuery.data ?? []).map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.case_title} {c.case_number ? `(${c.case_number})` : ""}
                            </option>
                          ))}
                        </MdSelect>
                      </div>
                    </div>
                  ) : (
                    <div className="lt-surface-tonal grid grid-cols-1 sm:grid-cols-2 gap-3 p-4">
                      <div className="sm:col-span-2">
                        <label htmlFor="cf-ntitle" className="block text-xs font-semibold mb-1">
                          新案件名称 *
                        </label>
                        <input
                          id="cf-ntitle"
                          type="text"
                          required
                          value={newCaseTitle}
                          onChange={(e) => setNewCaseTitle(e.target.value)}
                          className="lt-input text-xs py-1.5"
                        />
                      </div>
                      <div>
                        <label htmlFor="cf-ncat" className="block text-xs font-semibold mb-1">
                          案件大类
                        </label>
                        <MdSelect
                          id="cf-ncat"
                          value={newCaseCategory}
                          onChange={(e) => setNewCaseCategory(e.target.value as CaseCategory)}
                          className="text-xs py-1.5"
                        >
                          {(Object.keys(CASE_CATEGORY_META) as CaseCategory[]).map((cat) => (
                            <option key={cat} value={cat}>
                              {CASE_CATEGORY_META[cat].label}
                            </option>
                          ))}
                        </MdSelect>
                      </div>
                      <div>
                        <label htmlFor="cf-nnum" className="block text-xs font-semibold mb-1">
                          案号
                        </label>
                        <input
                          id="cf-nnum"
                          type="text"
                          value={newCaseNumber}
                          onChange={(e) => setNewCaseNumber(e.target.value)}
                          className="lt-input text-xs py-1.5"
                        />
                      </div>
                      <div>
                        <label htmlFor="cf-nclient" className="block text-xs font-semibold mb-1">
                          委托人 *
                        </label>
                        <input
                          id="cf-nclient"
                          type="text"
                          required
                          value={newClientName}
                          onChange={(e) => setNewClientName(e.target.value)}
                          className="lt-input text-xs py-1.5"
                        />
                      </div>
                      <div>
                        <label htmlFor="cf-nagency" className="block text-xs font-semibold mb-1">
                          受理机关（法院/检察院/仲裁委）
                        </label>
                        <input
                          id="cf-nagency"
                          type="text"
                          value={newAgency}
                          onChange={(e) => setNewAgency(e.target.value)}
                          placeholder="如 上海市第一中级人民法院"
                          className="lt-input text-xs py-1.5"
                        />
                      </div>
                      <div>
                        <label htmlFor="cf-nvis" className="block text-xs font-semibold mb-1">
                          可见性
                        </label>
                        <MdSelect
                          id="cf-nvis"
                          value={newVisibility}
                          onChange={(e) => setNewVisibility(e.target.value as CaseVisibility)}
                          className="text-xs py-1.5"
                        >
                          <option value="team">团队协同</option>
                          <option value="private">个人私密</option>
                        </MdSelect>
                      </div>
                      {newCaseCategory === "criminal" && (
                        <>
                          <div>
                            <label htmlFor="cf-ncrime" className="block text-xs font-semibold mb-1">
                              案件情形（拘留计算适用）
                            </label>
                            <MdSelect
                              id="cf-ncrime"
                              value={newCrimeType}
                              onChange={(e) => setNewCrimeType(e.target.value)}
                              className="text-xs py-1.5"
                            >
                              {CRIME_TYPE_OPTIONS.map((opt) => (
                                <option key={opt.value} value={opt.value}>
                                  {opt.label}
                                </option>
                              ))}
                            </MdSelect>
                          </div>
                          <div>
                            <label htmlFor="cf-ncharge" className="block text-xs font-semibold mb-1">
                              涉嫌罪名
                            </label>
                            <input
                              id="cf-ncharge"
                              type="text"
                              value={newChargeName}
                              onChange={(e) => setNewChargeName(e.target.value)}
                              placeholder="如 职务侵占罪"
                              className="lt-input text-xs py-1.5"
                            />
                          </div>
                        </>
                      )}
                    </div>
                  )}

                  {selectedItem.item_type === "invoice_archive" ? (
                    <div className="lt-surface-tonal px-3.5 py-2.5 text-xs">
                      <span className="font-semibold">默认归档目录：</span>
                      <span>{fileSubfolder}</span>
                      {Boolean(selectedPayload.invoice_meta) && (
                        <span className="ml-2 text-[var(--md-sys-color-on-surface-variant)]">
                          · 金额{" "}
                          {String(
                            (selectedPayload.invoice_meta as Record<string, unknown>)?.total_amount ??
                              "待核"
                          )}{" "}
                          元
                        </span>
                      )}
                    </div>
                  ) : (
                    <div className="lt-surface-tonal px-3.5 py-2.5 text-xs">
                      <span className="font-semibold">识别动作：</span>
                      <span>
                        {eventType
                          ? `${getEventTypeLabel(eventType)}（${eventDate}）`
                          : "仅归档至「01_司法与程序文书」"}
                        {authorityDays ? ` · 指定 ${authorityDays} 日` : ""}
                        {createHearing ? ` · 写入开庭日程 ${hearingStart.replace("T", " ")}` : ""}
                      </span>
                    </div>
                  )}

                  <div className="rounded-[var(--md-sys-shape-corner-md)] border border-[var(--md-sys-color-outline-variant)] bg-[var(--md-sys-color-surface-container-lowest)]">
                    <button
                      type="button"
                      aria-expanded={advancedOpen}
                      onClick={() => setAdvancedOpen((prev) => !prev)}
                      className="flex w-full items-center justify-between px-3.5 py-2.5 text-xs font-semibold text-[var(--md-sys-color-on-surface)] hover:bg-[var(--md-sys-color-surface-container-low)]"
                    >
                      <span>调整归档目录 / 程序事件 / 开庭日程</span>
                      <ChevronDown
                        className={`h-4 w-4 transition-transform ${advancedOpen ? "rotate-180" : ""}`}
                        aria-hidden="true"
                      />
                    </button>

                    {advancedOpen && (
                      <div className="space-y-3 border-t border-[var(--md-sys-color-outline-variant)]/60 p-3.5">
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                          <div>
                            <label htmlFor="cf-folder" className="block text-xs font-semibold mb-1">
                              文书归档目录
                            </label>
                            <MdSelect
                              id="cf-folder"
                              value={fileSubfolder}
                              onChange={(e) => setFileSubfolder(e.target.value)}
                              className="text-xs py-1.5"
                            >
                              {STANDARD_CASE_SUBFOLDERS.map((sf) => (
                                <option key={sf} value={sf}>
                                  {sf}
                                </option>
                              ))}
                            </MdSelect>
                          </div>
                          <div>
                            <label htmlFor="cf-stage" className="block text-xs font-semibold mb-1">
                              同步推进阶段（可选）
                            </label>
                            <MdSelect
                              id="cf-stage"
                              value={newStage}
                              onChange={(e) => setNewStage(e.target.value)}
                              className="text-xs py-1.5"
                            >
                              <option value="">不手动指定（由程序自动判断）</option>
                              {stageSuggestions.map((sg) => (
                                <option key={sg} value={sg}>
                                  {sg}
                                </option>
                              ))}
                            </MdSelect>
                          </div>
                        </div>

                        <div className="lt-surface-tonal grid grid-cols-1 sm:grid-cols-2 gap-3 p-3">
                          <div className="sm:col-span-2 text-xs font-semibold">
                            触发程序事件与计算期限（选填）
                          </div>
                          <div>
                            <label htmlFor="cf-evtype" className="block text-[11px] mb-1">
                              程序事件类型
                            </label>
                            <MdSelect
                              id="cf-evtype"
                              value={eventType}
                              onChange={(e) => setEventType(e.target.value)}
                              className="text-xs py-1.5"
                            >
                              <option value="">仅归档文件，不创建程序事件</option>
                              {filteredEventPresets.map((p) => (
                                <option key={p.value} value={p.value}>
                                  {p.label}
                                </option>
                              ))}
                            </MdSelect>
                          </div>
                          <div>
                            <label htmlFor="cf-evdate" className="block text-[11px] mb-1">
                              送达/起算日期
                            </label>
                            <input
                              id="cf-evdate"
                              type="date"
                              value={eventDate}
                              onChange={(e) => setEventDate(e.target.value)}
                              className="lt-input text-xs py-1.5"
                            />
                          </div>
                          <div>
                            <label htmlFor="cf-evmethod" className="block text-[11px] mb-1">
                              送达方式
                            </label>
                            <MdSelect
                              id="cf-evmethod"
                              value={serviceMethod}
                              onChange={(e) => setServiceMethod(e.target.value)}
                              className="text-xs py-1.5"
                            >
                              {SERVICE_METHOD_OPTIONS.map((m) => (
                                <option key={m.value} value={m.value}>
                                  {m.label}
                                </option>
                              ))}
                            </MdSelect>
                          </div>
                          {eventType === "evidence_notice_served" && (
                            <div>
                              <label htmlFor="cf-evdays" className="block text-[11px] mb-1">
                                法院指定举证天数
                              </label>
                              <input
                                id="cf-evdays"
                                type="number"
                                min={1}
                                value={authorityDays}
                                onChange={(e) => setAuthorityDays(e.target.value)}
                                placeholder="如 15"
                                className="lt-input text-xs py-1.5"
                              />
                            </div>
                          )}
                        </div>

                        <div className="lt-surface-tonal p-3 space-y-2.5">
                          <MdCheckbox
                            checked={createHearing}
                            onChange={setCreateHearing}
                            label={<span className="text-xs font-semibold">同时写入开庭 / 听证日程</span>}
                          />
                          {createHearing && (
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 pt-1">
                              <div>
                                <label htmlFor="cf-htitle" className="block text-[11px] mb-1">
                                  日程标题
                                </label>
                                <input
                                  id="cf-htitle"
                                  type="text"
                                  value={hearingTitle}
                                  onChange={(e) => setHearingTitle(e.target.value)}
                                  placeholder="如 第一次开庭审理"
                                  className="lt-input text-xs py-1.5"
                                />
                              </div>
                              <div>
                                <label htmlFor="cf-hloc" className="block text-[11px] mb-1">
                                  法庭/会议地点
                                </label>
                                <input
                                  id="cf-hloc"
                                  type="text"
                                  value={hearingLoc}
                                  onChange={(e) => setHearingLoc(e.target.value)}
                                  placeholder="如 第一法庭"
                                  className="lt-input text-xs py-1.5"
                                />
                              </div>
                              <div>
                                <label htmlFor="cf-hstart" className="block text-[11px] mb-1">
                                  开始时间（北京时间）
                                </label>
                                <input
                                  id="cf-hstart"
                                  type="datetime-local"
                                  value={hearingStart}
                                  onChange={(e) => setHearingStart(e.target.value)}
                                  className="lt-input text-xs py-1.5"
                                />
                              </div>
                              <div>
                                <label htmlFor="cf-hend" className="block text-[11px] mb-1">
                                  结束时间（北京时间）
                                </label>
                                <input
                                  id="cf-hend"
                                  type="datetime-local"
                                  value={hearingEnd}
                                  onChange={(e) => setHearingEnd(e.target.value)}
                                  className="lt-input text-xs py-1.5"
                                />
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>

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

      {/* Auto-Triage Policy Settings Modal */}
      <ModalDialog
        open={policyOpen}
        onClose={() => setPolicyOpen(false)}
        title="自动分拣与归档策略"
        subtitle="命中案号及规则且无歧义时按下方策略处理"
      >
        <div className="space-y-3">
          {policiesQuery.isLoading ? (
            <p className="py-6 text-center text-xs text-[var(--md-sys-color-on-surface-variant)]">
              正在加载规则开关...
            </p>
          ) : (
            <div className="divide-y divide-[var(--md-sys-color-outline-variant)]/60">
              {(policiesQuery.data ?? []).map((policy: AutoTriagePolicy) => {
                const isAuto = policy.mode === "auto_archive";
                return (
                  <div key={policy.policy_key} className="py-3.5 first:pt-0 last:pb-0">
                    <MdSwitch
                      checked={isAuto}
                      disabled={user?.system_role !== "admin" || updatePolicyMut.isPending}
                      onChange={(checked) =>
                        updatePolicyMut.mutate({
                          key: policy.policy_key,
                          mode: checked ? "auto_archive" : "require_confirm",
                        })
                      }
                      label={policy.label}
                      description={
                        isAuto
                          ? `当前：自动识别归档 · ${policy.description}`
                          : `当前：暂停待人工确认 · ${policy.description}`
                      }
                    />
                  </div>
                );
              })}
            </div>
          )}
          {user?.system_role !== "admin" && (
            <p className="text-[11px] text-[var(--md-sys-color-on-surface-variant)]">
              注：仅合伙人/管理员可调整全所分拣策略，当前为只读查看模式。
            </p>
          )}
        </div>
      </ModalDialog>

      {/* Batch Ignore Confirm Dialog */}
      <ConfirmDialog
        open={batchIgnoreConfirmOpen}
        onClose={() => setBatchIgnoreConfirmOpen(false)}
        onConfirm={() => batchIgnoreMut.mutate(needsActionIds)}
        loading={batchIgnoreMut.isPending}
        title="批量忽略待处理记录"
        description={`将当前队列中的 ${needsActionIds.length} 条待处理记录统一标记为已忽略，后续仍可在「已忽略」分类中恢复。确定继续吗？`}
        confirmLabel="确认批量忽略"
      />

      {/* Simulate SMS Modal */}
      <ModalDialog open={simOpen} onClose={() => setSimOpen(false)} title="模拟司法 / 发票短信接入">
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
            <span className="block text-xs font-semibold">选择短信样本：</span>
            <div className="flex flex-wrap gap-1.5">
              {SMS_PRESETS.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => {
                    setSimContent(p.content);
                    setSimSender(p.sender);
                  }}
                  className="lt-chip py-1 px-2.5 text-[11px]"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label htmlFor="sim-sender" className="block text-xs font-semibold mb-1">
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
            <label htmlFor="sim-content" className="block text-xs font-semibold mb-1">
              短信正文 *
            </label>
            <textarea
              id="sim-content"
              rows={4}
              required
              value={simContent}
              onChange={(e) => setSimContent(e.target.value)}
              className="lt-input font-mono text-xs"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-center">
            <div>
              <label htmlFor="sim-phone4" className="block text-xs font-semibold mb-1">
                手机号后4位（送达验证用）
              </label>
              <input
                id="sim-phone4"
                type="text"
                maxLength={4}
                value={simPhoneLast4}
                onChange={(e) => setSimPhoneLast4(e.target.value.replace(/\D/g, ""))}
                placeholder="例如 8966"
                className="lt-input"
              />
            </div>
            <div className="sm:pt-5">
              <MdCheckbox
                checked={simAutoFetch}
                onChange={setSimAutoFetch}
                label="自动尝试取回送达文书"
              />
            </div>
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
        title="手动上传文书或电子发票"
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

          <MdRadioGroup<"judicial_stage_and_doc" | "invoice_archive">
            value={uploadType}
            onChange={setUploadType}
            columns={2}
            ariaLabel="上传文件类型"
            options={[
              { value: "judicial_stage_and_doc", label: "司法送达文书", badge: "PDF/Word" },
              { value: "invoice_archive", label: "电子发票", badge: "PDF/OFD/XML" },
            ]}
          />

          <div>
            <label htmlFor="m-up-case" className="block text-xs font-semibold mb-1">
              预关联案件（可选，留空自动按文件内案号匹配）
            </label>
            <MdSelect
              id="m-up-case"
              value={uploadCaseId}
              onChange={(e) => setUploadCaseId(e.target.value)}
            >
              <option value="">自动识别匹配</option>
              {(casesQuery.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.case_title} {c.case_number ? `(${c.case_number})` : ""}
                </option>
              ))}
            </MdSelect>
          </div>

          <MdFileDropzone
            files={uploadFiles}
            onFilesChange={setUploadFiles}
            multiple
            accept=".pdf,.ofd,.xml,.doc,.docx,.jpg,.jpeg,.png"
            label="点击或拖拽文书/发票到此上传"
            hint="支持批量选择 PDF、OFD、XML、图片或 Word"
          />

          <div>
            <label htmlFor="m-up-notes" className="block text-xs font-semibold mb-1">
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

          <MdCheckbox
            checked={uploadAutoArchive}
            onChange={setUploadAutoArchive}
            label="识别出唯一案件且命中规则时直接自动归档"
          />

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
              <span>{manualUploadMut.isPending ? "解析中..." : "上传进入分拣队列"}</span>
            </button>
          </div>
        </form>
      </ModalDialog>
    </div>
  );
}
