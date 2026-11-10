// Builds creation/ and studio/ into plugin/hermes-r1/static/{app,studio}.
// Each app is: src/main.ts (bundled to app.js), src/style.css (bundled to app.css),
// index.html and any files in public/ (copied as-is).
import { build, context } from "esbuild";
import { cp, mkdir, rm, access } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const watch = process.argv.includes("--watch");
const apps = [
  { name: "creation", out: "plugin/hermes-r1/static/app", target: "chrome80" },
  { name: "studio", out: "plugin/hermes-r1/static/studio", target: "es2020" },
];

const exists = (p) => access(p).then(() => true, () => false);

for (const app of apps) {
  const src = join(root, app.name);
  const out = join(root, app.out);
  if (!(await exists(join(src, "src/main.ts")))) {
    console.log(`skip ${app.name} (no src/main.ts)`);
    continue;
  }
  await rm(out, { recursive: true, force: true });
  await mkdir(out, { recursive: true });
  const copyStatic = async () => {
    await cp(join(src, "index.html"), join(out, "index.html"));
    if (await exists(join(src, "public"))) await cp(join(src, "public"), out, { recursive: true });
  };
  await copyStatic();
  const entryPoints = [{ in: join(src, "src/main.ts"), out: "app" }];
  if (await exists(join(src, "src/style.css"))) entryPoints.push({ in: join(src, "src/style.css"), out: "app" });
  const options = {
    entryPoints,
    outdir: out,
    bundle: true,
    minify: !watch,
    sourcemap: watch ? "inline" : false,
    target: app.target,
    format: "iife",
    logLevel: "info",
  };
  if (watch) {
    const ctx = await context(options);
    await ctx.watch();
  } else {
    await build(options);
  }
}
if (watch) console.log("watching…");
