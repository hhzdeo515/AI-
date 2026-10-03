import type { Metadata } from "next";
import { IS_DEMO } from "../lib/mode";
import "./globals.css";
export const metadata: Metadata = { title: IS_DEMO ? "AI 眼镜 · Demo 演示版" : "AI 眼镜 · 智能助手完整版", description: IS_DEMO ? "无需登录，体验眼镜与戒指交互。所有处理结果为预设示例。" : "拍照解题、策论与面试练习、会议纪要。眼镜与戒指协同模拟。" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body data-app-mode={IS_DEMO ? "demo" : "full"}>{children}</body></html>;
}
