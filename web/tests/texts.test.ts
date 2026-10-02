// @vitest-environment node
// Тексты, общие с соседями: предупреждение «учебный стенд» — со страницей входа, MAX_TEXT — с правилами
// шлюза, запасные тексты отказов — с текстами шлюза и edge (веб показывает их, когда в ответе нет своего).
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { CALC_SEARCH_LIMIT, MODE_CHOICES, PERIOD_CHOICES } from "../src/calc";
import { CALC_LABELS, MAX_TEXT, MAX_TITLE, NOTICES, STAND_WARNING, T_BUSY, T_CALC_BUSY, T_CALC_CRASH, T_CALC_FIND, T_CALC_NO_TAG,
         T_CALC_PERIOD_LINE, T_CALC_RESTART, T_CALC_RUN, T_CALC_TITLE, T_DOWN, T_TAB_CALC, T_TAB_CHAT, T_TOO_MANY_TABS,
         TOOL_LABELS } from "../src/texts";

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
  expect(TOOL_LABELS).toEqual({ pcbk_tag_now: "текущее значение", pcbk_tag_period: "сводка за период", pcbk_catalog_search: "поиск тегов",
                               pcbk_save_agent: "сохранение агента",
                               pcbk_tag_series: "ряд и профиль", pcbk_calc_runtime: "наработка" });   // Д9-R88
});

test("calc_texts", () => {                                                        // Д9-R88: тексты вкладки дословно
  expect([T_TAB_CHAT, T_TAB_CALC, T_CALC_TITLE, T_CALC_FIND, T_CALC_RUN, T_CALC_BUSY, T_CALC_NO_TAG, T_CALC_CRASH, T_CALC_RESTART])
    .toEqual(["Чат", "Калькуляторы", "Калькуляторы БДРВ — те же расчёты, что у агентов", "Найти тег", "Посчитать", "Считаю…",
              "Выберите тег — найдите его по словам или впишите имя",
              "Вкладка калькуляторов столкнулась с ошибкой. Сбой отмечен для администратора.", "Перезапустить калькуляторы"]);
  expect(T_CALC_PERIOD_LINE("a", "b", "UTC+05:00")).toBe("Период: a — b (UTC+05:00)");
  expect(MODE_CHOICES.map((c) => c.label)).toEqual(["Текущее значение", "Сводка за период: среднее, минимум, максимум, размах",
                                                    "Профиль: как менялся параметр", "Наработка по тегу состояния"]);
  expect(PERIOD_CHOICES.map((c) => c.label)).toEqual(["Последний час", "Последние 8 часов", "Последние сутки", "Текущая смена",
    "Прошлая смена", "Сегодня", "Вчера", "Последние 7 суток", "Последние 30 суток", "Свой период (время историана)"]);
  expect([CALC_LABELS.query, CALC_LABELS.tag, CALC_LABELS.mode, CALC_LABELS.period, CALC_LABELS.from, CALC_LABELS.to, CALC_LABELS.points])
    .toEqual(["Слова для поиска", "Тег", "Режим", "Период", "С", "По", "Точек на графике"]);
});

test("calc_periods_and_search_limit_same_as_core", () => {
  // ключи периодов вкладки — готовые периоды службы (PRESETS periods.py) и «custom»; предел поиска вкладки — в пределе службы
  const periods = readFileSync("../core/pcbk_core/data/periods.py", "utf8");
  const presets = /^PRESETS = \(([^)]*)\)/m.exec(periods);
  expect(presets).not.toBeNull();
  expect(PERIOD_CHOICES.map((c) => c.key)).toEqual([...presets![1]!.matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]).concat("custom"));
  const limit = /^SEARCH_LIMIT = (\d+)$/m.exec(readFileSync("../core/pcbk_core/data/service.py", "utf8"));
  expect(limit).not.toBeNull();
  expect(CALC_SEARCH_LIMIT).toBeLessThanOrEqual(Number(limit![1]));
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
