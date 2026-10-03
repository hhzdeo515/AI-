import type { Metadata } from "next";
import { IS_DEMO } from "../lib/mode";
import "./globals.css";
export const metadata: Metadata = { title: "AI 眼镜 · 智能助手", description: "让思路更加清晰。拍照解题、策论与面试练习、会议纪要。" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body data-app-mode={IS_DEMO ? "demo" : "full"}>{children}</body></html>;
}
