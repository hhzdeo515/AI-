"use client";
import { useCallback, useEffect, useState } from "react";
import { ArrowRightIcon, EyeglassesIcon, LockKeyIcon } from "@phosphor-icons/react";
import { api, errorText, jsonPost } from "../lib/api";
import { Studio } from "./studio";

export function AuthGate() {
  const [session, setSession] = useState<{ authenticated: boolean; auth_enabled: boolean } | null>(null);
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const check = useCallback(async () => {
    setError("");
    try { setSession(await api("/api/session")); } catch (e) { setError(errorText(e)); }
  }, []);
  useEffect(() => {
    void check();
    const unauthorized = () => setSession({ authenticated: false, auth_enabled: true });
    window.addEventListener("assistant:unauthorized", unauthorized);
    return () => window.removeEventListener("assistant:unauthorized", unauthorized);
  }, [check]);
  async function login(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { await jsonPost("/api/login", { token }); setToken(""); await check(); }
    catch (e) { setError(errorText(e)); }
    finally { setBusy(false); }
  }
  async function logout() {
    await jsonPost("/api/logout", {});
    setSession({ authenticated: false, auth_enabled: true });
  }
  if (session?.authenticated) return <Studio authEnabled={session.auth_enabled} onLogout={logout} />;
  return <main className="login-shell min-h-[100dvh] px-6 py-12">
    <div className="mx-auto grid w-full max-w-5xl items-center gap-14 md:grid-cols-[1.2fr_1fr]">
      <section><div className="brand mb-12"><EyeglassesIcon size={28} weight="regular" /><span>AI 眼镜<span className="brand-sub">智能助手</span></span></div>
        <p className="eyebrow">眼镜与戒指 · 协同体验</p>
        <h1 className="mt-4 text-3xl leading-snug font-medium tracking-tight md:text-4xl">把注意力留给眼前，<br /><span className="text-accent">让思路更加清晰。</span></h1>
        <p className="muted mt-5 max-w-sm leading-7">拍照读题、持续练习、整理会议。真实处理结果，直接回到你的镜片视野。</p>
        <div className="login-lens mt-12" aria-hidden="true"><div className="login-lens-line" /><EyeglassesIcon size={72} weight="thin" /></div>
      </section>
      <section className="login-form"><LockKeyIcon size={26} className="text-accent" /><h2 className="mt-5 text-xl font-medium">{session ? "进入你的工作空间" : "连接工作空间"}</h2>
        <p className="muted mt-2 text-sm">{session ? "输入访问口令，安全地继续上次任务。" : "正在确认服务与登录状态。"}</p>
        {session ? <form onSubmit={login} className="mt-7 grid gap-5"><label className="field">访问口令<input type="password" name="password" autoComplete="current-password" required value={token} onChange={e => setToken(e.target.value)} autoFocus /></label>
          <button className="button primary justify-between" disabled={busy} type="submit">{busy ? "正在登录…" : "进入眼镜视野"}<ArrowRightIcon size={18} /></button></form> : <div className="skeleton mt-8 h-11" />}
        {error && <div className="error-box mt-5" role="alert">{error}{!session && <button className="button mt-3" onClick={() => void check()}>重新连接</button>}</div>}
        <p className="muted mt-7 text-xs leading-6">设备操作为模拟体验。图像、练习与会议内容由服务实际处理。</p>
      </section>
    </div>
  </main>;
}
