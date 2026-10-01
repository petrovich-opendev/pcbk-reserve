// Загрузчик чата web/public/boot.js (Д6-R51, Review Focus 3) под node --test: окно, документ, XMLHttpRequest и
// таймер подменены. Файл исполняется как классический скрипт в своём контексте vm и сам зовёт __pcbkBoot с
// window, document, XMLHttpRequest и window.setTimeout контекста — теми же путями, что в браузере.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const SRC = readFileSync(new URL("../public/boot.js", import.meta.url), "utf8");
// текст сбоя — точной строкой (preflight Н20): импортов у загрузчика нет, сверить с texts.ts нечем
const TEXT = "Чат не запустился в этом браузере. Сообщите преподавателю — сбой отмечен.";
const REPORT = {
  method: "POST", url: "/pcbk/api/web/report", async: true,
  headers: { "Content-Type": "application/json" }, body: '{"kind":"boot_failed"}',
};

class FakeErrorEvent {
  constructor(type) { this.type = type; }
}

/** Стенд загрузчика: readyState — состояние документа, когда скрипт исполняется (в <head> — "loading"). */
function stand(readyState = "loading") {
  let now = 0;
  const timers = [];
  const winListeners = [];
  const docListeners = [];
  const root = { textContent: "" };
  const sent = [];
  class XMLHttpRequest {
    constructor() { this.headers = {}; }
    open(method, url, async) { Object.assign(this, { method, url, async }); }
    setRequestHeader(name, value) { this.headers[name] = value; }
    send(body) { sent.push({ method: this.method, url: this.url, async: this.async, headers: this.headers, body }); }
  }
  const window = {
    ErrorEvent: FakeErrorEvent,
    addEventListener(type, fn, capture) { winListeners.push({ type, fn, capture: capture === true }); },
    setTimeout(fn, ms) { timers.push({ at: now + ms, fn }); return timers.length; },
  };
  const document = {
    readyState,
    addEventListener(type, fn) { docListeners.push({ type, fn }); },
    getElementById(id) { return id === "root" ? root : null; },
  };
  vm.runInNewContext(SRC, { window, document, XMLHttpRequest }, { filename: "boot.js" });
  const fire = (type, event, captureOnly = false) => {
    for (const l of winListeners) if (l.type === type && (l.capture || !captureOnly)) l.fn(event);
  };
  return {
    window, root, sent,
    domContentLoaded() {
      document.readyState = "interactive";
      for (const l of docListeners) if (l.type === "DOMContentLoaded") l.fn({ type: "DOMContentLoaded" });
    },
    advance(ms) {
      now += ms;
      for (const t of timers.splice(0)) (t.at <= now ? t.fn : () => timers.push(t))();
    },
    // ошибка выполнения: ErrorEvent на самом window
    runtimeError() { const e = new FakeErrorEvent("error"); e.target = window; fire("error", e); },
    // сбой загрузки ресурса: простое событие на элементе, не всплывает — до window доходит только в фазе захвата
    resourceError(tagName) { fire("error", { type: "error", target: { tagName } }, true); },
    rejection() { fire("unhandledrejection", { type: "unhandledrejection", reason: new Error("x") }); },
  };
}

test("boot_reports_once_on_timeout", () => {
  const s = stand();
  s.domContentLoaded();
  s.advance(19999);
  assert.deepEqual(s.sent, []);                                          // граница: 20 с ещё не прошли
  s.advance(1);
  assert.deepEqual(s.sent, [REPORT]);
  assert.equal(s.root.textContent, TEXT);
  s.advance(60000); s.runtimeError(); s.rejection(); s.resourceError("SCRIPT");
  assert.equal(s.sent.length, 1);                                       // отчёт один
});

test("boot_timer_starts_at_dom_content_loaded", () => {
  const s = stand();
  s.advance(60000);                                                      // медленный бандл: DOMContentLoaded ещё не было
  assert.deepEqual(s.sent, []);
  s.domContentLoaded();
  s.window.__pcbkStarted();                                              // модуль выполнился до события — старт сразу
  s.advance(60000);
  assert.deepEqual(s.sent, []);
  assert.equal(s.root.textContent, "");
  const late = stand("interactive");                                     // документ уже разобран — срок идёт сразу
  late.advance(20000);
  assert.deepEqual(late.sent, [REPORT]);
});

test("boot_catches_bundle_load_error", () => {
  const s = stand();
  s.resourceError("SCRIPT");                                             // бандл не загрузился (фаза захвата)
  assert.deepEqual(s.sent, [REPORT]);
  assert.equal(s.root.textContent, TEXT);
  const styles = stand();                                                // Н11: стили и картинки чат не роняют
  styles.resourceError("LINK"); styles.resourceError("IMG");
  assert.deepEqual(styles.sent, []);
});

test("boot_reports_once_on_error_before_start", () => {
  const s = stand();
  s.domContentLoaded();
  s.runtimeError();
  assert.deepEqual(s.sent, [REPORT]);
  s.runtimeError(); s.resourceError("SCRIPT"); s.rejection(); s.advance(20000);
  assert.equal(s.sent.length, 1);
  const rejected = stand();                                              // unhandledrejection до старта — тоже отчёт
  rejected.rejection(); rejected.rejection();
  assert.deepEqual(rejected.sent, [REPORT]);
});

test("boot_silent_after_start", () => {
  const s = stand();
  s.domContentLoaded();
  s.window.__pcbkStarted();
  s.runtimeError(); s.resourceError("SCRIPT"); s.rejection(); s.advance(20000);
  assert.deepEqual(s.sent, []);
  assert.equal(s.root.textContent, "");
});

test("boot_is_es5", () => {
  for (const bad of ["=>", "let ", "const ", "class ", "`", "fetch(", "async ", "await "])
    assert.ok(!SRC.includes(bad), bad);
  assert.doesNotMatch(SRC, /^\s*(?:import|export)\b/m);
  assert.doesNotThrow(() => new vm.Script(SRC, { filename: "boot.js" }));  // классический скрипт разбирается
});
