import { PageHeader } from "@/components/layout/page-header";
import { Copy, KeyRound, LoaderCircle, ShieldCheck, UserRoundCheck, UserRoundX } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";

import { createInviteCode, listInviteCodes, listUsers, resetUserPassword, revokeInviteCode, setUserStatus } from "./auth-api";
import { useAuth } from "./auth-provider";

export function MembersPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [label, setLabel] = useState("");
  const [expiresInDays, setExpiresInDays] = useState("7");
  const [maxUses, setMaxUses] = useState("10");
  const [noExpiry, setNoExpiry] = useState(false);
  const [unlimitedUses, setUnlimitedUses] = useState(false);
  const [noticeValue, setNoticeValue] = useState("");
  const [noticeLabel, setNoticeLabel] = useState("邀请码");
  const [error, setError] = useState("");
  const users = useQuery({ queryKey: ["auth-users"], queryFn: listUsers, enabled: Boolean(user?.is_owner) });
  const inviteCodes = useQuery({ queryKey: ["auth-invite-codes"], queryFn: listInviteCodes, enabled: Boolean(user?.is_owner) });
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["auth-users"] }),
      queryClient.invalidateQueries({ queryKey: ["auth-invite-codes"] }),
    ]);
  };
  const createMutation = useMutation({
    mutationFn: () => createInviteCode(
      label,
      noExpiry ? null : Number(expiresInDays),
      unlimitedUses ? null : Number(maxUses),
    ),
    onSuccess: async (response) => {
      setNoticeValue(response.invite_code.code);
      setNoticeLabel("邀请码");
      setLabel("");
      setError("");
      await refresh();
    },
    onError: (caught) => setError(caught instanceof ApiError ? caught.message : "创建邀请码失败。"),
  });
  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "active" | "disabled" }) => setUserStatus(id, status),
    onSuccess: refresh,
  });
  const revokeMutation = useMutation({ mutationFn: revokeInviteCode, onSuccess: refresh });
  const resetMutation = useMutation({
    mutationFn: (id: string) => resetUserPassword(id),
    onSuccess: (response) => {
      setNoticeValue(response.password);
      setNoticeLabel("新密码（仅显示本次）");
      setError("");
    },
    onError: (caught) => setError(caught instanceof ApiError ? caught.message : "重置密码失败。"),
  });
  if (!user?.is_owner) return <Navigate replace to="/agent" />;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    createMutation.mutate();
  };
  return (
    <section className="space-y-6">
      <PageHeader title="成员与邀请码" description="邀请伙伴加入，管理成员权限与访问状态。" icon={ShieldCheck} />
      <div className="rounded-xl border bg-card p-5">
        <h2 className="flex items-center gap-2 font-semibold"><KeyRound className="size-4" />创建邀请码</h2>
        <form className="mt-4 grid gap-3 sm:grid-cols-2" onSubmit={submit}>
          <label className="space-y-1 text-sm sm:col-span-2">
            <span className="font-medium">备注</span>
            <input className="h-10 w-full rounded-lg border bg-background px-3 outline-none focus:ring-2 focus:ring-ring" maxLength={80} onChange={(event) => setLabel(event.target.value)} placeholder="例如：第一批朋友" value={label} />
          </label>
          <label className="space-y-1 text-sm">
            <span className="font-medium">有效天数</span>
            <input className="h-10 w-full rounded-lg border bg-background px-3 outline-none focus:ring-2 focus:ring-ring" disabled={noExpiry} max={3650} min={1} onChange={(event) => setExpiresInDays(event.target.value)} required={!noExpiry} type="number" value={expiresInDays} />
            <span className="flex items-center gap-2 text-xs text-muted-foreground"><input checked={noExpiry} onChange={(event) => setNoExpiry(event.target.checked)} type="checkbox" />永久有效</span>
          </label>
          <label className="space-y-1 text-sm">
            <span className="font-medium">最多注册人数</span>
            <input className="h-10 w-full rounded-lg border bg-background px-3 outline-none focus:ring-2 focus:ring-ring" disabled={unlimitedUses} max={10000} min={1} onChange={(event) => setMaxUses(event.target.value)} required={!unlimitedUses} type="number" value={maxUses} />
            <span className="flex items-center gap-2 text-xs text-muted-foreground"><input checked={unlimitedUses} onChange={(event) => setUnlimitedUses(event.target.checked)} type="checkbox" />不限制人数</span>
          </label>
          <Button className="sm:col-span-2" disabled={createMutation.isPending} type="submit">
            {createMutation.isPending ? <LoaderCircle className="animate-spin" /> : <KeyRound />}
            生成邀请码
          </Button>
        </form>
        {error ? <p className="mt-3 text-sm text-destructive" role="alert">{error}</p> : null}
        {noticeValue ? (
          <div className="mt-4 rounded-lg bg-muted p-3">
            <p className="text-xs text-muted-foreground">{noticeLabel}</p>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate text-xs">{noticeValue}</code>
              <Button onClick={() => navigator.clipboard.writeText(noticeValue)} size="sm" type="button" variant="outline"><Copy />复制</Button>
            </div>
          </div>
        ) : null}
      </div>
      <div className="rounded-xl border bg-card p-5">
        <h2 className="font-semibold">邀请码</h2>
        <div className="mt-4 divide-y">
          {(inviteCodes.data?.items || []).map((item) => (
            <div className="flex flex-wrap items-center justify-between gap-3 py-3" key={item.id}>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{item.label || "未备注"} · {inviteCodeStatus(item.status)}</p>
                <p className="mt-1 font-mono text-xs">{item.code}</p>
                <p className="mt-1 text-xs text-muted-foreground">已注册 {item.use_count}/{item.max_uses ?? "不限"} · {item.expires_at ? `${new Date(item.expires_at).toLocaleString()} 到期` : "永久有效"}</p>
              </div>
              <div className="flex gap-2">
                <Button onClick={() => navigator.clipboard.writeText(item.code)} size="sm" variant="outline"><Copy />复制</Button>
                {item.status === "active" ? <Button disabled={revokeMutation.isPending} onClick={() => revokeMutation.mutate(item.id)} size="sm" variant="outline">停用</Button> : null}
              </div>
            </div>
          ))}
          {!inviteCodes.data?.items.length ? <p className="py-4 text-sm text-muted-foreground">还没有邀请码。</p> : null}
        </div>
      </div>
      <div className="rounded-xl border bg-card p-5">
        <h2 className="flex items-center gap-2 font-semibold"><ShieldCheck className="size-4" />成员</h2>
        <div className="mt-4 divide-y">
          {(users.data?.items || []).map((item) => (
            <div className="flex flex-wrap items-center justify-between gap-3 py-3" key={item.id}>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{item.display_name} {item.is_owner ? <span className="text-xs text-primary">所有者</span> : null}</p>
                <p className="truncate text-xs text-muted-foreground">{item.email}</p>
              </div>
              {!item.is_owner ? (
                <div className="flex gap-2">
                  {item.status === "active" ? <Button disabled={resetMutation.isPending} onClick={() => resetMutation.mutate(item.id)} size="sm" variant="outline">重置密码</Button> : null}
                  <Button
                    disabled={statusMutation.isPending}
                    onClick={() => statusMutation.mutate({ id: item.id, status: item.status === "active" ? "disabled" : "active" })}
                    size="sm"
                    variant="outline"
                  >
                    {item.status === "active" ? <UserRoundX /> : <UserRoundCheck />}
                    {item.status === "active" ? "禁用" : "启用"}
                  </Button>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function inviteCodeStatus(status: string) {
  return ({ active: "可使用", revoked: "已停用", expired: "已过期", exhausted: "人数已满" } as Record<string, string>)[status] || status;
}
