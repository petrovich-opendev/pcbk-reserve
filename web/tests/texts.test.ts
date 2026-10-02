// @vitest-environment node
// Тексты, общие с соседями: предупреждение «учебный стенд» — со страницей входа, MAX_TEXT — с правилами
// шлюза, запасные тексты отказов — с текстами шлюза и edge (веб показывает их, когда в ответе нет своего).
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { MAX_TEXT, MAX_TITLE, NOTICES, STAND_WARNING, T_BUSY, T_DOWN, T_TOO_MANY_TABS, TOOL_LABELS } from "../src/texts";

test("stand_warning_same_as_login_page", () => {
  const html = readFileSync("../edge/static/login.html", "utf8");
  expect(html).toContain(`<div class="stand" role="note">${STAND_WARNING}</div>`);
});

test("max_text_same_as_gateway_rules", () => {
  expect(readFileSync("../core/pcbk_core/gateway/rules.py", "utf8")).toMatch(new RegExp(`^MAX_TEXT = ${MAX_TEXT}$`, "m"));
});

test("fallback_texts_same_as_gateway", () => {
  const proxy = readFileSync("../core/pcbk_core/gateway/proxy.py", "utf8");
  expect(proxy).toContain(`"streams": "${T_TOO_MANY_TABS}"`);
  expect(proxy).toContain(`"busy": "${T_BUSY}"`);
  const edge = readFileSync("../edge/pcbk.conf.template", "utf8");
  expect(edge).toContain(`{"name":"Unavailable","data":{"message":"${T_DOWN}"}}`);
  expect(edge).toContain(`{"name":"WorkplaceBusy","data":{"message":"${T_BUSY}"}}`);
});

test("tool_labels", () => {
  expect(TOOL_LABELS).toEqual({ pcbk_tag_now: "текущее значение", pcbk_tag_period: "сводка за период", pcbk_catalog_search: "поиск тегов" });
});

test("notice_number_same_as_core", () => {                                        // preflight Ф14: номер — в core, текст — здесь
  const m = /^NOTICE = "([a-z0-9-]+)"$/m.exec(readFileSync("../core/pcbk_core/gateway/accounts.py", "utf8"));
  expect(m).not.toBeNull();
  expect([...NOTICES.keys()]).toEqual([m![1]]);                                  // текст есть ровно у номера core
  // Текст редакции закреплён: новый текст — новый номер (и в accounts.py), иначе студент «подтвердит» то, чего не видел
  expect(NOTICES.get("teacher-reads-1")).toBe(
    "Преподаватель видит ваши разговоры и агентов на этом стенде (только чтение). Не пишите сюда пароли и личные данные.");
});

test("max_title_same_as_gateway_rules", () => {                                   // preflight Н20: имя разговора 1–200 знаков
  expect(readFileSync("../core/pcbk_core/gateway/rules.py", "utf8")).toMatch(new RegExp(`^MAX_TITLE = ${MAX_TITLE}$`, "m"));
});
