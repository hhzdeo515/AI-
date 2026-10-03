import { createServer } from "node:http";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { readFile, stat } from "node:fs/promises";
import { dirname, extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const mode = process.argv[2];
if (!["demo", "full"].includes(mode)) throw new Error("Expected demo or full");
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../out");
const port = Number(process.env.PORT || 3000);
const backend = new URL(process.env.API_PROXY_TARGET || "http://127.0.0.1:5000");
if (!["http:", "https:"].includes(backend.protocol)) throw new Error("Invalid development backend");
if (mode === "demo") {
  const result = spawnSync(process.execPath, [fileURLToPath(new URL("build-legacy.mjs", import.meta.url)), mode], { stdio: "inherit" });
  if (result.status !== 0) process.exit(result.status || 1);
}
const types = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8" };
createServer(async (req, res) => {
  if (mode === "full") {
    const target = new URL(req.url, backend);
    if (target.origin !== backend.origin) { res.writeHead(400); res.end("Invalid path"); return; }
    const headers = { ...req.headers, host: backend.host };
    if (headers.origin === `http://${req.headers.host}`) headers.origin = backend.origin;
    const forward = (backend.protocol === "https:" ? httpsRequest : httpRequest)(target, { method: req.method, headers }, upstream => {
      res.writeHead(upstream.statusCode, upstream.headers); upstream.pipe(res);
    });
    forward.on("error", () => { res.writeHead(502, { "Content-Type": "text/plain; charset=utf-8" }); res.end("请先启动原版 Python 服务，或设置 API_PROXY_TARGET。"); });
    req.pipe(forward); return;
  }
  try {
    const pathname = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
    let file = resolve(root, `.${pathname}`);
    if (file !== root && !file.startsWith(root + sep)) throw new Error("Invalid path");
    if ((await stat(file)).isDirectory()) file = resolve(file, "index.html");
    const content = await readFile(file);
    res.writeHead(200, { "Content-Type": types[extname(file)] || "application/octet-stream", "Cache-Control": "no-store" });
    res.end(content);
  } catch { res.writeHead(404); res.end("Not Found"); }
}).listen(port, "127.0.0.1", () => console.log(`Original interface: http://127.0.0.1:${port}`));
