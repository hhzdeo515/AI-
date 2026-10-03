"use client";
import { useState } from "react";
import { ArrowRightIcon, EyeglassesIcon, PlayIcon } from "@phosphor-icons/react";
import { DEMO_SESSION_KEY, DEMO_STORE_KEY } from "../lib/mode";
import { Studio } from "./studio";

export function DemoGate() {
  const [entered, setEntered] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [notice, setNotice] = useState("");
  function clear() {
    try {
      localStorage.removeItem(DEMO_STORE_KEY); localStorage.removeItem(DEMO_SESSION_KEY);
      setNotice("本机演示记录已清除。"); setConfirmClear(false);
    } catch { setNotice("浏览器禁止访问存储，请检查隐私设置。"); }
  }
  if (entered) return <Studio authEnabled onLogout={async () => setEntered(false)} />;
  return <main className="login-shell min-h-[100dvh] px-6 py-12">
    <div className="mx-auto grid w-full max-w-5xl items-center gap-14 md:grid-cols-[1.2fr_1fr]">
      <section><div className="brand mb-12"><EyeglassesIcon size={28} /><span>AI 眼镜<span className="brand-sub">智能助手 · Demo</span></span></div>
        <p className="eyebrow">眼镜与戒指 · 协同体验</p>
        <h1 className="mt-4 text-3xl leading-snug font-medium tracking-tight md:text-4xl text-accent">让思路更加清晰。</h1>
        <p className="muted mt-5 max-w-sm leading-7">体验拍照解题、持续练习与会议纪要，让操作和结果在镜片视野中展开。</p>
        <div className="login-lens mt-12" aria-hidden="true"><div className="login-lens-line" /><EyeglassesIcon size={72} weight="thin" /></div>
      </section>
      <section className="login-form"><PlayIcon size={26} className="text-accent" /><p className="eyebrow mt-5">Demo · 无需登录</p><h2 className="mt-2 text-xl font-medium">体验你的眼镜工作空间</h2>
        <p className="muted mt-3 text-sm leading-7">演示模式使用预设示例，不调用 AI 服务。拍照和录音仅在当前设备预览，不会上传。</p>
        <button className="button primary mt-7 w-full justify-between" onClick={() => setEntered(true)}>进入演示<ArrowRightIcon size={18} /></button>
        <p className="muted mt-6 text-xs leading-6">最近 50 条示例结果和导入的文本资料保存在当前浏览器，不跨设备同步。完整版另行运行真实登录与后端处理。</p>
        <button className="button ghost small mt-4" onClick={() => setConfirmClear(true)}>清除本机演示记录</button>
        {confirmClear && <div className="notice mt-3"><p>清除本机的演示历史与资料？</p><div className="flex gap-2 mt-3"><button className="button small" onClick={clear}>确认清除</button><button className="button small" onClick={() => setConfirmClear(false)}>保留记录</button></div></div>}
        {notice && <p role="status" className="notice mt-3">{notice}</p>}
      </section>
    </div>
  </main>;
}
