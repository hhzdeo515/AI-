import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const mode = process.argv[2];
if (!["demo", "full"].includes(mode)) throw new Error("Usage: node scripts/build-legacy.mjs <demo|full>");
const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const web = resolve(frontend, "../langgraph-app/lg_assistant/web");
const output = join(frontend, "out");
// Only replace this project's generated export, never application data.
if (dirname(output) !== frontend || output !== resolve(frontend, "out")) throw new Error("Unsafe output directory");

let html = await readFile(join(web, "templates/index.html"), "utf8");
html = html.replace(/\{\{\s*url_for\('static',\s*filename='([^']+)'\)\s*\}\}/g, "/static/$1");
if (html.includes("{{") || html.includes("{%")) throw new Error("Unresolved template expression");
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await cp(join(web, "static"), join(output, "static"), { recursive: true });

if (mode === "demo") {
  await build({
    stdin: { contents: 'import { installLegacyDemoTransport } from "./lib/legacy-demo.ts"; installLegacyDemoTransport();', resolveDir: frontend, sourcefile: "legacy-demo-entry.ts", loader: "ts" },
    outfile: join(output, "static/legacy-demo.js"),
    bundle: true, format: "iife", platform: "browser", target: "es2022", minify: true,
    define: { "process.env.NEXT_PUBLIC_APP_MODE": '"demo"' },
  });
  html = html.replace("</head>", '<script src="/static/legacy-demo.js"></script>\n</head>');
  const appFile = join(output, "static/app.js");
  let app = await readFile(appFile, "utf8");
  const navigation = /window\.location\.href\s*=\s*"\/api\/export\?owner="[\s\S]*?"&format="\s*\+\s*fmt;/g;
  if ((app.match(navigation) || []).length !== 1) throw new Error("Legacy export handler changed");
  app = app.replace(navigation, "window.LegacyDemoExport(id, fmt);");
  app = app.replace('const fmts = ["docx", "pdf", "md", "txt"];', 'const fmts = ["md", "txt"];');
  if (!app.includes("async function toggleSpeech(text, btn) {")) throw new Error("Legacy speech handler changed");
  app = app.replace("async function toggleSpeech(text, btn) {", 'async function toggleSpeech(text, btn) { toast("当前工作空间暂不支持语音播报"); return;');
  await writeFile(appFile, app, "utf8");
  await mkdir(join(output, "login"), { recursive: true });
  await writeFile(join(output, "login/index.html"), html, "utf8");
}
await writeFile(join(output, "index.html"), html, "utf8");
console.log(`Original glasses and ring interface exported (${mode})`);
