// login.test.mjs — node --test edge/tests/*.test.mjs; fetch, DOM и таймеры поддельные
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { viewFor, loginError, meView, GIVE_UP_S, startPage } from "../static/login.js";

const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

test("состояния места", () => {
  assert.deepEqual(viewFor({ state: "starting" }), { text: "Готовлю рабочее место…", poll: true, ok: false });
  assert.equal(viewFor({ state: "sleeping" }).text, "Готовлю рабочее место…");
  assert.deepEqual(viewFor({ state: "ready" }), { text: "Рабочее место готово", poll: false, ok: true });
  const f = viewFor({ state: "failed", detail: "Рабочее место не создано — сообщите администратору." });
  assert.equal(f.text, "Не удалось подготовить рабочее место: Рабочее место не создано — сообщите администратору.");
  assert.equal(f.poll, false);
  assert.equal(GIVE_UP_S, 90);
  assert.deepEqual(viewFor({ state: "gave_up" }), { text: "Рабочее место не ответило за полторы минуты — обновите страницу или сообщите преподавателю.", poll: false, ok: false });
});

test("ошибки входа", () => {
  assert.equal(loginError(401, { name: "Unauthorized", data: { message: "Неверный логин или пароль" } }), "Неверный логин или пароль");
  assert.equal(loginError(503, { name: "Unavailable", data: { message: "Серверный слой не отвечает" } }), "Серверный слой не отвечает");
  assert.equal(loginError(502, "<html>"), "Серверный слой не отвечает");
});

test("ответ /me", () => {
  assert.deepEqual(meView(200, { login: "stu-03", role: "student", place: "03" }), { form: false, text: "Вы вошли как stu-03" });
  assert.deepEqual(meView(401, { name: "Unauthorized", data: { message: "Нужно войти" } }), { form: true, text: "" });
  assert.deepEqual(meView(503, { name: "Unavailable", data: { message: "Серверный слой не отвечает" } }),
                   { form: true, text: "Серверный слой не отвечает" });                // core лежит — форма всё равно есть
  assert.deepEqual(meView(504, "<html>"), { form: true, text: "Серверный слой не отвечает" });
});

test("непредвиденные ответы не ломают текст", () => {
  assert.equal(viewFor({ state: "failed" }).poll, false);
  assert.equal(viewFor({ state: "что-то новое" }).poll, false);                     // не опрашиваем вечно
  assert.equal(viewFor(null).poll, false);
  assert.equal(loginError(0, null), "Серверный слой не отвечает");                  // сеть оборвалась
  assert.equal(loginError(504, { name: "x" }), "Серверный слой не отвечает");
  assert.equal(meView(200, "<html>").form, true);                                    // 200 не по договору — форма
});

// --- ход страницы на поддельных DOM, fetch и часах -------------------------------------------

class FakeEl {
  constructor(id, extra = {}) {
    Object.assign(this, { id, hidden: false, disabled: false, textContent: "", value: "", attrs: {}, listeners: {} }, extra);
  }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  removeAttribute(name) { delete this.attrs[name]; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  fire(type) {
    const ev = { type, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    for (const fn of this.listeners[type] || []) fn(ev);
    return ev;
  }
}

const IDS = ["login-form", "login", "password", "submit", "who", "state", "logout"];

function harness(routes) {
  const els = Object.fromEntries(IDS.map((id) => [id, new FakeEl(id)]));
  els["login-form"].hidden = true;                       // как в login.html: до ответа /me формы нет
  els.who.hidden = true;
  els.logout.hidden = true;
  const doc = { getElementById: (id) => els[id] ?? null };
  const calls = [];
  const counts = {};
  async function fetchImpl(path, init = {}) {
    const key = `${init.method || "GET"} ${path}`;
    calls.push({ key, init });
    const n = (counts[key] = (counts[key] || 0) + 1);
    const handler = routes[key];
    if (!handler) throw new Error(`нет маршрута ${key}`);
    const [status, body] = await (typeof handler === "function" ? handler(n) : handler);   // обещание — ответ «в пути»
    if (status === 0) throw new TypeError("Failed to fetch");
    return { status, text: async () => (typeof body === "string" ? body : JSON.stringify(body)) };
  }
  let now = 0;
  let seq = 0;
  let tasks = [];
  const setTimeoutImpl = (fn, ms) => { tasks.push({ id: ++seq, at: now + ms, fn }); return seq; };
  const clearTimeoutImpl = (id) => { tasks = tasks.filter((t) => t.id !== id); };
  const flush = () => new Promise((resolve) => setImmediate(resolve));
  async function advance(ms) {
    const end = now + ms;
    for (;;) {
      await flush();
      const due = tasks.filter((t) => t.at <= end).sort((a, b) => a.at - b.at || a.id - b.id)[0];
      if (!due) break;
      tasks = tasks.filter((t) => t !== due);
      now = due.at;
      due.fn();
    }
    now = end;
    await flush();
  }
  startPage({ doc, fetchImpl, setTimeoutImpl, clearTimeoutImpl });
  const count = (key) => calls.filter((c) => c.key === key).length;
  return { els, calls, count, advance, flush, pending: () => tasks.length };
}

const NEED_LOGIN = [401, { name: "Unauthorized", data: { message: "Нужно войти" } }];
const STU = { login: "stu-03", role: "student", place: "03" };
const place = (state, detail = "") => ({ place: "03", state, detail });

test("вход студента: форма, «Готовлю…», потом «готово»", async () => {
  const h = harness({
    "GET /pcbk/api/auth/me": NEED_LOGIN,
    "POST /pcbk/api/auth/login": [200, STU],
    "POST /pcbk/api/workplace/wake": [202, place("starting")],
    "GET /pcbk/api/workplace": (n) => [200, place(n < 2 ? "starting" : "ready")],
  });
  await h.flush();
  assert.equal(h.els["login-form"].hidden, false);
  assert.equal(h.els.state.textContent, "");

  h.els.login.value = " stu-03 ";
  h.els.password.value = "<test-pw>";
  const ev = h.els["login-form"].fire("submit");
  assert.equal(ev.defaultPrevented, true);                                           // форма никуда не уходит
  await h.flush();

  const login = h.calls.find((c) => c.key === "POST /pcbk/api/auth/login").init;
  assert.equal(login.credentials, "same-origin");
  assert.equal(login.mode, undefined);             // режим cors по умолчанию: Origin уходит и при no-referrer
  assert.equal(login.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(login.body), { login: "stu-03", password: "<test-pw>" });
  assert.equal(h.els.password.value, "");                                            // пароль не остаётся в поле
  assert.equal(h.els["login-form"].hidden, true);
  assert.equal(h.els.who.hidden, false);
  assert.equal(h.els.who.textContent, "Вы вошли как stu-03");
  assert.equal(h.els.logout.hidden, false);
  assert.equal(h.els.state.textContent, "Готовлю рабочее место…");
  assert.equal(h.count("POST /pcbk/api/workplace/wake"), 1);
  for (const c of h.calls) assert.equal(c.init.credentials, "same-origin", c.key);

  await h.advance(1000);
  assert.equal(h.count("GET /pcbk/api/workplace"), 1);
  assert.equal(h.els.state.textContent, "Готовлю рабочее место…");
  await h.advance(1000);
  assert.equal(h.els.state.textContent, "Рабочее место готово");
  await h.advance(10_000);
  assert.equal(h.count("GET /pcbk/api/workplace"), 2);                               // готово — опрос кончился
  assert.equal(h.pending(), 0);                                                      // и срок ожидания снят
});

test("уже вошедший студент сразу будит место; преподаватель — без места", async () => {
  const stu = harness({
    "GET /pcbk/api/auth/me": [200, STU],
    "POST /pcbk/api/workplace/wake": [202, place("ready")],
  });
  await stu.flush();
  assert.equal(stu.els["login-form"].hidden, true);
  assert.equal(stu.els.who.textContent, "Вы вошли как stu-03");
  assert.equal(stu.els.state.textContent, "Рабочее место готово");

  const teach = harness({ "GET /pcbk/api/auth/me": [200, { login: "teach", role: "teacher", place: null }] });
  await teach.flush();
  assert.equal(teach.els.who.textContent, "Вы вошли как teach");
  assert.equal(teach.els.state.textContent, "У преподавателя нет рабочего места");
  assert.equal(teach.els.logout.hidden, false);
  assert.equal(teach.calls.length, 1);                                               // будить нечего
});

test("через GIVE_UP_S — «не ответило», опрос кончается", async () => {
  const h = harness({
    "GET /pcbk/api/auth/me": [200, STU],
    "POST /pcbk/api/workplace/wake": [202, place("sleeping")],
    "GET /pcbk/api/workplace": [200, place("starting")],
  });
  await h.advance(GIVE_UP_S * 1000 - 1);
  assert.equal(h.els.state.textContent, "Готовлю рабочее место…");
  await h.advance(1);
  assert.equal(h.els.state.textContent, viewFor({ state: "gave_up" }).text);
  const polls = h.count("GET /pcbk/api/workplace");
  assert.ok(polls >= GIVE_UP_S - 2 && polls <= GIVE_UP_S, String(polls));           // раз в 1 с
  await h.advance(10_000);
  assert.equal(h.count("GET /pcbk/api/workplace"), polls);
  assert.equal(h.pending(), 0);
});

test("отказ места и сбой опроса", async () => {
  const detail = "Нет связи с прокси сокета — рабочее место не поднять. Сообщите преподавателю.";
  const failed = harness({
    "GET /pcbk/api/auth/me": [200, STU],
    "POST /pcbk/api/workplace/wake": [202, place("starting")],
    "GET /pcbk/api/workplace": [200, place("failed", detail)],
  });
  await failed.advance(1000);
  assert.equal(failed.els.state.textContent, "Не удалось подготовить рабочее место: " + detail);
  await failed.advance(5000);
  assert.equal(failed.count("GET /pcbk/api/workplace"), 1);

  const flaky = harness({                                                            // core перезапускается
    "GET /pcbk/api/auth/me": [200, STU],
    "POST /pcbk/api/workplace/wake": [202, place("starting")],
    "GET /pcbk/api/workplace": (n) => (n === 1 ? [503, { name: "Unavailable", data: { message: "Серверный слой не отвечает" } }]
                                     : n === 2 ? [0] : [200, place("ready")]),
  });
  await flaky.advance(1000);
  assert.equal(flaky.els.state.textContent, "Серверный слой не отвечает");
  await flaky.advance(2000);
  assert.equal(flaky.els.state.textContent, "Рабочее место готово");

  const expired = harness({                                                          // сессия кончилась
    "GET /pcbk/api/auth/me": [200, STU],
    "POST /pcbk/api/workplace/wake": [202, place("starting")],
    "GET /pcbk/api/workplace": NEED_LOGIN,
  });
  await expired.advance(1000);
  assert.equal(expired.els["login-form"].hidden, false);
  assert.equal(expired.els.who.hidden, true);
  assert.equal(expired.els.state.textContent, "Нужно войти");
  await expired.advance(5000);
  assert.equal(expired.count("GET /pcbk/api/workplace"), 1);
});

test("ошибка входа остаётся на форме; core лежит — форма и причина", async () => {
  const h = harness({
    "GET /pcbk/api/auth/me": [503, { name: "Unavailable", data: { message: "Серверный слой не отвечает" } }],
    "POST /pcbk/api/auth/login": (n) => (n === 1 ? [401, { name: "Unauthorized", data: { message: "Неверный логин или пароль" } }]
                                        : [502, "<html>"]),
  });
  await h.flush();
  assert.equal(h.els["login-form"].hidden, false);
  assert.equal(h.els.state.textContent, "Серверный слой не отвечает");
  h.els.login.value = "stu-03";
  h.els.password.value = "<test-pw>";
  h.els["login-form"].fire("submit");
  await h.flush();
  assert.equal(h.els.state.textContent, "Неверный логин или пароль");
  assert.equal(h.els["login-form"].hidden, false);
  assert.equal(h.els.submit.disabled, false);
  assert.equal(h.els.password.value, "");
  h.els["login-form"].fire("submit");                                                 // пустой пароль — запроса нет
  await h.flush();
  assert.equal(h.els.state.textContent, "Введите логин и пароль");
  assert.equal(h.count("POST /pcbk/api/auth/login"), 1);
  h.els.password.value = "<test-pw>";
  h.els["login-form"].fire("submit");
  await h.flush();
  assert.equal(h.els.state.textContent, "Серверный слой не отвечает");
  assert.equal(h.count("POST /pcbk/api/workplace/wake"), 0);
});

test("«Выйти» закрывает сессию и гасит опрос", async () => {
  const h = harness({
    "GET /pcbk/api/auth/me": [200, STU],
    "POST /pcbk/api/workplace/wake": [202, place("starting")],
    "GET /pcbk/api/workplace": [200, place("starting")],
    "POST /pcbk/api/auth/logout": [200, {}],
  });
  await h.advance(1000);
  h.els.logout.fire("click");
  await h.flush();
  assert.equal(h.count("POST /pcbk/api/auth/logout"), 1);
  assert.equal(h.els["login-form"].hidden, false);
  assert.equal(h.els.who.hidden, true);
  assert.equal(h.els.logout.hidden, true);
  assert.equal(h.els.state.textContent, "Вы вышли");
  const polls = h.count("GET /pcbk/api/workplace");
  await h.advance(GIVE_UP_S * 1000);
  assert.equal(h.count("GET /pcbk/api/workplace"), polls);                          // опрос не идёт после выхода
  assert.equal(h.els.state.textContent, "Вы вышли");                                // и срок не перебивает текст
});

test("ответ, пришедший после выхода или срока, не перебивает", async () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const h = harness({
    "GET /pcbk/api/auth/me": [200, STU],
    "POST /pcbk/api/workplace/wake": [202, place("starting")],
    "GET /pcbk/api/workplace": (n) => (n === 1 ? held : [200, place("starting")]),
    "POST /pcbk/api/auth/logout": [200, {}],
  });
  await h.advance(1000);                                                             // опрос ушёл и висит
  h.els.logout.fire("click");
  await h.flush();
  release([200, place("ready")]);
  await h.advance(5000);
  assert.equal(h.els.state.textContent, "Вы вышли");
  assert.equal(h.count("GET /pcbk/api/workplace"), 1);

  let late;
  const hang = new Promise((resolve) => { late = resolve; });
  const g = harness({
    "GET /pcbk/api/auth/me": [200, STU],
    "POST /pcbk/api/workplace/wake": [202, place("starting")],
    "GET /pcbk/api/workplace": hang,                                                 // edge держит до 30 с
  });
  await g.advance(GIVE_UP_S * 1000);
  assert.equal(g.els.state.textContent, viewFor({ state: "gave_up" }).text);         // срок — по часам, не по ответу
  late([200, place("starting")]);
  await g.advance(5000);
  assert.equal(g.els.state.textContent, viewFor({ state: "gave_up" }).text);
  assert.equal(g.count("GET /pcbk/api/workplace"), 1);
  assert.equal(g.pending(), 0);
});

// --- статика: CSP без встроенного, без внешних ресурсов, без хранилищ --------------------------

test("login.html: форма, модуль и стили файлами, ничего встроенного и внешнего", () => {
  const html = read("../static/login.html");
  assert.match(html, /<html lang="ru">/);
  assert.equal((html.match(/<script\b/g) || []).length, 1);
  assert.match(html, /<script type="module" src="\/login\.js"><\/script>/);
  assert.match(html, /<link rel="stylesheet" href="\/login\.css">/);
  assert.doesNotMatch(html, /<style\b|\sstyle=|\son[a-z]+=|javascript:/i);           // style-src/script-src 'self'
  assert.doesNotMatch(html, /(src|href|action)="(https?:)?\/\//i);                    // внешних ресурсов нет
  for (const id of IDS) assert.match(html, new RegExp(`id="${id}"`), id);
  assert.match(html, /autocomplete="username"/);
  assert.match(html, /type="password"[^>]*autocomplete="current-password"|autocomplete="current-password"[^>]*type="password"/);
  assert.match(html, />Войти</);
  assert.match(html, />Выйти</);
  assert.match(html, /Учебный стенд/);
});

test("login.js и login.css: без хранилищ, cookie, innerHTML и внешних адресов", () => {
  const js = read("../static/login.js");
  assert.doesNotMatch(js, /localStorage|sessionStorage|indexedDB|document\.cookie|innerHTML|eval\(|new Function/);
  assert.doesNotMatch(js, /https?:\/\//);
  const css = read("../static/login.css");
  assert.doesNotMatch(css, /@import|url\(/);
  assert.match(css, /\[hidden\]/);
});

test("index.html: ссылка «Войти» на /login, предупреждение стенда на месте", () => {
  const html = read("../static/index.html");
  assert.match(html, /<a href="\/login">Войти<\/a>/);
  assert.match(html, /Учебный стенд: не для ведения технологического процесса/);
});

// --- шаблон edge: то, что без nginx -t видно по тексту (сам nginx -t — задача А6) --------------

function locations(conf) {
  const out = [];
  const re = /location\s+(=\s+)?(\S+)\s*\{/g;
  let m;
  while ((m = re.exec(conf))) {
    let depth = 1;
    let i = re.lastIndex;
    for (; depth > 0 && i < conf.length; i++) {
      if (conf[i] === "{") depth++;
      else if (conf[i] === "}") depth--;
      else if (conf[i] === "'") i = conf.indexOf("'", i + 1);   // тела JSON в кавычках
    }
    out.push({ exact: Boolean(m[1]), path: m[2], body: conf.slice(re.lastIndex, i - 1) });
  }
  return out;
}

test("шаблон edge: ошибки JSON на уровне server, пути шлюза, страница входа", () => {
  const conf = read("../pcbk.conf.template");
  const locs = locations(conf);
  const loc = (path) => locs.find((l) => l.path === path);

  for (const line of [
    'map $request_uri $pcbk_bad_api_uri {',
    '"~(%2[eEfF]|%5[cC]|/\\.|//)" 1;',
    "limit_req_zone $binary_remote_addr zone=pcbk_login:1m rate=5r/s;",
    "set $pcbk_core pcbk-core:8000;",
    "proxy_set_header X-Forwarded-Host $host;",
    'proxy_set_header X-Forwarded-For "";',
    'proxy_set_header X-Real-IP "";',
    'proxy_set_header Forwarded "";',
    "proxy_buffer_size 32k;",
    "proxy_buffers 8 32k;",
    "proxy_busy_buffers_size 64k;",
    "error_page 400 /pcbk-errors/400.json;",
    "error_page 405 /pcbk-errors/405.json;",
    "error_page 413 /pcbk-errors/413.json;",
    "error_page 414 /pcbk-errors/414.json;",
    "error_page 494 =400 /pcbk-errors/400.json;",
    "error_page 429 /pcbk-errors/429.json;",
    "error_page 502 503 504 =503 /pcbk-errors/503.json;",
    'if ($host !~* "^[a-z0-9-]{1,63}(\\.[a-z0-9-]{1,63}){0,5}$") { rewrite ^ /pcbk-errors/host.json last; }',
  ]) assert.ok(conf.includes(line), line);

  for (const l of locs) {                                                              // всё это — уровень server
    assert.doesNotMatch(l.body, /error_page[^;]*pcbk-errors/, l.path);
    assert.doesNotMatch(l.body, /proxy_set_header/, l.path);
  }
  for (const h of ["Connection", "X-Forwarded-Host", "X-Forwarded-For", "X-Real-IP", "Forwarded"]) {
    assert.equal(conf.match(new RegExp(`proxy_set_header ${h} `, "g"))?.length, 1, h);  // заголовки клиента к core не доходят
  }

  for (const [code, name] of [["400", "BadRequest"], ["405", "MethodNotAllowed"], ["413", "PayloadTooLarge"],
                              ["414", "URITooLong"], ["429", "TooManyAttempts"], ["503", "Unavailable"],
                              ["host", "BadRequest"]]) {
    const l = loc(`/pcbk-errors/${code}.json`);
    assert.ok(l && l.exact, code);
    assert.match(l.body, /\binternal;/, code);                                         // снаружи не видны
    assert.match(l.body, new RegExp(`return ${code === "host" ? 400 : code} '\\{"name":"${name}","data":\\{"message":"[^"]+"\\}\\}';`), code);
  }

  for (const path of ["/login", "/login.js", "/login.css"]) {
    const l = loc(path);
    assert.ok(l && l.exact, path);
    assert.ok(l.body.includes(`add_header Content-Security-Policy "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'" always;`), path);
    for (const h of ['add_header Cache-Control "no-store" always;', 'add_header X-Content-Type-Options "nosniff" always;',
                     'add_header Referrer-Policy "no-referrer" always;']) assert.ok(l.body.includes(h), `${path}: ${h}`);
    assert.match(l.body, new RegExp(`try_files /login\\.${path === "/login" ? "html" : path.split(".")[1]} =404;`), path);
  }

  const proxied = ["/pcbk/api/auth/", "/pcbk/api/workplace", "/pcbk/api/workplace/wake"];
  const passes = locs.flatMap((l) => [...l.body.matchAll(/proxy_pass\s+([^;]*);/g)].map((m) => [l.path, m[1]]));
  assert.equal(passes.length, conf.match(/proxy_pass\b/g).length);                     // каждый proxy_pass — внутри location
  assert.equal(new Set(passes.map(([path]) => path)).size, passes.length);              // по одному на location
  assert.deepEqual(Object.fromEntries(passes), {                                        // в core — только пути шлюза
    "/status": "http://$pcbk_watchdog", "/status.js": "http://$pcbk_watchdog", "/status.json": "http://$pcbk_watchdog",
    ...Object.fromEntries(proxied.map((path) => [path, "http://$pcbk_core"])),
  });
  assert.equal(loc("/pcbk/api/auth/").exact, false);
  assert.equal(loc("/pcbk/api/workplace").exact, true);
  assert.equal(loc("/pcbk/api/workplace/wake").exact, true);
  for (const path of proxied) {
    const b = loc(path).body;
    assert.ok(b.includes("proxy_pass http://$pcbk_core;"), path);
    assert.ok(b.includes("if ($pcbk_bad_api_uri) { return 400"), path);
    assert.ok(b.includes("client_max_body_size 64k;") && b.includes("proxy_read_timeout 30s;"), path);
    assert.equal(b.includes("limit_req zone=pcbk_login burst=20 nodelay;"), path === "/pcbk/api/auth/", path);
  }
  assert.ok(loc("/pcbk/api/auth/").body.includes("limit_req_status 429;"));
  assert.ok(loc("/pcbk/api/").body.includes("if ($pcbk_bad_api_uri) { return 400"));
  assert.match(loc("/pcbk/api/").body, /return 404 '\{"name":"NotFound"/);
  for (const [path, code] of [["/pcbk/api", 404], ["/pcbk/api/auth", 404], ["/pcbk/api/oc", 403]]) {
    assert.ok(loc(path)?.exact, path);                                                  // без «/» — не 301 HTML
    assert.match(loc(path).body, new RegExp(`return ${code} '\\{"name":"`), path);
  }

  for (const l of locs.filter((x) => x.path.startsWith("/pcbk/api"))) {                 // серверные error_page и заголовки не теряются
    assert.doesNotMatch(l.body, /error_page|proxy_set_header|add_header/, l.path);
    assert.match(l.body, /default_type application\/json;/, l.path);
  }
  assert.equal(locs.filter((l) => l.path === "/pcbk/api/oc/").length, 0);                // /pcbk/api/oc/* — Д5б
  assert.ok(loc("/status").body.includes("error_page 502 503 504 /watchdog-down.html;")); // страница состояния Д1 — своя
});
