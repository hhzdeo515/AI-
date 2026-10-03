import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

const [mode, command, ...args] = process.argv.slice(2);
if (!["demo", "full"].includes(mode) || !["build", "dev"].includes(command)) {
  throw new Error("Usage: node scripts/run-next.mjs <demo|full> <build|dev>");
}
const require = createRequire(import.meta.url);
const result = spawnSync(process.execPath, [require.resolve("next/dist/bin/next"), command, ...args], {
  stdio: "inherit", env: { ...process.env, NEXT_PUBLIC_APP_MODE: mode },
});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
