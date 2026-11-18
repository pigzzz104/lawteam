import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { Check, Copy, KeyRound, RefreshCw, ShieldCheck, Smartphone } from "lucide-react";
import { authApi } from "@/api/client";
import { useAuth } from "@/context/AuthContext";
import { copyTextSafely } from "@/lib/domain";

export function PhoneSetupGuide() {
  const { user, refreshUser } = useAuth();
  const [platformTab, setPlatformTab] = useState<"ios" | "android" | "mac">(
    user?.phone_os === "android" ? "android" : "ios"
  );
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [hmacSecret, setHmacSecret] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const webhookUrl = user
    ? `${window.location.origin}/api/webhooks/sms/${user.sms_webhook_token}`
    : "";

  const copyText = async (key: string, text: string) => {
    const ok = await copyTextSafely(text);
    if (!ok) {
      setErrorMsg("浏览器剪贴板写入受限，请手动选择复制");
      return;
    }
    setErrorMsg(null);
    setCopiedKey(key);
    window.setTimeout(() => setCopiedKey(null), 2000);
  };

  const rotateTokenMut = useMutation({
    mutationFn: () => authApi.updateMe({ rotate_sms_token: true }),
    onSuccess: async () => {
      await refreshUser();
      setErrorMsg(null);
      setFeedback("已生成新的个人短信 Webhook Token，请同步更新手机端配置");
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const saveSecretMut = useMutation({
    mutationFn: () =>
      authApi.updateMe({ sms_webhook_secret: hmacSecret.trim() || null }),
    onSuccess: async () => {
      await refreshUser();
      setHmacSecret("");
      setErrorMsg(null);
      setFeedback("已更新 Webhook HMAC-SHA256 签名密钥");
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  const iosPayloadJson = `{
  "content": "快捷指令变量：短信内容",
  "sender": "快捷指令变量：发件人",
  "received_at": "当前日期(ISO 8601)",
  "client_platform": "ios_shortcuts"
}`;

  const androidJson = `{
  "content": "[msg]",
  "sender": "[from]",
  "received_at": "[receive_time]",
  "timestamp": "[timestamp]",
  "sign": "[sign]",
  "client_platform": "android_smsforwarder"
}`;

  const macCurlSample = `curl -X POST "${webhookUrl}" \\
  -H "Content-Type: application/json" \\
  -d '{"content":"【法院送达】请查收(2026)沪01民终6604号送达文书","sender":"12368","client_platform":"mac_relay"}'`;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-[var(--md-sys-color-on-surface)]">短信接入</h1>
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

      {/* Personal Webhook Endpoint & Secret */}
      <section className="lt-card p-6 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Smartphone className="h-5 w-5 text-[var(--md-sys-color-primary)]" aria-hidden="true" />
            <h2 className="text-base font-bold text-[var(--md-sys-color-on-surface)]">
              专属短信 Webhook 地址
            </h2>
          </div>
          <button
            type="button"
            disabled={rotateTokenMut.isPending}
            onClick={() => {
              if (window.confirm("重置 Token 后，旧地址将立即失效并需重新配置手机转发，确认重置？")) {
                rotateTokenMut.mutate();
              }
            }}
            className="lt-btn-secondary border-[#f6d48b] bg-[#fef0d1] text-[#764900] hover:bg-[#fde6b4]"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{rotateTokenMut.isPending ? "重置中..." : "重置接入 Token"}</span>
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            readOnly
            aria-label="个人 SMS Webhook URL"
            value={webhookUrl}
            className="lt-input flex-1 min-w-[260px] bg-[var(--md-sys-color-surface-container-low)] font-mono text-xs"
          />
          <button
            type="button"
            onClick={() => copyText("url", webhookUrl)}
            className="lt-btn-primary"
          >
            {copiedKey === "url" ? (
              <Check className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <Copy className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            <span>{copiedKey === "url" ? "已复制" : "复制地址"}</span>
          </button>
        </div>

        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            saveSecretMut.mutate();
          }}
          className="flex flex-wrap items-end gap-3 border-t border-[var(--md-sys-color-outline-variant)]/60 pt-4"
        >
          <div className="flex-1 min-w-[240px]">
            <label
              htmlFor="hmac-secret"
              className="block text-xs font-semibold text-[var(--md-sys-color-on-surface-variant)] mb-1"
            >
              可选 HMAC-SHA256 防伪密钥（当前：
              {user?.has_sms_webhook_secret ? "已启用校验" : "未设置，仅校验 URL Token"}）
            </label>
            <input
              id="hmac-secret"
              type="text"
              value={hmacSecret}
              onChange={(e) => setHmacSecret(e.target.value)}
              placeholder="输入新密钥（留空点击保存可清除）"
              className="lt-input"
            />
          </div>
          <button
            type="submit"
            disabled={saveSecretMut.isPending}
            className="lt-btn-secondary py-2"
          >
            <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{saveSecretMut.isPending ? "保存中..." : "保存签名密钥"}</span>
          </button>
        </form>
      </section>

      {/* Platform Guides */}
      <section className="lt-card p-6 space-y-4">
        <div className="lt-segmented" role="tablist">
          {(
            [
              { key: "ios", label: "iOS 快捷指令" },
              { key: "android", label: "Android SmsForwarder" },
              { key: "mac", label: "Mac 短信转发" },
            ] as const
          ).map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={platformTab === t.key}
              onClick={() => setPlatformTab(t.key)}
              className={`rounded-full px-4 py-1.5 text-xs font-semibold transition-colors ${
                platformTab === t.key
                  ? "bg-[var(--md-sys-color-primary)] text-[var(--md-sys-color-on-primary)] shadow-sm"
                  : "text-[var(--md-sys-color-on-surface-variant)] hover:text-[var(--md-sys-color-on-surface)]"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {platformTab === "ios" && (
          <div className="space-y-4 text-sm text-slate-700">
            <ol className="list-decimal pl-5 space-y-2 text-xs leading-relaxed">
              <li>
                打开 iPhone <strong>“快捷指令”</strong> App → 底部 <strong>“自动化”</strong> → 点击右上角 <strong>+</strong>。
              </li>
              <li>
                选择触发器 <strong>“信息”</strong>：发件人留空，信息包含关键词填写 <code>法院</code> / <code>送达</code> / <code>12309</code> / <code>看守所</code> / <code>发票</code>（可建多条自动化），勾选 <strong>“立即运行”</strong> 并关闭“运行时通知”。
              </li>
              <li>
                添加操作 <strong>“获取 URL 内容”</strong>：URL 填入上方您的专属 Webhook 地址，方法选 <code>POST</code>，请求体选 <code>JSON</code>。
              </li>
              <li>
                若启用了 HMAC 密钥，请添加请求头 <code>X-Webhook-Signature</code>（对短信内容 <code>content</code> 计算 HMAC-SHA256 的 hex 或 Base64 值）；未启用密钥时直接 POST 即可。
              </li>
            </ol>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-600">iOS 快捷指令 JSON 模板</span>
                <button
                  type="button"
                  onClick={() => copyText("ios", iosPayloadJson)}
                  className="text-xs font-semibold text-blue-700 hover:underline"
                >
                  {copiedKey === "ios" ? "已复制" : "复制 JSON"}
                </button>
              </div>
              <pre className="rounded-lg bg-slate-900 p-4 text-xs text-slate-100 overflow-x-auto">
                {iosPayloadJson}
              </pre>
            </div>
          </div>
        )}

        {platformTab === "android" && (
          <div className="space-y-4 text-sm text-slate-700">
            <ol className="list-decimal pl-5 space-y-2 text-xs leading-relaxed">
              <li>
                安装开源 <strong>SmsForwarder（短信转发器）</strong>，授予读取短信与后台保活权限。
              </li>
              <li>
                进入 <strong>“发送通道” → “添加 Webhook”</strong>：请求方式选 <code>POST</code>，WebServer 地址填入上方您的专属 Webhook URL，编码选 <code>JSON</code>。
              </li>
              <li>
                若设置了 HMAC 密钥，请将其填入 SmsForwarder 的 <code>Secret</code> 字段，并在下方 JSON 中保留 <code>timestamp</code> 与 <code>sign</code> 字段（服务端原生兼容 SmsForwarder 签名算法，亦支持请求头 <code>X-Webhook-Signature</code> / <code>X-Sms-Sign</code>）。
              </li>
              <li>
                在 <strong>“转发规则”</strong> 添加关键词过滤（法院、人民法院、12368、12309、看守所、仲裁、发票），保存测试即可。
              </li>
            </ol>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-600">SmsForwarder JSON Body 模板</span>
                <button
                  type="button"
                  onClick={() => copyText("android", androidJson)}
                  className="text-xs font-semibold text-blue-700 hover:underline"
                >
                  {copiedKey === "android" ? "已复制" : "复制 JSON"}
                </button>
              </div>
              <pre className="rounded-lg bg-slate-900 p-4 text-xs text-slate-100 overflow-x-auto">
                {androidJson}
              </pre>
            </div>
          </div>
        )}

        {platformTab === "mac" && (
          <div className="space-y-4 text-sm text-slate-700">
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs text-blue-900 flex gap-2">
              <ShieldCheck className="h-4 w-4 text-blue-700 shrink-0 mt-0.5" aria-hidden="true" />
              <span>
                开启 iPhone“信息转发”到 Mac 后，可在 Mac 监听 <code>~/Library/Messages/chat.db</code> 并通过下方接口推送至收件箱。
              </span>
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-600">推送请求示例 (curl)</span>
                <button
                  type="button"
                  onClick={() => copyText("mac", macCurlSample)}
                  className="text-xs font-semibold text-blue-700 hover:underline"
                >
                  {copiedKey === "mac" ? "已复制" : "复制命令"}
                </button>
              </div>
              <pre className="rounded-lg bg-slate-900 p-4 text-xs text-slate-100 overflow-x-auto">
                {macCurlSample}
              </pre>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
