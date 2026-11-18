import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, CheckCircle2, Play, Save, Send } from "lucide-react";
import { authApi, remindersApi } from "@/api/client";
import { useAuth } from "@/context/AuthContext";
import {
  DELIVERY_STATUS_META,
  NOTIFY_CHANNEL_META,
  SOURCE_TYPE_LABELS,
  formatShanghaiDateTime,
} from "@/lib/domain";
import type { CourtAutoFetchMode, NotifyChannel, TestPushResponse } from "@/types/api";

const COURT_FETCH_MODE_HINTS: Record<CourtAutoFetchMode, string> = {
  auto_non_judgment:
    "传票、举证/应诉通知等程序文书自动填充尾号抓取并归档；判决书/裁定书等终局裁决签收前暂停提醒人工确认（推荐）。",
  auto_all:
    "收到含取件码或已知省级送达平台链接的短信后，立即自动填充手机尾号抓件并按规则归档。",
  confirm_before_sign:
    "所有 Tier-2 省级法院平台在签收前均先拦截待人工确认，避免误签收或触发回执。",
};

export function ReminderSettings() {
  const { user, refreshUser } = useAuth();
  const queryClient = useQueryClient();

  const [notifyChannel, setNotifyChannel] = useState<NotifyChannel>(
    user?.notify_channel || "wecom"
  );
  const [notifyTarget, setNotifyTarget] = useState(user?.notify_target || "");
  const [wecomUserid, setWecomUserid] = useState(user?.wecom_userid || "");
  const [phoneOs, setPhoneOs] = useState(user?.phone_os || "ios");
  const [phoneLast4, setPhoneLast4] = useState(user?.phone_last4 || "");
  const [idCardLast4, setIdCardLast4] = useState(user?.id_card_last4 || "");
  const [courtFetchMode, setCourtFetchMode] = useState<CourtAutoFetchMode>(
    user?.court_auto_fetch_mode || "confirm_before_sign"
  );
  const [calendarColor, setCalendarColor] = useState(user?.calendar_color || "#2563eb");

  const [statusFilter, setStatusFilter] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestPushResponse | null>(null);

  useEffect(() => {
    if (!user) return;
    setNotifyChannel(user.notify_channel || "wecom");
    setNotifyTarget(user.notify_target || "");
    setWecomUserid(user.wecom_userid || "");
    setPhoneOs(user.phone_os || "ios");
    setPhoneLast4(user.phone_last4 || "");
    setIdCardLast4(user.id_card_last4 || "");
    setCourtFetchMode(user.court_auto_fetch_mode || "confirm_before_sign");
    setCalendarColor(user.calendar_color || "#2563eb");
  }, [user]);

  const isDirty = Boolean(
    user &&
      (notifyChannel !== (user.notify_channel || "wecom") ||
        notifyTarget.trim() !== (user.notify_target || "") ||
        wecomUserid.trim() !== (user.wecom_userid || "") ||
        phoneOs !== (user.phone_os || "ios") ||
        phoneLast4.trim() !== (user.phone_last4 || "") ||
        idCardLast4.trim() !== (user.id_card_last4 || "") ||
        courtFetchMode !== (user.court_auto_fetch_mode || "confirm_before_sign") ||
        calendarColor !== (user.calendar_color || "#2563eb"))
  );

  const buildProfilePayload = () => ({
    notify_channel: notifyChannel,
    notify_target: notifyTarget.trim() || null,
    wecom_userid: wecomUserid.trim() || null,
    phone_os: phoneOs,
    phone_last4: phoneLast4.trim() || null,
    id_card_last4: idCardLast4.trim() || null,
    court_auto_fetch_mode: courtFetchMode,
    calendar_color: calendarColor,
  });

  const deliveriesQuery = useQuery({
    queryKey: ["reminder-deliveries", statusFilter],
    queryFn: () =>
      remindersApi.deliveries({
        status: statusFilter || undefined,
        limit: 100,
      }),
  });

  const saveProfileMut = useMutation({
    mutationFn: () => authApi.updateMe(buildProfilePayload()),
    onSuccess: async () => {
      await refreshUser();
      setErrorMsg(null);
      setFeedback("个人微信通道与核验参数已保存");
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const testPushMut = useMutation({
    mutationFn: async () => {
      if (isDirty) {
        await authApi.updateMe(buildProfilePayload());
        await refreshUser();
      }
      return remindersApi.testPush();
    },
    onSuccess: (res) => {
      setTestResult(res);
      setErrorMsg(null);
      if (isDirty) {
        setFeedback("已自动保存最新配置并完成测试推送");
      }
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const runSweepMut = useMutation({
    mutationFn: () => remindersApi.runOnce(),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["reminder-deliveries"] });
      setErrorMsg(null);
      setFeedback(
        `扫描完成：投递 ${res.sent_count} 条，已办结跳过 ${res.skipped_completed_count} 条，去重 ${res.already_processed_count} 条，失败 ${res.failed_count} 条`
      );
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const channelMeta = NOTIFY_CHANNEL_META[notifyChannel];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-[var(--md-sys-color-on-surface)]">提醒设置</h1>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={testPushMut.isPending}
            onClick={() => testPushMut.mutate()}
            className="lt-btn-accent"
          >
            <Send className="h-3.5 w-3.5" aria-hidden="true" />
            <span>
              {testPushMut.isPending
                ? "推送中..."
                : isDirty
                ? "保存并测试推送"
                : "发送测试推送"}
            </span>
          </button>
          <button
            type="button"
            disabled={runSweepMut.isPending}
            onClick={() => runSweepMut.mutate()}
            className="lt-btn-primary"
          >
            <Play className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{runSweepMut.isPending ? "扫描中..." : "立即扫描到期提醒"}</span>
          </button>
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

      {testResult && (
        <div
          className={`rounded-xl border p-4 text-xs space-y-1.5 ${
            testResult.ok
              ? "border-emerald-200 bg-emerald-50 text-emerald-900"
              : "border-rose-200 bg-rose-50 text-rose-900"
          }`}
        >
          <p className="font-bold flex items-center gap-1.5">
            <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
            <span>
              测试推送结果：{testResult.ok ? "成功" : "失败"} · 通道{" "}
              {NOTIFY_CHANNEL_META[testResult.channel as NotifyChannel]?.badge ?? testResult.channel}
              {testResult.dry_run ? "（本地演练模式，未实际发送外部消息）" : ""}
            </span>
          </p>
          <p>{testResult.summary}</p>
          {testResult.error_msg && <p className="text-rose-700">错误详情：{testResult.error_msg}</p>}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            saveProfileMut.mutate();
          }}
          className="lg:col-span-5 lt-card p-6 space-y-4"
        >
          <div className="flex items-center gap-2 border-b border-slate-100 pb-3">
            <BellRing className="h-5 w-5 text-blue-700" aria-hidden="true" />
            <h2 className="text-base font-bold text-slate-900">个人接收通道与核验配置</h2>
          </div>

          <div>
            <label htmlFor="rs-channel" className="block text-xs font-semibold text-slate-700 mb-1">
              微信推送通道
            </label>
            <select
              id="rs-channel"
              value={notifyChannel}
              onChange={(e) => setNotifyChannel(e.target.value as NotifyChannel)}
              className="lt-input"
            >
              {(Object.keys(NOTIFY_CHANNEL_META) as NotifyChannel[]).map((ch) => (
                <option key={ch} value={ch}>
                  {NOTIFY_CHANNEL_META[ch].label}
                </option>
              ))}
            </select>
            <p className="mt-1.5 rounded-lg bg-slate-50 border border-slate-200 p-2.5 text-xs text-slate-600 leading-relaxed">
              {channelMeta.hint}
            </p>
          </div>

          {notifyChannel === "wecom" ? (
            <div>
              <label htmlFor="rs-wecom" className="block text-xs font-semibold text-slate-700 mb-1">
                {channelMeta.targetLabel}
              </label>
              <input
                id="rs-wecom"
                type="text"
                value={wecomUserid}
                onChange={(e) => setWecomUserid(e.target.value)}
                placeholder={channelMeta.targetPlaceholder}
                className="lt-input"
              />
            </div>
          ) : (
            <div>
              <label htmlFor="rs-target" className="block text-xs font-semibold text-slate-700 mb-1">
                {channelMeta.targetLabel}
              </label>
              <input
                id="rs-target"
                type="text"
                value={notifyTarget}
                onChange={(e) => setNotifyTarget(e.target.value)}
                placeholder={channelMeta.targetPlaceholder}
                className="lt-input"
              />
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 border-t border-slate-100 pt-3">
            <div>
              <label htmlFor="rs-phone4" className="block text-xs font-semibold text-slate-700 mb-1">
                手机号后4位（法院送达验证）
              </label>
              <input
                id="rs-phone4"
                type="text"
                maxLength={4}
                value={phoneLast4}
                onChange={(e) => setPhoneLast4(e.target.value)}
                placeholder="例如 8966"
                className="lt-input"
              />
            </div>
            <div>
              <label htmlFor="rs-id4" className="block text-xs font-semibold text-slate-700 mb-1">
                身份证号后4位
              </label>
              <input
                id="rs-id4"
                type="text"
                maxLength={4}
                value={idCardLast4}
                onChange={(e) => setIdCardLast4(e.target.value)}
                placeholder="例如 101X"
                className="lt-input"
              />
            </div>
          </div>

          <div className="border-t border-slate-100 pt-3">
            <label htmlFor="rs-tier2-mode" className="block text-xs font-semibold text-slate-700 mb-1">
              省级法院（Tier-2）自动取件策略
            </label>
            <select
              id="rs-tier2-mode"
              value={courtFetchMode}
              onChange={(e) => setCourtFetchMode(e.target.value as CourtAutoFetchMode)}
              className="lt-input"
            >
              <option value="auto_non_judgment">仅自动抓非裁决通知（传票/举证/应诉自动，判决/裁定人工确认）</option>
              <option value="auto_all">全部 Tier-2 平台自动填充尾号抓取</option>
              <option value="confirm_before_sign">全部签收前先暂停待人工确认（保守）</option>
            </select>
            <p className="mt-1.5 rounded-lg bg-slate-50 border border-slate-200 p-2 text-[11px] text-slate-600 leading-relaxed">
              {COURT_FETCH_MODE_HINTS[courtFetchMode]}
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-center">
            <div>
              <label htmlFor="rs-os" className="block text-xs font-semibold text-slate-700 mb-1">
                主力手机系统
              </label>
              <select
                id="rs-os"
                value={phoneOs}
                onChange={(e) => setPhoneOs(e.target.value)}
                className="lt-input"
              >
                <option value="ios">iOS (iPhone)</option>
                <option value="android">Android</option>
              </select>
            </div>
            <div className="flex items-center justify-between gap-3 sm:pt-5">
              <label htmlFor="rs-color" className="text-xs font-semibold text-slate-700">
                日历专属色彩
              </label>
              <input
                id="rs-color"
                type="color"
                value={calendarColor}
                onChange={(e) => setCalendarColor(e.target.value)}
                className="h-9 w-16 rounded border border-slate-300 cursor-pointer"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={saveProfileMut.isPending}
            className="lt-btn-primary w-full py-2.5 text-sm"
          >
            <Save className="h-4 w-4" aria-hidden="true" />
            <span>{saveProfileMut.isPending ? "保存中..." : "保存配置"}</span>
          </button>
        </form>

        <section className="lg:col-span-7 lt-card p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-bold text-slate-900">提醒投递台账</h2>
            <div className="flex flex-wrap gap-1.5">
              {[
                { key: "", label: "全部" },
                { key: "sent", label: "已推送" },
                { key: "skipped_completed", label: "已办结跳过" },
                { key: "failed", label: "投递失败" },
              ].map((st) => (
                <button
                  key={st.key}
                  type="button"
                  onClick={() => setStatusFilter(st.key)}
                  className={`lt-chip py-1 ${statusFilter === st.key ? "lt-chip-active" : ""}`}
                >
                  {st.label}
                </button>
              ))}
            </div>
          </div>

          {deliveriesQuery.isLoading ? (
            <p className="py-12 text-center text-xs text-slate-600">正在加载投递记录...</p>
          ) : deliveriesQuery.isError ? (
            <div role="alert" className="lt-alert-error">
              投递台账加载失败，请稍后重试。
            </div>
          ) : (deliveriesQuery.data?.length ?? 0) === 0 ? (
            <p className="py-12 text-center text-xs text-slate-600">
              暂无提醒投递记录，可点击右上角“立即扫描到期提醒”触发评估。
            </p>
          ) : (
            <div tabIndex={0} role="region" aria-label="提醒投递台账表" className="table-wrapper max-h-[540px]">
              <table className="text-left text-xs">
                <thead className="bg-slate-50 text-slate-700">
                  <tr>
                    <th className="border-b border-slate-200 px-3 py-2.5 font-semibold">来源 / 幂等键</th>
                    <th className="border-b border-slate-200 px-3 py-2.5 font-semibold">状态</th>
                    <th className="border-b border-slate-200 px-3 py-2.5 font-semibold">接收人数</th>
                    <th className="border-b border-slate-200 px-3 py-2.5 font-semibold">投递时间</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {deliveriesQuery.data!.map((row) => {
                    const statusMeta = DELIVERY_STATUS_META[row.status] ?? {
                      label: row.status,
                      badgeClass: "bg-slate-100 text-slate-700",
                    };
                    return (
                      <tr key={row.id} className="hover:bg-slate-50">
                        <td className="px-3 py-2.5">
                          <p className="font-semibold text-slate-900">
                            {SOURCE_TYPE_LABELS[row.source_type] ?? row.source_type} #{row.source_id}
                          </p>
                          <p className="font-mono text-[11px] text-slate-600 break-all">
                            {row.idempotency_key}
                          </p>
                          {row.error_msg && (
                            <p className="text-[11px] text-rose-700 mt-0.5">{row.error_msg}</p>
                          )}
                        </td>
                        <td className="px-3 py-2.5 whitespace-nowrap">
                          <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${statusMeta.badgeClass}`}>
                            {statusMeta.label}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 whitespace-nowrap text-slate-700 tabular-nums">
                          {row.recipient_user_ids.length} 人
                        </td>
                        <td className="px-3 py-2.5 whitespace-nowrap text-slate-600 tabular-nums">
                          {formatShanghaiDateTime(row.sent_at || row.scheduled_at)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
