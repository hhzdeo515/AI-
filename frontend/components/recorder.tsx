"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { MicrophoneIcon, StopIcon } from "@phosphor-icons/react";
import { MediaLease, recordingMime } from "../lib/media";

export function Recorder({ disabled, onRecording, onFile }: { disabled: boolean; onRecording: (active: boolean) => void; onFile: (file: File) => void }) {
  const lease = useRef(new MediaLease());
  const recorder = useRef<MediaRecorder | null>(null);
  const mounted = useRef(true);
  const callback = useRef(onFile); callback.current = onFile;
  const activity = useRef(onRecording); activity.current = onRecording;
  const [phase, setPhase] = useState<"idle" | "opening" | "recording" | "stopping">("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState("");
  const stop = useCallback(() => {
    const current = recorder.current;
    if (current && current.state !== "inactive") { if (mounted.current) setPhase("stopping"); current.stop(); }
    else { if (mounted.current) setPhase("idle"); activity.current(false); }
    lease.current.stop();
  }, []);
  useEffect(() => {
    mounted.current = true;
    const hidden = () => { if (document.hidden) stop(); };
    document.addEventListener("visibilitychange", hidden); window.addEventListener("pagehide", stop);
    return () => { mounted.current = false; stop(); activity.current(false); document.removeEventListener("visibilitychange", hidden); window.removeEventListener("pagehide", stop); };
  }, [stop]);
  useEffect(() => {
    if (phase !== "recording") return;
    const start = Date.now();
    const timer = setInterval(() => { const value = Math.floor((Date.now() - start) / 1000); setSeconds(value); if (value >= 3600) stop(); }, 1000);
    return () => clearInterval(timer);
  }, [phase, stop]);
  async function start() {
    setError(""); setPhase("opening"); activity.current(true); setSeconds(0);
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") throw new Error("当前浏览器不支持录音，请使用 HTTPS 或上传音频文件。");
      const stream = await lease.current.open(() => navigator.mediaDevices.getUserMedia({ audio: true }));
      if (!stream || !mounted.current) return;
      const mime = recordingMime();
      const current = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recorder.current = current;
      const chunks: Blob[] = []; let bytes = 0;
      current.ondataavailable = event => { if (event.data.size) { chunks.push(event.data); bytes += event.data.size; if (bytes >= 28 * 1024 * 1024) stop(); } };
      current.onstop = () => {
        lease.current.stop(); recorder.current = null; activity.current(false);
        if (!mounted.current) return;
        setPhase("idle");
        if (!chunks.length) { setError("没有收到录音内容，请检查麦克风后重试。"); return; }
        const type = current.mimeType || mime || "audio/webm";
        const extension = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
        callback.current(new File(chunks, `录音-${Date.now()}.${extension}`, { type }));
      };
      current.onerror = () => { if (mounted.current) setError("录音中断，已停止麦克风。请检查试听内容。"); stop(); };
      stream.getTracks().forEach(track => track.addEventListener("ended", stop, { once: true }));
      current.start(1000); setPhase("recording");
    } catch (e) {
      lease.current.stop(); activity.current(false);
      if (mounted.current) { setPhase("idle"); setError(e instanceof Error && e.name === "NotAllowedError" ? "麦克风权限未开启，请允许访问或上传音频。" : "麦克风暂不可用，请使用音频文件或文字输入。"); }
    }
  }
  return <div><button type="button" data-ring-action className={`button ${phase === "recording" ? "recording" : ""}`} disabled={disabled && phase === "idle" || phase === "stopping"} onClick={() => phase === "idle" ? void start() : stop()}>
    {phase === "idle" ? <MicrophoneIcon size={19} /> : <StopIcon size={19} weight="fill" />}
    {phase === "idle" ? "开始录音" : phase === "opening" ? "取消开启" : phase === "stopping" ? "正在保存…" : `停止录音 ${Math.floor(seconds / 60).toString().padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`}</button>
    {error && <p className="error-box mt-3 text-sm" role="alert">{error}</p>}
  </div>;
}
