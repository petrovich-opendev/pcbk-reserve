// @vitest-environment node
// Тексты, общие с соседями: предупреждение «учебный стенд» — со страницей входа, MAX_TEXT — с правилами
// шлюза, запасные тексты отказов — с текстами шлюза и edge (веб показывает их, когда в ответе нет своего).
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { CALC_QUERY_CHARS, CALC_SEARCH_LIMIT, CALC_TAG_CHARS, DEFAULT_FORM, MODE_CHOICES, PERIOD_CHOICES, POINT_CHOICES,
         SIMILAR_NOTE_CORE } from "../src/calc";
import * as texts from "../src/texts";
import { CALC_LABELS, EVENT_KIND_LABELS, EVENTS_COLUMNS, MAX_TEXT, MAX_TITLE, NOTICES, STAND_WARNING, T_BUSY,
         T_CALC_BAD_RANGE, T_CALC_BUSY, T_CALC_CRASH, T_CALC_FIND, T_CALC_NO_TAG, T_CALC_PERIOD_LINE, T_CALC_RESTART,
         T_CALC_RUN, T_CALC_SIMILAR, T_CALC_TITLE, T_DOWN, T_EVENTS_CRASH, T_EVENTS_EMPTY, T_EVENTS_RESTART,
         T_EVENTS_TITLE, T_TAB_CALC, T_TAB_CHAT, T_TAB_EVENTS, T_TCH_CRASH, T_TCH_READ_ONLY, T_TCH_RESTART,
         T_TCH_SHARES_DONE, T_TCH_TITLE, T_TCH_WAKE, T_TOO_MANY_TABS, TOOL_LABELS } from "../src/texts";

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
                               pcbk_tag_series: "ряд и профиль", pcbk_calc_runtime: "наработка",
                               pcbk_my_agents: "мои агенты", pcbk_report_event: "запись события",
                               pcbk_calc_spc: "карта Шухарта" });                                     // Д9-R88, Д13, Д24
});

// --- Д13: вкладка «События» — тексты дословно ---

test("events_texts", () => {
  expect([T_TAB_CHAT, T_TAB_CALC, T_TAB_EVENTS, T_EVENTS_TITLE, T_EVENTS_CRASH, T_EVENTS_RESTART])
    .toEqual(["Чат", "Калькуляторы", "События", "События агентов",
              "Вкладка событий столкнулась с ошибкой. Сбой отмечен для администратора.", "Перезапустить события"]);
  expect(T_EVENTS_EMPTY).toBe(
    "Событий пока нет. Их записывают агенты контроля, например при проверке контрольной точки за смену.");
  expect(EVENT_KIND_LABELS).toEqual({ deviation: "Отклонение от задания", note: "Заметка" });
  expect(Object.values(EVENTS_COLUMNS)).toEqual(
    ["Время", "Агент", "Вид", "Заголовок", "Теги", "Период", "Значение и задание", "Текст"]);
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

// --- итоговая доводка Д9а: копии пределов и текста службы во вкладке — сверены (ревью А5, m-3, m-5) ---

test("calc_limits_and_similar_note_same_as_core", () => {
  const service = readFileSync("../core/pcbk_core/data/service.py", "utf8");
  const sql = readFileSync("../core/pcbk_core/data/sql.py", "utf8");
  const num = (src: string, name: string): number => {
    const m = new RegExp(`^${name} = (\\d+)\\b`, "m").exec(src);
    expect(m, name).not.toBeNull();
    return Number(m![1]);
  };
  expect([CALC_QUERY_CHARS, CALC_TAG_CHARS]).toEqual([num(service, "QUERY_CHARS"), num(service, "TAG_CHARS")]);
  const [low, high] = [num(sql, "SERIES_MIN_POINTS"), num(sql, "SERIES_MAX_POINTS")];
  expect(POINT_CHOICES.every((n) => Number.isInteger(n) && n >= low && n <= high)).toBe(true);
  expect(DEFAULT_FORM.points).toBe(num(sql, "SERIES_DEFAULT_POINTS"));
  expect(POINT_CHOICES).toContain(DEFAULT_FORM.points);
  const similar = /^SIMILAR_NOTE = "([^"]+)"$/m.exec(service);
  expect(similar).not.toBeNull();
  expect(SIMILAR_NOTE_CORE).toBe(similar![1]);
  expect([T_CALC_SIMILAR, T_CALC_BAD_RANGE, CALC_LABELS.pointsPerTag])
    .toEqual(["Точного совпадения нет — вот похожие теги", "Конец своего периода — позже начала", "Точек на тег"]);
});

// --- Д10а: кабинет преподавателя (Д10-R106) ---

test("teacher_texts", () => {
  // заглушка Д6 «У преподавателя пока нет чата…» снята: у преподавателя — кабинет
  expect(Object.hasOwn(texts, "T_TEACHER")).toBe(false);
  expect([T_TCH_TITLE, T_TCH_READ_ONLY, T_TCH_WAKE, T_TCH_SHARES_DONE, T_TCH_CRASH, T_TCH_RESTART])
    .toEqual(["Кабинет преподавателя", "Только чтение: писать в разговор студента нельзя", "Разбудить для просмотра",
              "Доли обновлены", "Кабинет преподавателя столкнулся с ошибкой. Сбой отмечен для администратора.",
              "Перезапустить кабинет"]);
  // подписи действий журнала — ровно ACTIONS журнала действий: Д10а (Д10-R99) и учётки Д10б (R108)
  expect(Object.keys(texts.ACTION_LABELS).sort()).toEqual(["account_add", "account_disable", "account_enable",
    "account_passwd", "account_unlock", "agent", "agents", "read", "sessions", "shares", "wake"]);
  expect([texts.ACTION_LABELS.sessions, texts.ACTION_LABELS.read, texts.ACTION_LABELS.wake, texts.ACTION_LABELS.shares])
    .toEqual(["открыл список разговоров", "прочитал разговор", "разбудил место", "перевёл долю"]);
  // действия учёток — словами, как остальные, а не кодом (сомнение 1 Б2, решение контроллера)
  expect([texts.ACTION_LABELS.account_add, texts.ACTION_LABELS.account_passwd, texts.ACTION_LABELS.account_disable,
          texts.ACTION_LABELS.account_enable, texts.ACTION_LABELS.account_unlock])
    .toEqual(["завёл учётку студента", "выдал новый пароль", "отключил учётку", "включил учётку", "снял замок входа"]);
  for (const label of Object.values(texts.ACTION_LABELS)) expect(label).not.toMatch(/[a-z_]/);   // ни одного кода
});
