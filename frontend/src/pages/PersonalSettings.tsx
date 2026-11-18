import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  BellRing,
  Check,
  CheckCircle2,
  Copy,
  History,
  KeyRound,
  RefreshCw,
  Save,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  Smartphone,
  Trash2,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { authApi, remindersApi } from "@/api/client";
import {
  ConfirmDialog,
  MdCheckbox,
  MdColorSwatches,
  MdRadioGroup,
  MdSegmentedButton,
  MdSelect,
} from "@/components/ui";
import { useAuth } from "@/context/AuthContext";
import {
  DELIVERY_STATUS_META,
  NOTIFY_CHANNEL_META,
  SOURCE_TYPE_LABELS,
  copyTextSafely,
  formatShanghaiDateTime,
} from "@/lib/domain";
import type { CourtAutoFetchMode, NotifyChannel, TestPushResponse } from "@/types/api";

type SettingsTab = "channel" | "offsets" | "sms" | "deliveries";

const COURT_FETCH_MODE_OPTIONS: Array<{
  value: CourtAutoFetchMode;
  label: string;
  description: string;
  badge?: string;
}> = [
  {
    value: "auto_non_judgment",
    label: "仅程序性文书自动签收（推荐）",
    badge: "推荐",
    description:
      "传票、举证/应诉通知等自动填尾号取回并归档；判决书、裁定书等终局裁决文书在签收前交由您确认。",
  },
  {
    value: "auto_all",
    label: "全部司法送达自动取回",
    description: "收到含取件码或官方平台链接的短信后，自动填尾号下载并按规则归档。",
  },
  {
    value: "confirm_before_sign",
    label: "签收前一律人工确认",
    description: "所有需验证身份或触发回执的法院平台链接均先拦截至收件箱，由办案人确认后取回。",
  },
];

const DEADLINE_OFFSET_PRESETS = [14, 10, 7, 5, 3, 1, 0];
const SCHEDULE_OFFSET_PRESETS = [
  { minutes: 10080, label: "提前 7 天" },
  { minutes: 4320, label: "提前 3 天" },
  { minutes: 1440, label: "提前 1 天" },
  { minutes: 120, label: "提前 2 小时" },
  { minutes: 30, label: "提前 30 分钟" },
];
const SMS_FILTER_KEYWORDS =
  "法院 人民法院 送达 12368 12309 检察院 看守所 公安 仲裁 发票 传票 判决 裁定";

export function PersonalSettings() {
  const { user, refreshUser } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTabParam = searchParams.get("tab");
  const initialTab: SettingsTab =
    initialTabParam === "offsets" ||
    initialTabParam === "sms" ||
    initialTabParam === "deliveries"
      ? initialTabParam
      : "channel";
  const [activeTab, setActiveTab] = useState<SettingsTab>(initialTab);

  useEffect(() => {
    const p = searchParams.get("tab");
    if (p === "channel" || p === "offsets" || p === "sms" || p === "deliveries") {
      setActiveTab(p);
    }
  }, [searchParams]);

  const switchTab = (tab: SettingsTab) => {
    setActiveTab(tab);
    setSearchParams(tab === "channel" ? {} : { tab }, { replace: true });
  };

  // Tab 1: channel & auto-fetch profile
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
  const [calendarColor, setCalendarColor] = useState(user?.calendar_color || "#1b56b3");

  // Tab 2: personal default extra offsets
  const [deadlineOffsets, setDeadlineOffsets] = useState<number[]>(
    user?.default_deadline_offsets_days ?? []
  );
  const [customDeadlineDay, setCustomDeadlineDay] = useState("");
  const [scheduleOffsets, setScheduleOffsets] = useState<number[]>(
    user?.default_schedule_offsets_minutes ?? []
  );

  // Tab 3: SMS webhook setup
  const [smsPlatformTab, setSmsPlatformTab] = useState<"ios" | "android" | "mac">(
    user?.phone_os === "android" ? "android" : "ios"
  );
  const [hmacSecret, setHmacSecret] = useState("");
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [rotateConfirmOpen, setRotateConfirmOpen] = useState(false);
  const [clearSecretConfirmOpen, setClearSecretConfirmOpen] = useState(false);

  // Tab 4: delivery ledger
  const [statusFilter, setStatusFilter] = useState<string>("");

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
    setCalendarColor(user.calendar_color || "#1b56b3");
    setDeadlineOffsets(user.default_deadline_offsets_days ?? []);
    setScheduleOffsets(user.default_schedule_offsets_minutes ?? []);
  }, [user]);

  const isProfileDirty = Boolean(
    user &&
      (notifyChannel !== (user.notify_channel || "wecom") ||
        notifyTarget.trim() !== (user.notify_target || "") ||
        wecomUserid.trim() !== (user.wecom_userid || "") ||
        phoneOs !== (user.phone_os || "ios") ||
        phoneLast4.trim() !== (user.phone_last4 || "") ||
        idCardLast4.trim() !== (user.id_card_last4 || "") ||
        courtFetchMode !== (user.court_auto_fetch_mode || "confirm_before_sign") ||
        calendarColor.toLowerCase() !== (user.calendar_color || "#1b56b3").toLowerCase())
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

  const saveProfileMut = useMutation({
    mutationFn: () => authApi.updateMe(buildProfilePayload()),
    onSuccess: async () => {
      await refreshUser();
      setErrorMsg(null);
      setFeedback("通知通道与核验参数已保存");
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const testPushMut = useMutation({
    mutationFn: async () => {
      if (isProfileDirty) {
        await authApi.updateMe(buildProfilePayload());
        await refreshUser();
      }
      return remindersApi.testPush();
    },
    onSuccess: (res) => {
      setTestResult(res);
      setErrorMsg(null);
      if (isProfileDirty) {
        setFeedback("已保存配置并完成测试推送");
      }
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const saveOffsetsMut = useMutation({
    mutationFn: () =>
      authApi.updateMe({
        default_deadline_offsets_days: deadlineOffsets,
        default_schedule_offsets_minutes: scheduleOffsets,
      }),
    onSuccess: async () => {
      await refreshUser();
      setErrorMsg(null);
      setFeedback("个人默认追加提醒时间已保存");
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const rotateTokenMut = useMutation({
    mutationFn: () => authApi.updateMe({ rotate_sms_token: true }),
    onSuccess: async () => {
      await refreshUser();
      setRotateConfirmOpen(false);
      setErrorMsg(null);
      setFeedback("已更换专属短信接收地址，请同步更新手机端设置");
    },
    onError: (err: Error) => {
      setRotateConfirmOpen(false);
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const saveSecretMut = useMutation({
    mutationFn: (secret: string | null) =>
      authApi.updateMe({ sms_webhook_secret: secret }),
    onSuccess: async (_data, secret) => {
      await refreshUser();
      setHmacSecret("");
      setClearSecretConfirmOpen(false);
      setErrorMsg(null);
      setFeedback(secret ? "已设置短信转发安全签名密钥" : "已清除签名密钥，恢复普通模式接收");
    },
    onError: (err: Error) => {
      setClearSecretConfirmOpen(false);
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const deliveriesQuery = useQuery({
    queryKey: ["reminder-deliveries", statusFilter],
    queryFn: () =>
      remindersApi.deliveries({
        status: statusFilter || undefined,
        limit: 100,
      }),
    enabled: activeTab === "deliveries",
  });

  const webhookUrl = user
    ? `${window.location.origin}/api/webhooks/sms/${user.sms_webhook_token}`
    : "";

  const copyText = async (key: string, text: string) => {
    const ok = await copyTextSafely(text);
    if (!ok) {
      setErrorMsg("剪贴板访问受限，请手动选中复制");
      return;
    }
    setErrorMsg(null);
    setCopiedKey(key);
    window.setTimeout(() => setCopiedKey(null), 2000);
  };

  const toggleDeadlineDay = (day: number, checked: boolean) => {
    setDeadlineOffsets((prev) => {
      const next = checked ? [...prev, day] : prev.filter((x) => x !== day);
      return Array.from(new Set(next)).sort((a, b) => b - a);
    });
  };

  const handleAddCustomDay = () => {
    const n = Number.parseInt(customDeadlineDay.trim(), 10);
    if (Number.isNaN(n) || n < 0 || n > 365) {
      setErrorMsg("请输入 0 至 365 之间的整数天数");
      return;
    }
    setErrorMsg(null);
    toggleDeadlineDay(n, true);
    setCustomDeadlineDay("");
  };

  const toggleScheduleMinute = (min: number, checked: boolean) => {
    setScheduleOffsets((prev) => {
      const next = checked ? [...prev, min] : prev.filter((x) => x !== min);
      return Array.from(new Set(next)).sort((a, b) => b - a);
    });
  };

  const channelMeta = NOTIFY_CHANNEL_META[notifyChannel];
  const extraDays = Array.from(new Set([...DEADLINE_OFFSET_PRESETS, ...deadlineOffsets])).sort(
    (a, b) => b - a
  );

  const iosPayloadTemplate = `{
  "content": "快捷指令变量：短信内容",
  "sender": "快捷指令变量：发件人",
  "received_at": "当前日期",
  "client_platform": "ios_shortcuts"
}`;

  const androidPayloadTemplate = `{
  "content": "[msg]",
  "sender": "[from]",
  "received_at": "[receive_time]",
  "timestamp": "[timestamp]",
  "sign": "[sign]",
  "client_platform": "android_smsforwarder"
}`;

  const curlSample = `curl -X POST "${webhookUrl}" \\
  -H "Content-Type: application/json" \\
  -d '{"content":"【法院送达】请查收(2026)沪01民终6604号送达文书","sender":"12368","client_platform":"mac_relay"}'`;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-[var(--md-sys-color-on-surface)]">个人设置</h1>
        {activeTab === "channel" && (
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
                : isProfileDirty
                  ? "保存并测试推送"
                  : "发送测试推送"}
            </span>
          </button>
        )}
      </div>

      <MdSegmentedButton<SettingsTab>
        value={activeTab}
        onChange={switchTab}
        ariaLabel="个人设置分类"
        options={[
          {
            value: "channel",
            label: "通知与签收策略",
            icon: <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />,
          },
          {
            value: "offsets",
            label: "默认追加提醒",
            icon: <BellRing className="h-3.5 w-3.5" aria-hidden="true" />,
          },
          {
            value: "sms",
            label: "手机短信转发",
            icon: <Smartphone className="h-3.5 w-3.5" aria-hidden="true" />,
          },
          {
            value: "deliveries",
            label: "推送记录",
            icon: <History className="h-3.5 w-3.5" aria-hidden="true" />,
          },
        ]}
      />

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

      {activeTab === "channel" && testResult && (
        <div
          className={`rounded-[var(--md-sys-shape-corner-md)] border p-4 text-xs space-y-1 ${
            testResult.ok
              ? "border-[var(--md-sys-color-tertiary)]/35 bg-[var(--md-sys-color-tertiary-container)] text-[var(--md-sys-color-on-tertiary-container)]"
              : "border-[var(--md-sys-color-error)]/35 bg-[var(--md-sys-color-error-container)] text-[var(--md-sys-color-on-error-container)]"
          }`}
        >
          <p className="flex items-center gap-1.5 font-semibold">
            <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />
            <span>
              {testResult.summary}
              {testResult.dry_run ? "（当前为试运行模拟模式，不向真实微信发消息）" : ""}
            </span>
          </p>
          {testResult.error_msg && <p>原因：{testResult.error_msg}</p>}
        </div>
      )}

      {/* Tab 1: 通知与签收策略 */}
      {activeTab === "channel" && (
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            saveProfileMut.mutate();
          }}
          className="lt-card p-6 space-y-6"
        >
          <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
            <div>
              <label
                htmlFor="settings-notify-channel"
                className="mb-1.5 block text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)]"
              >
                微信/机器提醒通道
              </label>
              <MdSelect
                id="settings-notify-channel"
                value={notifyChannel}
                onChange={(e) => setNotifyChannel(e.target.value as NotifyChannel)}
              >
                {Object.entries(NOTIFY_CHANNEL_META).map(([key, meta]) => (
                  <option key={key} value={key}>
                    {meta.label}
                  </option>
                ))}
              </MdSelect>
              <p className="mt-1.5 text-xs text-[var(--md-sys-color-on-surface-variant)]">
                {channelMeta?.hint}
              </p>
            </div>

            <div>
              <label
                htmlFor="settings-notify-target"
                className="mb-1.5 block text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)]"
              >
                {channelMeta?.targetLabel || "接收账号/密钥"}
              </label>
              <input
                id="settings-notify-target"
                value={notifyChannel === "wecom" ? wecomUserid : notifyTarget}
                onChange={(e) =>
                  notifyChannel === "wecom"
                    ? setWecomUserid(e.target.value)
                    : setNotifyTarget(e.target.value)
                }
                placeholder={channelMeta?.targetPlaceholder}
                className="lt-input"
              />
            </div>

            {notifyChannel !== "wecom" && (
              <div>
                <label
                  htmlFor="settings-wecom-backup"
                  className="mb-1.5 block text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)]"
                >
                  企业微信 UserID（可选，用于快捷卡片确认）
                </label>
                <input
                  id="settings-wecom-backup"
                  value={wecomUserid}
                  onChange={(e) => setWecomUserid(e.target.value)}
                  placeholder="留空则仅用上方主通道"
                  className="lt-input"
                />
              </div>
            )}

            <div>
              <span className="mb-1.5 block text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)]">
                常用转发设备系统
              </span>
              <MdSegmentedButton<"ios" | "android">
                value={phoneOs === "android" ? "android" : "ios"}
                onChange={(v) => setPhoneOs(v)}
                ariaLabel="选择常用手机系统"
                options={[
                  { value: "ios", label: "iPhone / iOS" },
                  { value: "android", label: "Android 手机" },
                ]}
              />
            </div>

            <div>
              <label
                htmlFor="settings-phone-last4"
                className="mb-1.5 block text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)]"
              >
                常用收件手机号后 4 位
              </label>
              <input
                id="settings-phone-last4"
                maxLength={4}
                value={phoneLast4}
                onChange={(e) => setPhoneLast4(e.target.value.replace(/\D/g, ""))}
                placeholder="法院送达验证用，如 8966"
                className="lt-input"
              />
            </div>

            <div>
              <label
                htmlFor="settings-id-last4"
                className="mb-1.5 block text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)]"
              >
                身份证号后 4 位（可选）
              </label>
              <input
                id="settings-id-last4"
                maxLength={4}
                value={idCardLast4}
                onChange={(e) => setIdCardLast4(e.target.value.trim().toUpperCase())}
                placeholder="部分法院要求核验，如 123X"
                className="lt-input"
              />
            </div>

            <div className="md:col-span-2">
              <span className="mb-2 block text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)]">
                日历个人标识色
              </span>
              <MdColorSwatches value={calendarColor} onChange={setCalendarColor} />
            </div>
          </div>

          <div className="space-y-2.5 pt-2 border-t border-[var(--md-sys-color-outline-variant)]/70">
            <span className="block text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)]">
              法院电子送达文书签收策略
            </span>
            <MdRadioGroup<CourtAutoFetchMode>
              value={courtFetchMode}
              onChange={setCourtFetchMode}
              options={COURT_FETCH_MODE_OPTIONS}
              ariaLabel="法院电子送达文书签收策略"
            />
          </div>

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={saveProfileMut.isPending}
              className="lt-btn-primary"
            >
              <Save className="h-4 w-4" aria-hidden="true" />
              <span>{saveProfileMut.isPending ? "保存中..." : "保存配置"}</span>
            </button>
          </div>
        </form>
      )}

      {/* Tab 2: 默认追加提醒 */}
      {activeTab === "offsets" && (
        <section className="lt-card p-6 space-y-6">
          <p className="text-xs text-[var(--md-sys-color-on-surface-variant)]">
            在每条案件规则或日程自身的提醒时间之外，为个人额外追加提前通知节点；不勾选则仅按案件默认规则提醒。
          </p>

          <div className="space-y-3">
            <h2 className="text-sm font-bold text-[var(--md-sys-color-on-surface)]">
              法定期限 · 个人追加提前天数
            </h2>
            <div className="flex flex-wrap gap-3">
              {extraDays.map((day) => (
                <div
                  key={day}
                  className="rounded-full border border-[var(--md-sys-color-outline-variant)] bg-[var(--md-sys-color-surface-container-low)] px-3.5 py-1.5"
                >
                  <MdCheckbox
                    checked={deadlineOffsets.includes(day)}
                    onChange={(c) => toggleDeadlineDay(day, c)}
                    label={day === 0 ? "到期当天 (09:00)" : `提前 ${day} 天`}
                  />
                </div>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <input
                type="number"
                min={0}
                max={365}
                value={customDeadlineDay}
                onChange={(e) => setCustomDeadlineDay(e.target.value)}
                placeholder="自定义天数（如 21）"
                aria-label="自定义提前天数"
                className="lt-input w-44"
              />
              <button type="button" onClick={handleAddCustomDay} className="lt-btn-secondary">
                添加天数
              </button>
            </div>
          </div>

          <div className="space-y-3 border-t border-[var(--md-sys-color-outline-variant)]/70 pt-5">
            <h2 className="text-sm font-bold text-[var(--md-sys-color-on-surface)]">
              日程安排 · 个人追加提前节点
            </h2>
            <div className="flex flex-wrap gap-3">
              {SCHEDULE_OFFSET_PRESETS.map((opt) => (
                <div
                  key={opt.minutes}
                  className="rounded-full border border-[var(--md-sys-color-outline-variant)] bg-[var(--md-sys-color-surface-container-low)] px-3.5 py-1.5"
                >
                  <MdCheckbox
                    checked={scheduleOffsets.includes(opt.minutes)}
                    onChange={(c) => toggleScheduleMinute(opt.minutes, c)}
                    label={opt.label}
                  />
                </div>
              ))}
            </div>
          </div>

          <div className="flex justify-end">
            <button
              type="button"
              disabled={saveOffsetsMut.isPending}
              onClick={() => saveOffsetsMut.mutate()}
              className="lt-btn-primary"
            >
              <Save className="h-4 w-4" aria-hidden="true" />
              <span>{saveOffsetsMut.isPending ? "保存中..." : "保存个人提醒偏好"}</span>
            </button>
          </div>
        </section>
      )}

      {/* Tab 3: 手机短信转发 */}
      {activeTab === "sms" && (
        <div className="space-y-5">
          <section className="lt-card p-6 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-sm font-bold text-[var(--md-sys-color-on-surface)]">
                专属短信接收地址
              </h2>
              <button
                type="button"
                onClick={() => setRotateConfirmOpen(true)}
                className="lt-btn-secondary"
              >
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                <span>重置接收地址</span>
              </button>
            </div>

            <div className="flex flex-wrap items-center gap-2 rounded-[var(--md-sys-shape-corner-md)] bg-[var(--md-sys-color-surface-container)] p-3">
              <code className="flex-1 break-all text-xs font-mono text-[var(--md-sys-color-on-surface)]">
                {webhookUrl}
              </code>
              <button
                type="button"
                onClick={() => copyText("webhookUrl", webhookUrl)}
                className="lt-btn-primary"
              >
                {copiedKey === "webhookUrl" ? (
                  <>
                    <Check className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>已复制</span>
                  </>
                ) : (
                  <>
                    <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>复制地址</span>
                  </>
                )}
              </button>
            </div>

            <div className="lt-surface-tonal p-3.5 flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0 flex-1 space-y-0.5">
                <p className="text-xs font-semibold text-[var(--md-sys-color-on-surface)]">
                  手机自动化推荐过滤关键词（全套一键复制）
                </p>
                <p className="text-xs text-[var(--md-sys-color-on-surface-variant)] break-words">
                  {SMS_FILTER_KEYWORDS}
                </p>
              </div>
              <button
                type="button"
                onClick={() => copyText("keywords", SMS_FILTER_KEYWORDS)}
                className="lt-btn-secondary shrink-0"
              >
                {copiedKey === "keywords" ? "已复制关键词" : "复制全套关键词"}
              </button>
            </div>

            <div className="border-t border-[var(--md-sys-color-outline-variant)]/70 pt-4 space-y-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <KeyRound className="h-4 w-4 text-[var(--md-sys-color-primary)]" aria-hidden="true" />
                <span className="text-xs font-semibold text-[var(--md-sys-color-on-surface)]">
                  转发签名验证密钥（可选）
                </span>
                <span
                  className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                    user?.has_sms_webhook_secret
                      ? "bg-[var(--md-sys-color-tertiary-container)] text-[var(--md-sys-color-on-tertiary-container)]"
                      : "bg-[var(--md-sys-color-surface-container-high)] text-[var(--md-sys-color-on-surface-variant)]"
                  }`}
                >
                  {user?.has_sms_webhook_secret ? "已启用校验" : "未设置（普通模式）"}
                </span>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="password"
                  value={hmacSecret}
                  onChange={(e) => setHmacSecret(e.target.value)}
                  placeholder="输入新的安全密钥（至少 6 位）"
                  aria-label="转发签名验证密钥"
                  className="lt-input max-w-sm"
                />
                <button
                  type="button"
                  disabled={!hmacSecret.trim() || saveSecretMut.isPending}
                  onClick={() => saveSecretMut.mutate(hmacSecret.trim())}
                  className="lt-btn-primary"
                >
                  保存安全密钥
                </button>
                {user?.has_sms_webhook_secret && (
                  <button
                    type="button"
                    disabled={saveSecretMut.isPending}
                    onClick={() => setClearSecretConfirmOpen(true)}
                    className="lt-btn-danger"
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>清除密钥</span>
                  </button>
                )}
              </div>
            </div>
          </section>

          <section className="lt-card p-6 space-y-4">
            <MdSegmentedButton<"ios" | "android" | "mac">
              value={smsPlatformTab}
              onChange={setSmsPlatformTab}
              ariaLabel="手机短信转发指南"
              options={[
                { value: "ios", label: "iPhone 快捷指令（3 步）" },
                { value: "android", label: "Android 短信转发器（3 步）" },
                { value: "mac", label: "Mac 信息转发" },
              ]}
            />

            {smsPlatformTab === "ios" && (
              <div className="space-y-3 text-xs text-[var(--md-sys-color-on-surface)] leading-relaxed">
                <ol className="list-decimal pl-5 space-y-1.5">
                  <li>打开“快捷指令”App →“自动化”→ 点击右上角“+”→ 选“信息”触发器并粘贴上方关键词。</li>
                  <li>勾选“立即运行”（关闭运行时通知），添加动作“获取 URL 内容”。</li>
                  <li>填入上方专属接收地址，方法选 POST，请求体选 JSON 并按下方模板填入短信变量。</li>
                </ol>
                <div className="lt-surface-tonal p-3.5 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold">快捷指令请求体模板</span>
                    <button
                      type="button"
                      onClick={() => copyText("ios", iosPayloadTemplate)}
                      className="lt-btn-secondary py-1 px-3"
                    >
                      {copiedKey === "ios" ? "已复制" : "复制模板"}
                    </button>
                  </div>
                  <pre className="overflow-x-auto font-mono text-[11px] text-[var(--md-sys-color-on-surface)]">
                    {iosPayloadTemplate}
                  </pre>
                </div>
              </div>
            )}

            {smsPlatformTab === "android" && (
              <div className="space-y-3 text-xs text-[var(--md-sys-color-on-surface)] leading-relaxed">
                <ol className="list-decimal pl-5 space-y-1.5">
                  <li>安装 SmsForwarder（短信转发器），授予读取短信和后台运行权限。</li>
                  <li>在“发送通道”添加 Webhook，填入上方接收地址，请求方式选 POST，编码选 JSON。</li>
                  <li>在“转发规则”填入上方司法/发票关键词；如设置了签名密钥，在通道 Secret 一栏填写一致即可。</li>
                </ol>
                <div className="lt-surface-tonal p-3.5 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold">SmsForwarder 请求体模板</span>
                    <button
                      type="button"
                      onClick={() => copyText("android", androidPayloadTemplate)}
                      className="lt-btn-secondary py-1 px-3"
                    >
                      {copiedKey === "android" ? "已复制" : "复制模板"}
                    </button>
                  </div>
                  <pre className="overflow-x-auto font-mono text-[11px] text-[var(--md-sys-color-on-surface)]">
                    {androidPayloadTemplate}
                  </pre>
                </div>
              </div>
            )}

            {smsPlatformTab === "mac" && (
              <div className="space-y-3 text-xs text-[var(--md-sys-color-on-surface-variant)]">
                <p className="flex items-center gap-1.5 text-[var(--md-sys-color-on-surface)] font-medium">
                  <ShieldCheck className="h-4 w-4 text-[var(--md-sys-color-primary)]" aria-hidden="true" />
                  在 iPhone 开启“信息转发”到 Mac 后，可将 Mac 收到的短信直接 POST 推送到上方专属地址。
                </p>
                <details className="lt-surface-tonal p-3.5">
                  <summary className="cursor-pointer font-semibold text-[var(--md-sys-color-on-surface)]">
                    开发调试命令示例
                  </summary>
                  <div className="mt-3 space-y-2">
                    <button
                      type="button"
                      onClick={() => copyText("mac", curlSample)}
                      className="lt-btn-secondary py-1 px-3"
                    >
                      {copiedKey === "mac" ? "已复制命令" : "复制测试命令"}
                    </button>
                    <pre className="overflow-x-auto font-mono text-[11px] text-[var(--md-sys-color-on-surface)]">
                      {curlSample}
                    </pre>
                  </div>
                </details>
              </div>
            )}
          </section>
        </div>
      )}

      {/* Tab 4: 推送记录 */}
      {activeTab === "deliveries" && (
        <section className="lt-card p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <MdSegmentedButton<string>
              value={statusFilter}
              onChange={setStatusFilter}
              ariaLabel="推送状态筛选"
              options={[
                { value: "", label: "全部" },
                { value: "sent", label: "已推送" },
                { value: "failed", label: "投递失败" },
                { value: "skipped_completed", label: "已办结跳过" },
              ]}
            />
            <button
              type="button"
              onClick={() => deliveriesQuery.refetch()}
              className="lt-btn-secondary"
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              <span>刷新</span>
            </button>
          </div>

          {deliveriesQuery.isLoading ? (
            <p className="py-8 text-center text-xs text-[var(--md-sys-color-on-surface-variant)]">
              正在加载推送记录...
            </p>
          ) : (deliveriesQuery.data ?? []).length === 0 ? (
            <p className="py-8 text-center text-xs text-[var(--md-sys-color-on-surface-variant)]">
              暂无符合条件的提醒推送记录
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-[var(--md-sys-color-outline-variant)]/70 text-xs">
                <thead>
                  <tr className="text-left font-semibold text-[var(--md-sys-color-on-surface-variant)]">
                    <th className="py-2.5 pr-4">事项与关联案件</th>
                    <th className="py-2.5 pr-4">类型</th>
                    <th className="py-2.5 pr-4">接收人</th>
                    <th className="py-2.5 pr-4">状态</th>
                    <th className="py-2.5 pr-4">触发 / 投递时间</th>
                    <th className="py-2.5">说明</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--md-sys-color-outline-variant)]/50">
                  {(deliveriesQuery.data ?? []).map((row) => {
                    const stMeta = DELIVERY_STATUS_META[row.status] ?? {
                      label: row.status,
                      badgeClass: "bg-[var(--md-sys-color-surface-container)] text-[var(--md-sys-color-on-surface)]",
                    };
                    return (
                      <tr key={row.id} className="align-top">
                        <td className="py-3 pr-4">
                          <div className="font-semibold text-[var(--md-sys-color-on-surface)]">
                            {row.source_title || `#${row.source_id}`}
                          </div>
                          {row.case_id && (
                            <Link
                              to={`/cases/${row.case_id}`}
                              className="mt-0.5 inline-block text-[11px] font-medium text-[var(--md-sys-color-primary)] hover:underline"
                            >
                              {row.case_title || "查看案件"}
                              {row.case_number ? `（${row.case_number}）` : ""}
                            </Link>
                          )}
                        </td>
                        <td className="py-3 pr-4 whitespace-nowrap text-[var(--md-sys-color-on-surface-variant)]">
                          {SOURCE_TYPE_LABELS[row.source_type] ?? row.source_type}
                        </td>
                        <td className="py-3 pr-4 text-[var(--md-sys-color-on-surface)]">
                          {row.recipient_names.length > 0
                            ? row.recipient_names.join("、")
                            : `${row.recipient_user_ids.length} 人`}
                        </td>
                        <td className="py-3 pr-4 whitespace-nowrap">
                          <span
                            className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${stMeta.badgeClass}`}
                          >
                            {stMeta.label}
                          </span>
                        </td>
                        <td className="py-3 pr-4 whitespace-nowrap text-[var(--md-sys-color-on-surface-variant)]">
                          <div>{formatShanghaiDateTime(row.sent_at || row.scheduled_at)}</div>
                          {row.retry_count > 0 && (
                            <div className="text-[11px]">已重试 {row.retry_count} 次</div>
                          )}
                        </td>
                        <td className="py-3 text-[var(--md-sys-color-on-surface-variant)]">
                          {row.error_msg ? (
                            <span className="text-[var(--md-sys-color-error)]">{row.error_msg}</span>
                          ) : (
                            "—"
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      <ConfirmDialog
        open={rotateConfirmOpen}
        onClose={() => setRotateConfirmOpen(false)}
        onConfirm={() => rotateTokenMut.mutate()}
        loading={rotateTokenMut.isPending}
        title="更换专属短信接收地址"
        description="更换后，旧的接收地址将立即失效。您需要将新地址重新复制到手机快捷指令或短信转发器中。"
        confirmLabel="确认更换"
        tone="danger"
      />

      <ConfirmDialog
        open={clearSecretConfirmOpen}
        onClose={() => setClearSecretConfirmOpen(false)}
        onConfirm={() => saveSecretMut.mutate(null)}
        loading={saveSecretMut.isPending}
        title="清除转发签名密钥"
        description="清除后将关闭该接收地址的签名校验，改为普通模式接收短信。确定继续吗？"
        confirmLabel="确认清除"
        tone="danger"
      />
    </div>
  );
}
