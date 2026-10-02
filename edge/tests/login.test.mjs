// login.test.mjs — node --test edge/tests/*.test.mjs; fetch, DOM и таймеры поддельные
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { viewFor, loginError, meView, GIVE_UP_S, CHAT_URL, startPage } from "../static/login.js";

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

const IDS = ["login-form", "login", "password", "submit", "who", "state", "logout", "to-chat"];

function harness(routes) {
  const els = Object.fromEntries(IDS.map((id) => [id, new FakeEl(id)]));
  els["login-form"].hidden = true;                       // как в login.html: до ответа /me формы нет
  els.who.hidden = true;
  els.logout.hidden = true;
  els["to-chat"].hidden = true;                          // запасная ссылка «Открыть чат» — до «готово» скрыта
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
  const navigations = [];                                // переходы страницы (в Node нет location)
  startPage({ doc, fetchImpl, setTimeoutImpl, clearTimeoutImpl, navigate: (url) => navigations.push(url) });
  const count = (key) => calls.filter((c) => c.key === key).length;
  return { els, calls, count, advance, flush, navigations, pending: () => tasks.length };
}

const NEED_LOGIN = [401, { name: "Unauthorized", data: { message: "Нужно войти" } }];
const STU = { login: "stu-03", role: "student", place: "03" };
const place = (state, detail = "") => ({ place: "03", state, detail });
// /me: первый вопрос страницы — без сессии, после входа (Д6б) — учётка; ответ входа предупреждения не несёт
const meAfterLogin = (account) => (n) => (n === 1 ? NEED_LOGIN : [200, account]);
const NOTICE = "teacher-reads-1";

test("вход студента: форма, «Готовлю…», потом «готово»", async () => {
  const h = harness({
    "GET /pcbk/api/auth/me": meAfterLogin(STU),
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
  assert.deepEqual(h.calls.map((c) => c.key), ["GET /pcbk/api/auth/me", "POST /pcbk/api/auth/login",
                                               "GET /pcbk/api/auth/me", "POST /pcbk/api/workplace/wake"]);   // будит после /me
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
  assert.deepEqual(h.navigations, []);                                               // срок вышел — не в чат
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
  assert.deepEqual(failed.navigations, []);                                          // отказ — не в чат

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
  assert.deepEqual(flaky.navigations, [CHAT_URL]);                                   // сбой опроса перетерпели

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
  assert.deepEqual(expired.navigations, []);
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

test("ready_navigates_to_chat_once", async () => {
  assert.equal(CHAT_URL, "/chat/");
  const h = harness({
    "GET /pcbk/api/auth/me": meAfterLogin(STU),
    "POST /pcbk/api/auth/login": [200, STU],
    "POST /pcbk/api/workplace/wake": [202, place("starting")],
    "GET /pcbk/api/workplace": (n) => [200, place(n < 2 ? "starting" : "ready")],
    "POST /pcbk/api/auth/logout": [200, {}],
  });
  await h.flush();
  h.els.login.value = "stu-03";
  h.els.password.value = "<test-pw>";
  h.els["login-form"].fire("submit");
  await h.flush();
  assert.equal(h.els.state.textContent, "Готовлю рабочее место…");
  await h.advance(1000);                                                             // опрос: ещё «starting»
  assert.deepEqual(h.navigations, []);
  assert.equal(h.els["to-chat"].hidden, true);
  await h.advance(1000);                                                             // «ready»
  assert.equal(h.els.state.textContent, "Рабочее место готово");
  assert.deepEqual(h.navigations, [CHAT_URL]);
  assert.equal(h.els["to-chat"].hidden, false);                                      // запасная ссылка «Открыть чат»
  await h.advance(GIVE_UP_S * 1000);
  assert.deepEqual(h.navigations, [CHAT_URL]);                                       // ровно один раз за ход ожидания
  assert.equal(h.count("GET /pcbk/api/workplace"), 2);
  h.els.logout.fire("click");                                                         // переход не случился — «Выйти» прячет ссылку
  await h.flush();
  assert.equal(h.els["to-chat"].hidden, true);

  const ready = harness({                                                             // место уже готово — сразу в чат
    "GET /pcbk/api/auth/me": [200, STU],
    "POST /pcbk/api/workplace/wake": [200, place("ready")],
  });
  await ready.advance(GIVE_UP_S * 1000);
  assert.deepEqual(ready.navigations, [CHAT_URL]);
  assert.equal(ready.els["to-chat"].hidden, false);
  assert.equal(ready.count("GET /pcbk/api/workplace"), 0);
});

test("teacher_never_navigates", async () => {
  const TEACH = { login: "teach", role: "teacher", place: null };
  const signedIn = harness({ "GET /pcbk/api/auth/me": [200, TEACH] });
  const fresh = harness({ "GET /pcbk/api/auth/me": meAfterLogin({ ...TEACH, notice: null }),
                          "POST /pcbk/api/auth/login": [200, TEACH] });
  await fresh.flush();
  fresh.els.login.value = "teach";
  fresh.els.password.value = "<test-pw>";
  fresh.els["login-form"].fire("submit");
  for (const h of [signedIn, fresh]) {
    await h.advance(GIVE_UP_S * 1000);
    assert.equal(h.els.state.textContent, "У преподавателя нет рабочего места");
    assert.deepEqual(h.navigations, []);                                             // у преподавателя чата нет
    assert.equal(h.els["to-chat"].hidden, true);
    assert.equal(h.count("POST /pcbk/api/workplace/wake"), 0);
  }
});

// --- предупреждение при первом входе (Д6-R49, Review Focus 1 Д6б): вход не будит место до «Понятно» ------------

async function submitLogin(h) {
  await h.flush();
  h.els.login.value = "stu-03";
  h.els.password.value = "<test-pw>";
  h.els["login-form"].fire("submit");
  await h.flush();
}

test("login_does_not_wake_before_notice", async () => {
  const h = harness({
    "GET /pcbk/api/auth/me": meAfterLogin({ ...STU, notice: NOTICE }),
    "POST /pcbk/api/auth/login": [200, STU],                                         // ответ входа — без notice
    "POST /pcbk/api/workplace/wake": [202, place("starting")],
  });
  await submitLogin(h);
  assert.deepEqual(h.navigations, [CHAT_URL]);                                       // предупреждение — в чате
  assert.equal(h.count("POST /pcbk/api/workplace/wake"), 0);
  assert.equal(h.count("GET /pcbk/api/auth/me"), 2);
  assert.equal(h.els["to-chat"].hidden, false);                                      // запасная ссылка «Открыть чат»
  assert.equal(h.els["login-form"].hidden, true);
  assert.equal(h.els.who.textContent, "Вы вошли как stu-03");
  assert.equal(h.els.logout.hidden, false);
  await h.advance(GIVE_UP_S * 1000);
  assert.equal(h.count("POST /pcbk/api/workplace/wake"), 0);                         // и потом не будит
  assert.deepEqual(h.navigations, [CHAT_URL]);
  assert.equal(h.pending(), 0);

  const odd = harness({                                                              // незнакомое значение — тоже чат решает
    "GET /pcbk/api/auth/me": meAfterLogin({ ...STU, notice: 7 }),
    "POST /pcbk/api/auth/login": [200, STU],
  });
  await submitLogin(odd);
  assert.deepEqual(odd.navigations, [CHAT_URL]);
  assert.equal(odd.count("POST /pcbk/api/workplace/wake"), 0);
});

test("open_session_with_notice_goes_to_chat", async () => {
  const h = harness({ "GET /pcbk/api/auth/me": [200, { ...STU, notice: NOTICE }] });
  await h.advance(GIVE_UP_S * 1000);
  assert.deepEqual(h.navigations, [CHAT_URL]);
  assert.equal(h.count("POST /pcbk/api/workplace/wake"), 0);
  assert.equal(h.calls.length, 1);                                                   // /me уже прочитан — второй не нужен
  assert.equal(h.els["login-form"].hidden, true);
  assert.equal(h.els["to-chat"].hidden, false);

  const acked = harness({                                                            // подтверждено (null) — как в Д6а
    "GET /pcbk/api/auth/me": [200, { ...STU, notice: null }],
    "POST /pcbk/api/workplace/wake": [200, place("ready")],
  });
  await acked.flush();
  assert.equal(acked.count("POST /pcbk/api/workplace/wake"), 1);
  assert.equal(acked.els.state.textContent, "Рабочее место готово");
  assert.deepEqual(acked.navigations, [CHAT_URL]);
});

test("me_failure_after_login_does_not_wake", async () => {                           // раунд 2, В4
  const STORE = [503, { name: "Unavailable", data: { message: "Учётные записи недоступны — повторите позже" } }];
  for (const [answer, text] of [[STORE, "Учётные записи недоступны — повторите позже"], [[0], "Серверный слой не отвечает"],
                                [[502, "<html>"], "Серверный слой не отвечает"]]) {
    const h = harness({
      "GET /pcbk/api/auth/me": (n) => (n === 1 ? NEED_LOGIN : answer),
      "POST /pcbk/api/auth/login": [200, STU],
      "POST /pcbk/api/workplace/wake": [202, place("starting")],
    });
    await submitLogin(h);
    assert.equal(h.els.state.textContent, text, text);
    assert.equal(h.els.state.attrs["data-kind"], "fail");
    assert.equal(h.els["to-chat"].hidden, false);                                    // решит чат своим «Повторить»
    assert.equal(h.els["login-form"].hidden, true);                                  // вход удался — сессия есть
    assert.equal(h.els.who.textContent, "Вы вошли как stu-03");
    assert.equal(h.els.logout.hidden, false);
    await h.advance(GIVE_UP_S * 1000);
    assert.equal(h.count("POST /pcbk/api/workplace/wake"), 0, text);
    assert.deepEqual(h.navigations, []);
    assert.equal(h.pending(), 0);
  }

  const gone = harness({                                                             // Н5: /me после входа 401 — форма, не ссылка
    "GET /pcbk/api/auth/me": NEED_LOGIN,
    "POST /pcbk/api/auth/login": [200, STU],
  });
  await submitLogin(gone);
  assert.equal(gone.els["login-form"].hidden, false);
  assert.equal(gone.els["to-chat"].hidden, true);
  assert.equal(gone.els.state.textContent, "Нужно войти");
  assert.equal(gone.count("POST /pcbk/api/workplace/wake"), 0);
});

test("login_form_busy_until_me_answers", async () => {                               // второй вход, пока /me в пути, не уходит
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const h = harness({
    "GET /pcbk/api/auth/me": (n) => (n === 1 ? NEED_LOGIN : held),
    "POST /pcbk/api/auth/login": [200, STU],
    "POST /pcbk/api/workplace/wake": [200, place("ready")],
  });
  await submitLogin(h);
  assert.equal(h.els.submit.disabled, true);
  h.els.password.value = "<test-pw>";
  h.els["login-form"].fire("submit");
  await h.flush();
  assert.equal(h.count("POST /pcbk/api/auth/login"), 1);
  release([200, STU]);
  await h.flush();
  assert.equal(h.els.submit.disabled, false);
  assert.equal(h.count("POST /pcbk/api/workplace/wake"), 1);                         // notice нет (старый core) — как в Д6а
  assert.deepEqual(h.navigations, [CHAT_URL]);
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
  assert.match(html, /<p id="to-chat" hidden><a href="\/chat\/">Открыть чат<\/a><\/p>/);  // до «готово» скрыта
});

test("login.js и login.css: без хранилищ, cookie, innerHTML и внешних адресов", () => {
  const js = read("../static/login.js");
  assert.doesNotMatch(js, /localStorage|sessionStorage|indexedDB|document\.cookie|innerHTML|eval\(|new Function/);
  assert.doesNotMatch(js, /https?:\/\//);
  const css = read("../static/login.css");
  assert.doesNotMatch(css, /@import|url\(/);
  assert.match(css, /\[hidden\]/);
});

test("index.html: ссылки «Войти» на /login и «Чат» на /chat/, предупреждение стенда на месте", () => {
  const html = read("../static/index.html");
  assert.match(html, /<a href="\/login">Войти<\/a>/);
  assert.match(html, /<a href="\/chat\/">Чат<\/a>/);
  assert.doesNotMatch(html, /появятся здесь позже/);
  assert.match(html, /Учебный стенд: не для ведения технологического процесса/);
});

// --- шаблон edge: то, что без nginx -t видно по тексту (сам nginx -t — задача А6) --------------

// комментарий nginx — от «#» в начале слова вне кавычек до конца строки: скобки в нём разборщик не считает
// (итоговая доводка Д6б, Б4-m5). Комментарий заменяется пробелами — смещения и номера строк те же
function stripComments(conf) {
  return conf.split("\n").map((line) => {
    let quote = null;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (quote) {
        if (ch === "\\") i++;
        else if (ch === quote) quote = null;
      } else if (ch === "'" || ch === '"') quote = ch;
      else if (ch === "#" && (i === 0 || /[\s;{}]/.test(line[i - 1]))) return line.slice(0, i) + " ".repeat(line.length - i);
    }
    return line;
  }).join("\n");
}

// location с модификатором (= или ^~) и портом своего server: server-блоки идут подряд на верхнем уровне, так что
// location принадлежит последнему server перед ним. Регулярных location (~, ~*) в шаблоне нет — разборщик их не видит.
// Тела — без комментариев
function locations(raw) {
  const conf = stripComments(raw);
  const servers = [...conf.matchAll(/^server\s*\{/gm)].map((m) => ({
    at: m.index, port: Number(/^\s*listen\s+(\d+)/m.exec(conf.slice(m.index))[1]),
  }));
  const out = [];
  const re = /location\s+(=|\^~)?\s*(\S+)\s*\{/g;
  let m;
  while ((m = re.exec(conf))) {
    const at = m.index;
    let depth = 1;
    let i = re.lastIndex;
    for (; depth > 0 && i < conf.length; i++) {
      if (conf[i] === "{") depth++;
      else if (conf[i] === "}") depth--;
      else if (conf[i] === "'") i = conf.indexOf("'", i + 1);   // тела JSON в кавычках
    }
    const port = servers.filter((s) => s.at < at).at(-1)?.port;
    out.push({ exact: m[1] === "=", mod: m[1] ?? "", path: m[2], port, body: conf.slice(re.lastIndex, i - 1) });
  }
  return out;
}

test("разборщик шаблона: скобки и кавычки в комментариях не в счёт", () => {   // Б4-m5, положительный контроль
  const conf = 'server {\n    listen 8443;\n    # чужое { в комментарии\n' +
               '    location = /a { # ещё { и }\n        return 200 \'{"a":"#не комментарий"}\';  # хвост }\n    }\n' +
               '    location ^~ /b/ { return 204; }\n}\n';
  const locs = locations(conf);
  assert.deepEqual(locs.map((l) => [l.mod, l.path, l.port]), [["=", "/a", 8443], ["^~", "/b/", 8443]]);
  assert.equal(locs[0].body.trim(), 'return 200 \'{"a":"#не комментарий"}\';');          // «#» в кавычках — текст
  assert.equal(locs[1].body.trim(), "return 204;");
  assert.equal(stripComments(conf).length, conf.length);
});

test("шаблон edge: ошибки JSON на уровне server, пути шлюза, страница входа", () => {
  const conf = read("../pcbk.conf.template");
  const locs = locations(conf);
  const loc = (path) => locs.find((l) => l.path === path && l.port === 8443);

  for (const line of [
    'map $request_uri $pcbk_bad_api_uri {',
    '"~^[^?]*(%2[eEfF]|%5[cC]|/\\.|//)" 1;',                                     // только путь до «?» (Д5б)
    "map $uri $pcbk_api_csp {",                                                        // путь после нормализации и error_page
    `"~^/pcbk(?:/api(?:/|$)|-errors/)" "default-src 'none'";`,
    "add_header Content-Security-Policy $pcbk_api_csp always;",
    "map $request $pcbk_log_request {",                                                // журнал доступа без строки запроса,
    '"~^(\\S+) ([^?\\s]*)\\S* (\\S+)$" "$1 $2 $3";',                                  // метод — исходный (Б5-m1)
    `'$remote_addr - $remote_user [$time_local] "$pcbk_log_request" '`,
    "limit_req_zone $pcbk_oc_session zone=pcbk_oc_rate:1m rate=20r/s;",                // N1
    "limit_req_zone $pcbk_oc_session zone=pcbk_oc_event:1m rate=20r/s;",
    "limit_req_zone $pcbk_oc_session zone=pcbk_place_rate:1m rate=5r/s;",              // ручки места (остаток Д5б)
    "access_log /var/log/nginx/access.log pcbk;",
    "map $cookie_pcbk_session $pcbk_oc_session {",
    '"~^[A-Za-z0-9_-]{43}$" $cookie_pcbk_session;',
    "limit_conn_zone $pcbk_oc_session zone=pcbk_oc_session:1m;",
    '    default "-";\n    "~^[A-Za-z0-9_-]{43}$" $cookie_pcbk_session;\n}',           // без cookie — корзина «-», не мимо предела
    "limit_conn_zone $server_port zone=pcbk_oc_all:1m;",
    "client_body_temp_path /var/lib/pcbk-edge/client;",
    "proxy_temp_path       /var/lib/pcbk-edge/proxy;",
    "error_page 509 =503 /pcbk-errors/busy.json;",
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
  assert.doesNotMatch(conf, /^\s*"" "";/m);                                           // запросы без cookie — не мимо предела
  assert.equal(conf.match(/\$request(?![_a-z])/g).length, 1);                           // строка запроса целиком — только
  assert.ok(conf.includes("map $request $pcbk_log_request {"));                          // источник карты журнала

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
  const busy = loc("/pcbk-errors/busy.json");                                          // limit_conn /pcbk/api/oc/ — как занятость шлюза
  assert.ok(busy && busy.exact && /\binternal;/.test(busy.body));
  assert.ok(busy.body.includes(`return 503 '{"name":"WorkplaceBusy","data":{"message":"Слишком много одновременных запросов — повторите позже"}}';`));

  for (const path of ["/login", "/login.js", "/login.css"]) {
    const l = loc(path);
    assert.ok(l && l.exact, path);
    assert.ok(l.body.includes(`add_header Content-Security-Policy "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'" always;`), path);
    for (const h of ['add_header Cache-Control "no-store" always;', 'add_header X-Content-Type-Options "nosniff" always;',
                     'add_header Referrer-Policy "no-referrer" always;']) assert.ok(l.body.includes(h), `${path}: ${h}`);
    assert.match(l.body, new RegExp(`try_files /login\\.${path === "/login" ? "html" : path.split(".")[1]} =404;`), path);
  }

  const proxied = ["/pcbk/api/auth/", "/pcbk/api/auth/me", "/pcbk/api/auth/notice", "/pcbk/api/workplace",
                   "/pcbk/api/workplace/wake", "/pcbk/api/agents",                      // «Мои агенты» (Д7б, Д7-R64)
                   "/pcbk/api/calc/"];                                                  // калькуляторы (Д9а, Д9-R87)
  const oc = ["/pcbk/api/oc/", "/pcbk/api/oc/event"];                                   // Д5б
  const report = "/pcbk/api/web/report";                                               // отчёты браузеров (Д6б, Д6-R50)
  const passes = locs.flatMap((l) => [...l.body.matchAll(/proxy_pass\s+([^;]*);/g)].map((m) => [l.path, m[1]]));
  assert.equal(passes.length, conf.match(/proxy_pass\b/g).length);                     // каждый proxy_pass — внутри location
  assert.equal(new Set(passes.map(([path]) => path)).size, passes.length);              // по одному на location
  assert.deepEqual(Object.fromEntries(passes), {                                        // в core — только пути шлюза
    "/status": "http://$pcbk_watchdog", "/status.js": "http://$pcbk_watchdog", "/status.json": "http://$pcbk_watchdog",
    ...Object.fromEntries([...proxied, ...oc, report].map((path) => [path, "http://$pcbk_core"])),
  });
  // отчёты браузеров (Д6-R50): частота — по cookie сессии (карта Б5, без cookie — корзина «-»), а не по IP: весь класс
  // бывает за одним адресом; отказ предела — 509 → busy.json (503 WorkplaceBusy), как у прочих пределов Д5б (Н2);
  // тело — до 1 КиБ (core сам режет до 256 Б), срок — 10 с
  assert.ok(conf.includes("limit_req_zone $pcbk_oc_session zone=pcbk_web:1m rate=1r/s;"));
  assert.equal(conf.match(/zone=pcbk_web:/g).length, 1);
  assert.equal(loc(report).exact, true);
  {
    const b = loc(report).body;
    for (const line of ["proxy_pass http://$pcbk_core;", "if ($pcbk_bad_api_uri) { return 400",
                        "limit_req zone=pcbk_web burst=10 nodelay;", "limit_req_log_level warn;",
                        "client_max_body_size 1k;", "proxy_read_timeout 10s;"])
      assert.ok(b.includes(line), `${report}: ${line}`);
    assert.deepEqual([...b.matchAll(/limit_req_status (\d+);/g)].map((m) => m[1]), ["509"], report);   // не 429 входа
    assert.doesNotMatch(b, /zone=pcbk_login|zone=pcbk_oc|zone=pcbk_place|limit_conn/, report);           // своя зона
  }
  assert.equal(loc("/pcbk/api/auth/").exact, false);
  // «кто я» и подтверждение (итоговая доводка Д6б, m2; Н17): своя зона, а не корзина входа — класс за одним NAT не
  // делит её с /me. Ключ — cookie сессии своего вида, без неё — адрес клиента: общей корзины «-» нет (первый /me
  // страницы входа идёт без cookie, класс открывает её разом); запас 40 — класс из 30 за одним адресом.
  // Отказ — 509 → busy.json, как у ручек места
  assert.ok(conf.includes('map $cookie_pcbk_session $pcbk_me_key {\n    default $binary_remote_addr;\n' +
                          '    "~^[A-Za-z0-9_-]{43}$" $cookie_pcbk_session;\n}'));
  assert.ok(conf.includes("limit_req_zone $pcbk_me_key zone=pcbk_auth_session:1m rate=5r/s;"));
  assert.equal(conf.match(/zone=pcbk_auth_session:/g).length, 1);
  assert.equal(conf.match(/\$pcbk_me_key\b/g).length, 2);                            // карта и зона — больше нигде
  for (const path of ["/pcbk/api/auth/me", "/pcbk/api/auth/notice"]) {
    const b = loc(path).body;
    assert.equal(loc(path).exact, true, path);
    assert.ok(b.includes("limit_req zone=pcbk_auth_session burst=40 nodelay;"), path);
    assert.deepEqual([...b.matchAll(/limit_req_status (\d+);/g)].map((m) => m[1]), ["509"], path);   // не 429 входа
    assert.ok(b.includes("limit_req_log_level warn;"), path);
    assert.doesNotMatch(b, /zone=pcbk_login|zone=pcbk_oc|zone=pcbk_place|zone=pcbk_web|limit_conn/, path);
  }
  assert.equal(locs.filter((l) => l.port === 8443 && l.path.startsWith("/pcbk/api/auth/")).length, 3);   // вход и выход — по IP
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
  for (const path of ["/pcbk/api/workplace", "/pcbk/api/workplace/wake"]) {             // остаток Д5б: поток без учётки
    const b = loc(path).body;
    assert.ok(b.includes("limit_req zone=pcbk_place_rate burst=20 nodelay;"), path);
    assert.deepEqual([...b.matchAll(/limit_req_status (\d+);/g)].map((m) => m[1]), ["509"], path);   // busy.json, не 429 входа
    assert.ok(b.includes("limit_req_log_level warn;"), path);
  }
  // «Мои агенты» (Д7б, Д7-R64): копия location = /pcbk/api/workplace — зона pcbk_place_rate, 509 → busy.json, тело
  // 64k, срок 30 с, сверка сырого пути; тела сравниваются без комментариев и с точностью до пробелов
  {
    const flat = (path) => loc(path).body.trim().split(/\s+/).join(" ");
    assert.equal(loc("/pcbk/api/agents").exact, true);
    assert.equal(locs.filter((l) => l.port === 8443 && l.path === "/pcbk/api/agents").length, 1);
    assert.ok(flat("/pcbk/api/workplace").includes("limit_req zone=pcbk_place_rate burst=20 nodelay;"));   // контроль
    assert.equal(flat("/pcbk/api/agents"), flat("/pcbk/api/workplace"));
    // Д9-R87: калькуляторы — префикс (имя инструмента сверяет core), копия пределов ручек места
    const calc = "/pcbk/api/calc/";
    assert.equal(loc(calc).exact, false);
    assert.equal(locs.filter((l) => l.port === 8443 && l.path === calc).length, 1);
    assert.equal(flat(calc), flat("/pcbk/api/workplace"));
  }
  assert.ok(loc("/pcbk/api/").body.includes("if ($pcbk_bad_api_uri) { return 400"));
  assert.match(loc("/pcbk/api/").body, /return 404 '\{"name":"NotFound"/);
  for (const [path, code] of [["/pcbk/api", 404], ["/pcbk/api/auth", 404], ["/pcbk/api/oc", 403],
                              ["/pcbk/api/calc", 404]]) {                               // Д9а, m7: калькуляторы
    assert.ok(loc(path)?.exact, path);                                                  // без «/» — не 301 HTML
    assert.match(loc(path).body, new RegExp(`return ${code} '\\{"name":"`), path);
  }

  for (const l of locs.filter((x) => x.path.startsWith("/pcbk/api"))) {                 // серверные error_page и заголовки не теряются
    assert.doesNotMatch(l.body, /error_page|proxy_set_header|add_header/, l.path);
    assert.match(l.body, /default_type application\/json;/, l.path);
  }
  assert.equal(loc("/pcbk/api/oc/").exact, false);                                     // вызовы адаптера OpenCode (Д5б)
  assert.equal(loc("/pcbk/api/oc/event").exact, true);
  for (const path of oc) {
    const b = loc(path).body;
    for (const line of ["proxy_pass http://$pcbk_core;", "if ($pcbk_bad_api_uri) { return 400", "client_max_body_size 512k;",
                        "limit_conn pcbk_oc_session 10;", "limit_conn pcbk_oc_all 64;", "limit_conn_status 509;",
                        "limit_conn_log_level warn;", "proxy_request_buffering on;", "proxy_buffers 4 32k;",
                        "limit_req_status 509;", "limit_req_log_level warn;", "client_body_timeout 10s;"])
      assert.ok(b.includes(line), `${path}: ${line}`);
    assert.doesNotMatch(b, /zone=pcbk_login/, path);                                     // корзина входа — не здесь
  }
  assert.ok(loc("/pcbk/api/oc/").body.includes("limit_req zone=pcbk_oc_rate burst=200 nodelay;"));   // N1: ≥ 124
  assert.ok(loc("/pcbk/api/oc/event").body.includes("limit_req zone=pcbk_oc_event burst=60 nodelay;"));
  // отказ limit_req и limit_conn: код 509 → error_page на уровне server → busy.json → 503 WorkplaceBusy, как у
  // занятости места в core. Не 429: error_page 429 отдаёт «Слишком много попыток входа», и чат Д6 его не повторяет
  for (const path of oc) {
    for (const kind of ["limit_req", "limit_conn"]) {
      const codes = [...loc(path).body.matchAll(new RegExp(`${kind}_status (\\d+);`, "g"))].map((m) => m[1]);
      assert.deepEqual(codes, ["509"], `${path}: ${kind}_status`);
    }
  }
  assert.equal(conf.match(/error_page 509 /g).length, 1);
  assert.ok(conf.includes("error_page 509 =503 /pcbk-errors/busy.json;"));
  assert.match(loc("/pcbk-errors/busy.json").body,
    /return 503 '\{"name":"WorkplaceBusy","data":\{"message":"Слишком много одновременных запросов — повторите позже"\}\}';/);
  const call = loc("/pcbk/api/oc/").body;                                               // срок edge больше хода core (25 с)
  assert.ok(call.includes("proxy_read_timeout 45s;") && call.includes("proxy_max_temp_file_size 8m;"));
  assert.doesNotMatch(call, /proxy_buffering off|proxy_max_temp_file_size 0/);          // ответ core читается без остановки
  const stream = loc("/pcbk/api/oc/event").body;                                        // поток: без буфера, шлюз рвёт первым
  for (const line of ["proxy_buffering off;", "proxy_cache off;", "gzip off;", "proxy_read_timeout 90s;", "send_timeout 60s;"])
    assert.ok(stream.includes(line), line);
  assert.ok(loc("/status").body.includes("error_page 502 503 504 /watchdog-down.html;")); // страница состояния Д1 — своя
});

const CHAT_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; " +
                 "font-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'";   // Д6-R41

test("шаблон edge: чат на 8443 — свой CSP, кэш и сжатие ассетов; опись — только на 8080", () => {
  const conf = read("../pcbk.conf.template");
  const locs = locations(conf);
  const one = (port, mod, path) => {
    const found = locs.filter((l) => l.port === port && l.mod === mod && l.path === path);
    assert.equal(found.length, 1, `${port} ${mod} ${path}`);
    return found[0].body;
  };
  assert.deepEqual([...new Set(locs.map((l) => l.port))], [8443, 8080]);            // разборщик видит оба server
  assert.ok(locs.some((l) => l.mod === "^~"));                                       // и location ^~

  const redirect = one(8443, "=", "/chat");
  assert.match(redirect, /absolute_redirect off;/);                                  // Location относительный (туннель)
  assert.match(redirect, /return 301 \/chat\/;/);

  const page = one(8443, "=", "/chat/");
  const assets = one(8443, "^~", "/chat/assets/");
  const other = one(8443, "^~", "/chat/");                                              // 404 прочего под /chat/
  const boot = one(8443, "=", "/chat/boot.js");                                         // загрузчик Д6б (Д6-R51)
  // CSP R41 и заголовки server — у всех ответов /chat*: и у перенаправления, и у 404 (итоговое ревью Д6а, P3)
  for (const [name, b] of [["/chat", redirect], ["/chat/", page], ["/chat/assets/", assets], ["^~ /chat/", other],
                           ["/chat/boot.js", boot]]) {
    assert.ok(b.includes(`add_header Content-Security-Policy "${CHAT_CSP}" always;`), name);
    assert.ok(b.includes('add_header X-Content-Type-Options "nosniff" always;'), name);   // server-заголовки не наследуются
    assert.ok(b.includes('add_header Referrer-Policy "no-referrer" always;'), name);
    assert.equal(b.match(/add_header Cache-Control /g)?.length, 1, name);
    assert.equal(b.match(/add_header /g)?.length, 4, name);                             // и ничего сверх четырёх
    assert.doesNotMatch(b, /unsafe-|data:|blob:|proxy_pass/, name);
  }
  for (const [name, b] of [["/chat", redirect], ["/chat/", page], ["^~ /chat/", other], ["/chat/boot.js", boot]])
    assert.ok(b.includes('add_header Cache-Control "no-store" always;'), name);
  assert.match(page, /charset utf-8;/);
  assert.match(page, /try_files \/chat\/index\.html =404;/);
  assert.doesNotMatch(page, /gzip/);
  // загрузчик — имя без хеша: no-store своей location, а не год из assets/; в нём русский текст сбоя — charset
  assert.match(boot, /charset utf-8;/);
  assert.match(boot, /try_files \/chat\/boot\.js =404;/);
  assert.doesNotMatch(boot, /gzip|pcbk_asset_cache|immutable/);
  for (const line of ["add_header Cache-Control $pcbk_asset_cache always;", "gzip on;",
                      "gzip_types application/javascript text/javascript text/css;", "gzip_vary on;", "try_files $uri =404;"])
    assert.ok(assets.includes(line), line);
  assert.ok(conf.includes('map $status $pcbk_asset_cache {\n    default "no-store";\n' +       // год — только у файла
                          '    200     "public, max-age=31536000, immutable";\n' +
                          '    304     "public, max-age=31536000, immutable";\n}'));

  assert.match(one(8443, "^~", "/chat/"),                                              // build.json и прочее — не на 8443,
               /types \{ \}\s*default_type "text\/plain; charset=utf-8";\s*return 404 /);  // тип — не по расширению
  assert.match(one(8443, "=", "/favicon.ico"), /return 204;/);                         // решение по А4: без 404 иконки
  assert.equal(locs.filter((l) => l.port === 8443 && l.path.startsWith("/chat")).length, 5);

  // карта CSP /pcbk/api/ (Д5б) /chat/ не накрывает: иначе default-src 'none' второй строкой заголовка
  const api = new RegExp(/map \$uri \$pcbk_api_csp \{\n\s*default "";\n\s*"~([^"]+)"/.exec(conf)[1]);
  assert.ok(api.test("/pcbk/api/oc/session") && api.test("/pcbk-errors/503.json"));   // положительный контроль
  for (const path of ["/chat", "/chat/", "/chat/assets/index-abc.js", "/chat/boot.js", "/chat/build.json", "/favicon.ico"])
    assert.equal(api.test(path), false, path);

  // 8080 (А1, Д6-R46): страница и опись для сторожа — тот же корень, без TLS
  assert.match(one(8080, "=", "/chat/"), /root \/usr\/share\/nginx\/pcbk;\s*try_files \/chat\/index\.html =404;/);
  assert.match(one(8080, "^~", "/chat/"), /root \/usr\/share\/nginx\/pcbk;\s*try_files \$uri =404;/);
});
