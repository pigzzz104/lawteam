import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Edit3, Plus, Scale, Trash2 } from "lucide-react";
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
import type { CaseCategory, DeadlineRule, HolidayRollMode, RiskLevel } from "@/types/api";

export function RuleManager() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const isAdmin = user?.system_role === "admin";

  const [categoryFilter, setCategoryFilter] = useState<CaseCategory | "">("");
  const [searchText, setSearchText] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<DeadlineRule | null>(null);
  const [ruleCode, setRuleCode] = useState("");
  const [name, setName] = useState("");
  const [category, setCategory] = useState<CaseCategory>("civil");
  const [triggerEvent, setTriggerEvent] = useState("");
  const [conditionJson, setConditionJson] = useState("");
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

  const openCreate = (seed?: DeadlineRule) => {
    setEditingRule(null);
    setRuleCode(seed ? `${seed.rule_code}_CUSTOM` : "");
    setName(seed ? `${seed.name}（自定义副本）` : "");
    setCategory(seed ? seed.case_category : categoryFilter || "civil");
    setTriggerEvent(seed ? seed.trigger_event_type : "judgment_served");
    setConditionJson(
      seed && Object.keys(seed.condition_match || {}).length > 0
        ? JSON.stringify(seed.condition_match)
        : ""
    );
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
    setConditionJson(
      Object.keys(rule.condition_match || {}).length > 0
        ? JSON.stringify(rule.condition_match)
        : ""
    );
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
      let conditionMatch: Record<string, unknown> = {};
      if (conditionJson.trim()) {
        try {
          const parsed = JSON.parse(conditionJson.trim());
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("匹配条件必须是 JSON 对象");
          }
          conditionMatch = parsed as Record<string, unknown>;
        } catch (err) {
          if (err instanceof SyntaxError) {
            throw new Error('匹配条件 JSON 格式不正确，例如 {"is_foreign_related": true}');
          }
          throw err;
        }
      }
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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["rules"] });
      setModalOpen(false);
      setModalError(null);
      setErrorMsg(null);
      setFeedback(editingRule ? "规则已更新" : "已新增自定义法定期限规则");
    },
    onError: (err: Error) => setModalError(err.message),
  });

  const toggleMut = useMutation({
    mutationFn: (rule: DeadlineRule) =>
      rulesApi.update(rule.id, { is_enabled: !rule.is_enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["rules"] });
      setErrorMsg(null);
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

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-[var(--md-sys-color-on-surface)]">期限规则</h1>
        {isAdmin && (
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

      <search className="lt-card p-4 space-y-3">
        <input
          type="search"
          aria-label="搜索规则名称、编码、触发事件或法条依据"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          placeholder="搜索规则编码、触发事件或法条（如 黄金37天、民诉法）..."
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
                  <th className="border-b border-slate-200 px-4 py-3 font-semibold">触发事件</th>
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
                {filteredRules.map((rule) => (
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
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

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
              触发事件标识（英文 snake_case）*
            </label>
            <input
              id="rl-trigger"
              type="text"
              list="rl-trigger-presets"
              required
              value={triggerEvent}
              onChange={(e) => setTriggerEvent(e.target.value)}
              placeholder="例如 judgment_served"
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
              需与事件录入的英文标识一致（如 <code>judgment_served</code>、<code>detention_notified</code>），可从下拉建议选择。
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

          <div>
            <label htmlFor="rl-cond" className="block text-xs font-semibold text-slate-700 mb-1">
              案件属性限定条件（可选 JSON）
            </label>
            <input
              id="rl-cond"
              type="text"
              value={conditionJson}
              onChange={(e) => setConditionJson(e.target.value)}
              placeholder='例如 {"is_foreign_related": true} 或 {"custody_status": "在押"}'
              className="lt-input font-mono text-xs"
            />
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
