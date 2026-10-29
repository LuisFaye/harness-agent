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
    <section className="mx-auto w-full max-w-5xl space-y-6">
      <PageHeader title="成员与邀请码" description="邀请伙伴加入，管理成员权限与访问状态。" icon={ShieldCheck} />
      <div className="rounded-2xl border border-border/85 bg-card p-5 shadow-2xs sm:p-6">
        <h2 className="flex items-center gap-2 font-semibold tracking-tight"><KeyRound className="size-4 text-primary" />创建邀请码</h2>
        <form className="mt-4 grid gap-3.5 sm:grid-cols-2" onSubmit={submit}>
          <label className="space-y-1.5 text-sm sm:col-span-2">
            <span className="font-medium">备注</span>
            <input className="h-10 w-full rounded-xl border border-border/85 bg-background px-3.5 shadow-2xs outline-none transition-colors focus:border-primary/45 focus:ring-2 focus:ring-ring/30" maxLength={80} onChange={(event) => setLabel(event.target.value)} placeholder="例如：第一批朋友" value={label} />
          </label>
          <label className="space-y-1.5 text-sm">
            <span className="font-medium">有效天数</span>
            <input className="h-10 w-full rounded-xl border border-border/85 bg-background px-3.5 shadow-2xs outline-none transition-colors focus:border-primary/45 focus:ring-2 focus:ring-ring/30" disabled={noExpiry} max={3650} min={1} onChange={(event) => setExpiresInDays(event.target.value)} required={!noExpiry} type="number" value={expiresInDays} />
            <span className="flex items-center gap-2 text-xs text-muted-foreground"><input checked={noExpiry} onChange={(event) => setNoExpiry(event.target.checked)} type="checkbox" />永久有效</span>
          </label>
          <label className="space-y-1.5 text-sm">
            <span className="font-medium">最多注册人数</span>
            <input className="h-10 w-full rounded-xl border border-border/85 bg-background px-3.5 shadow-2xs outline-none transition-colors focus:border-primary/45 focus:ring-2 focus:ring-ring/30" disabled={unlimitedUses} max={10000} min={1} onChange={(event) => setMaxUses(event.target.value)} required={!unlimitedUses} type="number" value={maxUses} />
            <span className="flex items-center gap-2 text-xs text-muted-foreground"><input checked={unlimitedUses} onChange={(event) => setUnlimitedUses(event.target.checked)} type="checkbox" />不限制人数</span>
          </label>
          <Button className="sm:col-span-2" disabled={createMutation.isPending} type="submit">
            {createMutation.isPending ? <LoaderCircle className="animate-spin" /> : <KeyRound />}
            生成邀请码
          </Button>
        </form>
        {error ? <p className="mt-3 text-sm text-destructive" role="alert">{error}</p> : null}
        {noticeValue ? (
          <div className="mt-4 rounded-xl border border-border/75 bg-muted/50 p-3.5">
            <p className="text-xs font-medium text-muted-foreground">{noticeLabel}</p>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded-lg border border-border/60 bg-background px-2.5 py-1.5 font-mono text-xs">{noticeValue}</code>
              <Button onClick={() => navigator.clipboard.writeText(noticeValue)} size="sm" type="button" variant="outline"><Copy />复制</Button>
            </div>
          </div>
        ) : null}
      </div>
      <div className="rounded-2xl border border-border/85 bg-card p-5 shadow-2xs sm:p-6">
        <h2 className="font-semibold tracking-tight">邀请码</h2>
        <div className="mt-4 divide-y divide-border/65">
          {(inviteCodes.data?.items || []).map((item) => (
            <div className="flex flex-wrap items-center justify-between gap-3 py-3.5" key={item.id}>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="truncate text-sm font-medium">{item.label || "未备注"}</p>
                  <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${item.status === "active" ? "border-success/20 bg-success/10 text-success" : "border-border/70 bg-muted/60 text-muted-foreground"}`}>
                    {inviteCodeStatus(item.status)}
                  </span>
                </div>
                <p className="mt-1 font-mono text-xs text-foreground/85">{item.code}</p>
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
      <div className="rounded-2xl border border-border/85 bg-card p-5 shadow-2xs sm:p-6">
        <h2 className="flex items-center gap-2 font-semibold tracking-tight"><ShieldCheck className="size-4 text-primary" />成员</h2>
        <div className="mt-4 divide-y divide-border/65">
          {(users.data?.items || []).map((item) => (
            <div className="flex flex-wrap items-center justify-between gap-3 py-3.5" key={item.id}>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <p className="truncate text-sm font-medium">{item.display_name}</p>
                  {item.is_owner ? <span className="rounded-full border border-primary/20 bg-primary-soft px-2 py-0.5 text-xs font-medium text-primary">所有者</span> : null}
                </div>
                <p className="mt-0.5 truncate text-xs text-muted-foreground">{item.email}</p>
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
