"use client";
import { IS_DEMO } from "../lib/mode";
import { Modal } from "./modal";
export interface ExamSettings { profile: string; exam_date: string; require_knowledge: boolean; use_public_knowledge: boolean; allow_web: boolean }
export const defaultSettings: ExamSettings = { profile: "auto", exam_date: "", require_knowledge: false, use_public_knowledge: true, allow_web: true };
export function Settings({ value, onChange, count, onLibrary, onClose }: { value: ExamSettings; onChange: (value: ExamSettings) => void; count: number; onLibrary: () => void; onClose: () => void }) {
  const internal = count > 0 || value.profile === "internal";
  return <Modal title="解题与参考资料设置" onClose={onClose}><div className="grid gap-5"><label className="field">考试场景<select value={value.profile} onChange={e => onChange({ ...value, profile: e.target.value })}><option value="auto">自动判断</option><option value="recruitment">国央企 / 事业单位招聘</option><option value="internal">单位内部考试</option><option value="campus">高校课程测试</option></select></label><label className="field">考试日期<input type="date" value={value.exam_date} onChange={e => onChange({ ...value, exam_date: e.target.value })} /></label>
    <button className="button justify-between" onClick={onLibrary}>选择参考资料<span>{count ? `已选 ${count} 份` : "未选择"}</span></button>
    <label className="check-field"><input type="checkbox" checked={internal || value.require_knowledge} disabled={internal} onChange={e => onChange({ ...value, require_knowledge: e.target.checked })} />只在有资料依据时作答</label>
    <label className="check-field"><input type="checkbox" checked={!internal && !value.require_knowledge && value.use_public_knowledge} disabled={internal || value.require_knowledge} onChange={e => onChange({ ...value, use_public_knowledge: e.target.checked })} />使用内置公开知识</label>
    <label className="check-field"><input type="checkbox" checked={!internal && value.allow_web} disabled={internal} onChange={e => onChange({ ...value, allow_web: e.target.checked })} />允许联网核对公开信息</label>
    {!IS_DEMO && <p className="notice text-sm">{internal ? "使用所选资料与原图核对。缺少依据时会提示补充，不进行联网检索。" : "依据题目所述日期处理。缺失的信息会明确标注。"}</p>}<button className="button primary justify-center" onClick={onClose}>完成设置</button></div></Modal>;
}
