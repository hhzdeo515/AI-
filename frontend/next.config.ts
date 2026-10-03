import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

export default function config(phase: string): NextConfig {
  const development = phase === PHASE_DEVELOPMENT_SERVER;
  const demo = process.env.NEXT_PUBLIC_APP_MODE === "demo";
  const backend = (process.env.API_PROXY_TARGET || "http://127.0.0.1:5000").replace(/\/$/, "");
  return {
    ...(development ? {} : { output: "export" }),
    images: { unoptimized: true },
    trailingSlash: true,
    ...(development && !demo ? {
      async rewrites() {
        return ["/api/:path*", "/health", "/legacy", "/static/:path*"].map(source => ({ source, destination: `${backend}${source}` }));
      },
    } : {}),
  };
}
