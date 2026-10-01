// Проверка собранной статики под строгий CSP и опись сборки (Д6-R40, preflight M1, M6).
// Фикстура пишет каталог сборки во временный путь; дерево репозитория не трогается.
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { afterAll, expect, test } from "vitest";
import { checkDist, KNOWN, NEWER_API, NEWER_API_PATTERNS } from "../scripts/check-dist.mjs";
import { guardOutDir } from "../vite.config.ts";

type Fx = {
  html: string;
  js: string;
  css?: string;
  build?: string;
  extraFile?: string;
  extraBody?: string;
  listExtraInManifest?: boolean;
  badSum?: boolean;
  more?: string[];                                                      // ещё файлы, и в описи тоже
};

const BUILD = "test-0123456789ab";
const JS = "assets/index-A1b2c3.js";
const CSS = "assets/index-D4e5f6.css";
const BOOT = '<script src="/chat/boot.js"></script>';                  // загрузчик Д6б (Д6-R51) — как пишет Vite
const CLEAN: Fx = {
  html: [
    "<!doctype html>",
    '<html lang="ru">',
    "<head>",
    '<meta charset="utf-8">',
    `<meta name="pcbk-build" content="${BUILD}">`,
    BOOT,
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
  const files: Record<string, string> = { [JS]: f.js, [CSS]: f.css ?? "body{margin:0}", "boot.js": "/* boot */" };
  for (const p of f.more ?? []) files[p] = `/* ${p} */`;
  if (f.extraFile) files[f.extraFile] = f.extraBody ?? "extra";
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
  for (const s of ["stand.pcbk.ru", "ai-lab.example", "10.20.30.40", "20ABC_001_PV"])      // pcbk.ru и ai-lab — порознь
    expect(checkDist(fixture({ ...CLEAN, js: `const x = "${s}";` })).errors.length, s).toBeGreaterThan(0);
  expect(checkDist(fixture({ ...CLEAN, js: 'fetch("http://127.0.0.1")' })).errors).toEqual([]);
});
test("check_dist_warns_on_newer_api", () => {
  const r = checkDist(fixture({ ...CLEAN, js: "a.toSorted(); Promise.withResolvers()" }));
  expect(r.errors).toEqual([]); expect(r.warnings.join(" ")).toMatch(/toSorted.*withResolvers|withResolvers.*toSorted/);
});
// итоговое ревью Д6а, P4: каждое API новее Chrome 109 — предупреждение (не ошибка), и без ложных срабатываний
test("check_dist_warns_on_each_newer_api", () => {
  const cases: [string, string][] = [
    ["Promise.withResolvers", "const {promise:p}=Promise.withResolvers();"],
    [".toSorted(", "a.toSorted((x,y)=>x-y);"], [".toReversed(", "a.toReversed();"], [".toSpliced(", "a.toSpliced(0,1);"],
    [".with(", "a.with(0,1);"], ["Object.groupBy", "Object.groupBy(a,f);"], ["Map.groupBy", "Map.groupBy(a,f);"],
    ["Array.fromAsync", "await Array.fromAsync(g);"],
    [".union(", "s.union(t);"], [".intersection(", "s.intersection(t);"], [".difference(", "s.difference(t);"],
    [".symmetricDifference(", "s.symmetricDifference(t);"], [".isSubsetOf(", "s.isSubsetOf(t);"],
    [".isSupersetOf(", "s.isSupersetOf(t);"], [".isDisjointFrom(", "s.isDisjointFrom(t);"],
    [".isWellFormed(", "x.isWellFormed();"], [".toWellFormed(", "x.toWellFormed();"], ["Iterator.from(", "Iterator.from(g);"],
    ["помощники итераторов", "m.values().map(f);"], ["помощники итераторов", "m.keys().take(3);"],
    ["помощники итераторов", "s.entries().drop(1).toArray();"],
    ["флаг регулярки v", "const r=/[\\p{L}--[a-z]]/v;"], ["флаг регулярки v", "x.match(/a/gv)"],
    ["флаг регулярки v (new RegExp)", 'new RegExp("[a]","v")'],
  ];
  for (const [what, js] of cases) {
    const r = checkDist(fixture({ ...CLEAN, js }));
    expect(r.errors, js).toEqual([]);
    expect(r.warnings.length, js).toBe(1);
    expect(r.warnings[0], js).toContain(`API новее Chrome 109 — ${what}`);
  }
  // каждое имя списка и каждый шаблон проверены хоть одним случаем
  for (const api of NEWER_API) expect(cases.some(([w]) => w === api), api).toBe(true);
  for (const { what } of NEWER_API_PATTERNS) expect(cases.some(([w]) => w === what), what).toBe(true);
  // без ложных срабатываний: старые API, массив из Object.values, деление, обычные флаги, take/drop не на итераторе
  for (const js of ["a.findLast(f);a.findLastIndex(f);a.at(-1);", "Object.values(o).map(f);Object.entries(o).filter(f);",
                    "[...m.values()].map(f);", "x=a/b/v;y=(c)/d/v;", "x.replace(/a/g,'');/b/giu.test(y);",
                    "new RegExp(s,'gu');", "q.take(3);q.drop(1);", "lodash.take(a,2);"]) {
    expect(checkDist(fixture({ ...CLEAN, js })).warnings, js).toEqual([]);
  }
});
test("check_dist_manifest_from_disk", () => {                           // раунд 2, Minor 1: файл из public/ тоже в описи
  const dir = fixture({ ...CLEAN, extraFile: "boot.js", listExtraInManifest: true });
  expect(checkDist(dir).errors).toEqual([]);
  expect(checkDist(fixture({ ...CLEAN, extraFile: "boot.js" })).errors).toContainEqual(expect.stringMatching(/опись/));
});
test("check_dist_rejects_manifest_mismatch", () => {
  expect(checkDist(fixture({ ...CLEAN, extraFile: "assets/x.js" })).errors).toContainEqual(expect.stringMatching(/опись/));
  expect(checkDist(fixture({ ...CLEAN, badSum: true })).errors).toContainEqual(expect.stringMatching(/сумма/));
  expect(checkDist(fixture({ ...CLEAN, extraFile: "assets/index.js.map", listExtraInManifest: true })).errors)
    .toContainEqual(expect.stringMatching(/карта исходников/));                          // в описи, но всё равно ошибка
  const gone = fixture({ ...CLEAN, more: ["assets/chunk-0.js"] });                      // файл из описи пропал с диска
  rmSync(join(gone, "assets/chunk-0.js"));
  expect(checkDist(gone).errors).toEqual(["опись: файла assets/chunk-0.js нет в каталоге сборки"]);
});

test("check_dist_rejects_missing_link_handlers_and_symlinks", () => {
  const missing = fixture({ ...CLEAN, html: CLEAN.html.replace("</head>", '<link rel="icon" href="/chat/assets/icon.png"></head>') });
  expect(checkDist(missing).errors).toEqual([expect.stringMatching(/<link>.*href ведёт на отсутствующий файл assets\/icon\.png/)]);
  const handler = fixture({ ...CLEAN, html: CLEAN.html.replace("<body>", '<body onload="go()">') });
  expect(checkDist(handler).errors).toEqual([expect.stringMatching(/<body>.*встроенный обработчик onload=/)]);
  const link = fixture(CLEAN);
  symlinkSync(join(link, JS), join(link, "assets/alias.js"));
  expect(checkDist(link).errors).toContainEqual(expect.stringMatching(/assets\/alias\.js: не обычный файл/));
});

test("check_dist_forbidden_hosts_apart", () => {
  const host = checkDist(fixture({ ...CLEAN, js: 'const u = "stand.pcbk.ru";' })).errors;
  expect(host).toEqual([expect.stringMatching(/\(pcbk\.ru\)/)]);
  const lab = checkDist(fixture({ ...CLEAN, js: 'const u = "ai-lab.example";' })).errors;
  expect(lab).toEqual([expect.stringMatching(/\(ai-lab\)/)]);
});

test("check_dist_ipv4_at_sentence_end", () => {
  for (const js of ['"адрес 10.20.30.40."', '"(10.20.30.40)"', '"10.20.30.40, дальше"', '"конец.10.20.30.40"'])
    expect(checkDist(fixture({ ...CLEAN, js })).errors, js).toEqual([expect.stringMatching(/адрес IPv4/)]);
  for (const js of ['"1.2.3.4.5"', '"v19.3.0"', '"300.20.30.40"', '"10.20.30.400"', '"см. 127.0.0.1."'])
    expect(checkDist(fixture({ ...CLEAN, js })).errors, js).toEqual([]);
});

test("check_dist_parses_tags_like_browser", () => {
  for (const tag of ['<script/src="https://cdn.example/x.js"></script>', "<script/src=//x/y.js></script>"])  // разобран как <script>, а не пропущен
    expect(checkDist(fixture({ ...CLEAN, html: CLEAN.html.replace("</head>", tag + "</head>") })).errors, tag)
      .toEqual([expect.stringMatching(/<script>.*src ведёт наружу/)]);
  for (const tag of ['<img srcset="/chat/assets/index-A1b2c3.js 1x, https://cdn.example/a.png 2x">',
                     '<img srcset="data:image/png;base64,AAAA 1x">',
                     '<img srcset="/chat/assets/index-A1b2c3.js 1x, /chat/assets/nope.png 2x">'])
    expect(checkDist(fixture({ ...CLEAN, html: CLEAN.html.replace("</head>", tag + "</head>") })).errors.length, tag).toBeGreaterThan(0);
  const ok = '<img alt="" srcset="/chat/assets/index-A1b2c3.js 1x, /chat/assets/index-D4e5f6.css 2x">';
  expect(checkDist(fixture({ ...CLEAN, html: CLEAN.html.replace("</head>", ok + "</head>") })).errors).toEqual([]);
  const broken = checkDist(fixture({ ...CLEAN, html: CLEAN.html.replace("</head>", '<script src="/chat/x.js" "></head>') }));
  expect(broken.errors).toContainEqual(expect.stringMatching(/тег не разобран/));    // неразобранный тег — ошибка, не пропуск
});

test("check_dist_warns_on_eval_and_css_outside", () => {
  for (const [js, what] of [['var f = new Function("return 1");', "new Function"], ["var F=Function;", "=Function"],
                            ["g(a=Function,b)", "=Function"], ['eval("1")', "eval("]]) {
    const r = checkDist(fixture({ ...CLEAN, js }));
    expect(r.errors, js).toEqual([]);
    expect(r.warnings, js).toEqual([expect.stringMatching(new RegExp(`unsafe-eval.*${what.replace(/[.()]/g, "\\$&")} ×1`))]);
  }
  for (const js of ["retrieval(x)", "a.evaluate(b)", "class Function2{}"])
    expect(checkDist(fixture({ ...CLEAN, js })).warnings, js).toEqual([]);
  for (const [css, what] of [["a{background:url(https://cdn.example/a.png)}", "url("], ['a{background:url("data:image/png;base64,AA")}', "url("],
                             ["a{background:url( //x/a.png)}", "url("], ['@import "https://cdn.example/a.css";', "@import"],
                             ["@import url(//x/a.css);", "@import"]]) {
    const r = checkDist(fixture({ ...CLEAN, css }));
    expect(r.errors, css).toEqual([]);
    expect(r.warnings, css).toEqual([expect.stringMatching(new RegExp(`${CSS}: CSS ведёт наружу.*${what.replace(/[.()]/g, "\\$&")} ×1`))]);
  }
  for (const css of ["a{background:url(/chat/assets/a.png)}", 'a{background:url("./a.png")}', '@import "./b.css";'])
    expect(checkDist(fixture({ ...CLEAN, css })).warnings, css).toEqual([]);
});

test("check_dist_warns_on_boot_js", () => {                                            // Д6б: boot.js из public/ — тоже JS сборки
  const r = checkDist(fixture({ ...CLEAN, extraFile: "boot.js", extraBody: "Promise.withResolvers();eval(s)", listExtraInManifest: true }));
  expect(r.errors).toEqual([]);
  expect(r.warnings).toEqual([expect.stringMatching(/^boot\.js: API новее Chrome 109 — Promise\.withResolvers ×1/),
                              expect.stringMatching(/^boot\.js: .*unsafe-eval.*eval\( ×1/)]);
});

// Д6б (Д6-R51, preflight Ф15): загрузчик /chat/boot.js — ровно один классический <script src> без type, async,
// defer и nomodule (итоговая доводка Д6б, Б1-m3: nomodule браузер класса не исполняет); любой другой <script src> не
// модулем — ошибка. Без загрузчика отчёт boot_failed пропал бы молча
test("check_dist_requires_one_classic_boot", () => {
  const real = readFileSync("public/boot.js", "utf8");                                 // настоящий загрузчик
  const r = checkDist(fixture({ ...CLEAN, extraFile: "boot.js", extraBody: real, listExtraInManifest: true }));
  expect([r.errors, r.warnings]).toEqual([[], []]);
  const html = (tag: string) => CLEAN.html.replace(BOOT, tag);
  for (const tag of ["", BOOT + BOOT, '<script type="module" src="/chat/boot.js"></script>',
                     '<script type="text/javascript" src="/chat/boot.js"></script>',
                     '<script src="/chat/boot.js" defer></script>', '<script async src="/chat/boot.js"></script>',
                     '<script nomodule src="/chat/boot.js"></script>', '<script src="/chat/boot.js" NOMODULE></script>',
                     '<script type="Module" src="/chat/boot.js"></script>' + BOOT])  // модуль с тем же файлом — лишний
    expect(checkDist(fixture({ ...CLEAN, html: html(tag) })).errors, tag)
      .toEqual([expect.stringMatching(/загрузчик \/chat\/boot\.js/)]);
  for (const tag of [`<script src="/chat/${JS}"></script>`, `<script type="text/javascript" src="/chat/${JS}"></script>`])
    expect(checkDist(fixture({ ...CLEAN, html: html(BOOT + tag) })).errors, tag)
      .toEqual([expect.stringMatching(/<script>.*не модуль/)]);
});

// Ограда emptyOutDir (vite.config.ts): сборка не стирает каталог, который не похож на прежнюю сборку чата
test("build_refuses_foreign_out_dir", () => {
  const out = mkdtempSync(join(root, "out-"));
  expect(() => guardOutDir(join(out, "absent"))).not.toThrow();                          // нет каталога
  expect(() => guardOutDir(out)).not.toThrow();                                          // пустой
  writeFileSync(join(out, "login.html"), "<!doctype html>");
  expect(() => guardOutDir(out)).toThrow(/не пуст и в нём нет build\.json/);            // чужая статика (edge/static)
  writeFileSync(join(out, "build.json"), "{}");
  expect(() => guardOutDir(out)).not.toThrow();                                          // прежняя сборка чата
  expect(readdirSync(out).sort()).toEqual(["build.json", "login.html"]);                 // ограда сама ничего не трогает
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
  const far = CLEAN.html.replace("<head>", `<head><!--${"x".repeat(64 * 1024)}-->`);   // сторож читает первые 64 КиБ
  expect(checkDist(fixture({ ...CLEAN, html: far })).errors).toContainEqual(expect.stringMatching(/метка сборки дальше первых 64 КиБ/));
  const more = Array.from({ length: 5 }, (_, i) => `assets/chunk-${i}.js`);           // 3 + 5 = 8 файлов — предел
  expect(checkDist(fixture({ ...CLEAN, more })).errors).toEqual([]);
  expect(checkDist(fixture({ ...CLEAN, more, extraFile: "assets/extra.js", listExtraInManifest: true })).errors)
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
