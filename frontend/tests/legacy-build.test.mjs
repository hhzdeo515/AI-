// @vitest-environment node
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { expect, it, vi } from "vitest";

it("exports the original interface and installs a browser transport without Node globals", async () => {
  const frontend = fileURLToPath(new URL("../", import.meta.url));
  const readOutput = name => readFileSync(new URL(`../out/${name}`, import.meta.url), "utf8");
  execFileSync(process.execPath, ["scripts/build-legacy.mjs", "full"], { cwd: frontend });
  expect(readOutput("index.html")).toContain('id="hardware-demo"');
  expect(readOutput("index.html")).not.toContain("legacy-demo.js");
  expect(readOutput("static/app.js")).toBe(readFileSync(new URL("../../langgraph-app/lg_assistant/web/static/app.js", import.meta.url), "utf8"));

  execFileSync(process.execPath, ["scripts/build-legacy.mjs", "demo"], { cwd: frontend });
  const html = readOutput("index.html");
  expect(html).not.toContain("_next/");
  expect(html.indexOf("legacy-demo.js")).toBeLessThan(html.indexOf("exam-camera.js"));
  expect(readOutput("login/index.html")).toBe(html);
  const dom = new JSDOM(html, { url: "https://assistant.example/", runScripts: "outside-only" });
  try {
    const network = vi.fn(() => { throw new Error("Business requests must remain local"); });
    Object.assign(dom.window, { fetch: network, Response, Request, Headers });
    expect(() => dom.window.eval(readOutput("static/legacy-demo.js"))).not.toThrow();
    expect((await (await dom.window.fetch("/health")).json()).ok).toBe(true);
    expect((await dom.window.fetch("/api/unknown")).status).toBe(404);
    expect(network).not.toHaveBeenCalled();
  } finally { dom.window.close(); }
}, 20000);
