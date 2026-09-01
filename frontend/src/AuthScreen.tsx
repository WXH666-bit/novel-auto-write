import { FormEvent, useMemo, useState } from "react";
import {
  ArrowRight,
  CheckCircle2,
  ChevronLeft,
  CircleAlert,
  KeyRound,
  LockKeyhole,
  Mail,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import {
  apiErrorCode,
  apiErrorStatus,
  forgotPassword,
  deleteAccount,
  changePassword,
  loginAccount,
  normalizeAuthSession,
  registerAccount,
  resendVerification,
  resetPassword,
  verifyEmail,
} from "./api";
import type { AuthSession, AuthView } from "./types";

type AuthScreenProps = {
  initialView?: AuthView;
  token?: string;
  onAuthenticated: (session: AuthSession) => void;
  onNavigate?: (view: AuthView) => void;
};

const pageCopy: Record<Exclude<AuthView, "account">, { eyebrow: string; title: string; detail: string }> = {
  login: {
    eyebrow: "RETURN TO THE MANUSCRIPT",
    title: "继续你的故事",
    detail: "每个用户拥有独立的故事正典、章节与模型密钥。",
  },
  register: {
    eyebrow: "OPEN A NEW NOTEBOOK",
    title: "建立你的编剧室",
    detail: "注册后先验证邮箱，再开始保存第一套故事正典。",
  },
  "verify-email": {
    eyebrow: "VERIFY THE MARGIN",
    title: "确认你的邮箱",
    detail: "验证邮箱后，章节、设定与 Provider 才会进入你的私有工作区。",
  },
  "forgot-password": {
    eyebrow: "RECOVER THE THREAD",
    title: "找回账号",
    detail: "我们会发送一封有效期有限的重置邮件。无论邮箱是否存在，提示都保持一致。",
  },
  "reset-password": {
    eyebrow: "RESTORE ACCESS",
    title: "设置新密码",
    detail: "新密码保存后，其他设备上的会话可以一并撤销。",
  },
};

function takeUrlToken() {
  if (typeof window === "undefined") return "";
  const queryToken = new URLSearchParams(window.location.search).get("token") || "";
  const fragment = window.location.hash.replace(/^#/, "");
  const fragmentToken = new URLSearchParams(fragment).get("token") || "";
  const token = fragmentToken || queryToken;
  if (token) {
    window.history.replaceState(window.history.state, "", window.location.pathname);
  }
  return token;
}

function getAuthViewFromPath() {
  if (typeof window === "undefined") return undefined;
  const segment = window.location.pathname
    .replace(/\/+$/, "")
    .split("/")
    .pop()
    ?.toLowerCase();
  if (segment === "verify-email") return "verify-email" as const;
  if (segment === "reset-password") return "reset-password" as const;
  return undefined;
}

function friendlyError(error: unknown) {
  const code = apiErrorCode(error);
  if (code === "email_not_verified") return "邮箱尚未验证，请先查收验证邮件。";
  if (code === "invalid_credentials") return "邮箱或密码不正确。";
  if (code === "account_locked") return "登录尝试过多，请稍后再试。";
  if (code === "token_expired") return "链接已过期，请重新发送。";
  if (apiErrorStatus(error) === 429) return "操作过于频繁，请稍后再试。";
  return error instanceof Error ? error.message : "操作没有完成，请稍后再试。";
}

export default function AuthScreen({
  initialView = "login",
  token: tokenProp,
  onAuthenticated,
  onNavigate,
}: AuthScreenProps) {
  const [initialToken] = useState(() => tokenProp || takeUrlToken());
  const [view, setView] = useState<AuthView>(
    getAuthViewFromPath() ||
      (initialToken
        ? initialView === "verify-email"
          ? "verify-email"
          : "reset-password"
        : initialView),
  );
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [token, setToken] = useState(initialToken);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const copy = pageCopy[view === "account" ? "login" : view];
  const switchView = (next: AuthView) => {
    setView(next);
    setError("");
    setMessage("");
    onNavigate?.(next);
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (view === "login") {
        const session = await loginAccount({ email: email.trim(), password });
        if (!session.user.is_email_verified) {
          setMessage("登录成功，但邮箱尚未验证。请先完成验证。" );
          switchView("verify-email");
        } else {
          onAuthenticated(session);
        }
      } else if (view === "register") {
        await registerAccount({
          email: email.trim(),
          password,
          display_name: displayName.trim() || undefined,
        });
        setMessage("验证邮件已发送。请在 24 小时内点击邮件中的链接。" );
        switchView("verify-email");
      } else if (view === "verify-email") {
        const result = await verifyEmail(token.trim());
        const session = normalizeAuthSession(result);
        if (session.user.id && session.user.email) onAuthenticated(session);
        else {
          setMessage("邮箱已验证，请使用密码登录。" );
          switchView("login");
        }
      } else if (view === "forgot-password") {
        await forgotPassword(email.trim());
        setMessage("如果该邮箱已注册，重置链接会很快送达；请检查收件箱和垃圾邮件。" );
      } else if (view === "reset-password") {
        await resetPassword({ token: token.trim(), password: newPassword });
        setMessage("密码已重置，请使用新密码登录。" );
        setPassword("");
        setNewPassword("");
        switchView("login");
      }
    } catch (requestError) {
      if (view === "login" && apiErrorCode(requestError) === "email_not_verified") {
        switchView("verify-email");
        setMessage("邮箱尚未验证。请粘贴邮件中的令牌后继续。" );
      } else {
        setError(friendlyError(requestError));
      }
    } finally {
      setBusy(false);
    }
  };

  const formHeading = useMemo(() => {
    if (view === "verify-email") return token ? "粘贴验证链接中的令牌" : "打开邮件中的验证链接";
    if (view === "reset-password") return "用一次性链接设置新密码";
    return "你的故事，从这里继续";
  }, [token, view]);

  return (
    <div className="auth-shell">
      <div className="auth-manuscript" aria-hidden="true">
        <div className="auth-manuscript-top">
          <span className="auth-seal"><BookSeal /></span>
          <span>章回 / PRIVATE STORY DESK</span>
        </div>
        <div className="auth-manuscript-copy">
          <p className="eyebrow">故事正典 · 版本留痕 · 私有密钥</p>
          <h1>让每一章，<em>有来处。</em></h1>
          <p>从人物第一次出现，到一条尚未回收的线索，所有已确认的事实都会留下来源。你拒绝的草稿，不会偷偷改写故事。</p>
        </div>
        <div className="auth-timeline">
          <div className="auth-timeline-line" />
          <div className="auth-timeline-item"><i /> <span>邮箱验证</span><small>确认这本手稿属于你</small></div>
          <div className="auth-timeline-item"><i /> <span>添加 Provider</span><small>模型密钥只进你的凭据库</small></div>
          <div className="auth-timeline-item"><i /> <span>接受正典</span><small>生成前先固定故事状态</small></div>
        </div>
        <span className="auth-page-mark">01 / CANON</span>
      </div>

      <main className="auth-panel">
        <div className="auth-panel-inner">
          <div className="auth-panel-head">
            <span className="auth-panel-kicker">{copy.eyebrow}</span>
            <div className="auth-panel-lock"><LockKeyhole size={14} /> 私有工作区</div>
          </div>
          <h2>{copy.title}</h2>
          <p className="auth-detail">{copy.detail}</p>
          <div className="auth-rule" />
          <p className="auth-form-heading">{formHeading}</p>

          {message && <div className="auth-message auth-message-success" role="status"><CheckCircle2 size={15} /> <span>{message}</span></div>}
          {error && <div className="auth-message auth-message-error" role="alert"><CircleAlert size={15} /> <span>{error}</span></div>}

          <form className="auth-form" onSubmit={submit}>
            {(view === "login" || view === "register" || view === "forgot-password" || view === "verify-email") && (
              <label className="auth-field">
                <span>邮箱{view === "verify-email" && <small>重发验证邮件时填写</small>}</span>
                <div className="auth-input-wrap"><Mail size={15} /><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete={view === "login" ? "email" : "username"} placeholder="you@example.com" required={view !== "verify-email"} /></div>
              </label>
            )}
            {view === "register" && (
              <label className="auth-field">
                <span>显示名称 <small>可选</small></span>
                <div className="auth-input-wrap"><Sparkles size={15} /><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} autoComplete="name" placeholder="你的笔名或称呼" /></div>
              </label>
            )}
            {view === "login" || view === "register" ? (
              <label className="auth-field">
                <span>密码 <small>{view === "register" ? "至少 12 位" : ""}</small></span>
                <div className="auth-input-wrap"><KeyRound size={15} /><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={view === "login" ? "current-password" : "new-password"} minLength={view === "register" ? 12 : undefined} required /></div>
              </label>
            ) : null}
            {(view === "verify-email" || view === "reset-password") && (
              <label className="auth-field">
                <span>{view === "verify-email" ? "验证令牌" : "重置令牌"}</span>
                <div className="auth-input-wrap"><Mail size={15} /><input value={token} onChange={(event) => setToken(event.target.value)} placeholder="粘贴邮件中的链接令牌" required /></div>
              </label>
            )}
            {view === "reset-password" && (
              <label className="auth-field">
                <span>新密码 <small>至少 12 位</small></span>
                <div className="auth-input-wrap"><KeyRound size={15} /><input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} autoComplete="new-password" minLength={12} required /></div>
              </label>
            )}
            <button className="button button-primary auth-submit" disabled={busy} type="submit">
              {busy ? "正在保存…" : view === "login" ? "进入工作台" : view === "register" ? "发送验证邮件" : view === "verify-email" ? "确认邮箱" : view === "forgot-password" ? "发送重置链接" : "保存新密码"}
              {!busy && <ArrowRight size={15} />}
            </button>
          </form>

          <div className="auth-links">
            {view === "login" && <><button onClick={() => switchView("register")}>注册新账号</button><button onClick={() => switchView("forgot-password")}>忘记密码</button></>}
            {view === "register" && <button onClick={() => switchView("login")}><ChevronLeft size={13} /> 已有账号，返回登录</button>}
            {view === "verify-email" && <><button disabled={busy} onClick={() => { if (!email.trim()) { setError("请输入注册邮箱后重新发送验证邮件。"); return; } setBusy(true); resendVerification(email).then(() => { setMessage("验证邮件已重新发送。"); setError(""); }, (e) => setError(friendlyError(e))).finally(() => setBusy(false)); }}>重新发送验证邮件</button><button onClick={() => switchView("login")}>返回登录</button></>}
            {view === "forgot-password" && <button onClick={() => switchView("login")}><ChevronLeft size={13} /> 返回登录</button>}
            {view === "reset-password" && <button onClick={() => switchView("login")}><ChevronLeft size={13} /> 返回登录</button>}
          </div>
          <div className="auth-trust-note"><ShieldCheck size={14} /> <span>Provider 密钥由系统凭据库隔离保存；数据库只保留用户与 Provider 索引，密钥不会进入项目、日志或导出包。</span></div>
        </div>
      </main>
    </div>
  );
}

function BookSeal() {
  return <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4"><path d="M4.5 5.5c3-1.2 5.5-.3 7.5 1.1v13c-2-1.4-4.5-2.3-7.5-1.1V5.5Z" /><path d="M19.5 5.5c-3-1.2-5.5-.3-7.5 1.1v13c2-1.4 4.5-2.3 7.5-1.1V5.5Z" /><path d="M12 6.8v13" /></svg>;
}

export function AccountSecurityView({
  session,
  onBack,
  onLogout,
  onLogoutAll,
  onSession,
}: {
  session: AuthSession;
  onBack: () => void;
  onLogout: () => void;
  onLogoutAll: () => void;
  onSession: (session: AuthSession) => void;
}) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [revoke, setRevoke] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const savePassword = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const nextSession = await changePassword({ current_password: currentPassword, new_password: newPassword, revoke_other_sessions: revoke });
      setCurrentPassword("");
      setNewPassword("");
      setMessage(revoke ? "密码已更新，其他设备已退出。" : "密码已更新。" );
      onSession(nextSession);
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="account-page">
      <div className="account-page-top"><button className="back-to-library" onClick={onBack}><ChevronLeft size={15} /> 返回工作台</button><span className="settings-version">ACCOUNT / SECURITY</span></div>
      <div className="account-hero"><div><p className="eyebrow">ACCOUNT LEDGER</p><h1>账号与安全</h1><p>这是你的私有工作区边界。小说、章节、正典、任务、审核包和 Provider 都只属于当前账号。</p></div><div className="account-seal"><ShieldCheck size={21} /><span>SESSION<br />SEALED</span></div></div>
      <div className="account-layout">
        <section className="settings-card account-card"><div className="settings-card-head"><div><h2>账号信息</h2><p>验证状态决定是否可以进入编剧室。</p></div><span className="connection-state"><span className="status-dot green" /> {session.user.is_email_verified ? "邮箱已验证" : "待验证"}</span></div><div className="account-meta"><div><span>邮箱</span><strong>{session.user.email}</strong></div><div><span>显示名称</span><strong>{session.user.display_name || "未设置"}</strong></div><div><span>账号创建</span><strong>{session.user.created_at ? session.user.created_at.slice(0, 10) : "—"}</strong></div></div></section>
        <section className="settings-card account-card"><div className="settings-card-head"><div><h2>修改密码</h2><p>密码使用 Argon2id 保存。你可以同时撤销其他设备。</p></div><KeyRound size={17} color="var(--teal)" /></div>{message && <div className="auth-message auth-message-success"><CheckCircle2 size={15} /> <span>{message}</span></div>}{error && <div className="auth-message auth-message-error"><CircleAlert size={15} /> <span>{error}</span></div>}<form className="account-password-form" onSubmit={savePassword}><label className="field"><span>当前密码</span><input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} autoComplete="current-password" required /></label><label className="field"><span>新密码 <small>至少 12 位</small></span><input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} autoComplete="new-password" minLength={12} required /></label><label className="account-check"><input type="checkbox" checked={revoke} onChange={(event) => setRevoke(event.target.checked)} /><span>修改后退出其他设备</span></label><div className="form-actions"><button className="button button-primary" disabled={busy}>{busy ? "正在保存…" : "更新密码"}</button></div></form></section>
        <AccountDangerZone onLogout={onLogout} onLogoutAll={onLogoutAll} />
      </div>
    </div>
  );
}

function AccountDangerZone({ onLogout, onLogoutAll }: { onLogout: () => void; onLogoutAll: () => void }) {
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const removeAccount = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await deleteAccount(password);
      onLogout();
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setBusy(false);
    }
  };
  return <section className="settings-card account-card account-danger"><div className="settings-card-head"><div><h2>危险区域</h2><p>退出所有设备会保留数据；注销账号会删除账号和其凭据。</p></div><LockKeyhole size={17} color="var(--red)" /></div><div className="account-danger-actions"><button className="button button-secondary" onClick={onLogout}>退出当前设备</button><button className="button button-secondary" onClick={onLogoutAll}>退出所有设备</button><button className="button button-danger" onClick={() => { setDeleteOpen((open) => !open); setError(""); }}>注销账号</button></div>{deleteOpen && <form className="account-delete-form" onSubmit={removeAccount}><p>这是不可逆操作。输入当前密码确认删除账号、小说和系统凭据。</p><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" placeholder="当前密码" required />{error && <span className="account-delete-error">{error}</span>}<button className="button button-danger" disabled={busy}>{busy ? "正在注销…" : "确认注销账号"}</button></form>}</section>;
}
