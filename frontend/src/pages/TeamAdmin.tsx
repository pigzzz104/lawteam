import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Edit3, Plus, ShieldCheck, Users } from "lucide-react";
import { usersApi } from "@/api/client";
import { ModalDialog } from "@/components/ModalDialog";
import { useAuth } from "@/context/AuthContext";
import { NOTIFY_CHANNEL_META, SYSTEM_ROLE_LABELS } from "@/lib/domain";
import type { NotifyChannel, SystemRole, UserSummary } from "@/types/api";

export function TeamAdmin() {
  const { user } = useAuth();
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
  const [calendarColor, setCalendarColor] = useState("#2563eb");
  const [isActive, setIsActive] = useState(true);
  const [feedback, setFeedback] = useState<string | null>(null);
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
    setCalendarColor("#2563eb");
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
    setCalendarColor(u.calendar_color || "#2563eb");
    setIsActive(u.is_active);
    setModalError(null);
    setModalOpen(true);
  };

  const saveMut = useMutation({
    mutationFn: () => {
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
        calendar_color: calendarColor,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
      setModalOpen(false);
      setModalError(null);
      setFeedback(editingUser ? "成员信息已更新" : "已新增团队成员");
    },
    onError: (err: Error) => setModalError(err.message),
  });

  if (user?.system_role !== "admin") {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-6 text-sm text-amber-900">
        仅合伙人/管理员可以访问团队成员与权限管理页面。
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-[var(--md-sys-color-on-surface)]">团队成员</h1>
        <button type="button" onClick={openCreate} className="lt-btn-primary px-5 py-2.5 text-sm">
          <Plus className="h-4 w-4" aria-hidden="true" />
          <span>新增团队成员</span>
        </button>
      </div>

      {feedback && (
        <div role="status" className="lt-alert-success">
          {feedback}
        </div>
      )}

      <div className="lt-card overflow-hidden">
        {usersQuery.isLoading ? (
          <p className="py-12 text-center text-sm text-slate-600">正在加载成员列表...</p>
        ) : usersQuery.isError ? (
          <div role="alert" className="m-4 lt-alert-error">
            成员列表加载失败，请刷新重试。
          </div>
        ) : (usersQuery.data?.length ?? 0) === 0 ? (
          <p className="py-12 text-center text-sm text-slate-600">暂无团队成员记录。</p>
        ) : (
          <div tabIndex={0} role="region" aria-label="团队成员列表" className="table-wrapper">
            <table className="text-left text-xs">
              <thead className="bg-[var(--md-sys-color-surface-container-low)] text-[var(--md-sys-color-on-surface-variant)]">
                <tr>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">成员</th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">系统角色</th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">微信通道 / 目标</th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">核验尾号</th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold">状态</th>
                  <th className="border-b border-[var(--md-sys-color-outline-variant)] px-4 py-3 font-semibold text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {usersQuery.data!.map((u) => (
                  <tr key={u.id} className="hover:bg-slate-50/70">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2.5">
                        <span
                          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white"
                          style={{ backgroundColor: u.calendar_color || "#2563eb" }}
                        >
                          {u.full_name.slice(0, 1)}
                        </span>
                        <div>
                          <p className="font-bold text-slate-900">{u.full_name}</p>
                          <p className="font-mono text-[11px] text-slate-600">{u.username}</p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      <span className="inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-0.5 font-medium text-slate-800">
                        {u.system_role === "admin" ? (
                          <ShieldCheck className="h-3 w-3 text-blue-700" aria-hidden="true" />
                        ) : (
                          <Users className="h-3 w-3 text-slate-600" aria-hidden="true" />
                        )}
                        <span>{SYSTEM_ROLE_LABELS[u.system_role]}</span>
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <p className="font-medium text-slate-800">
                        {NOTIFY_CHANNEL_META[u.notify_channel]?.badge ?? u.notify_channel}
                      </p>
                      <p className="text-[11px] font-mono text-slate-600">
                        {u.notify_channel === "wecom"
                          ? u.wecom_userid || "未配置 UserID"
                          : u.notify_target || "未配置接收 Key"}
                      </p>
                    </td>
                    <td className="px-4 py-3 font-mono text-slate-700">
                      {u.phone_last4 ? `手机 *${u.phone_last4}` : "-"}
                      {u.id_card_last4 ? ` · 证 *${u.id_card_last4}` : ""}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                          u.is_active
                            ? "bg-emerald-100 text-emerald-800"
                            : "bg-slate-200 text-slate-700"
                        }`}
                      >
                        {u.is_active ? "正常" : "停用"}
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
          className="space-y-3"
        >
          {modalError && (
            <div role="alert" className="lt-alert-error">
              {modalError}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="tm-user" className="block text-xs font-semibold text-slate-700 mb-1">
                登录用户名 *
              </label>
              <input
                id="tm-user"
                type="text"
                required
                autoFocus={!editingUser}
                disabled={Boolean(editingUser)}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="lt-input disabled:bg-slate-100"
              />
            </div>
            <div>
              <label htmlFor="tm-name" className="block text-xs font-semibold text-slate-700 mb-1">
                姓名 *
              </label>
              <input
                id="tm-name"
                type="text"
                required
                autoFocus={Boolean(editingUser)}
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className="lt-input"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor="tm-role" className="block text-xs font-semibold text-slate-700 mb-1">
                系统角色 *
              </label>
              <select
                id="tm-role"
                value={systemRole}
                onChange={(e) => setSystemRole(e.target.value as SystemRole)}
                className="lt-input"
              >
                <option value="admin">合伙人/管理员</option>
                <option value="lawyer">执业律师</option>
                <option value="assistant">律师助理</option>
              </select>
            </div>
            <div>
              <label htmlFor="tm-pwd" className="block text-xs font-semibold text-slate-700 mb-1">
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
              <label htmlFor="tm-ch" className="block text-xs font-semibold text-slate-700 mb-1">
                默认微信推送通道
              </label>
              <select
                id="tm-ch"
                value={notifyChannel}
                onChange={(e) => setNotifyChannel(e.target.value as NotifyChannel)}
                className="lt-input"
              >
                {(Object.keys(NOTIFY_CHANNEL_META) as NotifyChannel[]).map((ch) => (
                  <option key={ch} value={ch}>
                    {NOTIFY_CHANNEL_META[ch].badge} ({ch})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="tm-target" className="block text-xs font-semibold text-slate-700 mb-1">
                {notifyChannel === "wecom" ? "企业微信 UserID" : "个人微信推送 Key/UID"}
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

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-center">
            <div>
              <label htmlFor="tm-phone4" className="block text-xs font-semibold text-slate-700 mb-1">
                手机号后4位
              </label>
              <input
                id="tm-phone4"
                type="text"
                maxLength={4}
                value={phoneLast4}
                onChange={(e) => setPhoneLast4(e.target.value)}
                placeholder="如 8966"
                className="lt-input"
              />
            </div>
            <div>
              <label htmlFor="tm-id4" className="block text-xs font-semibold text-slate-700 mb-1">
                证件号后4位
              </label>
              <input
                id="tm-id4"
                type="text"
                maxLength={4}
                value={idCardLast4}
                onChange={(e) => setIdCardLast4(e.target.value)}
                placeholder="如 101X"
                className="lt-input"
              />
            </div>
            <div>
              <label htmlFor="tm-os" className="block text-xs font-semibold text-slate-700 mb-1">
                手机系统
              </label>
              <select
                id="tm-os"
                value={phoneOs}
                onChange={(e) => setPhoneOs(e.target.value)}
                className="lt-input"
              >
                <option value="ios">iOS</option>
                <option value="android">Android</option>
              </select>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
            <div className="flex items-center gap-3">
              <label htmlFor="tm-color" className="text-xs font-semibold text-slate-700">
                日历色块
              </label>
              <input
                id="tm-color"
                type="color"
                value={calendarColor}
                onChange={(e) => setCalendarColor(e.target.value)}
                className="h-8 w-14 rounded border border-slate-300 cursor-pointer"
              />
            </div>

            {editingUser && (
              <label className="inline-flex items-center gap-2 text-xs text-slate-700 cursor-pointer">
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(e) => setIsActive(e.target.checked)}
                />
                <span>账号处于启用状态</span>
              </label>
            )}
          </div>

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
