import { AgentMark } from "@/components/layout/agent-mark";
import { KeyRound, LoaderCircle, LockKeyhole, Mail, UserRoundPlus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";

import { useAuth } from "./auth-provider";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const { loading, setupRequired, user } = useAuth();
  const location = useLocation();
  if (loading) {
    return <AuthLoading />;
  }
  if (!user) {
    return (
      <Navigate
        replace
        state={{ from: location.pathname }}
        to={setupRequired ? "/setup" : "/login"}
      />
    );
  }
  return children;
}

export function OwnerGate({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  return user?.is_owner ? children : <Navigate replace to="/agent" />;
}

export function LoginPage() {
  const { loading, login, setupRequired, user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  if (!loading && setupRequired) {
    return <Navigate replace to="/setup" />;
  }
  const loginState = location.state as { from?: string } | null;
  if (user) {
    const from = loginState?.from || "/agent";
    return <Navigate replace to={from} />;
  }
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await login(email, password);
      navigate(loginState?.from || "/agent", { replace: true });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <AuthLayout description="使用受邀注册的邮箱进入你的私人办公空间" title="登录 SB Agent">
      <form className="space-y-4" onSubmit={submit}>
        <AuthField autoComplete="email" icon={Mail} label="邮箱" onChange={setEmail} type="email" value={email} />
        <AuthField autoComplete="current-password" icon={LockKeyhole} label="密码" onChange={setPassword} type="password" value={password} />
        <AuthError message={error} />
        <Button className="w-full" disabled={submitting} type="submit">
          {submitting ? <LoaderCircle className="animate-spin" /> : null}
          登录
        </Button>
        <p className="text-center text-sm text-muted-foreground">
          有邀请码？<Link className="font-medium text-primary hover:underline" to="/register">注册账号</Link>
        </p>
      </form>
    </AuthLayout>
  );
}

export function SetupPage() {
  const { bootstrapOwner, loading, setupRequired, user } = useAuth();
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  if (user) return <Navigate replace to="/agent" />;
  if (!loading && !setupRequired) return <Navigate replace to="/login" />;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await bootstrapOwner(email, displayName, password);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <AuthLayout description="创建首位所有者。系统初始化完成后，这个入口会自动关闭。" title="初始化工作台">
      <form className="space-y-4" onSubmit={submit}>
        <AuthField autoComplete="name" icon={UserRoundPlus} label="显示名称" onChange={setDisplayName} value={displayName} />
        <AuthField autoComplete="email" icon={Mail} label="邮箱" onChange={setEmail} type="email" value={email} />
        <AuthField autoComplete="new-password" help="至少 6 位" icon={LockKeyhole} label="密码" minLength={6} onChange={setPassword} type="password" value={password} />
        <AuthError message={error} />
        <Button className="w-full" disabled={submitting} type="submit">
          {submitting ? <LoaderCircle className="animate-spin" /> : null}
          创建所有者
        </Button>
      </form>
    </AuthLayout>
  );
}

export function RegisterPage() {
  const { loading, register, setupRequired, user } = useAuth();
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  if (user) return <Navigate replace to="/agent" />;
  if (!loading && setupRequired) return <Navigate replace to="/setup" />;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await register(code, email, displayName, password);
      navigate("/agent", { replace: true });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <AuthLayout description="填写管理员提供的邀请码，注册后立即进入自己的独立工作空间。" title="注册 SB Agent">
      <form className="space-y-4" onSubmit={submit}>
        <AuthField autoComplete="one-time-code" icon={KeyRound} label="邀请码" onChange={setCode} value={code} />
        <AuthField autoComplete="name" icon={UserRoundPlus} label="显示名称" onChange={setDisplayName} value={displayName} />
        <AuthField autoComplete="email" icon={Mail} label="邮箱" onChange={setEmail} type="email" value={email} />
        <AuthField autoComplete="new-password" help="至少 6 位" icon={LockKeyhole} label="密码" minLength={6} onChange={setPassword} type="password" value={password} />
        <AuthError message={error} />
        <Button className="w-full" disabled={submitting || !code.trim()} type="submit">
          {submitting ? <LoaderCircle className="animate-spin" /> : null}
          完成注册
        </Button>
        <p className="text-center text-sm text-muted-foreground">
          已有账号？<Link className="font-medium text-primary hover:underline" to="/login">返回登录</Link>
        </p>
      </form>
    </AuthLayout>
  );
}

function AuthLayout({ children, description, title }: { children: React.ReactNode; description: string; title: string }) {
  return (
    <main className="auth-layout grid min-h-svh place-items-center bg-muted/30 px-4 py-10">
      <section className="w-full max-w-md rounded-2xl border bg-background p-6 shadow-xl shadow-black/5 sm:p-8">
        <div className="mb-7 flex items-center gap-3">
          <AgentMark className="size-11" />
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
            <p className="mt-1 text-sm text-muted-foreground">{description}</p>
          </div>
        </div>
        {children}
      </section>
    </main>
  );
}

function AuthField({ autoComplete, help, icon: Icon, label, minLength, onChange, type = "text", value }: {
  autoComplete: string;
  help?: string;
  icon: typeof Mail;
  label: string;
  minLength?: number;
  onChange: (value: string) => void;
  type?: string;
  value: string;
}) {
  return (
    <label className="block space-y-1.5 text-sm font-medium">
      <span>{label}{help ? <span className="ml-2 font-normal text-muted-foreground">{help}</span> : null}</span>
      <span className="flex items-center rounded-lg border bg-background px-3 focus-within:ring-2 focus-within:ring-ring">
        <Icon aria-hidden="true" className="size-4 text-muted-foreground" />
        <input
          autoComplete={autoComplete}
          className="h-11 w-full bg-transparent px-3 text-sm outline-none placeholder:text-muted-foreground"
          minLength={minLength}
          onChange={(event) => onChange(event.target.value)}
          required
          type={type}
          value={value}
        />
      </span>
    </label>
  );
}

function AuthError({ message }: { message: string }) {
  return message ? <p className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">{message}</p> : null;
}

function AuthLoading() {
  return <main className="grid min-h-svh place-items-center"><LoaderCircle aria-label="正在检查登录状态" className="animate-spin text-muted-foreground" /></main>;
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "操作失败，请稍后重试。";
}
