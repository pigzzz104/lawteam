import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Edit3, Play, Plus, ShieldCheck, Users } from "lucide-react";
import { remindersApi, usersApi } from "@/api/client";
import { ModalDialog } from "@/components/ModalDialog";
import { MdColorSwatches, MdSelect, MdSwitch } from "@/components/ui";
import { useAuth } from "@/context/AuthContext";
import { NOTIFY_CHANNEL_META, SYSTEM_ROLE_LABELS } from "@/lib/domain";
import type { CourtAutoFetchMode, NotifyChannel, SystemRole, UserSummary } from "@/types/api";

const COURT_FETCH_MODE_LABELS: Record<CourtAutoFetchMode, string> = {
  confirm_before_sign: "签收前一律人工确认",
  auto_non_judgment: "仅程序性文书自动签收（推荐）",
  auto_all: "全部司法送达自动取回",
};

export function TeamAdmin() {
  const { user, refreshUser } = useAuth();
  const queryClient = useQueryClient();

  const [modalOpen, setModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<UserSummary | null>(null);
  const [username, setUsername] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [systemRole, setSystemRole] = useState<SystemRole>("lawyer");
  const [notifyChannel, setNotifyChannel] = useState<NotifyChannel>("serverchan");
  const [notifyTarget, setNotifyTarget] = useState("");
  const [wecomUserid, setWecomUserid] = useState("");
  const [phoneOs, setPhoneOs] = useState("ios");
  const [phoneLast4, setPhoneLast4] = useState("");
  const [idCardLast4, setIdCardLast4] = useState("");
  const [courtFetchMode, setCourtFetchMode] = useState<CourtAutoFetchMode>("confirm_before_sign");
  const [calendarColor, setCalendarColor] = useState("#1b56b3");
  const [isActive, setIsActive] = useState(true);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);

  const usersQuery = useQuery({
    queryKey: ["users", "all"],
    queryFn: usersApi.list,
  });

  const openCreate = () => {
    setEditingUser(null);
    setUsername("");
    setFullName("");
    setPassword("");
    setSystemRole("lawyer");
    setNotifyChannel("serverchan");
    setNotifyTarget("");
    setWecomUserid("");
    setPhoneOs("ios");
    setPhoneLast4("");
    setIdCardLast4("");
    setCourtFetchMode("confirm_before_sign");
    setCalendarColor("#1b56b3");
    setIsActive(true);
    setModalError(null);
    setModalOpen(true);
  };

  const openEdit = (u: UserSummary) => {
    setEditingUser(u);
    setUsername(u.username);
    setFullName(u.full_name);
    setPassword("");
    setSystemRole(u.system_role);
    setNotifyChannel(u.notify_channel || "wecom");
    setNotifyTarget(u.notify_target || "");
    setWecomUserid(u.wecom_userid || "");
    setPhoneOs(u.phone_os || "ios");
    setPhoneLast4(u.phone_last4 || "");
    setIdCardLast4(u.id_card_last4 || "");
    setCourtFetchMode(u.court_auto_fetch_mode || "confirm_before_sign");
    setCalendarColor(u.calendar_color || "#1b56b3");
    setIsActive(u.is_active);
    setModalError(null);
    setModalOpen(true);
  };

  const saveMut = useMutation({
    mutationFn: async () => {
      if (editingUser) {
        return usersApi.update(editingUser.id, {
          full_name: fullName.trim(),
          system_role: systemRole,
          notify_channel: notifyChannel,
          notify_target: notifyTarget.trim() || null,
          wecom_userid: wecomUserid.trim() || null,
          phone_os: phoneOs,
          phone_last4: phoneLast4.trim() || null,
          id_card_last4: idCardLast4.trim() || null,
          court_auto_fetch_mode: courtFetchMode,
          calendar_color: calendarColor,
          is_active: isActive,
          ...(password.trim() ? { new_password: password.trim() } : {}),
        });
      }
      return usersApi.create({
        username: username.trim(),
        full_name: fullName.trim(),
        password: password.trim(),
        system_role: systemRole,
        notify_channel: notifyChannel,
        notify_target: notifyTarget.trim() || null,
        wecom_userid: wecomUserid.trim() || null,
        phone_os: phoneOs,
        phone_last4: phoneLast4.trim() || null,
        id_card_last4: idCardLast4.trim() || null,
        court_auto_fetch_mode: courtFetchMode,
        calendar_color: calendarColor,
      });
    },
    onSuccess: async (saved) => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
      queryClient.invalidateQueries({ queryKey: ["cases"] });
      if (user && saved.id === user.id) {
        await refreshUser();
      }
      setModalOpen(false);
      setModalError(null);
      setErrorMsg(null);
      setFeedback(editingUser ? "成员信息已更新" : "已新增团队成员");
    },
    onError: (err: Error) => setModalError(err.message),
  });

  const runSweepMut = useMutation({
    mutationFn: () => remindersApi.runOnce(),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["reminder-deliveries"] });
      setErrorMsg(null);
      setFeedback(
        `全所扫描完成：投递 ${res.sent_count} 条，已办结跳过 ${res.skipped_completed_count} 条，去重 ${res.already_processed_count} 条，失败 ${res.failed_count} 条`
      );
    },
    onError: (err: Error) => {
      setFeedback(null);
      setErrorMsg(err.message);
    },
  });

  if (user?.system_role !== "admin") {
    return (
      <div role="alert" className="lt-alert-error">
        仅合伙人/管理员可以访问团队成员与全所调度管理。
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-[var(--md-sys-color-on-surface)]">团队成员</h1>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={runSweepMut.isPending}
            onClick={() => runSweepMut.mutate()}
            className="lt-btn-accent"
          >
            <Play className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{runSweepMut.isPending ? "扫描中..." : "立即扫描全所到期提醒"}</span>
          </button>
          <button type="button" onClick={openCreate} className="lt-btn-primary">
            <Plus className="h-4 w-4" aria-hidden="true" />
            <span>新增团队成员</span>
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

      <div className="lt-card overflow-hidden">
        {usersQuery.isLoading ? (
          <p className="py-12 text-center text-sm text-[var(--md-sys-color-on-surface-variant)]">
            正在加载成员列表...
          </p>
        ) : usersQuery.isError ? (
          <div role="alert" className="m-4 lt-alert-error">
            成员列表加载失败，请刷新重试。
          </div>
        ) : (usersQuery.data?.length ?? 0) === 0 ? (
          <p className="py-12 text-center text-sm text-[var(--md-sys-color-on-surface-variant)]">
            暂无团队成员记录。
          </p>
        ) : (
          <div tabIndex={0} role="region" aria-label="团队成员列表" className="table-wrapper">
            <table className="text-left text-xs">
              <thead className="bg-[var(--md-sys-color-surface-container-low)] text-[var(--md-sys-color-on-surface-variant)]">
                <tr>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">
                    成员
                  </th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">
                    系统角色
                  </th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">
                    提醒通道 / 接收目标
                  </th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">
                    签收策略与尾号
                  </th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">
                    状态
                  </th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold text-right">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--md-sys-color-outline-variant)]/50">
                {usersQuery.data!.map((u) => (
                  <tr
                    key={u.id}
                    className="hover:bg-[var(--md-sys-color-surface-container-low)]/50"
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2.5">
                        <span
                          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white"
                          style={{ backgroundColor: u.calendar_color || "#1b56b3" }}
                        >
                          {u.full_name.slice(0, 1)}
                        </span>
                        <div>
                          <p className="font-bold text-[var(--md-sys-color-on-surface)]">
                            {u.full_name}
                          </p>
                          <p className="font-mono text-[11px] text-[var(--md-sys-color-on-surface-variant)]">
                            {u.username}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      <span className="inline-flex items-center gap-1 rounded-full bg-[var(--md-sys-color-surface-container)] px-2.5 py-0.5 font-medium">
                        {u.system_role === "admin" ? (
                          <ShieldCheck
                            className="h-3 w-3 text-[var(--md-sys-color-primary)]"
                            aria-hidden="true"
                          />
                        ) : (
                          <Users
                            className="h-3 w-3 text-[var(--md-sys-color-on-surface-variant)]"
                            aria-hidden="true"
                          />
                        )}
                        <span>{SYSTEM_ROLE_LABELS[u.system_role]}</span>
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <p className="font-medium">
                        {NOTIFY_CHANNEL_META[u.notify_channel]?.badge ?? u.notify_channel}
                      </p>
                      <p className="text-[11px] font-mono text-[var(--md-sys-color-on-surface-variant)]">
                        {u.notify_channel === "wecom"
                          ? u.wecom_userid || "未配置 UserID"
                          : u.notify_target || "未配置接收 Key"}
                      </p>
                    </td>
                    <td className="px-4 py-3 text-[var(--md-sys-color-on-surface-variant)]">
                      <div>
                        {u.court_auto_fetch_mode
                          ? COURT_FETCH_MODE_LABELS[u.court_auto_fetch_mode]
                          : "按个人默认"}
                      </div>
                      <div className="font-mono text-[11px]">
                        {u.phone_last4 ? `手机 *${u.phone_last4}` : "未填手机尾号"}
                        {u.id_card_last4 ? ` · 证 *${u.id_card_last4}` : ""}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${
                          u.is_active
                            ? "bg-[var(--md-sys-color-tertiary-container)] text-[var(--md-sys-color-on-tertiary-container)]"
                            : "bg-[var(--md-sys-color-surface-container-high)] text-[var(--md-sys-color-on-surface-variant)]"
                        }`}
                      >
                        {u.is_active ? "正常" : "已停用"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        type="button"
                        onClick={() => openEdit(u)}
                        className="lt-btn-secondary py-1 px-3 text-xs"
                      >
                        <Edit3 className="h-3 w-3" aria-hidden="true" />
                        <span>编辑</span>
                      </button>
                    </td>
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
        title={editingUser ? `编辑成员：${editingUser.full_name}` : "新增团队成员"}
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
              <label htmlFor="tm-user" className="block text-xs font-semibold mb-1">
                登录用户名 *
              </label>
              <input
                id="tm-user"
                type="text"
                required
                disabled={Boolean(editingUser)}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="lt-input disabled:bg-[var(--md-sys-color-surface-container)]"
              />
            </div>
            <div>
              <label htmlFor="tm-name" className="block text-xs font-semibold mb-1">
                姓名 *
              </label>
              <input
                id="tm-name"
                type="text"
                required
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className="lt-input"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="tm-role" className="block text-xs font-semibold mb-1">
                系统角色 *
              </label>
              <MdSelect
                id="tm-role"
                disabled={Boolean(editingUser && user && editingUser.id === user.id)}
                value={systemRole}
                onChange={(e) => setSystemRole(e.target.value as SystemRole)}
              >
                <option value="admin">合伙人/管理员</option>
                <option value="lawyer">执业律师</option>
                <option value="assistant">律师助理</option>
              </MdSelect>
            </div>
            <div>
              <label htmlFor="tm-pwd" className="block text-xs font-semibold mb-1">
                {editingUser ? "重置密码（留空不修改）" : "初始密码 *"}
              </label>
              <input
                id="tm-pwd"
                type="password"
                required={!editingUser}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="lt-input"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="tm-ch" className="block text-xs font-semibold mb-1">
                默认提醒通道
              </label>
              <MdSelect
                id="tm-ch"
                value={notifyChannel}
                onChange={(e) => setNotifyChannel(e.target.value as NotifyChannel)}
              >
                {(Object.keys(NOTIFY_CHANNEL_META) as NotifyChannel[]).map((ch) => (
                  <option key={ch} value={ch}>
                    {NOTIFY_CHANNEL_META[ch].label}
                  </option>
                ))}
              </MdSelect>
            </div>
            <div>
              <label htmlFor="tm-target" className="block text-xs font-semibold mb-1">
                {notifyChannel === "wecom" ? "企业微信 UserID" : "接收密钥 / UID"}
              </label>
              <input
                id="tm-target"
                type="text"
                value={notifyChannel === "wecom" ? wecomUserid : notifyTarget}
                onChange={(e) =>
                  notifyChannel === "wecom"
                    ? setWecomUserid(e.target.value)
                    : setNotifyTarget(e.target.value)
                }
                className="lt-input"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label htmlFor="tm-phone4" className="block text-xs font-semibold mb-1">
                手机号后4位
              </label>
              <input
                id="tm-phone4"
                type="text"
                maxLength={4}
                value={phoneLast4}
                onChange={(e) => setPhoneLast4(e.target.value.replace(/\D/g, ""))}
                placeholder="如 8966"
                className="lt-input"
              />
            </div>
            <div>
              <label htmlFor="tm-id4" className="block text-xs font-semibold mb-1">
                证件号后4位
              </label>
              <input
                id="tm-id4"
                type="text"
                maxLength={4}
                value={idCardLast4}
                onChange={(e) => setIdCardLast4(e.target.value.trim().toUpperCase())}
                placeholder="如 101X"
                className="lt-input"
              />
            </div>
            <div>
              <label htmlFor="tm-os" className="block text-xs font-semibold mb-1">
                常用手机系统
              </label>
              <MdSelect id="tm-os" value={phoneOs} onChange={(e) => setPhoneOs(e.target.value)}>
                <option value="ios">iPhone / iOS</option>
                <option value="android">Android</option>
              </MdSelect>
            </div>
          </div>

          <div>
            <label htmlFor="tm-fetch-mode" className="block text-xs font-semibold mb-1">
              法院电子送达文书签收策略
            </label>
            <MdSelect
              id="tm-fetch-mode"
              value={courtFetchMode}
              onChange={(e) => setCourtFetchMode(e.target.value as CourtAutoFetchMode)}
            >
              {(Object.keys(COURT_FETCH_MODE_LABELS) as CourtAutoFetchMode[]).map((mode) => (
                <option key={mode} value={mode}>
                  {COURT_FETCH_MODE_LABELS[mode]}
                </option>
              ))}
            </MdSelect>
          </div>

          <div>
            <span className="mb-2 block text-xs font-semibold">日历标识色</span>
            <MdColorSwatches value={calendarColor} onChange={setCalendarColor} />
          </div>

          {editingUser && (
            <div className="pt-1 border-t border-[var(--md-sys-color-outline-variant)]/55">
              <MdSwitch
                checked={isActive}
                disabled={Boolean(user && editingUser.id === user.id)}
                onChange={setIsActive}
                label="账号处于启用状态"
                description={
                  user && editingUser.id === user.id
                    ? "不能停用或降权当前正在登录的管理员账号"
                    : "停用后该成员将不再接收任何提醒推送"
                }
              />
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={() => setModalOpen(false)} className="lt-btn-secondary">
              取消
            </button>
            <button type="submit" disabled={saveMut.isPending} className="lt-btn-primary">
              {saveMut.isPending ? "保存中..." : "保存成员"}
            </button>
          </div>
        </form>
      </ModalDialog>
    </div>
  );
}
