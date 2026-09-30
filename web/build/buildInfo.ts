// Д6-R40: метка сборки в index.html и опись build.json для сторожа (строка «Веб-чат», Д6-R46).
//
// Опись строится с диска, обходом каталога сборки после записи всех файлов: в неё входят и
// assets/* из бандла, и файлы из public/ (с Д6б — boot.js), которых в bundle нет. Сами
// index.html и build.json в опись не входят. Форма путей и число записей — договор со
// сторожем; его проверяет scripts/check-dist.mjs последним шагом `npm run build`.
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type { Plugin } from "vite";

export const BUILD_RE = /^[A-Za-z0-9._-]{1,64}$/;
const NOT_IN_MANIFEST = new Set(["index.html", "build.json"]);

function walk(dir: string, prefix: string, out: string[]): void {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix + entry.name;
    if (entry.isDirectory()) walk(join(dir, entry.name), rel + "/", out);
    else if (!(prefix === "" && NOT_IN_MANIFEST.has(entry.name))) out.push(rel);
  }
}

export function buildManifest(build: string, outDir: string): { build: string; files: Record<string, string> } {
  const paths: string[] = [];
  walk(outDir, "", paths);
  const files: Record<string, string> = {};
  for (const p of paths.sort()) files[p] = createHash("sha256").update(readFileSync(join(outDir, p))).digest("hex");
  return { build, files };
}

export function pcbkBuildInfo(build: string): Plugin {
  if (!BUILD_RE.test(build)) throw new Error("PCBK_BUILD не по шаблону [A-Za-z0-9._-]{1,64}");
  let outDir = "";
  return {
    name: "pcbk-build-info",
    apply: "build",                                                     // без описи в vitest и dev-сервере
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
    },
    transformIndexHtml() {
      // Вид метки — ровно WEB_META_RE сторожа: name, затем content, без « />».
      return [{ tag: "meta", attrs: { name: "pcbk-build", content: build }, injectTo: "head" }];
    },
    closeBundle() {
      writeFileSync(join(outDir, "build.json"), JSON.stringify(buildManifest(build, outDir), null, 2) + "\n");
    },
  };
}
