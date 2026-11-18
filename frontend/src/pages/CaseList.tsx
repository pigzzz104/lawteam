import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Archive,
  Briefcase,
  Clock,
  FolderOpen,
  Lock,
  Plus,
  Search,
  Users,
} from "lucide-react";
import { casesApi, rulesApi, usersApi } from "@/api/client";
import { ModalDialog } from "@/components/ModalDialog";
import { CategoryBadge, CustodyBadge } from "@/components/StatusBadges";
import { useAuth } from "@/context/AuthContext";
import { CASE_CATEGORY_META, CASE_MEMBER_ROLE_LABELS } from "@/lib/domain";
import type { CaseCategory, CaseMemberRole, CaseVisibility } from "@/types/api";

export function CaseList() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [categoryFilter, setCategoryFilter] = useState<CaseCategory | "">("");
  const [searchText, setSearchText] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [staleOnly, setStaleOnly] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [caseTitle, setCaseTitle] = useState("");
  const [caseNumber, setCaseNumber] = useState("");
  const [caseCategory, setCaseCategory] = useState<CaseCategory>("civil");
  const [subtype, setSubtype] = useState("");
  const [stage, setStage] = useState("");
  const [visibility, setVisibility] = useState<CaseVisibility>("team");
  const [clientName, setClientName] = useState("");
  const [clientIdLast4, setClientIdLast4] = useState("");
  const [opponentName, setOpponentName] = useState("");
  const [handlingAgency, setHandlingAgency] = useState("");
  const [isForeignRelated, setIsForeignRelated] = useState(false);
  const [custodyStatus, setCustodyStatus] = useState("");
  const [custodyPlace, setCustodyPlace] = useState("");
  const [crimeType, setCrimeType] = useState("");
  const [selectedMembers, setSelectedMembers] = useState<
    Array<{ user_id: number; role_in_case: CaseMemberRole }>
  >([]);

  const casesQuery = useQuery({
    queryKey: ["cases", categoryFilter, showArchived, searchText],
    queryFn: () =>
      casesApi.list({
        case_category: categoryFilter || undefined,
        is_archived: showArchived ? undefined : false,
        search: searchText.trim() || undefined,
      }),
  });

  const templatesQuery = useQuery({
    queryKey: ["stage-templates"],
    queryFn: rulesApi.stageTemplates,
  });

  const usersQuery = useQuery({
    queryKey: ["users", "all"],
    queryFn: usersApi.list,
  });

  const categoryTpl = templatesQuery.data?.categories[caseCategory];
  const subtypeOptions = categoryTpl?.subtypes ? Object.keys(categoryTpl.subtypes) : [];
  const resolvedSubtype = subtype || categoryTpl?.default_subtype || "";
  const stageOptions =
    (resolvedSubtype && categoryTpl?.subtypes?.[resolvedSubtype]) ||
    categoryTpl?.stages ||
    [];

  const openCreateModal = () => {
    setCaseTitle("");
    setCaseNumber("");
    setCaseCategory("civil");
    setSubtype("");
    setStage("");
    setVisibility("team");
    setClientName("");
    setClientIdLast4("");
    setOpponentName("");
    setHandlingAgency("");
    setIsForeignRelated(false);
    setCustodyStatus("");
    setCustodyPlace("");
    setCrimeType("");
    setSelectedMembers([]);
    setCreateOpen(true);
  };

  const createCaseMut = useMutation({
    mutationFn: () =>
      casesApi.create({
        case_title: caseTitle.trim(),
        case_number: caseNumber.trim() || null,
        case_category: caseCategory,
        procedure_subtype: resolvedSubtype || null,
        current_stage: stage || stageOptions[0] || null,
        visibility,
        client_name: clientName.trim(),
        client_id_last4: clientIdLast4.trim() || null,
        opponent_name: opponentName.trim() || null,
        handling_agency: handlingAgency.trim() || null,
        is_foreign_related: isForeignRelated,
        custody_status: caseCategory === "criminal" ? custodyStatus.trim() || null : null,
        custody_place: caseCategory === "criminal" ? custodyPlace.trim() || null : null,
        crime_type: caseCategory === "criminal" ? crimeType.trim() || null : null,
        members: selectedMembers,
      }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ["cases"] });
      setCreateOpen(false);
      navigate(`/cases/${created.case.id}`);
    },
  });

  const filteredCases = useMemo(() => {
    const rows = casesQuery.data ?? [];
    if (!staleOnly) return rows;
    return rows.filter((r) => r.stale_warning);
  }, [casesQuery.data, staleOnly]);

  const toggleMember = (userId: number, role: CaseMemberRole) => {
    setSelectedMembers((prev) => {
      const exists = prev.find((m) => m.user_id === userId);
      if (exists && exists.role_in_case === role) {
        return prev.filter((m) => m.user_id !== userId);
      }
      if (exists) {
        return prev.map((m) => (m.user_id === userId ? { ...m, role_in_case: role } : m));
      }
      return [...prev, { user_id: userId, role_in_case: role }];
    });
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-[var(--md-sys-color-on-surface)]">案件管理</h1>
        <button
          type="button"
          onClick={openCreateModal}
          className="lt-btn-primary px-5 py-2.5 text-sm"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          <span>新建案件</span>
        </button>
      </div>

      <search className="lt-card p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[240px]">
            <Search
              className="h-4 w-4 text-[var(--md-sys-color-on-surface-variant)] absolute left-3.5 top-1/2 -translate-y-1/2"
              aria-hidden="true"
            />
            <input
              type="search"
              aria-label="搜索案件名称、案号、委托人或对方当事人"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="搜索案名、案号（如 (2026)沪01民终6604号）、当事人..."
              className="lt-input pl-10"
            />
          </div>

          <label className="inline-flex items-center gap-1.5 text-xs font-medium text-[var(--md-sys-color-on-surface-variant)] cursor-pointer">
            <input
              type="checkbox"
              checked={staleOnly}
              onChange={(e) => setStaleOnly(e.target.checked)}
            />
            <span>仅看阶段停滞预警</span>
          </label>
          <label className="inline-flex items-center gap-1.5 text-xs font-medium text-[var(--md-sys-color-on-surface-variant)] cursor-pointer">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            />
            <span>包含已归档案件</span>
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={() => setCategoryFilter("")}
            className={`lt-chip py-1 ${categoryFilter === "" ? "lt-chip-active" : ""}`}
          >
            全部{categoryFilter === "" && casesQuery.data ? ` (${casesQuery.data.length})` : ""}
          </button>
          {(Object.keys(CASE_CATEGORY_META) as CaseCategory[]).map((cat) => (
            <button
              key={cat}
              type="button"
              onClick={() => setCategoryFilter(cat)}
              className={`lt-chip py-1 ${categoryFilter === cat ? "lt-chip-active" : ""}`}
            >
              {CASE_CATEGORY_META[cat].label}
              {categoryFilter === cat && casesQuery.data ? ` (${casesQuery.data.length})` : ""}
            </button>
          ))}
        </div>
      </search>

      {casesQuery.isLoading ? (
        <div className="lt-card p-12 text-center text-sm text-slate-600">
          正在加载案件列表...
        </div>
      ) : casesQuery.isError ? (
        <div role="alert" className="lt-alert-error flex items-center justify-between gap-3">
          <span>案件列表加载失败，请重试。</span>
          <button
            type="button"
            onClick={() => casesQuery.refetch()}
            className="lt-btn-secondary py-1"
          >
            重新加载
          </button>
        </div>
      ) : filteredCases.length === 0 ? (
        <div className="rounded-[20px] border border-dashed border-[var(--md-sys-color-outline-variant)] bg-[var(--md-sys-color-surface-container-lowest)] p-12 text-center space-y-2">
          <Briefcase
            className="h-8 w-8 text-[var(--md-sys-color-on-surface-variant)] mx-auto"
            aria-hidden="true"
          />
          <p className="text-sm font-semibold text-[var(--md-sys-color-on-surface)]">
            暂无符合条件的案件
          </p>
          <p className="text-xs text-[var(--md-sys-color-on-surface-variant)]">
            可点击右上角“新建案件”，或前往“收件箱”从法院短信自动建案。
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {filteredCases.map((c) => (
            <Link
              key={c.id}
              to={`/cases/${c.id}`}
              className="group lt-card flex flex-col justify-between p-5 hover:border-blue-500 hover:shadow-md transition-all"
            >
              <div className="space-y-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <CategoryBadge category={c.case_category} />
                    <span className="rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-700">
                      {c.procedure_subtype}
                    </span>
                    {c.visibility === "private" && (
                      <span className="inline-flex items-center gap-1 rounded border border-slate-200 bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
                        <Lock className="h-3 w-3" aria-hidden="true" />
                        私密
                      </span>
                    )}
                    {c.is_archived && (
                      <span className="inline-flex items-center gap-1 rounded border border-slate-300 bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
                        <Archive className="h-3 w-3" aria-hidden="true" />
                        已归档
                      </span>
                    )}
                  </div>
                  {c.stale_warning && (
                    <span className="inline-flex items-center gap-1 rounded-md border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-900 shrink-0 tabular-nums">
                      <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                      停滞 {c.stage_days_elapsed} 天
                    </span>
                  )}
                </div>

                <div>
                  <h2 className="text-base font-bold text-slate-900 group-hover:text-blue-700 line-clamp-2">
                    {c.case_title}
                  </h2>
                  <p className="mt-0.5 text-xs font-mono text-slate-600 tabular-nums">
                    {c.case_number || "未填写案号"}
                  </p>
                </div>

                <div className="space-y-1 text-xs text-slate-700">
                  <p>
                    <span className="text-slate-600">委托人：</span>
                    <span className="font-semibold text-slate-900">{c.client_name}</span>
                    {c.opponent_name ? (
                      <span className="text-slate-600"> vs {c.opponent_name}</span>
                    ) : null}
                  </p>
                  {c.handling_agency && (
                    <p className="truncate">
                      <span className="text-slate-600">办案机构：</span>
                      {c.handling_agency}
                    </p>
                  )}
                </div>

                <div className="flex flex-wrap gap-1.5">
                  <span className="inline-flex items-center gap-1 rounded-md bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-800 tabular-nums">
                    <Clock className="h-3 w-3" aria-hidden="true" />
                    {c.current_stage}（{c.stage_days_elapsed}天）
                  </span>
                  <CustodyBadge status={c.custody_status} place={c.custody_place} />
                </div>
              </div>

              <div className="mt-4 flex items-center justify-between border-t border-slate-100 pt-3 text-xs">
                <div className="flex items-center gap-1.5 text-slate-600">
                  <Users className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
                  <span className="truncate max-w-[160px]">
                    {c.members.map((m) => m.full_name).join("、") || "仅本人"}
                  </span>
                </div>
                <div className="flex items-center gap-2 shrink-0 tabular-nums">
                  {c.open_deadlines_count > 0 ? (
                    <span className="rounded-md bg-rose-50 px-2 py-0.5 font-semibold text-rose-800 border border-rose-200">
                      {c.open_deadlines_count} 项未办结
                      {c.next_deadline_date ? ` · ${c.next_deadline_date}` : ""}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-slate-600">
                      <FolderOpen className="h-3.5 w-3.5" aria-hidden="true" />
                      无未办结期限
                    </span>
                  )}
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}

      <ModalDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="新建协同案件"
        maxWidthClass="max-w-3xl"
      >
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            createCaseMut.mutate();
          }}
          className="space-y-4"
        >
          {createCaseMut.error && (
            <div role="alert" className="lt-alert-error">
              {(createCaseMut.error as Error).message}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="sm:col-span-2">
              <label htmlFor="nc-title" className="block text-xs font-semibold text-slate-700 mb-1">
                案件名称 *
              </label>
              <input
                id="nc-title"
                type="text"
                required
                autoFocus
                value={caseTitle}
                onChange={(e) => setCaseTitle(e.target.value)}
                placeholder="例如 张三与某公司合同纠纷二审 / 王五涉嫌诈骗案侦查阶段"
                className="lt-input"
              />
            </div>

            <div>
              <label htmlFor="nc-category" className="block text-xs font-semibold text-slate-700 mb-1">
                案件大类 *
              </label>
              <select
                id="nc-category"
                value={caseCategory}
                onChange={(e) => {
                  setCaseCategory(e.target.value as CaseCategory);
                  setSubtype("");
                  setStage("");
                }}
                className="lt-input"
              >
                {(Object.keys(CASE_CATEGORY_META) as CaseCategory[]).map((cat) => (
                  <option key={cat} value={cat}>
                    {CASE_CATEGORY_META[cat].label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label htmlFor="nc-number" className="block text-xs font-semibold text-slate-700 mb-1">
                案号（用于自动匹配短信）
              </label>
              <input
                id="nc-number"
                type="text"
                value={caseNumber}
                onChange={(e) => setCaseNumber(e.target.value)}
                placeholder="例如 (2026)沪01民终6604号"
                className="lt-input"
              />
            </div>

            <div>
              <label htmlFor="nc-subtype" className="block text-xs font-semibold text-slate-700 mb-1">
                程序子类型
              </label>
              {subtypeOptions.length > 0 ? (
                <select
                  id="nc-subtype"
                  value={resolvedSubtype}
                  onChange={(e) => {
                    setSubtype(e.target.value);
                    setStage("");
                  }}
                  className="lt-input"
                >
                  {subtypeOptions.map((st) => (
                    <option key={st} value={st}>
                      {st}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  id="nc-subtype"
                  type="text"
                  value={resolvedSubtype}
                  onChange={(e) => setSubtype(e.target.value)}
                  className="lt-input"
                />
              )}
            </div>

            <div>
              <label htmlFor="nc-stage" className="block text-xs font-semibold text-slate-700 mb-1">
                初始所处阶段
              </label>
              {stageOptions.length > 0 ? (
                <select
                  id="nc-stage"
                  value={stage || stageOptions[0] || ""}
                  onChange={(e) => setStage(e.target.value)}
                  className="lt-input"
                >
                  {stageOptions.map((sg) => (
                    <option key={sg} value={sg}>
                      {sg}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  id="nc-stage"
                  type="text"
                  value={stage}
                  onChange={(e) => setStage(e.target.value)}
                  className="lt-input"
                />
              )}
            </div>

            <div>
              <label htmlFor="nc-client" className="block text-xs font-semibold text-slate-700 mb-1">
                委托人 / 当事人姓名 *
              </label>
              <input
                id="nc-client"
                type="text"
                required
                value={clientName}
                onChange={(e) => setClientName(e.target.value)}
                className="lt-input"
              />
            </div>

            <div>
              <label htmlFor="nc-opponent" className="block text-xs font-semibold text-slate-700 mb-1">
                对方当事人 / 被害方
              </label>
              <input
                id="nc-opponent"
                type="text"
                value={opponentName}
                onChange={(e) => setOpponentName(e.target.value)}
                className="lt-input"
              />
            </div>

            <div>
              <label htmlFor="nc-agency" className="block text-xs font-semibold text-slate-700 mb-1">
                承办法院 / 检察院 / 公安 / 仲裁委
              </label>
              <input
                id="nc-agency"
                type="text"
                value={handlingAgency}
                onChange={(e) => setHandlingAgency(e.target.value)}
                placeholder="例如 上海市第一中级人民法院"
                className="lt-input"
              />
            </div>

            <div>
              <label htmlFor="nc-client-id" className="block text-xs font-semibold text-slate-700 mb-1">
                当事人证件后4位（用于法院送达身份校验）
              </label>
              <input
                id="nc-client-id"
                type="text"
                maxLength={4}
                value={clientIdLast4}
                onChange={(e) => setClientIdLast4(e.target.value)}
                placeholder="如 102X"
                className="lt-input"
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-6 rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-xs">
            <div className="flex items-center gap-3">
              <span className="font-semibold text-slate-700">可见性：</span>
              <label className="inline-flex items-center gap-1 cursor-pointer">
                <input
                  type="radio"
                  name="nc-visibility"
                  checked={visibility === "team"}
                  onChange={() => setVisibility("team")}
                />
                <span>团队协同</span>
              </label>
              <label className="inline-flex items-center gap-1 cursor-pointer">
                <input
                  type="radio"
                  name="nc-visibility"
                  checked={visibility === "private"}
                  onChange={() => setVisibility("private")}
                />
                <span>个人私密</span>
              </label>
            </div>
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={isForeignRelated}
                onChange={(e) => setIsForeignRelated(e.target.checked)}
              />
              <span>涉外案件（上诉期自动按30日计算）</span>
            </label>
          </div>

          {caseCategory === "criminal" && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 rounded-lg border border-rose-200 bg-rose-50/60 p-3">
              <div>
                <label htmlFor="nc-custody" className="block text-xs font-semibold text-rose-900 mb-1">
                  羁押状态
                </label>
                <input
                  id="nc-custody"
                  type="text"
                  value={custodyStatus}
                  onChange={(e) => setCustodyStatus(e.target.value)}
                  placeholder="在押 / 取保候审 / 监视居住"
                  className="w-full rounded border border-rose-200 bg-white px-2.5 py-1.5 text-xs"
                />
              </div>
              <div>
                <label htmlFor="nc-place" className="block text-xs font-semibold text-rose-900 mb-1">
                  羁押看守所
                </label>
                <input
                  id="nc-place"
                  type="text"
                  value={custodyPlace}
                  onChange={(e) => setCustodyPlace(e.target.value)}
                  placeholder="如 上海市第一看守所"
                  className="w-full rounded border border-rose-200 bg-white px-2.5 py-1.5 text-xs"
                />
              </div>
              <div>
                <label htmlFor="nc-crime" className="block text-xs font-semibold text-rose-900 mb-1">
                  涉嫌罪名
                </label>
                <input
                  id="nc-crime"
                  type="text"
                  value={crimeType}
                  onChange={(e) => setCrimeType(e.target.value)}
                  placeholder="如 职务侵占罪"
                  className="w-full rounded border border-rose-200 bg-white px-2.5 py-1.5 text-xs"
                />
              </div>
            </div>
          )}

          {visibility === "team" && (usersQuery.data?.length ?? 0) > 0 && (
            <div className="space-y-2 rounded-lg border border-slate-200 p-3">
              <p className="text-xs font-semibold text-slate-700">
                分配团队成员（默认将您设为办案组长）
              </p>
              <div className="space-y-1.5">
                {usersQuery.data!.map((u) => {
                  const chosen = selectedMembers.find((m) => m.user_id === u.id);
                  const isSelf = u.id === user?.id;
                  return (
                    <div
                      key={u.id}
                      className="flex flex-wrap items-center justify-between gap-2 text-xs py-1 border-b border-slate-100 last:border-b-0"
                    >
                      <span className="font-medium text-slate-800">
                        {u.full_name}
                        {isSelf && (
                          <span className="ml-1.5 text-[11px] text-slate-500">（本人 · 默认主办）</span>
                        )}
                      </span>
                      <div className="flex items-center gap-1.5">
                        {(["lead", "co", "supervisor"] as CaseMemberRole[]).map((r) => (
                          <button
                            key={r}
                            type="button"
                            onClick={() => toggleMember(u.id, r)}
                            className={`rounded-full px-3 py-1 border text-[11px] font-medium transition-colors ${
                              chosen?.role_in_case === r
                                ? "border-transparent bg-[var(--md-sys-color-primary)] text-[var(--md-sys-color-on-primary)]"
                                : "border-[var(--md-sys-color-outline-variant)] bg-white text-[var(--md-sys-color-on-surface-variant)] hover:bg-[var(--md-sys-color-surface-container-low)]"
                            }`}
                          >
                            {CASE_MEMBER_ROLE_LABELS[r]}
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setCreateOpen(false)}
              className="lt-btn-secondary"
            >
              取消
            </button>
            <button type="submit" disabled={createCaseMut.isPending} className="lt-btn-primary">
              {createCaseMut.isPending ? "创建中..." : "创建案件"}
            </button>
          </div>
        </form>
      </ModalDialog>
    </div>
  );
}
