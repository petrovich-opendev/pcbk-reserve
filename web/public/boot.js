/*
 * Загрузчик чата (Д6-R51): отчёт boot_failed, если чат не запустился в браузере.
 *
 * Классический скрипт на ES5: без модулей, без стрелок, без fetch (XMLHttpRequest). Его подключает
 * index.html раньше модуля чата; Vite копирует файл из public/ как есть в chat/boot.js, и edge отдаёт
 * его с no-store (имя без хеша). До вызова window.__pcbkStarted() отчёт вызывают:
 *   - событие error на window в фазе захвата: ошибка выполнения (ErrorEvent) или сбой загрузки
 *     скрипта (цель SCRIPT, так ловится и бандл); сбой стилей и картинок чат не роняет и не считается;
 *   - unhandledrejection;
 *   - 20 с без старта, считая от DOMContentLoaded: модуль выполняется до этого события, поэтому
 *     медленная загрузка бандла таймер не запускает.
 * Отчёт один: POST /pcbk/api/web/report с телом {"kind":"boot_failed"} и текст в #root. Ответ не
 * читается и не повторяется. После старта загрузчик ничего не делает: сбои React ловит ErrorBoundary.
 *
 * __pcbkBoot(win, doc, xhrFactory, setTimeoutImpl) — фабрика для тестов под Node (web/tests/boot.test.mjs);
 * последней строкой файл зовёт её сам с настоящими window, document, XMLHttpRequest и setTimeout.
 */
function __pcbkBoot(win, doc, xhrFactory, setTimeoutImpl) {
  "use strict";
  var BOOT_TIMEOUT_MS = 20000;
  var REPORT_URL = "/pcbk/api/web/report";
  var REPORT_BODY = "{\"kind\":\"boot_failed\"}";
  var TEXT = "Чат не запустился в этом браузере. Сообщите преподавателю — сбой отмечен.";
  var started = false;
  var reported = false;
  var armed = false;

  function report() {
    if (started || reported) return;
    reported = true;
    try {
      var xhr = xhrFactory();
      xhr.open("POST", REPORT_URL, true);
      xhr.setRequestHeader("Content-Type", "application/json");
      xhr.send(REPORT_BODY);
    } catch (e) {
      /* отчёт не ушёл — второй попытки нет */
    }
    try {
      var root = doc.getElementById("root");
      if (root) root.textContent = TEXT;
    } catch (e) {
      /* без #root — только отчёт */
    }
  }

  function counts(e) {
    var target = e && e.target;
    if (target && target !== win && typeof target.tagName === "string") {
      return target.tagName.toUpperCase() === "SCRIPT";
    }
    return typeof win.ErrorEvent === "function" && e instanceof win.ErrorEvent;
  }

  function arm() {
    if (armed) return;
    armed = true;
    setTimeoutImpl(report, BOOT_TIMEOUT_MS);
  }

  win.addEventListener("error", function (e) {
    if (!started && counts(e)) report();
  }, true);
  win.addEventListener("unhandledrejection", function () {
    if (!started) report();
  });
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", arm);
  else arm();

  win.__pcbkStarted = function () {
    started = true;
  };
}

__pcbkBoot(window, document, function () {
  return new XMLHttpRequest();
}, function (fn, ms) {
  return window.setTimeout(fn, ms);
});
