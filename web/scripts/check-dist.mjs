// Проверка собранной статики чата — последний шаг `npm run build` (Д6-R40, Д6-R41, Д6-R46).
//
//   node scripts/check-dist.mjs <каталог сборки>
//
// Код 1 — есть ошибки (сборка не годится к выкладке); предупреждения и известные исключения
// печатаются, но сборку не роняют. Найденные строки заказчика не печатаются — только вид,
// файл и смещение.
import { createHash } from "node:crypto";
import { existsSync, lstatSync, readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const BASE = "/chat/";
export const WEB_MAX_FILES = 8;
// Договор со сторожем (watchdog/pcbk_watchdog/checks.py, _web_manifest): WEB_META_RE — search,
// WEB_FILE_RE — fullmatch и без «.»/«..» последним звеном, sha256 — 64 знака в нижнем регистре,
// записей в описи — от 1 до WEB_MAX_FILES (preflight M1).
export const WEB_META_RE = /<meta name="pcbk-build" content="([A-Za-z0-9._-]{1,64})">/;
export const WEB_FILE_RE = /^(?:assets\/[A-Za-z0-9._-]{1,100}|boot\.js)$/;
const SHA256_RE = /^[0-9a-f]{64}$/;
const NOT_IN_MANIFEST = new Set(["index.html", "build.json"]);

// API новее Chrome 109: предупреждение, решение — по ответу владельца о браузерах класса.
export const NEWER_API = [
  "Promise.withResolvers", ".toSorted(", ".toReversed(", ".toSpliced(",
  "Object.groupBy", "Map.groupBy", "Array.fromAsync",
];

/**
 * Известные исключения: пара «API + фрагмент источника» (preflight M6). Фрагмент — шаблон кода
 * после минификации (имена переменных в нём любые); вхождение API внутри фрагмента идёт в `known`
 * с источником, а не в `warnings`, то же API в другом коде — по-прежнему предупреждение. `sample` —
 * фрагмент из настоящей сборки (для теста). Каждое исключение — с записью в журнале дня и
 * решением контролёра.
 * @type {{ api: string, fragment: RegExp, sample: string, source: string }[]}
 */
export const KNOWN = [
  {
    // useCollectionItem: setItemMap(map => map.set(element, {...itemData, element}).toSorted(sortByDocumentPosition)).
    // `map` здесь — OrderedDict радикса (class extends Map) со своим методом toSorted, а не массив:
    // Array.prototype.toSorted этот вызов не нужен.
    api: ".toSorted(",
    fragment: /\{\.\.\.[\w$]+,element:[\w$]+\}\)(?:,[\w$]+)?\.toSorted\([\w$]+\)/g,
    sample: "t.set(s,{...e,element:s}).toSorted(Iv):(t.set(s,{...e,element:s}),t.toSorted(Iv))",
    source: "@radix-ui/react-collection 1.1.15 (метод своего OrderedDict, не Array.prototype)",
  },
];

/**
 * Строки, которым не место в сборке: адреса и имена хостов заказчика, имена тегов БДРВ.
 * @type {{ what: string, re: RegExp, allow?: string[] }[]}
 */
const FORBIDDEN = [
  { what: "имя хоста заказчика (pcbk.ru)", re: /pcbk\.ru/gi },
  { what: "имя хоста заказчика (ai-lab)", re: /ai-lab/gi },
  {
    what: "адрес IPv4",
    re: /(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\d.])/g,
    allow: ["127.0.0.1"],
  },
  { what: "имя тега", re: /\b\d{2}[A-Z][A-Z0-9]*_[A-Z0-9_]*(?:PV|SP|MV|LMN)\b/g },
];

/**
 * Известные исключения запрещённых строк из кода библиотек (адрес-образец и т. п.): пара
 * «вид + фрагмент источника» (шаблон, как у KNOWN), с записью в журнале дня и решением
 * контролёра. Пока пусто.
 * @type {{ what: string, fragment: RegExp, source: string }[]}
 */
export const KNOWN_STRINGS = [];

const TAG_RE = /<([A-Za-z][\w-]*)((?:\s+[^\s"'>\/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?)*)\s*\/?>/g;
const ATTR_RE = /([^\s"'>\/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;

function attrsOf(text) {
  /** @type {Map<string, string>} */
  const attrs = new Map();
  for (const m of text.matchAll(ATTR_RE)) attrs.set(m[1].toLowerCase(), m[2] ?? m[3] ?? m[4] ?? "");
  return attrs;
}

function walk(dir, prefix, out, errors) {
  for (const name of readdirSync(dir).sort()) {
    const rel = prefix + name;
    const st = lstatSync(join(dir, name));
    if (st.isDirectory()) walk(join(dir, name), rel + "/", out, errors);
    else if (st.isFile()) out.push(rel);
    else errors.push(`${rel}: не обычный файл (ссылка или устройство)`);
  }
}

function indexesOf(text, needle) {
  const at = [];
  for (let i = text.indexOf(needle); i !== -1; i = text.indexOf(needle, i + 1)) at.push(i);
  return at;
}

/** Отрезки [начало, конец) всех вхождений шаблона `fragment` в `text`. */
function spans(text, fragment) {
  return [...text.matchAll(new RegExp(fragment.source, "g"))].map((m) => [m.index, m.index + m[0].length]);
}

/** Первое исключение, внутри вхождения фрагмента которого целиком лежит [index, index + length). */
function coveredBy(exceptions, text, index, length) {
  return exceptions.find((k) => (k.at ??= spans(text, k.fragment)).some(([a, b]) => a <= index && index + length <= b));
}

function checkIndexHtml(html, dir, errors) {
  for (const m of html.matchAll(/<style\b/gi)) errors.push(`index.html: встроенный <style> (смещение ${m.index})`);
  for (const m of html.matchAll(TAG_RE)) {
    const tag = m[1].toLowerCase();
    const attrs = attrsOf(m[2]);
    const at = `index.html, <${tag}> (смещение ${m.index})`;
    if (tag === "script" && !attrs.has("src")) errors.push(`${at}: встроенный скрипт без src`);
    if (attrs.has("style")) errors.push(`${at}: атрибут style=`);
    for (const name of attrs.keys()) if (name.startsWith("on")) errors.push(`${at}: встроенный обработчик ${name}=`);
    for (const name of ["src", "href"]) {
      if (!attrs.has(name)) continue;
      const value = /** @type {string} */ (attrs.get(name)).trim();
      if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(value) || /^[\\/]{2}/.test(value)) {
        errors.push(`${at}: ${name} ведёт наружу или на схему (http:, https:, data:, //) — CSP 'self' это закроет`);
        continue;
      }
      const path = value.replace(/[?#].*$/, "");
      if (tag === "a") continue;                                          // переходы (<a href="/login">) — не файлы сборки
      if (!path.startsWith(BASE)) {
        errors.push(`${at}: ${name} вне ${BASE}`);
        continue;
      }
      const rel = path.slice(BASE.length);
      if (!rel || rel.split("/").includes("..") || !existsSync(join(dir, rel)))
        errors.push(`${at}: ${name} ведёт на отсутствующий файл ${rel || "(пусто)"}`);
    }
  }
}

/**
 * @param {string} dir каталог сборки
 * @returns {{ errors: string[], warnings: string[], known: string[] }}
 */
export function checkDist(dir) {
  /** @type {string[]} */ const errors = [];
  /** @type {string[]} */ const warnings = [];
  /** @type {string[]} */ const known = [];

  if (!existsSync(dir) || !lstatSync(dir).isDirectory()) return { errors: [`нет каталога сборки ${dir}`], warnings, known };
  /** @type {string[]} */ const onDisk = [];
  walk(dir, "", onDisk, errors);
  const texts = new Map(onDisk.map((p) => [p, readFileSync(join(dir, p))]));

  // index.html: CSP без 'unsafe-inline', только свои файлы, метка сборки в форме сторожа
  const htmlBuf = texts.get("index.html");
  const html = htmlBuf ? htmlBuf.toString("utf8") : "";
  if (!htmlBuf) errors.push("нет index.html");
  else checkIndexHtml(html, dir, errors);
  const meta = WEB_META_RE.exec(html);
  if (htmlBuf && !meta) errors.push('index.html: нет метки сборки <meta name="pcbk-build" content="…"> в форме сторожа');

  // build.json: форма, метка, пути и число записей — как их примет сторож; суммы — с диска
  /** @type {Record<string, string> | null} */ let files = null;
  const manifestBuf = texts.get("build.json");
  if (!manifestBuf) errors.push("нет описи build.json");
  else {
    let manifest = null;
    try { manifest = JSON.parse(manifestBuf.toString("utf8")); } catch { errors.push("опись build.json — не JSON"); }
    if (manifest !== null) {
      const okShape = typeof manifest === "object" && !Array.isArray(manifest)
        && typeof manifest.build === "string" && /^[A-Za-z0-9._-]{1,64}$/.test(manifest.build)
        && typeof manifest.files === "object" && manifest.files !== null && !Array.isArray(manifest.files)
        && Object.values(manifest.files).every((s) => typeof s === "string" && SHA256_RE.test(s));
      if (!okShape) errors.push('опись build.json не той формы: {"build": str, "files": {путь: sha256}}');
      else {
        files = manifest.files;
        if (meta && meta[1] !== manifest.build) errors.push("метка сборки в index.html и опись build.json — разных сборок");
      }
    }
  }
  if (files) {
    const listed = Object.keys(files);
    if (!listed.length) errors.push("опись пуста — сторож сочтёт опись неверной");
    if (listed.length > WEB_MAX_FILES)
      errors.push(`в описи больше ${WEB_MAX_FILES} записей (${listed.length}) — сторож сочтёт опись неверной`);
    for (const p of listed) {
      if (!WEB_FILE_RE.test(p) || /\/\.{1,2}$/.test(p))                    // «assets/..»: edge свёл бы его к /chat/
        errors.push(`опись: путь ${JSON.stringify(p)} не в форме сторожа (assets/<имя> или boot.js)`);
      else if (!texts.has(p)) errors.push(`опись: файла ${p} нет в каталоге сборки`);
      else if (createHash("sha256").update(/** @type {Buffer} */ (texts.get(p))).digest("hex") !== files[p])
        errors.push(`сумма ${p} не совпадает с описью`);
    }
    for (const p of onDisk)
      if (!NOT_IN_MANIFEST.has(p) && !Object.hasOwn(files, p)) errors.push(`опись: файл ${p} есть в каталоге, но не в описи`);
  }

  // assets/: без карт исходников, не больше WEB_MAX_FILES файлов
  for (const p of onDisk) if (p.endsWith(".map")) errors.push(`${p}: карта исходников (.map) в сборке`);
  const assets = onDisk.filter((p) => p.startsWith("assets/"));
  if (assets.length > WEB_MAX_FILES) errors.push(`в assets/ больше ${WEB_MAX_FILES} файлов (${assets.length})`);

  // строки заказчика — в любом файле сборки; API новее Chrome 109 — в assets/*.js
  for (const [p, buf] of texts) {
    const text = buf.toString("latin1");                                  // смещения — байтовые
    for (const f of FORBIDDEN) {
      const exceptions = KNOWN_STRINGS.filter((k) => k.what === f.what).map((k) => ({ ...k }));
      for (const m of text.matchAll(f.re)) {
        if (f.allow?.includes(m[0])) continue;
        const k = coveredBy(exceptions, text, m.index, m[0].length);
        if (k) known.push(`${p}: ${f.what} (смещение ${m.index}) — известное исключение: ${k.source}`);
        else errors.push(`${p}: ${f.what} (смещение ${m.index})`);
      }
    }
    if (!/^assets\/.+\.js$/.test(p)) continue;
    for (const api of NEWER_API) {
      const exceptions = KNOWN.filter((k) => k.api === api).map((k) => ({ ...k }));
      /** @type {Map<string, number>} */ const bySource = new Map();
      let rest = 0;
      for (const i of indexesOf(text, api)) {
        const k = coveredBy(exceptions, text, i, api.length);
        if (k) bySource.set(k.source, (bySource.get(k.source) ?? 0) + 1);
        else rest++;
      }
      if (rest) warnings.push(`${p}: API новее Chrome 109 — ${api} ×${rest}`);
      for (const [source, n] of bySource) known.push(`${p}: ${api} ×${n} — известное исключение: ${source}`);
    }
  }
  return { errors, warnings, known };
}

function main(argv) {
  const dir = argv[2];
  if (!dir) {
    console.error("использование: node scripts/check-dist.mjs <каталог сборки>");
    return 2;
  }
  const { errors, warnings, known } = checkDist(resolve(dir));
  const block = (title, lines) => lines.length && console.log(`${title} (${lines.length}):\n${lines.map((l) => `  - ${l}`).join("\n")}`);
  console.log(`check-dist: ${resolve(dir)}`);
  block("предупреждения — API новее Chrome 109", warnings);
  block("известные исключения", known);
  if (errors.length) {
    console.error(`check-dist: ошибки (${errors.length}):\n${errors.map((l) => `  - ${l}`).join("\n")}`);
    return 1;
  }
  console.log("check-dist: ошибок нет");
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exitCode = main(process.argv);
