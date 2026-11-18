import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Copy,
  Edit3,
  Plus,
  RefreshCw,
  Scale,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import { rulesApi } from "@/api/client";
import { ModalDialog } from "@/components/ModalDialog";
import { CategoryBadge, RiskBadge } from "@/components/StatusBadges";
import { useAuth } from "@/context/AuthContext";
import {
  CASE_CATEGORY_META,
  EVENT_TYPE_PRESETS,
  HOLIDAY_ROLL_LABELS,
  getEventTypeLabel,
} from "@/lib/domain";
import type {
  AutoTriagePolicy,
  AutoTriagePolicyMode,
  CaseCategory,
  DeadlineRule,
  HolidayRollMode,
  RiskLevel,
} from "@/types/api";

type ForeignScope = "any" | "false" | "true";
type CustodyScope = "any" | "in_custody" | "not_in_custody" | "bail" | "residential";
type CrimeTypeScope = "any" | "一般犯罪" | "流窜/多次/结伙作案";

const MANAGED_CONDITION_KEYS = new Set([
  "is_foreign_related",
  "custody_status",
  "custody_status_not",
  "crime_type",
]);

function extractConditionForm(cond?: Record<string, unknown> | null): {
  foreignScope: ForeignScope;
  custodyScope: CustodyScope;
  crimeTypeScope: CrimeTypeScope;
  extraFields: Record<string, unknown>;
} {
  const raw = cond ?? {};
  let foreignScope: ForeignScope = "any";
  if (raw.is_foreign_related === true) foreignScope = "true";
  else if (raw.is_foreign_related === false) foreignScope = "false";

  let custodyScope: CustodyScope = "any";
  if (raw.custody_status === "在押") custodyScope = "in_custody";
  else if (raw.custody_status_not === "在押") custodyScope = "not_in_custody";
  else if (raw.custody_status === "取保候审") custodyScope = "bail";
  else if (raw.custody_status === "监视居住") custodyScope = "residential";

  let crimeTypeScope: CrimeTypeScope = "any";
  if (raw.crime_type === "一般犯罪" || raw.crime_type === "流窜/多次/结伙作案") {
    crimeTypeScope = raw.crime_type;
  }

  const extraFields: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(raw)) {
    if (!MANAGED_CONDITION_KEYS.has(k)) {
      extraFields[k] = v;
    }
  }
  return { foreignScope, custodyScope, crimeTypeScope, extraFields };
}

function buildConditionMatch(
  foreignScope: ForeignScope,
  custodyScope: CustodyScope,
  crimeTypeScope: CrimeTypeScope,
  extraFields: Record<string, unknown>
): Record<string, unknown> {
  const result: Record<string, unknown> = { ...extraFields };
  if (foreignScope === "true") result.is_foreign_related = true;
  else if (foreignScope === "false") result.is_foreign_related = false;

  if (custodyScope === "in_custody") result.custody_status = "在押";
  else if (custodyScope === "not_in_custody") result.custody_status_not = "在押";
  else if (custodyScope === "bail") result.custody_status = "取保候审";
  else if (custodyScope === "residential") result.custody_status = "监视居住";

  if (crimeTypeScope !== "any") result.crime_type = crimeTypeScope;
  return result;
}

function formatConditionTags(cond?: Record<string, unknown> | null): string[] {
  const raw = cond ?? {};
  const tags: string[] = [];
  if (raw.is_foreign_related === true) tags.push("仅涉外案件");
  else if (raw.is_foreign_related === false) tags.push("仅境内案件");

  if (raw.custody_status === "在押") tags.push("仅在押");
  else if (raw.custody_status_not === "在押") tags.push("仅非在押");
  else if (typeof raw.custody_status === "string" && raw.custody_status) {
    tags.push(`羁押状态：${raw.custody_status}`);
  }

  if (typeof raw.crime_type === "string" && raw.crime_type) {
    tags.push(raw.crime_type);
  }
  return tags;
}

export function RuleManager() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const isAdmin = user?.system_role === "admin";

  const [activeTab, setActiveTab] = useState<"rules" | "auto_triage">("rules");
  const [categoryFilter, setCategoryFilter] = useState<CaseCategory | "">("");
  const [searchText, setSearchText] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);
  const [pendingRecalcRule, setPendingRecalcRule] = useState<{
    id: number;
    name: string;
  } | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<DeadlineRule | null>(null);
  const [ruleCode, setRuleCode] = useState("");
  const [name, setName] = useState("");
  const [category, setCategory] = useState<CaseCategory>("civil");
  const [triggerEvent, setTriggerEvent] = useState("");
  const [foreignScope, setForeignScope] = useState<ForeignScope>("any");
  const [custodyScope, setCustodyScope] = useState<CustodyScope>("any");
  const [crimeTypeScope, setCrimeTypeScope] = useState<CrimeTypeScope>("any");
  const [extraConditionFields, setExtraConditionFields] = useState<Record<string, unknown>>({});
  const [durationVal, setDurationVal] = useState(15);
  const [durationUnit, setDurationUnit] = useState<"days" | "months" | "years">("days");
  const [rollMode, setRollMode] = useState<HolidayRollMode>("roll_workday");
  const [riskLevel, setRiskLevel] = useState<RiskLevel>("critical");
  const [legalBasis, setLegalBasis] = useState("");
  const [offsetsText, setOffsetsText] = useState("7,3,1,0");

  const rulesQuery = useQuery({
    queryKey: ["rules", categoryFilter],
    queryFn: () => rulesApi.list({ case_category: categoryFilter || undefined }),
  });

  const policiesQuery = useQuery({
    queryKey: ["auto-triage-policies"],
    queryFn: () => rulesApi.autoTriagePolicies(),
  });

  const filteredRules = useMemo(() => {
    const kw = searchText.trim().toLowerCase();
    return (rulesQuery.data ?? []).filter(
      (r) =>
        !kw ||
        r.name.toLowerCase().includes(kw) ||
        r.rule_code.toLowerCase().includes(kw) ||
        r.trigger_event_type.toLowerCase().includes(kw) ||
        getEventTypeLabel(r.trigger_event_type).toLowerCase().includes(kw) ||
        r.legal_basis.toLowerCase().includes(kw)
    );
  }, [rulesQuery.data, searchText]);

  const applyConditionToForm = (cond?: Record<string, unknown> | null) => {
    const parsed = extractConditionForm(cond);
    setForeignScope(parsed.foreignScope);
    setCustodyScope(parsed.custodyScope);
    setCrimeTypeScope(parsed.crimeTypeScope);
    setExtraConditionFields(parsed.extraFields);
  };

  const openCreate = (seed?: DeadlineRule) => {
    setEditingRule(null);
    setRuleCode(seed ? `${seed.rule_code}_CUSTOM` : "");
    setName(seed ? `${seed.name}（自定义副本）` : "");
    setCategory(seed ? seed.case_category : categoryFilter || "civil");
    setTriggerEvent(seed ? seed.trigger_event_type : "judgment_served");
    applyConditionToForm(seed?.condition_match);
    setDurationVal(seed ? seed.duration_value : 15);
    setDurationUnit(seed ? seed.duration_unit : "days");
    setRollMode(seed ? seed.holiday_roll_mode : "roll_workday");
    setRiskLevel(seed ? seed.risk_level : "high");
    setLegalBasis(seed ? seed.legal_basis : "");
    setOffsetsText(seed ? seed.default_offsets_days.join(",") : "7,3,1,0");
    setModalError(null);
    setModalOpen(true);
  };

  const openEdit = (rule: DeadlineRule) => {
    setEditingRule(rule);
    setRuleCode(rule.rule_code);
    setName(rule.name);
    setCategory(rule.case_category);
    setTriggerEvent(rule.trigger_event_type);
    applyConditionToForm(rule.condition_match);
    setDurationVal(rule.duration_value);
    setDurationUnit(rule.duration_unit);
    setRollMode(rule.holiday_roll_mode);
    setRiskLevel(rule.risk_level);
    setLegalBasis(rule.legal_basis);
    setOffsetsText(rule.default_offsets_days.join(","));
    setModalError(null);
    setModalOpen(true);
  };

  const saveMut = useMutation({
    mutationFn: async () => {
      const conditionMatch = buildConditionMatch(
        foreignScope,
        custodyScope,
        crimeTypeScope,
        extraConditionFields
      );
      const parsedOffsets = offsetsText
        .split(",")
        .map((s) => Number(s.trim()))
        .filter((n) => !Number.isNaN(n) && n >= 0);

      if (editingRule) {
        return rulesApi.update(editingRule.id, {
          name: name.trim(),
          case_category: category,
          trigger_event_type: triggerEvent.trim(),
          condition_match: conditionMatch,
          duration_value: durationVal,
          duration_unit: durationUnit,
          holiday_roll_mode: rollMode,
          legal_basis: legalBasis.trim(),
          risk_level: riskLevel,
          default_offsets_days: parsedOffsets.length > 0 ? parsedOffsets : [7, 3, 1, 0],
        });
      }
      return rulesApi.create({
        rule_code: ruleCode.trim(),
        name: name.trim(),
        case_category: category,
        trigger_event_type: triggerEvent.trim(),
        condition_match: conditionMatch,
        duration_value: durationVal,
        duration_unit: durationUnit,
        holiday_roll_mode: rollMode,
        legal_basis: legalBasis.trim(),
        risk_level: riskLevel,
        default_offsets_days: parsedOffsets.length > 0 ? parsedOffsets : [7, 3, 1, 0],
        is_enabled: true,
      });
    },
    onSuccess: (savedRule) => {
      queryClient.invalidateQueries({ queryKey: ["rules"] });
      setModalOpen(false);
      setModalError(null);
      setErrorMsg(null);
      setFeedback(editingRule ? "规则已更新" : "已新增自定义法定期限规则");
      if (savedRule.is_enabled) {
        setPendingRecalcRule({ id: savedRule.id, name: savedRule.name });
      }
    },
    onError: (err: Error) => setModalError(err.message),
  });

  const toggleMut = useMutation({
    mutationFn: (rule: DeadlineRule) =>
      rulesApi.update(rule.id, { is_enabled: !rule.is_enabled }),
    onSuccess: (updatedRule) => {
      queryClient.invalidateQueries({ queryKey: ["rules"] });
      setErrorMsg(null);
      if (updatedRule.is_enabled) {
        setFeedback(`已启用规则「${updatedRule.name}」`);
        setPendingRecalcRule({ id: updatedRule.id, name: updatedRule.name });
      } else {
        setFeedback(`已停用规则「${updatedRule.name}」`);
        if (pendingRecalcRule?.id === updatedRule.id) {
          setPendingRecalcRule(null);
        }
      }
    },
    onError: (err: Error) => setErrorMsg(err.message),
  });

  const recalculateMut = useMutation({
    mutationFn: (ruleId: number) => rulesApi.recalculate(ruleId),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["cases"] });
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
      setPendingRecalcRule(null);
      setErrorMsg(null);
      setFeedback(
        `已重算规则「${res.rule_code}」：涉及 ${res.affected_cases_count} 件在办案件 / ${res.affected_events_count} 个事件；更新 ${res.updated_deadlines_count} 条、补生 ${res.created_deadlines_count} 条期限（已跳过人工锁定 ${res.skipped_manual_override_count} 条、已办结 ${res.skipped_completed_count} 条）。`
      );
    },
    onError: (err: Error) => setErrorMsg(err.message),
  });

  const deleteMut = useMutation({
    mutationFn: (ruleId: number) => rulesApi.remove(ruleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["rules"] });
      setErrorMsg(null);
      setFeedback("已删除自定义规则");
    },
    onError: (err: Error) => setErrorMsg(err.message),
  });

  const updatePolicyMut = useMutation({
    mutationFn: ({ policyKey, mode }: { policyKey: string; mode: AutoTriagePolicyMode }) =>
      rulesApi.updateAutoTriagePolicy(policyKey, mode),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: ["auto-triage-policies"] });
      setErrorMsg(null);
      setFeedback(
        `已将「${updated.label}」调整为：${
          updated.mode === "auto_archive" ? "默认自动归档/处理" : "人工二次复核"
        }`
      );
    },
    onError: (err: Error) => setErrorMsg(err.message),
  });

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold text-[var(--md-sys-color-on-surface)]">规则与分拣策略</h1>
          <p className="text-xs text-slate-600">
            统一管理全案种法定期限计算规则，以及短信文书与发票的默认自动归档策略。
          </p>
        </div>
        {isAdmin && activeTab === "rules" && (
          <button
            type="button"
            onClick={() => openCreate()}
            className="lt-btn-primary px-5 py-2.5 text-sm"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            <span>新建自定义规则</span>
          </button>
        )}
      </div>

      <div
        role="tablist"
        aria-label="规则管理模块切换"
        className="flex flex-wrap gap-2 border-b border-slate-200 pb-2"
      >
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "rules"}
          onClick={() => setActiveTab("rules")}
          className={`lt-chip px-4 py-1.5 text-xs ${
            activeTab === "rules" ? "lt-chip-active" : ""
          }`}
        >
          <Scale className="h-3.5 w-3.5" aria-hidden="true" />
          <span>法定期限规则库</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "auto_triage"}
          onClick={() => setActiveTab("auto_triage")}
          className={`lt-chip px-4 py-1.5 text-xs ${
            activeTab === "auto_triage" ? "lt-chip-active" : ""
          }`}
        >
          <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
          <span>自动分拣策略</span>
        </button>
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

      {activeTab === "rules" ? (
        <>
          <search className="lt-card p-4 space-y-3">
            <input
              type="search"
              aria-label="搜索规则名称、编码、触发事件或法条依据"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="搜索规则编码、触发事件或法条（如 黄金37天、民诉法、执行异议）..."
              className="lt-input"
            />
            <div className="flex flex-wrap gap-1.5">
              <button
                type="button"
                onClick={() => setCategoryFilter("")}
                className={`lt-chip py-1 ${categoryFilter === "" ? "lt-chip-active" : ""}`}
              >
                全部规则
              </button>
              {(Object.keys(CASE_CATEGORY_META) as CaseCategory[]).map((cat) => (
                <button
                  key={cat}
                  type="button"
                  onClick={() => setCategoryFilter(cat)}
                  className={`lt-chip py-1 ${categoryFilter === cat ? "lt-chip-active" : ""}`}
                >
                  {CASE_CATEGORY_META[cat].label}
                </button>
              ))}
            </div>
          </search>

          {isAdmin && pendingRecalcRule && (
            <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 flex flex-wrap items-center justify-between gap-3">
              <div className="text-xs text-amber-900">
                <p className="font-semibold">
                  规则「{pendingRecalcRule.name}」已变更
                </p>
                <p className="mt-0.5 text-amber-800">
                  可一键重算所有受影响在办案件的未完成期限（自动跳过律师人工锁定及已办结期限）。
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={recalculateMut.isPending}
                  onClick={() => recalculateMut.mutate(pendingRecalcRule.id)}
                  className="lt-btn-primary py-1.5 px-3 text-xs"
                >
                  <RefreshCw
                    className={`h-3.5 w-3.5 ${recalculateMut.isPending ? "animate-spin" : ""}`}
                    aria-hidden="true"
                  />
                  <span>{recalculateMut.isPending ? "重算中..." : "立即重算受影响期限"}</span>
                </button>
                <button
                  type="button"
                  onClick={() => setPendingRecalcRule(null)}
                  className="lt-btn-secondary py-1.5 px-2.5 text-xs"
                >
                  暂不重算
                </button>
              </div>
            </div>
          )}

          <div className="lt-card overflow-hidden">
            {rulesQuery.isLoading ? (
              <p className="py-12 text-center text-sm text-slate-600">正在加载规则库...</p>
            ) : rulesQuery.isError ? (
              <div role="alert" className="m-4 lt-alert-error">
                规则库加载失败，请刷新重试。
              </div>
            ) : filteredRules.length === 0 ? (
              <p className="py-12 text-center text-sm text-slate-600">没有匹配的法定期限规则。</p>
            ) : (
              <div tabIndex={0} role="region" aria-label="法定期限规则表" className="table-wrapper max-h-[650px]">
                <table className="text-left text-xs">
                  <thead className="bg-slate-50 text-slate-700">
                    <tr>
                      <th className="border-b border-slate-200 px-4 py-3 font-semibold">规则名称 & 法条</th>
                      <th className="border-b border-slate-200 px-4 py-3 font-semibold">案种</th>
                      <th className="border-b border-slate-200 px-4 py-3 font-semibold">触发事件与条件</th>
                      <th className="border-b border-slate-200 px-4 py-3 font-semibold">期限 / 顺延模式</th>
                      <th className="border-b border-slate-200 px-4 py-3 font-semibold">风险等级</th>
                      <th className="border-b border-slate-200 px-4 py-3 font-semibold">状态</th>
                      {isAdmin && (
                        <th className="border-b border-slate-200 px-4 py-3 font-semibold text-right">
                          操作
                        </th>
                      )}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {filteredRules.map((rule) => {
                      const conditionTags = formatConditionTags(rule.condition_match);
                      return (
                        <tr
                          key={rule.id}
                          className={!rule.is_enabled ? "bg-slate-50/80" : "hover:bg-slate-50/60"}
                        >
                          <td className="px-4 py-3 max-w-xs">
                            <div className="flex items-center gap-1.5">
                              <Scale className="h-3.5 w-3.5 text-slate-500 shrink-0" aria-hidden="true" />
                              <span className={`font-bold ${rule.is_enabled ? "text-slate-900" : "text-slate-600"}`}>
                                {rule.name}
                              </span>
                            </div>
                            <p className="mt-0.5 font-mono text-[11px] text-slate-600">
                              {rule.rule_code} {rule.is_system_builtin ? "· 预置" : "· 自定义"}
                            </p>
                            <p className="mt-0.5 text-[11px] text-slate-600 line-clamp-1">{rule.legal_basis}</p>
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap">
                            <CategoryBadge category={rule.case_category} />
                          </td>
                          <td className="px-4 py-3">
                            <p className="font-medium text-slate-800">
                              {getEventTypeLabel(rule.trigger_event_type)}
                            </p>
                            <p className="font-mono text-[11px] text-slate-600">{rule.trigger_event_type}</p>
                            {conditionTags.length > 0 && (
                              <div className="mt-1 flex flex-wrap gap-1">
                                {conditionTags.map((tag) => (
                                  <span
                                    key={tag}
                                    className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700"
                                  >
                                    {tag}
                                  </span>
                                ))}
                              </div>
                            )}
                          </td>
                          <td className="px-4 py-3">
                            <p className="font-semibold text-slate-900 tabular-nums">
                              {rule.duration_value}{" "}
                              {rule.duration_unit === "days"
                                ? "天"
                                : rule.duration_unit === "months"
                                ? "个月"
                                : "年"}
                            </p>
                            <p className="text-[11px] text-slate-600">
                              {HOLIDAY_ROLL_LABELS[rule.holiday_roll_mode]}
                            </p>
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap">
                            <RiskBadge risk={rule.risk_level} />
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap">
                            <span
                              className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                                rule.is_enabled
                                  ? "bg-emerald-100 text-emerald-800"
                                  : "bg-slate-200 text-slate-700"
                              }`}
                            >
                              {rule.is_enabled ? "启用中" : "已停用"}
                            </span>
                          </td>
                          {isAdmin && (
                            <td className="px-4 py-3 whitespace-nowrap text-right space-x-1.5">
                              {rule.is_enabled && (
                                <button
                                  type="button"
                                  disabled={recalculateMut.isPending}
                                  onClick={() => recalculateMut.mutate(rule.id)}
                                  title="重算受影响在办案件的未完成期限"
                                  className="lt-btn-secondary py-1 px-2.5 text-[11px]"
                                >
                                  <RefreshCw className="h-3 w-3" aria-hidden="true" />
                                  <span>重算</span>
                                </button>
                              )}
                              <button
                                type="button"
                                onClick={() => openCreate(rule)}
                                title="复制为新规则"
                                className="lt-btn-secondary py-1 px-2.5 text-[11px]"
                              >
                                <Copy className="h-3 w-3" aria-hidden="true" />
                                <span>复制</span>
                              </button>
                              <button
                                type="button"
                                onClick={() => openEdit(rule)}
                                className="lt-btn-secondary py-1 px-2.5 text-[11px]"
                              >
                                <Edit3 className="h-3 w-3" aria-hidden="true" />
                                <span>编辑</span>
                              </button>
                              <button
                                type="button"
                                disabled={toggleMut.isPending}
                                onClick={() => toggleMut.mutate(rule)}
                                className="lt-btn-secondary py-1 px-2.5 text-[11px]"
                              >
                                {rule.is_enabled ? "停用" : "启用"}
                              </button>
                              {!rule.is_system_builtin && (
                                <button
                                  type="button"
                                  aria-label={`删除规则 ${rule.name}`}
                                  title="删除自定义规则"
                                  onClick={() => {
                                    if (window.confirm(`确认删除自定义规则「${rule.name}」？`)) {
                                      deleteMut.mutate(rule.id);
                                    }
                                  }}
                                  className="lt-btn-danger py-1 px-2 text-[11px]"
                                >
                                  <Trash2 className="h-3 w-3" aria-hidden="true" />
                                </button>
                              )}
                            </td>
                          )}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      ) : (
        <div className="space-y-4">
          <div className="rounded-xl border border-indigo-200 bg-indigo-50/70 p-4 flex items-start gap-3">
            <ShieldCheck className="h-5 w-5 text-indigo-700 shrink-0 mt-0.5" aria-hidden="true" />
            <div className="text-xs text-indigo-950 space-y-1">
              <p className="font-semibold">默认自动归档，异常或敏感类别按需人工二次复核</p>
              <p className="text-indigo-900">
                为尽量减少律师日常操作负担，所有类别默认采用「自动归档/处理（auto_archive）」——
                仅在未精确匹配案号、缺乏对应期限规则或需要人机核验时才提醒人工确认。
                如团队希望某类文书或发票强制二次把关，可在此切换为「人工二次复核」。
              </p>
            </div>
          </div>

          <div className="lt-card divide-y divide-slate-100">
            {policiesQuery.isLoading ? (
              <p className="py-12 text-center text-sm text-slate-600">正在加载自动分拣策略...</p>
            ) : policiesQuery.isError ? (
              <div role="alert" className="m-4 lt-alert-error">
                自动分拣策略加载失败，请刷新重试。
              </div>
            ) : (
              (policiesQuery.data ?? []).map((policy: AutoTriagePolicy) => {
                const isAuto = policy.mode === "auto_archive";
                return (
                  <div
                    key={policy.policy_key}
                    className="p-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4"
                  >
                    <div className="space-y-1 max-w-2xl">
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-sm font-bold text-slate-900">{policy.label}</h2>
                        <span
                          className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                            isAuto
                              ? "bg-emerald-100 text-emerald-800"
                              : "bg-amber-100 text-amber-800"
                          }`}
                        >
                          {isAuto ? "默认自动归档" : "人工二次复核"}
                        </span>
                      </div>
                      <p className="text-xs text-slate-600 leading-relaxed">{policy.description}</p>
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0">
                      <button
                        type="button"
                        disabled={!isAdmin || updatePolicyMut.isPending}
                        onClick={() =>
                          updatePolicyMut.mutate({
                            policyKey: policy.policy_key,
                            mode: "auto_archive",
                          })
                        }
                        className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
                          isAuto
                            ? "border-emerald-600 bg-emerald-600 text-white"
                            : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                        } disabled:opacity-60`}
                      >
                        自动归档
                      </button>
                      <button
                        type="button"
                        disabled={!isAdmin || updatePolicyMut.isPending}
                        onClick={() =>
                          updatePolicyMut.mutate({
                            policyKey: policy.policy_key,
                            mode: "require_confirm",
                          })
                        }
                        className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
                          !isAuto
                            ? "border-amber-600 bg-amber-600 text-white"
                            : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                        } disabled:opacity-60`}
                      >
                        人工复核
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
          {!isAdmin && (
            <p className="text-[11px] text-slate-500">
              提示：当前为只读视图，仅管理员（合伙人）可调整团队自动分拣策略。
            </p>
          )}
        </div>
      )}

      <ModalDialog
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editingRule ? "编辑法定期限规则" : "新建自定义法定期限规则"}
      >
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            saveMut.mutate();
          }}
          className="space-y-3"
        >
          {modalError && (
            <div role="alert" className="lt-alert-error">
              {modalError}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="rl-code" className="block text-xs font-semibold text-slate-700 mb-1">
                规则唯一编码 *
              </label>
              <input
                id="rl-code"
                type="text"
                required
                autoFocus={!editingRule}
                disabled={Boolean(editingRule)}
                value={ruleCode}
                onChange={(e) => setRuleCode(e.target.value)}
                placeholder="例如 CUSTOM_ARB_SUPPLEMENT"
                className="lt-input disabled:bg-slate-100"
              />
            </div>
            <div>
              <label htmlFor="rl-cat" className="block text-xs font-semibold text-slate-700 mb-1">
                适用案种 *
              </label>
              <select
                id="rl-cat"
                value={category}
                onChange={(e) => setCategory(e.target.value as CaseCategory)}
                className="lt-input"
              >
                {(Object.keys(CASE_CATEGORY_META) as CaseCategory[]).map((cat) => (
                  <option key={cat} value={cat}>
                    {CASE_CATEGORY_META[cat].label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label htmlFor="rl-name" className="block text-xs font-semibold text-slate-700 mb-1">
              规则名称 *
            </label>
            <input
              id="rl-name"
              type="text"
              required
              autoFocus={Boolean(editingRule)}
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="lt-input"
            />
          </div>

          <div>
            <label htmlFor="rl-trigger" className="block text-xs font-semibold text-slate-700 mb-1">
              触发事件 *
            </label>
            <input
              id="rl-trigger"
              type="text"
              list="rl-trigger-presets"
              required
              value={triggerEvent}
              onChange={(e) => setTriggerEvent(e.target.value)}
              placeholder="例如 judgment_served 或选择下方常用事件"
              className="lt-input font-mono"
            />
            <datalist id="rl-trigger-presets">
              {EVENT_TYPE_PRESETS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </datalist>
            <p className="mt-1 text-[11px] text-slate-600">
              当前对应事件：{getEventTypeLabel(triggerEvent || "未填写")}。
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label htmlFor="rl-val" className="block text-xs font-semibold text-slate-700 mb-1">
                期限数值 *
              </label>
              <input
                id="rl-val"
                type="number"
                min={0}
                required
                value={durationVal}
                onChange={(e) => setDurationVal(Number(e.target.value))}
                className="lt-input"
              />
            </div>
            <div>
              <label htmlFor="rl-unit" className="block text-xs font-semibold text-slate-700 mb-1">
                单位
              </label>
              <select
                id="rl-unit"
                value={durationUnit}
                onChange={(e) => setDurationUnit(e.target.value as "days" | "months" | "years")}
                className="lt-input"
              >
                <option value="days">天</option>
                <option value="months">月</option>
                <option value="years">年</option>
              </select>
            </div>
            <div>
              <label htmlFor="rl-risk" className="block text-xs font-semibold text-slate-700 mb-1">
                风险等级
              </label>
              <select
                id="rl-risk"
                value={riskLevel}
                onChange={(e) => setRiskLevel(e.target.value as RiskLevel)}
                className="lt-input"
              >
                <option value="critical">致命死线</option>
                <option value="high">刚性程序</option>
                <option value="normal">常规节点</option>
                <option value="low">内部待办</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="rl-roll" className="block text-xs font-semibold text-slate-700 mb-1">
                节假日顺延模式
              </label>
              <select
                id="rl-roll"
                value={rollMode}
                onChange={(e) => setRollMode(e.target.value as HolidayRollMode)}
                className="lt-input"
              >
                <option value="roll_workday">遇节假日顺延至首个工作日</option>
                <option value="no_roll_custody">刑事在押不因节假日顺延</option>
                <option value="calendar_exact">自然日精确到期</option>
              </select>
            </div>
            <div>
              <label htmlFor="rl-offsets" className="block text-xs font-semibold text-slate-700 mb-1">
                提前提醒天数（逗号分隔）
              </label>
              <input
                id="rl-offsets"
                type="text"
                value={offsetsText}
                onChange={(e) => setOffsetsText(e.target.value)}
                placeholder="7,3,1,0"
                className="lt-input"
              />
            </div>
          </div>

          <div>
            <label htmlFor="rl-basis" className="block text-xs font-semibold text-slate-700 mb-1">
              法律条文依据 *
            </label>
            <input
              id="rl-basis"
              type="text"
              required
              value={legalBasis}
              onChange={(e) => setLegalBasis(e.target.value)}
              placeholder="例如 《民事诉讼法》第171条"
              className="lt-input"
            />
          </div>

          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-2.5">
            <p className="text-xs font-semibold text-slate-800">适用案件属性限定（可选）</p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
              <div>
                <label htmlFor="rl-foreign" className="block text-[11px] font-medium text-slate-600 mb-1">
                  涉外属性
                </label>
                <select
                  id="rl-foreign"
                  value={foreignScope}
                  onChange={(e) => setForeignScope(e.target.value as ForeignScope)}
                  className="lt-input text-xs"
                >
                  <option value="any">不限（全部案件）</option>
                  <option value="false">仅境内案件</option>
                  <option value="true">仅涉外案件</option>
                </select>
              </div>
              <div>
                <label htmlFor="rl-custody" className="block text-[11px] font-medium text-slate-600 mb-1">
                  羁押状态
                </label>
                <select
                  id="rl-custody"
                  value={custodyScope}
                  onChange={(e) => setCustodyScope(e.target.value as CustodyScope)}
                  className="lt-input text-xs"
                >
                  <option value="any">不限</option>
                  <option value="in_custody">仅在押</option>
                  <option value="not_in_custody">仅非在押</option>
                  <option value="bail">仅取保候审</option>
                  <option value="residential">仅监视居住</option>
                </select>
              </div>
              <div>
                <label htmlFor="rl-crime" className="block text-[11px] font-medium text-slate-600 mb-1">
                  作案/犯罪类型
                </label>
                <select
                  id="rl-crime"
                  value={crimeTypeScope}
                  onChange={(e) => setCrimeTypeScope(e.target.value as CrimeTypeScope)}
                  className="lt-input text-xs"
                >
                  <option value="any">不限</option>
                  <option value="一般犯罪">仅一般犯罪</option>
                  <option value="流窜/多次/结伙作案">流窜/多次/结伙作案</option>
                </select>
              </div>
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={() => setModalOpen(false)} className="lt-btn-secondary">
              取消
            </button>
            <button type="submit" disabled={saveMut.isPending} className="lt-btn-primary">
              {saveMut.isPending ? "保存中..." : "保存规则"}
            </button>
          </div>
        </form>
      </ModalDialog>
    </div>
  );
}
