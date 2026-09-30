// Проверка собранной статики под строгий CSP и опись сборки (Д6-R40, preflight M1, M6).
// Фикстура пишет каталог сборки во временный путь; дерево репозитория не трогается.
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { afterAll, expect, test } from "vitest";
import { checkDist, KNOWN } from "../scripts/check-dist.mjs";

type Fx = {
  html: string;
  js: string;
  build?: string;
  extraFile?: string;
  listExtraInManifest?: boolean;
  badSum?: boolean;
  more?: string[];                                                      // ещё файлы, и в описи тоже
};

const BUILD = "test-0123456789ab";
const JS = "assets/index-A1b2c3.js";
const CSS = "assets/index-D4e5f6.css";
const CLEAN: Fx = {
  html: [
    "<!doctype html>",
    '<html lang="ru">',
    "<head>",
    '<meta charset="utf-8">',
    `<meta name="pcbk-build" content="${BUILD}">`,
    `<script type="module" crossorigin src="/chat/${JS}"></script>`,
    `<link rel="stylesheet" crossorigin href="/chat/${CSS}">`,
    "</head>",
    '<body><div id="root"></div></body>',
    "</html>",
    "",
  ].join("\n"),
  js: 'console.log("ok");',
};

const root = mkdtempSync(join(tmpdir(), "pcbk-check-dist-"));
afterAll(() => rmSync(root, { recursive: true, force: true }));

const sha256 = (body: string) => createHash("sha256").update(body).digest("hex");

function fixture(f: Fx): string {
  const dir = mkdtempSync(join(root, "dist-"));
  const files: Record<string, string> = { [JS]: f.js, [CSS]: "body{margin:0}" };
  for (const p of f.more ?? []) files[p] = `/* ${p} */`;
  if (f.extraFile) files[f.extraFile] = "extra";
  writeFileSync(join(dir, "index.html"), f.html);
  const manifest: Record<string, string> = {};
  for (const [p, body] of Object.entries(files)) {
    mkdirSync(dirname(join(dir, p)), { recursive: true });
    writeFileSync(join(dir, p), body);
    if (p !== f.extraFile || f.listExtraInManifest) manifest[p] = sha256(body);
  }
  if (f.badSum) manifest[JS] = sha256("другое содержимое");
  writeFileSync(join(dir, "build.json"), JSON.stringify({ build: f.build ?? BUILD, files: manifest }));
  return dir;
}

test("check_dist_accepts_clean_build", () => expect(checkDist(fixture(CLEAN)).errors).toEqual([]));
test("check_dist_rejects_inline_and_external", () => {
  for (const html of ['<script>alert(1)</script>', '<style>a{}</style>', '<div style="x">',
                      '<script type="module" src="https://cdn.example/x.js"></script>',
                      '<link rel="stylesheet" href="data:text/css,a">', '<script src="//x/y.js"></script>'])
    expect(checkDist(fixture({ ...CLEAN, html: CLEAN.html.replace("</head>", html + "</head>") })).errors.length, html).toBeGreaterThan(0);
});
test("check_dist_rejects_forbidden_strings", () => {
  for (const s of ["ai-lab.pcbk.ru", "10.20.30.40", "20ABC_001_PV"])
    expect(checkDist(fixture({ ...CLEAN, js: `const x = "${s}";` })).errors.length, s).toBeGreaterThan(0);
  expect(checkDist(fixture({ ...CLEAN, js: 'fetch("http://127.0.0.1")' })).errors).toEqual([]);
});
test("check_dist_warns_on_newer_api", () => {
  const r = checkDist(fixture({ ...CLEAN, js: "a.toSorted(); Promise.withResolvers()" }));
  expect(r.errors).toEqual([]); expect(r.warnings.join(" ")).toMatch(/toSorted.*withResolvers|withResolvers.*toSorted/);
});
test("check_dist_manifest_from_disk", () => {                           // раунд 2, Minor 1: файл из public/ тоже в описи
  const dir = fixture({ ...CLEAN, extraFile: "boot.js", listExtraInManifest: true });
  expect(checkDist(dir).errors).toEqual([]);
  expect(checkDist(fixture({ ...CLEAN, extraFile: "boot.js" })).errors).toContainEqual(expect.stringMatching(/опись/));
});
test("check_dist_rejects_manifest_mismatch", () => {
  expect(checkDist(fixture({ ...CLEAN, extraFile: "assets/x.js" })).errors).toContainEqual(expect.stringMatching(/опись/));
  expect(checkDist(fixture({ ...CLEAN, badSum: true })).errors).toContainEqual(expect.stringMatching(/сумма/));
  expect(checkDist(fixture({ ...CLEAN, extraFile: "assets/index.js.map" })).errors).toContainEqual(expect.stringMatching(/\.map/));
});

// preflight M1: сборка, зелёная у check-dist, должна быть зелёной и у сторожа (WEB_META_RE, WEB_FILE_RE, WEB_MAX_FILES)
test("check_dist_matches_watchdog_contract", () => {
  const meta = `<meta name="pcbk-build" content="${BUILD}">`;
  expect(checkDist(fixture({ ...CLEAN, html: CLEAN.html.replace(meta, "") })).errors)
    .toContainEqual(expect.stringMatching(/метк/));
  expect(checkDist(fixture({ ...CLEAN, html: CLEAN.html.replace(meta, meta.replace(">", " />")) })).errors)
    .toContainEqual(expect.stringMatching(/метк/));
  expect(checkDist(fixture({ ...CLEAN, build: "other-build" })).errors)
    .toContainEqual(expect.stringMatching(/метк.*опис|опис.*метк/));
  expect(checkDist(fixture({ ...CLEAN, extraFile: "favicon.ico", listExtraInManifest: true })).errors)
    .toContainEqual(expect.stringMatching(/опись.*favicon\.ico/));
  const empty = fixture(CLEAN);                                                        // сторож: записей от 1 до 8
  writeFileSync(join(empty, "build.json"), JSON.stringify({ build: BUILD, files: {} }));
  expect(checkDist(empty).errors).toContainEqual(expect.stringMatching(/опись пуста/));
  const dots = fixture(CLEAN);                                                         // «assets/..» — не файл сборки
  writeFileSync(join(dots, "build.json"), JSON.stringify({ build: BUILD, files: { "assets/..": sha256("x") } }));
  expect(checkDist(dots).errors).toContainEqual(expect.stringMatching(/опись: путь "assets\/\.\."/));
  const more = Array.from({ length: 6 }, (_, i) => `assets/chunk-${i}.js`);           // 2 + 6 = 8 файлов — предел
  expect(checkDist(fixture({ ...CLEAN, more })).errors).toEqual([]);
  expect(checkDist(fixture({ ...CLEAN, more, extraFile: "boot.js", listExtraInManifest: true })).errors)
    .toContainEqual(expect.stringMatching(/опис.*больше 8/));                           // 9 записей: сторож отверг бы
});

// preflight M6: известное исключение — пара «API + фрагмент источника»; оно печатается отдельно
// и не прячет то же API в другом коде
test("check_dist_reports_known_exceptions_apart", () => {
  const radix = KNOWN.find((k) => k.api === ".toSorted(");
  expect(radix?.source).toMatch(/@radix-ui\/react-collection/);
  const r = checkDist(fixture({ ...CLEAN, js: `var q=1;${radix!.sample};var z=2;` }));
  expect(r.errors).toEqual([]);
  expect(r.warnings).toEqual([]);
  expect(r.known.join(" ")).toMatch(/toSorted\( ×2.*@radix-ui\/react-collection/);
  const both = checkDist(fixture({ ...CLEAN, js: `${radix!.sample};b.toSorted()` }));
  expect(both.warnings.join(" ")).toMatch(/toSorted\( ×1/);
  expect(both.known.join(" ")).toMatch(/toSorted\( ×2/);
});
