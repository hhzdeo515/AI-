import { build } from "esbuild";
import { fileURLToPath } from "node:url";

await build({
  entryPoints: [fileURLToPath(new URL("../lib/smart-glasses.js", import.meta.url))],
  outfile: fileURLToPath(new URL("../../langgraph-app/lg_assistant/web/static/smart-glasses.js", import.meta.url)),
  bundle: true,
  format: "iife",
  platform: "browser",
  target: "es2022",
  minify: true,
  legalComments: "eof",
});
