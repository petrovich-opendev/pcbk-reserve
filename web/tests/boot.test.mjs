// Загрузчик чата web/public/boot.js (Д6-R51, Review Focus 3) под node --test: окно, документ, XMLHttpRequest и
// таймер подменены. Файл исполняется как классический скрипт в своём контексте vm и сам зовёт __pcbkBoot с
// window, document, XMLHttpRequest и window.setTimeout контекста — теми же путями, что в браузере.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

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

// ES5 — по дереву разбора TypeScript (devDependencies), а не подстроками (итоговая доводка Д6б, Б1-m1): белый список
// видов узлов ES5, у бинарного выражения — белый список операторов, плюс флаги и тексты, которых видом узла не видно
// (let/const, параметры по умолчанию, catch без переменной, флаги регулярки, 0b/0o/«_» в числах, \u{…}, запятая
// после последнего аргумента). Токены, которые обходит forEachChild (?. , ..., *, async), в белом списке нет.
// Возвращает нарушения «вид (строка N)».
const S = ts.SyntaxKind;
const ES5_NODES = new Set([
  S.SourceFile, S.EndOfFileToken, S.FunctionDeclaration, S.FunctionExpression, S.Parameter, S.Block,
  S.VariableStatement, S.VariableDeclarationList, S.VariableDeclaration, S.ExpressionStatement, S.IfStatement,
  S.ReturnStatement, S.TryStatement, S.CatchClause, S.ThrowStatement, S.ForStatement, S.ForInStatement,
  S.WhileStatement, S.DoStatement, S.BreakStatement, S.ContinueStatement, S.SwitchStatement, S.CaseBlock,
  S.CaseClause, S.DefaultClause, S.LabeledStatement, S.EmptyStatement, S.DebuggerStatement,
  S.Identifier, S.StringLiteral, S.NumericLiteral, S.RegularExpressionLiteral,
  S.TrueKeyword, S.FalseKeyword, S.NullKeyword, S.ThisKeyword,
  S.CallExpression, S.NewExpression, S.PropertyAccessExpression, S.ElementAccessExpression, S.BinaryExpression,
  S.PrefixUnaryExpression, S.PostfixUnaryExpression, S.ConditionalExpression, S.QuestionToken, S.ColonToken,
  S.ParenthesizedExpression, S.TypeOfExpression, S.DeleteExpression, S.VoidExpression,
  S.ObjectLiteralExpression, S.PropertyAssignment, S.GetAccessor, S.SetAccessor, S.ArrayLiteralExpression,
  S.OmittedExpression,
]);
const ES5_OPERATORS = new Set([
  S.PlusToken, S.MinusToken, S.AsteriskToken, S.SlashToken, S.PercentToken, S.LessThanLessThanToken,
  S.GreaterThanGreaterThanToken, S.GreaterThanGreaterThanGreaterThanToken, S.LessThanToken, S.GreaterThanToken,
  S.LessThanEqualsToken, S.GreaterThanEqualsToken, S.EqualsEqualsToken, S.ExclamationEqualsToken,
  S.EqualsEqualsEqualsToken, S.ExclamationEqualsEqualsToken, S.AmpersandToken, S.BarToken, S.CaretToken,
  S.AmpersandAmpersandToken, S.BarBarToken, S.EqualsToken, S.PlusEqualsToken, S.MinusEqualsToken,
  S.AsteriskEqualsToken, S.SlashEqualsToken, S.PercentEqualsToken, S.LessThanLessThanEqualsToken,
  S.GreaterThanGreaterThanEqualsToken, S.GreaterThanGreaterThanGreaterThanEqualsToken, S.AmpersandEqualsToken,
  S.BarEqualsToken, S.CaretEqualsToken, S.CommaToken, S.InKeyword, S.InstanceOfKeyword,
]);
const BLOCK_SCOPED = ts.NodeFlags.Let | ts.NodeFlags.Const | (ts.NodeFlags.Using ?? 0);

function es5Violations(source) {
  const sf = ts.createSourceFile("boot.js", source, ts.ScriptTarget.ES5, true, ts.ScriptKind.JS);
  const out = [];
  const bad = (node, what) =>
    out.push(`${what ?? S[node.kind]} (строка ${sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1})`);
  for (const d of sf.parseDiagnostics ?? []) out.push(`разбор: ${ts.flattenDiagnosticMessageText(d.messageText, " ")}`);
  const visit = (node) => {
    if (!ES5_NODES.has(node.kind)) {
      bad(node);
      return;
    }
    const text = node.getText(sf);
    switch (node.kind) {
      case S.BinaryExpression:
        if (!ES5_OPERATORS.has(node.operatorToken.kind)) bad(node.operatorToken);
        visit(node.left);
        visit(node.right);
        return;
      case S.VariableDeclarationList:
        if (node.flags & BLOCK_SCOPED) bad(node, "let/const");
        break;
      case S.Parameter:
        if (node.initializer) bad(node, "параметр по умолчанию");
        break;
      case S.CatchClause:
        if (!node.variableDeclaration) bad(node, "catch без переменной");
        break;
      case S.RegularExpressionLiteral:
        if (!/^[gim]*$/.test(text.slice(text.lastIndexOf("/") + 1))) bad(node, "флаг регулярки новее ES5");
        break;
      case S.NumericLiteral:
        if (/^0[bBoO]|_/.test(text)) bad(node, "число 0b/0o/с «_»");
        break;
      case S.StringLiteral:
        if (/\\u\{/.test(text)) bad(node, "\\u{…}");
        break;
      case S.CallExpression:
      case S.NewExpression:
        if (node.arguments?.hasTrailingComma) bad(node, "запятая после последнего аргумента");
        break;
      case S.FunctionDeclaration:
      case S.FunctionExpression:
        if (node.parameters.hasTrailingComma) bad(node, "запятая после последнего параметра");
        break;
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

test("boot_is_es5", () => {
  assert.deepEqual(es5Violations(SRC), []);
  assert.doesNotMatch(SRC, /\bfetch\(/);                                // XMLHttpRequest: fetch нет в Chrome без него
  assert.doesNotThrow(() => new vm.Script(SRC, { filename: "boot.js" }));  // классический скрипт разбирается
});

test("es5_check_catches_newer_syntax", () => {
  // положительный контроль разбора: каждая конструкция новее ES5 — нарушение, а ES5 рядом с ней — нет
  for (const bad of ["let a = 1;", "const a = 1;", "var f = () => 1;", "class A {}", "var s = `x`;", "var s = `${a}`;",
                     "a?.b;", "a?.[0];", "f?.();", "f(...a);", "var o = {...a};", "var c = a ?? b;", "var p = a ** 2;",
                     "a **= 2;", "a ||= b;", "a ??= b;", "for (var x of y) {}", "async function f() {}",
                     "function* g() {}", "function f(a = 1) {}", "function f(...a) {}", "var {a} = o;", "var [a] = o;",
                     "try {} catch {}", "var o = {a};", "var o = {[k]: 1};", "var o = {m() {}};", "var r = /x/u;",
                     "var r = /x/y;", "var r = /x/s;", "var n = 0b1;", "var n = 0o7;", "var n = 1_000;", "var n = 1n;",
                     "var s = '\\u{1F600}';", "f(a, b,);", "function f(a, b,) {}", "import('x');", "tag`x`;",
                     "export var a = 1;", "import a from 'a';", "label: for (let i = 0; ; ) {}"])
    assert.notDeepEqual(es5Violations(bad), [], bad);
  for (const good of ["var a = 1, b;", "function f(a, b) { 'use strict'; return a ? b : !a; }",
                      "var o = {a: 1, 'b': 2, 3: 4, get x() { return 1; }, set x(v) {}};", "var r = /x[/]y/gim;",
                      "try { f(); } catch (e) { g(e); } finally { h(); }", "for (var k in o) { if (k) continue; }",
                      "var x = typeof a === 'string' && a instanceof B || 'k' in o;", "a = [1, , 2,];",
                      "var n = 0x1F + 1e3 + .5;", "switch (a) { case 1: break; default: }", "do { i++; } while (--i);",
                      "var s = '\\u0041';", "new X;", "delete o.a; void 0;", "out: for (;;) { break out; }"])
    assert.deepEqual(es5Violations(good), [], good);
});
