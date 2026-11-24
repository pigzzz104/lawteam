import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Edit3, Plus, RefreshCw, Scale, Trash2 } from "lucide-react";
import { rulesApi } from "@/api/client";
import { ModalDialog } from "@/components/ModalDialog";
import { CategoryBadge, RiskBadge } from "@/components/StatusBadges";
import { ConfirmDialog, MdSelect, MdSwitch } from "@/components/ui";
import { useAuth } from "@/context/AuthContext";
import {
  CASE_CATEGORY_META,
  EVENT_TYPE_PRESETS,
  HOLIDAY_ROLL_LABELS,
  RISK_LEVEL_OPTIONS,
  getEventTypeLabel,
} from "@/lib/domain";
import type {
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

function parseOffsetsText(text: string): number[] {
  const parsed = text
    .split(/[，,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => Number(s))
    .filter((n) => Number.isInteger(n) && n >= 0 && n <= 365);
  return Array.from(new Set(parsed)).sort((a, b) => b - a);
}

function nextCopyName(baseName: string, existingNames: Set<string>): string {
  const stripped = baseName.replace(/（副本(?:\s*\d+)?）$/, "").trim();
  let idx = 1;
  while (existingNames.has(`${stripped}（副本 ${idx}）`)) {
    idx += 1;
  }
  return `${stripped}（副本 ${idx}）`;
}

export function RuleManager() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const isAdmin = user?.system_role === "admin";

  const [categoryFilter, setCategoryFilter] = useState<CaseCategory | "">("");
  const [searchText, setSearchText] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);
  const [pendingRecalcRule, setPendingRecalcRule] = useState<{
    id: number;
    name: string;
  } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DeadlineRule | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<DeadlineRule | null>(null);
  const [name, setName] = useState("");
  const [category, setCategory] = useState<CaseCategory>("civil");
  const [triggerEvent, setTriggerEvent] = useState("judgment_served");
  const [foreignScope, setForeignScope] = useState<ForeignScope>("any");
  const [custodyScope, setCustodyScope] = useState<CustodyScope>("any");
  const [crimeTypeScope, setCrimeTypeScope] = useState<CrimeTypeScope>("any");
  const [extraConditionFields, setExtraConditionFields] = useState<Record<string, unknown>>({});
  const [durationVal, setDurationVal] = useState(15);
  const [durationUnit, setDurationUnit] = useState<"days" | "months" | "years">("days");
  const [rollMode, setRollMode] = useState<HolidayRollMode>("roll_workday");
  const [riskLevel, setRiskLevel] = useState<RiskLevel>("critical");
  const [legalBasis, setLegalBasis] = useState("");
  const [offsetsText, setOffsetsText] = useState("7, 3, 1, 0");

  const rulesQuery = useQuery({
    queryKey: ["rules", categoryFilter],
    queryFn: () => rulesApi.list({ case_category: categoryFilter || undefined }),
  });

  const filteredRules = useMemo(() => {
    const kw = searchText.trim().toLowerCase();
    return (rulesQuery.data ?? []).filter(
      (r) =>
        !kw ||
        r.name.toLowerCase().includes(kw) ||
        getEventTypeLabel(r.trigger_event_type).toLowerCase().includes(kw) ||
        r.legal_basis.toLowerCase().includes(kw)
    );
  }, [rulesQuery.data, searchText]);

  const categoryEvents = useMemo(() => {
    const matched = EVENT_TYPE_PRESETS.filter(
      (p) => !p.categories || p.categories.includes(category)
    );
    return matched.length > 0 ? matched : EVENT_TYPE_PRESETS;
  }, [category]);

  const applyConditionToForm = (cond?: Record<string, unknown> | null) => {
    const parsed = extractConditionForm(cond);
    setForeignScope(parsed.foreignScope);
    setCustodyScope(parsed.custodyScope);
    setCrimeTypeScope(parsed.crimeTypeScope);
    setExtraConditionFields(parsed.extraFields);
  };

  const openCreate = (seed?: DeadlineRule) => {
    setEditingRule(null);
    const existingNames = new Set((rulesQuery.data ?? []).map((r) => r.name));
    const targetCat: CaseCategory = seed ? seed.case_category : categoryFilter || "civil";
    const defaultEv =
      EVENT_TYPE_PRESETS.find((p) => !p.categories || p.categories.includes(targetCat))?.value ??
      "judgment_served";

    setName(seed ? nextCopyName(seed.name, existingNames) : "");
    setCategory(targetCat);
    setTriggerEvent(seed ? seed.trigger_event_type : defaultEv);
    applyConditionToForm(seed?.condition_match);
    setDurationVal(seed ? seed.duration_value : 15);
    setDurationUnit(seed ? seed.duration_unit : "days");
    setRollMode(seed ? seed.holiday_roll_mode : "roll_workday");
    setRiskLevel(seed ? seed.risk_level : "high");
    setLegalBasis(seed ? seed.legal_basis : "");
    setOffsetsText(seed ? seed.default_offsets_days.join(", ") : "7, 3, 1, 0");
    setModalError(null);
    setModalOpen(true);
  };

  const openEdit = (rule: DeadlineRule) => {
    setEditingRule(rule);
    setName(rule.name);
    setCategory(rule.case_category);
    setTriggerEvent(rule.trigger_event_type);
    applyConditionToForm(rule.condition_match);
    setDurationVal(rule.duration_value);
    setDurationUnit(rule.duration_unit);
    setRollMode(rule.holiday_roll_mode);
    setRiskLevel(rule.risk_level);
    setLegalBasis(rule.legal_basis);
    setOffsetsText(rule.default_offsets_days.join(", "));
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
      const parsedOffsets = parseOffsetsText(offsetsText);
      const finalOffsets = parsedOffsets.length > 0 ? parsedOffsets : [7, 3, 1, 0];

      if (editingRule) {
        return rulesApi.update(editingRule.id, {
          name: name.trim(),
          case_category: category,
          trigger_event_type: triggerEvent,
          condition_match: conditionMatch,
          duration_value: durationVal,
          duration_unit: durationUnit,
          holiday_roll_mode: rollMode,
          legal_basis: legalBasis.trim(),
          risk_level: riskLevel,
          default_offsets_days: finalOffsets,
        });
      }
      const generatedCode = `CUSTOM_${category.toUpperCase()}_${Date.now().toString(36).toUpperCase()}`;
      return rulesApi.create({
        rule_code: generatedCode,
        name: name.trim(),
        case_category: category,
        trigger_event_type: triggerEvent,
        condition_match: conditionMatch,
        duration_value: durationVal,
        duration_unit: durationUnit,
        holiday_roll_mode: rollMode,
        legal_basis: legalBasis.trim(),
        risk_level: riskLevel,
        default_offsets_days: finalOffsets,
        is_enabled: true,
      });
    },
    onSuccess: (savedRule) => {
      queryClient.invalidateQueries({ queryKey: ["rules"] });
      setModalOpen(false);
      setModalError(null);
      setErrorMsg(null);
      setFeedback(editingRule ? `规则「${savedRule.name}」已保存` : `已新增规则「${savedRule.name}」`);
      if (savedRule.is_enabled) {
        setPendingRecalcRule({ id: savedRule.id, name: savedRule.name });
      }
    },
    onError: (err: Error) => setModalError(err.message),
  });

  const toggleMut = useMutation({
    mutationFn: (rule: DeadlineRule) => rulesApi.update(rule.id, { is_enabled: !rule.is_enabled }),
    onSuccess: (updatedRule) => {
      queryClient.invalidateQueries({ queryKey: ["rules"] });
      setErrorMsg(null);
      if (updatedRule.is_enabled) {
        setFeedback(`已启用规则「${updatedRule.name}」`);
      } else {
        setFeedback(`已停用规则「${updatedRule.name}」`);
      }
      setPendingRecalcRule({ id: updatedRule.id, name: updatedRule.name });
    },
    onError: (err: Error) => setErrorMsg(err.message),
  });

  const recalculateMut = useMutation({
    mutationFn: (rule: { id: number; name: string }) => rulesApi.recalculate(rule.id),
    onSuccess: (res, vars) => {
      queryClient.invalidateQueries({ queryKey: ["cases"] });
      queryClient.invalidateQueries({ queryKey: ["case-detail"] });
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
      setPendingRecalcRule(null);
      setErrorMsg(null);
      setFeedback(
        `已重算「${vars.name}」：涉及 ${res.affected_cases_count} 件在办案件；更新 ${res.updated_deadlines_count} 条、补生 ${res.created_deadlines_count} 条期限（已保留人工调整 ${res.skipped_manual_override_count} 条、已办结 ${res.skipped_completed_count} 条）。`
      );
    },
    onError: (err: Error) => setErrorMsg(err.message),
  });

  const deleteMut = useMutation({
    mutationFn: (ruleId: number) => rulesApi.remove(ruleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["rules"] });
      setDeleteTarget(null);
      setErrorMsg(null);
      setFeedback("已删除自定义规则");
    },
    onError: (err: Error) => {
      setDeleteTarget(null);
      setErrorMsg(err.message);
    },
  });

  const previewOffsets = parseOffsetsText(offsetsText);
  const unitLabel = durationUnit === "days" ? "天" : durationUnit === "months" ? "个月" : "年";

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-[var(--md-sys-color-on-surface)]">期限规则</h1>
        {isAdmin && (
          <button type="button" onClick={() => openCreate()} className="lt-btn-primary px-5 py-2.5 text-sm">
            <Plus className="h-4 w-4" aria-hidden="true" />
            <span>新建自定义规则</span>
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

      <search className="lt-card p-4 space-y-3">
        <input
          type="search"
          aria-label="搜索规则名称、触发事件或法条依据"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          placeholder="搜索规则名称、触发事件或法律依据..."
          className="lt-input"
        />
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={() => setCategoryFilter("")}
            className={`lt-chip py-1 ${categoryFilter === "" ? "lt-chip-active" : ""}`}
          >
            全部案种
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
        <div className="rounded-[var(--md-sys-shape-corner-md)] border border-[#f6d48b] bg-[#fef1d6] px-4 py-3 flex flex-wrap items-center justify-between gap-3">
          <div className="text-xs text-[#7a4b00]">
            <p className="font-semibold">规则「{pendingRecalcRule.name}」已变更</p>
            <p className="mt-0.5">可一键重算受影响在办案件的未办结期限（保留律师手动锁定记录）。</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={recalculateMut.isPending}
              onClick={() => recalculateMut.mutate(pendingRecalcRule)}
              className="lt-btn-primary py-1.5 px-3 text-xs"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${recalculateMut.isPending ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
              <span>{recalculateMut.isPending ? "重算中..." : "立即重算在办案件"}</span>
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
          <p className="py-12 text-center text-sm text-[var(--md-sys-color-on-surface-variant)]">
            正在加载规则库...
          </p>
        ) : rulesQuery.isError ? (
          <div role="alert" className="m-4 lt-alert-error">
            规则库加载失败，请刷新重试。
          </div>
        ) : filteredRules.length === 0 ? (
          <p className="py-12 text-center text-sm text-[var(--md-sys-color-on-surface-variant)]">
            没有匹配的法定期限规则。
          </p>
        ) : (
          <div tabIndex={0} role="region" aria-label="法定期限规则表" className="table-wrapper max-h-[650px]">
            <table className="text-left text-xs">
              <thead className="bg-[var(--md-sys-color-surface-container-low)] text-[var(--md-sys-color-on-surface-variant)]">
                <tr>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">
                    规则名称 & 依据
                  </th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">
                    案种
                  </th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">
                    触发事件与条件
                  </th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">
                    期限与顺延
                  </th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">
                    重要程度
                  </th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">
                    启用状态
                  </th>
                  {isAdmin && (
                    <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold text-right">
                      操作
                    </th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {filteredRules.map((rule) => {
                  const conditionTags = formatConditionTags(rule.condition_match);
                  return (
                    <tr
                      key={rule.id}
                      className={
                        !rule.is_enabled
                          ? "bg-surface-low/60 opacity-75"
                          : "hover:bg-surface-low/50"
                      }
                    >
                      <td className="px-4 py-3 max-w-xs">
                        <div className="flex items-center gap-1.5">
                          <Scale className="h-3.5 w-3.5 text-[var(--md-sys-color-primary)] shrink-0" aria-hidden="true" />
                          <span className="font-bold text-[var(--md-sys-color-on-surface)]">{rule.name}</span>
                        </div>
                        <p className="mt-0.5 text-[11px] text-[var(--md-sys-color-on-surface-variant)] line-clamp-1">
                          {rule.is_system_builtin ? "系统内置" : "自定义"} · {rule.legal_basis}
                        </p>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <CategoryBadge category={rule.case_category} />
                      </td>
                      <td className="px-4 py-3">
                        <p className="font-medium text-[var(--md-sys-color-on-surface)]">
                          {getEventTypeLabel(rule.trigger_event_type)}
                        </p>
                        {conditionTags.length > 0 && (
                          <div className="mt-1 flex flex-wrap gap-1">
                            {conditionTags.map((tag) => (
                              <span
                                key={tag}
                                className="rounded-full bg-[var(--md-sys-color-secondary-container)] px-2 py-0.5 text-[10px] font-medium text-[var(--md-sys-color-on-secondary-container)]"
                              >
                                {tag}
                              </span>
                            ))}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <p className="font-semibold text-[var(--md-sys-color-on-surface)] tabular-nums">
                          {rule.duration_value}{" "}
                          {rule.duration_unit === "days"
                            ? "天"
                            : rule.duration_unit === "months"
                              ? "个月"
                              : "年"}
                        </p>
                        <p className="text-[11px] text-[var(--md-sys-color-on-surface-variant)]">
                          {HOLIDAY_ROLL_LABELS[rule.holiday_roll_mode]}
                        </p>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <RiskBadge risk={rule.risk_level} />
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        {isAdmin ? (
                          <MdSwitch
                            checked={rule.is_enabled}
                            disabled={toggleMut.isPending}
                            onChange={() => toggleMut.mutate(rule)}
                            ariaLabel={`${rule.is_enabled ? "停用" : "启用"}规则 ${rule.name}`}
                          />
                        ) : (
                          <span
                            className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${
                              rule.is_enabled
                                ? "bg-[var(--md-sys-color-tertiary-container)] text-[var(--md-sys-color-on-tertiary-container)]"
                                : "bg-[var(--md-sys-color-surface-container-high)] text-[var(--md-sys-color-on-surface-variant)]"
                            }`}
                          >
                            {rule.is_enabled ? "启用中" : "已停用"}
                          </span>
                        )}
                      </td>
                      {isAdmin && (
                        <td className="px-4 py-3 whitespace-nowrap text-right space-x-1.5">
                          {rule.is_enabled && (
                            <button
                              type="button"
                              disabled={recalculateMut.isPending}
                              onClick={() => recalculateMut.mutate({ id: rule.id, name: rule.name })}
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
                          {!rule.is_system_builtin && (
                            <button
                              type="button"
                              aria-label={`删除规则 ${rule.name}`}
                              onClick={() => setDeleteTarget(rule)}
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

      {/* Create / Edit Rule Modal */}
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
          className="space-y-3.5"
        >
          {modalError && (
            <div role="alert" className="lt-alert-error">
              {modalError}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="rl-name" className="block text-xs font-semibold mb-1">
                规则名称 *
              </label>
              <input
                id="rl-name"
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="例如 涉外仲裁补充举证期"
                className="lt-input"
              />
            </div>
            <div>
              <label htmlFor="rl-cat" className="block text-xs font-semibold mb-1">
                适用案种 *
              </label>
              <MdSelect
                id="rl-cat"
                value={category}
                onChange={(e) => {
                  const nextCat = e.target.value as CaseCategory;
                  setCategory(nextCat);
                  if (!editingRule) {
                    const nextEvs = EVENT_TYPE_PRESETS.filter(
                      (p) => !p.categories || p.categories.includes(nextCat)
                    );
                    if (nextEvs.length > 0 && !nextEvs.some((p) => p.value === triggerEvent)) {
                      setTriggerEvent(nextEvs[0].value);
                    }
                  }
                }}
              >
                {(Object.keys(CASE_CATEGORY_META) as CaseCategory[]).map((cat) => (
                  <option key={cat} value={cat}>
                    {CASE_CATEGORY_META[cat].label}
                  </option>
                ))}
              </MdSelect>
            </div>
          </div>

          <div>
            <label htmlFor="rl-trigger" className="block text-xs font-semibold mb-1">
              触发程序事件 *
            </label>
            <MdSelect
              id="rl-trigger"
              value={triggerEvent}
              onChange={(e) => setTriggerEvent(e.target.value)}
            >
              {!categoryEvents.some((p) => p.value === triggerEvent) && triggerEvent && (
                <option value={triggerEvent}>{getEventTypeLabel(triggerEvent)}</option>
              )}
              {categoryEvents.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </MdSelect>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label htmlFor="rl-val" className="block text-xs font-semibold mb-1">
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
              <label htmlFor="rl-unit" className="block text-xs font-semibold mb-1">
                单位
              </label>
              <MdSelect
                id="rl-unit"
                value={durationUnit}
                onChange={(e) => setDurationUnit(e.target.value as "days" | "months" | "years")}
              >
                <option value="days">天</option>
                <option value="months">个月</option>
                <option value="years">年</option>
              </MdSelect>
            </div>
            <div>
              <label htmlFor="rl-risk" className="block text-xs font-semibold mb-1">
                重要程度
              </label>
              <MdSelect
                id="rl-risk"
                value={riskLevel}
                onChange={(e) => setRiskLevel(e.target.value as RiskLevel)}
              >
                {RISK_LEVEL_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </MdSelect>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="rl-roll" className="block text-xs font-semibold mb-1">
                节假日顺延模式
              </label>
              <MdSelect
                id="rl-roll"
                value={rollMode}
                onChange={(e) => setRollMode(e.target.value as HolidayRollMode)}
              >
                <option value="roll_workday">遇节假日顺延至首个工作日</option>
                <option value="no_roll_custody">刑事在押不因节假日顺延</option>
                <option value="calendar_exact">自然日精确到期</option>
              </MdSelect>
            </div>
            <div>
              <label htmlFor="rl-offsets" className="block text-xs font-semibold mb-1">
                提前提醒天数（逗号或空格分隔）
              </label>
              <input
                id="rl-offsets"
                type="text"
                value={offsetsText}
                onChange={(e) => setOffsetsText(e.target.value)}
                placeholder="7, 3, 1, 0"
                className="lt-input"
              />
            </div>
          </div>

          <div>
            <label htmlFor="rl-basis" className="block text-xs font-semibold mb-1">
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

          <div className="lt-surface-tonal p-3 space-y-2.5">
            <p className="text-xs font-semibold">适用案件属性限定（可选）</p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
              <div>
                <label htmlFor="rl-foreign" className="block text-[11px] font-medium mb-1">
                  涉外属性
                </label>
                <MdSelect
                  id="rl-foreign"
                  value={foreignScope}
                  onChange={(e) => setForeignScope(e.target.value as ForeignScope)}
                  className="text-xs py-1.5"
                >
                  <option value="any">不限（全部案件）</option>
                  <option value="false">仅境内案件</option>
                  <option value="true">仅涉外案件</option>
                </MdSelect>
              </div>
              <div>
                <label htmlFor="rl-custody" className="block text-[11px] font-medium mb-1">
                  羁押状态
                </label>
                <MdSelect
                  id="rl-custody"
                  value={custodyScope}
                  onChange={(e) => setCustodyScope(e.target.value as CustodyScope)}
                  className="text-xs py-1.5"
                >
                  <option value="any">不限</option>
                  <option value="in_custody">仅在押</option>
                  <option value="not_in_custody">仅非在押</option>
                  <option value="bail">仅取保候审</option>
                  <option value="residential">仅监视居住</option>
                </MdSelect>
              </div>
              <div>
                <label htmlFor="rl-crime" className="block text-[11px] font-medium mb-1">
                  作案/犯罪类型
                </label>
                <MdSelect
                  id="rl-crime"
                  value={crimeTypeScope}
                  onChange={(e) => setCrimeTypeScope(e.target.value as CrimeTypeScope)}
                  className="text-xs py-1.5"
                >
                  <option value="any">不限</option>
                  <option value="一般犯罪">仅一般犯罪</option>
                  <option value="流窜/多次/结伙作案">流窜/多次/结伙作案</option>
                </MdSelect>
              </div>
            </div>
          </div>

          <div className="rounded-[var(--md-sys-shape-corner-md)] border border-primary/25 bg-primary-container/35 px-3.5 py-2.5 text-xs text-[var(--md-sys-color-on-primary-container)]">
            <span className="font-semibold">预期效果预览：</span>
            <span>
              当「{CASE_CATEGORY_META[category].label}」发生「{getEventTypeLabel(triggerEvent)}」后，自动起算{" "}
              {durationVal} {unitLabel}（{HOLIDAY_ROLL_LABELS[rollMode]}），提前{" "}
              {(previewOffsets.length > 0 ? previewOffsets : [7, 3, 1, 0]).join("、")} 天提醒。
            </span>
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

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget) deleteMut.mutate(deleteTarget.id);
        }}
        loading={deleteMut.isPending}
        title="删除自定义规则"
        description={`确定永久删除自定义规则「${deleteTarget?.name ?? ""}」吗？删除后不会影响已生成的历史期限。`}
        confirmLabel="确认删除"
        tone="danger"
      />
    </div>
  );
}
