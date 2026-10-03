"use client";
import { useEffect, useRef, useState } from "react";
import { CameraIcon } from "@phosphor-icons/react";
import { MediaLease } from "../lib/media";
import { Modal } from "./modal";

export function Camera({ onCapture, onClose }: { onCapture: (file: File) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(true);
  const lease = useRef(new MediaLease());
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const start = async () => {
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("当前环境不支持摄像头，请通过 HTTPS 打开，或选择相册上传。");
        const stream = await lease.current.open(() => navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 } }, audio: false }));
        if (!stream || !mounted.current) return;
        if (video.current) { video.current.srcObject = stream; await video.current.play(); }
        if (mounted.current) { setStarting(false); setReady(true); }
      } catch (e) { if (mounted.current) { setStarting(false); setError(e instanceof Error && e.name === "NotAllowedError" ? "摄像头权限未开启，请允许访问或使用相册上传。" : "摄像头暂不可用，请检查权限或使用相册上传。"); } }
    };
    void start();
    const stop = () => { lease.current.stop(); if (mounted.current) { setReady(false); setStarting(false); setError("摄像头已关闭，请关闭窗口后重新打开。"); } };
    const hidden = () => { if (document.hidden) stop(); };
    document.addEventListener("visibilitychange", hidden); window.addEventListener("pagehide", stop);
    return () => { mounted.current = false; lease.current.stop(); document.removeEventListener("visibilitychange", hidden); window.removeEventListener("pagehide", stop); };
  }, []);
  function capture() {
    const source = video.current;
    if (!ready || !source?.videoWidth) return;
    const canvas = document.createElement("canvas"); canvas.width = source.videoWidth; canvas.height = source.videoHeight;
    canvas.getContext("2d")?.drawImage(source, 0, 0);
    canvas.toBlob(blob => {
      if (!mounted.current) return;
      if (!blob) { setError("照片生成失败，请重试。"); return; }
      lease.current.stop(); onCapture(new File([blob], `题目-${Date.now()}.jpg`, { type: "image/jpeg" })); onClose();
    }, "image/jpeg", 0.94);
  }
  return <Modal title="拍摄题目" onClose={onClose} wide><div className="camera-preview"><video ref={video} autoPlay muted playsInline aria-label="摄像头预览" /><div className="camera-guide" aria-hidden="true" /></div>
    <div className="mt-4 flex flex-wrap items-center justify-between gap-4"><p className="muted text-sm">让完整题干、图表和选项都进入画面。</p><button className="button primary" disabled={!ready} onClick={capture}><CameraIcon size={19} />拍照并使用</button></div>
    {starting && <p className="muted mt-3" role="status">正在开启摄像头…</p>}{error && <p className="error-box mt-3" role="alert">{error}</p>}
  </Modal>;
}
