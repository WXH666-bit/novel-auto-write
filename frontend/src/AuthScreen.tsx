import { FormEvent, useEffect, useMemo, useState } from "react";
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
  UserRound,
} from "lucide-react";
import {
  apiErrorCode,
  apiErrorStatus,
  forgotPassword,
  deleteAccount,
  changePassword,
  getAuthConfig,
  loginAccount,
  normalizeAuthSession,
  registerAccount,
  resendVerification,
  resetPassword,
  verifyEmail,
} from "./api";
import type { AuthConfig, AuthMode, AuthSession, AuthView } from "./types";
import InkLandscape, { InkInteractionLayer } from "./InkLandscape";

type AuthScreenProps = {
  initialView?: AuthView;
  token?: string;
  onAuthenticated: (session: AuthSession) => void;
  onSessionCleared?: () => void;
  onNavigate?: (view: AuthView) => void;
};

const pageCopy: Record<Exclude<AuthView, "account">, { eyebrow: string; title: string; detail: string }> = {
  login: {
    eyebrow: "回到你的故事",
    title: "继续你的故事",
    detail: "你的小说、人物资料和模型设置只属于当前账号。",
  },
  register: {
    eyebrow: "建立新的写作空间",
    title: "建立你的编剧室",
    detail: "注册并验证邮箱后，就可以开始保存自己的故事。",
  },
  "verify-email": {
    eyebrow: "确认账号归属",
    title: "确认你的邮箱",
    detail: "验证邮箱后，章节、设定与 Provider 才会进入你的私有工作区。",
  },
  "forgot-password": {
    eyebrow: "找回登录方式",
    title: "找回账号",
    detail: "我们会发送一封有效期有限的重置邮件。无论邮箱是否存在，提示都保持一致。",
  },
  "reset-password": {
    eyebrow: "重新进入工作区",
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

export function getAuthViewFromPath() {
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

function clearAuthDeepLinkPath() {
  if (typeof window === "undefined") return;
  if (getAuthViewFromPath()) {
    window.history.replaceState(window.history.state, "", "/");
  }
}

function friendlyError(error: unknown, mode: AuthMode = "email") {
  const code = apiErrorCode(error);
  if (code === "email_not_verified") return "邮箱尚未验证，请先查收验证邮件。";
  if (code === "invalid_credentials") {
    return mode === "username" ? "用户名或密码不正确。" : "邮箱或密码不正确。";
  }
  if (code === "account_locked") return "登录尝试过多，请稍后再试。";
  if (code === "token_expired") return "链接已过期，请重新发送。";
  if (apiErrorStatus(error) === 429) return "操作过于频繁，请稍后再试。";
  return error instanceof Error ? error.message : "操作没有完成，请稍后再试。";
}

export default function AuthScreen({
  initialView = "login",
  token: tokenProp,
  onAuthenticated,
  onSessionCleared,
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
  const [identifier, setIdentifier] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [token, setToken] = useState(initialToken);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null);
  const [authConfigError, setAuthConfigError] = useState("");
  const [authConfigLoading, setAuthConfigLoading] = useState(true);
  const [configAttempt, setConfigAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setAuthConfig(null);
    setAuthConfigError("");
    setAuthConfigLoading(true);
    getAuthConfig()
      .then((nextConfig) => {
        if (cancelled) return;
        setAuthConfig(nextConfig);
        if (nextConfig.mode === "username") {
          // Never carry an email, token, or password into a username-only
          // deployment, including when a user opens an email deep link.
          setIdentifier("");
          setDisplayName("");
          setPassword("");
          setNewPassword("");
          setToken("");
          if (["verify-email", "forgot-password", "reset-password"].includes(view)) {
            setView("login");
            clearAuthDeepLinkPath();
            onNavigate?.("login");
          }
        } else if (
          (view === "verify-email" && !nextConfig.verification_required) ||
          ((view === "forgot-password" || view === "reset-password") &&
            !nextConfig.password_reset_available)
        ) {
          setView("login");
          clearAuthDeepLinkPath();
          onNavigate?.("login");
          setToken("");
        }
      })
      .catch(() => {
        if (cancelled) return;
        // The URL token was already removed from browser history. Keep it in
        // component memory so a transient config failure can be retried.
        setAuthConfigError("无法读取服务器登录配置，请联系部署管理员后重试。");
      })
      .finally(() => {
        if (!cancelled) setAuthConfigLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [configAttempt, onNavigate]);

  const authMode: AuthMode = authConfig?.mode || "email";
  const emailMode = authMode === "email";
  const verificationRequired =
    emailMode && Boolean(authConfig?.verification_required);
  const passwordResetAvailable =
    emailMode && Boolean(authConfig?.password_reset_available);
  const currentView: Exclude<AuthView, "account"> =
    view === "account"
      ? "login"
      : authMode === "username" &&
          ["verify-email", "forgot-password", "reset-password"].includes(view)
        ? "login"
        : view;

  const copy = useMemo(() => {
    const base = pageCopy[currentView];
    if (currentView !== "register") return base;
    if (authMode === "username") {
      return {
        ...base,
        detail: "创建用户名与密码，随后即可直接登录并进入你的私有工作区。",
      };
    }
    if (!verificationRequired) {
      return {
        ...base,
        detail: "注册后即可使用账号密码登录，开始保存你的故事。",
      };
    }
    return base;
  }, [authMode, currentView, verificationRequired]);

  const switchView = (next: AuthView) => {
    const emailOnlyView =
      next === "verify-email" ||
      next === "forgot-password" ||
      next === "reset-password";
    if (
      (authMode === "username" && emailOnlyView) ||
      (next === "verify-email" && !verificationRequired) ||
      ((next === "forgot-password" || next === "reset-password") &&
        !passwordResetAvailable)
    ) {
      return;
    }
    setView(next);
    setError("");
    setMessage("");
    if (next !== "verify-email" && next !== "reset-password") {
      clearAuthDeepLinkPath();
    }
    onNavigate?.(next);
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (currentView === "login") {
        const session = await loginAccount({
          identifier: identifier.trim(),
          password,
        });
        if (verificationRequired && !session.user.is_email_verified) {
          switchView("verify-email");
          setMessage("登录成功，但邮箱尚未验证。请先完成验证。" );
        } else {
          onAuthenticated(session);
        }
      } else if (currentView === "register") {
        await registerAccount({
          ...(emailMode
            ? { email: identifier.trim() }
            : { username: identifier.trim() }),
          password,
          display_name: displayName.trim() || undefined,
        });
        switchView(verificationRequired ? "verify-email" : "login");
        setMessage(
          verificationRequired
            ? "验证邮件已发送。请在 24 小时内点击邮件中的链接。"
            : "注册成功，请使用刚设置的账号密码登录。",
        );
      } else if (currentView === "verify-email") {
        const result = await verifyEmail(token.trim());
        const session = normalizeAuthSession(result);
        if (session.user.id) {
          clearAuthDeepLinkPath();
          onAuthenticated(session);
        }
        else {
          switchView("login");
          setMessage("邮箱已验证，请使用密码登录。" );
        }
      } else if (currentView === "forgot-password") {
        await forgotPassword(identifier.trim());
        setMessage("如果该邮箱已注册，重置链接会很快送达；请检查收件箱和垃圾邮件。" );
      } else if (currentView === "reset-password") {
        await resetPassword({ token: token.trim(), password: newPassword });
        setPassword("");
        setNewPassword("");
        switchView("login");
        onSessionCleared?.();
        setMessage("密码已重置，请使用新密码登录。" );
      }
    } catch (requestError) {
      if (
        currentView === "login" &&
        verificationRequired &&
        apiErrorCode(requestError) === "email_not_verified"
      ) {
        switchView("verify-email");
        setMessage("邮箱尚未验证。请粘贴邮件中的令牌后继续。" );
      } else {
        setError(friendlyError(requestError, authMode));
      }
    } finally {
      setBusy(false);
    }
  };

  const formHeading = useMemo(() => {
    if (currentView === "verify-email") return token ? "粘贴验证链接中的令牌" : "打开邮件中的验证链接";
    if (currentView === "reset-password") return "用一次性链接设置新密码";
    if (currentView === "register" && authMode === "username") {
      return "创建一个用于登录的用户名";
    }
    if (currentView === "register" && verificationRequired) {
      return "先注册，再确认邮箱";
    }
    return "你的故事，从这里继续";
  }, [authMode, currentView, token, verificationRequired]);

  if (authConfigLoading) {
    return (
      <div className="auth-loading">
        <div className="auth-loading-mark"><LockKeyhole size={19} /></div>
        <span>正在读取服务器登录配置…</span>
      </div>
    );
  }
  if (authConfigError || !authConfig) {
    return (
      <div className="auth-loading" role="alert">
        <div className="auth-loading-mark"><CircleAlert size={19} /></div>
        <span>{authConfigError || "服务器登录配置不可用。"}</span>
        <button className="button button-primary" onClick={() => setConfigAttempt((attempt) => attempt + 1)}>
          重新加载
        </button>
      </div>
    );
  }

  const identifierLabel = emailMode ? "邮箱" : "用户名";
  const identifierPlaceholder = emailMode ? "you@example.com" : "例如：linche";
  const IdentifierIcon = emailMode ? Mail : UserRound;

  return (
    <div className="auth-shell">
      <InkInteractionLayer />
      <div className="auth-manuscript" aria-hidden="true">
        <InkLandscape className="auth-ink" tone="dark" />
        <div className="auth-manuscript-top">
          <span className="auth-seal"><BookSeal /></span>
          <span>章回 / 私人写作台</span>
        </div>
        <div className="auth-manuscript-copy">
          <p className="eyebrow">正文 · 人物 · 情节 · 仅你可见</p>
          <h1>让每一章，<em>有来处。</em></h1>
          <p>从人物第一次出现，到一条尚未回收的线索，所有已确认的事实都会留下来源。你拒绝的草稿，不会偷偷改写故事。</p>
        </div>
        <div className="auth-timeline">
          <div className="auth-timeline-line" />
          <div className="auth-timeline-item"><i /> <span>{verificationRequired ? "邮箱验证" : "账号密码"}</span><small>{verificationRequired ? "确认这本手稿属于你" : "按部署方式保护工作区"}</small></div>
          <div className="auth-timeline-item"><i /> <span>添加 Provider</span><small>模型密钥只进你的凭据库</small></div>
          <div className="auth-timeline-item"><i /> <span>确认建议</span><small>只有接受的内容才用于后续写作</small></div>
        </div>
        <span className="auth-page-mark">写作 / 资料</span>
      </div>

      <main className="auth-panel">
        <div className="auth-panel-inner" key={currentView}>
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
            {(currentView === "login" || currentView === "register" || currentView === "forgot-password" || currentView === "verify-email") && (
              <label className="auth-field">
                <span>{identifierLabel}{currentView === "verify-email" && <small>重发验证邮件时填写</small>}</span>
                <div className="auth-input-wrap"><IdentifierIcon size={15} /><input type={emailMode ? "email" : "text"} value={identifier} onChange={(event) => setIdentifier(event.target.value)} autoComplete={emailMode ? "email" : "username"} placeholder={identifierPlaceholder} required={currentView !== "verify-email"} /></div>
              </label>
            )}
            {currentView === "register" && (
              <label className="auth-field">
                <span>显示名称 <small>可选</small></span>
                <div className="auth-input-wrap"><Sparkles size={15} /><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} autoComplete="name" placeholder="你的笔名或称呼" /></div>
              </label>
            )}
            {currentView === "login" || currentView === "register" ? (
              <label className="auth-field">
                <span>密码 <small>{currentView === "register" ? "至少 12 位" : ""}</small></span>
                <div className="auth-input-wrap"><KeyRound size={15} /><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={currentView === "login" ? "current-password" : "new-password"} minLength={currentView === "register" ? 12 : undefined} required /></div>
              </label>
            ) : null}
            {(currentView === "verify-email" || currentView === "reset-password") && (
              <label className="auth-field">
                <span>{currentView === "verify-email" ? "验证令牌" : "重置令牌"}</span>
                <div className="auth-input-wrap"><Mail size={15} /><input value={token} onChange={(event) => setToken(event.target.value)} placeholder="粘贴邮件中的链接令牌" required /></div>
              </label>
            )}
            {currentView === "reset-password" && (
              <label className="auth-field">
                <span>新密码 <small>至少 12 位</small></span>
                <div className="auth-input-wrap"><KeyRound size={15} /><input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} autoComplete="new-password" minLength={12} required /></div>
              </label>
            )}
            <button className="button button-primary auth-submit" disabled={busy} type="submit">
              {busy ? "正在保存…" : currentView === "login" ? "进入工作台" : currentView === "register" ? verificationRequired ? "发送验证邮件" : "创建账号" : currentView === "verify-email" ? "确认邮箱" : currentView === "forgot-password" ? "发送重置链接" : "保存新密码"}
              {!busy && <ArrowRight size={15} />}
            </button>
          </form>

          <div className="auth-links">
            {currentView === "login" && <><button onClick={() => switchView("register")}>注册新账号</button>{passwordResetAvailable && <button onClick={() => switchView("forgot-password")}>忘记密码</button>}</>}
            {currentView === "register" && <button onClick={() => switchView("login")}><ChevronLeft size={13} /> 已有账号，返回登录</button>}
            {currentView === "verify-email" && verificationRequired && <><button disabled={busy} onClick={() => { if (!identifier.trim()) { setError("请输入注册邮箱后重新发送验证邮件。"); return; } setBusy(true); resendVerification(identifier).then(() => { setMessage("验证邮件已重新发送。"); setError(""); }, (e) => setError(friendlyError(e, authMode))).finally(() => setBusy(false)); }}>重新发送验证邮件</button><button onClick={() => switchView("login")}>返回登录</button></>}
            {currentView === "forgot-password" && passwordResetAvailable && <button onClick={() => switchView("login")}><ChevronLeft size={13} /> 返回登录</button>}
            {currentView === "reset-password" && passwordResetAvailable && <button onClick={() => switchView("login")}><ChevronLeft size={13} /> 返回登录</button>}
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
  const usernameAccount = Boolean(session.user.username);
  const accountIdentifier = session.user.username || session.user.email || "—";

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
      <InkInteractionLayer />
      <div className="account-page-top"><button className="back-to-library" onClick={onBack}><ChevronLeft size={15} /> 返回工作台</button><span className="settings-version">ACCOUNT / SECURITY</span></div>
      <div className="account-hero"><div><p className="eyebrow">ACCOUNT LEDGER</p><h1>账号与安全</h1><p>这是你的私有工作区边界。小说、章节、正典、任务、审核包和 Provider 都只属于当前账号。</p></div><div className="account-seal"><ShieldCheck size={21} /><span>SESSION<br />SEALED</span></div></div>
      <div className="account-layout">
        <section className="settings-card account-card"><div className="settings-card-head"><div><h2>账号信息</h2><p>{usernameAccount ? "用户名和密码用于进入你的私有工作区。" : "验证状态决定是否可以进入编剧室。"}</p></div><span className="connection-state"><span className="status-dot green" /> {usernameAccount ? "用户名登录" : session.user.is_email_verified ? "邮箱已验证" : "待验证"}</span></div><div className="account-meta"><div><span>{usernameAccount ? "用户名" : "邮箱"}</span><strong>{accountIdentifier}</strong></div><div><span>显示名称</span><strong>{session.user.display_name || "未设置"}</strong></div><div><span>账号创建</span><strong>{session.user.created_at ? session.user.created_at.slice(0, 10) : "—"}</strong></div></div></section>
        <section className="settings-card account-card"><div className="settings-card-head"><div><h2>修改密码</h2><p>密码使用 Argon2id 保存。你可以同时撤销其他设备。</p></div><KeyRound size={17} color="var(--teal)" /></div>{message && <div className="auth-message auth-message-success"><CheckCircle2 size={15} /> <span>{message}</span></div>}{error && <div className="auth-message auth-message-error"><CircleAlert size={15} /> <span>{error}</span></div>}<form className="account-password-form" onSubmit={savePassword}><label className="field"><span>当前密码</span><input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} autoComplete="current-password" required /></label><label className="field"><span>新密码 <small>至少 12 位</small></span><input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} autoComplete="new-password" minLength={12} required /></label><label className="account-check"><input type="checkbox" checked={revoke} onChange={(event) => setRevoke(event.target.checked)} /><span>修改后退出其他设备</span></label><div className="form-actions"><button className="button button-primary" disabled={busy}>{busy ? "正在保存…" : "更新密码"}</button></div></form></section>
        <AccountDangerZone onLogout={onLogout} onLogoutAll={onLogoutAll} mode={usernameAccount ? "username" : "email"} />
      </div>
    </div>
  );
}

function AccountDangerZone({ onLogout, onLogoutAll, mode }: { onLogout: () => void; onLogoutAll: () => void; mode: AuthMode }) {
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
      setError(friendlyError(requestError, mode));
    } finally {
      setBusy(false);
    }
  };
  return <section className="settings-card account-card account-danger"><div className="settings-card-head"><div><h2>危险区域</h2><p>退出所有设备会保留数据；注销账号会删除账号和其凭据。</p></div><LockKeyhole size={17} color="var(--red)" /></div><div className="account-danger-actions"><button className="button button-secondary" onClick={onLogout}>退出当前设备</button><button className="button button-secondary" onClick={onLogoutAll}>退出所有设备</button><button className="button button-danger" onClick={() => { setDeleteOpen((open) => !open); setError(""); }}>注销账号</button></div>{deleteOpen && <form className="account-delete-form" onSubmit={removeAccount}><p>这是不可逆操作。输入当前密码确认删除账号、小说和系统凭据。</p><input aria-label="输入当前密码确认注销账号" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" placeholder="当前密码" required />{error && <span className="account-delete-error" role="alert">{error}</span>}<button className="button button-danger" disabled={busy}>{busy ? "正在注销…" : "确认注销账号"}</button></form>}</section>;
}
