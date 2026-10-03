import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = { title: "AI 眼镜 · 智能助手", description: "拍照解题、策论与面试练习、会议纪要。眼镜与戒指协同模拟。" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
