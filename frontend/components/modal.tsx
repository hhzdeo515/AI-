"use client";
import { useEffect, useRef } from "react";
import { XIcon } from "@phosphor-icons/react";
export function Modal({ title, onClose, children, wide = false }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const node = ref.current; node?.showModal(); return () => node?.close(); }, []);
  return <dialog ref={ref} className={`modal ${wide ? "modal-wide" : ""}`} aria-label={title} onCancel={e => { e.preventDefault(); onClose(); }} onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
    <div className="modal-inner"><header className="flex items-center justify-between gap-5 border-b border-line px-5 py-4"><h2 className="text-lg font-medium">{title}</h2><button className="icon-button" onClick={onClose} aria-label="关闭"><XIcon size={21} /></button></header><div className="p-5 md:p-7">{children}</div></div>
  </dialog>;
}
