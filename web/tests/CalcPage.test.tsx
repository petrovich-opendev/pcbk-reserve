// Вкладка «Калькуляторы» (Д9-R88): ничего не спрашивает при открытии, поиск тега, расчёт по режимам, отказы и конец
// сессии. calcApi: ответы по инструментам — один ответ на каждый вызов или список по очереди (как api() в App.test.tsx);
// прочие ручки Api — vi.fn(), которые тест не зовёт.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { Api, CalcTool, Reply } from "../src/api";
import { profileSegments } from "../src/calc";
import { CalcPage } from "../src/components/CalcPage";
import { SIMILAR_NOTE_CORE } from "../src/calc";
import { T_CALC_BAD_RANGE, T_CALC_BUSY, T_CALC_NO_RANGE, T_CALC_NO_TAG, T_CALC_SIMILAR, T_DOWN } from "../src/texts";

type Answer = [number, unknown];
function calcApi(o: Partial<Record<CalcTool, Answer | Answer[]>>) {
  const left = new Map(Object.entries(o).map(([k, v]) => [k, typeof v![0] === "number" ? [v as Answer] : (v as Answer[])]));
  const calc = vi.fn(async (tool: CalcTool, _args: Record<string, unknown>) => {
    const list = left.get(tool) ?? [[500, null] as Answer];
    const [status, body] = list.length > 1 ? list.shift()! : list[0]!;
    return { status, body };
  });
  return { me: vi.fn(), logout: vi.fn(), wake: vi.fn(), workplace: vi.fn(), ackNotice: vi.fn(), agents: vi.fn(),
           report: vi.fn(async () => {}), calc } as unknown as Api & { calc: typeof calc };
}

const PERIOD_OK = { tool: "tag_period", status: "ok", notes: ["границы смен 06:00, 14:00, 22:00 — допущение, технологом не подтверждено"],
  period: { start: "2026-09-30T22:00:00+05:00", end: "2026-10-01T06:00:00+05:00", tz: "UTC+05:00", preset: "prev_shift" },
  source: "сводка историана по сырым данным: AnalogSummaryHistory, одна корзина на весь период",
  avg_kind: { code: "unverified", text: "вид не подтверждён сверкой" },
  items: [{ tag: "20FAKE_001_PV", status: "ok", unit: "л/час", avg: 12.4, min: 10, max: 15, range: 5, percent_good: 100 }] };

test("calc_page_no_calls_on_open", () => {
  const api = calcApi({});
  render(<CalcPage api={api} onSignedOut={vi.fn()} />);
  expect(api.calc).not.toHaveBeenCalled();
});

test("calc_page_search_and_pick", async () => {
  const api = calcApi({ catalog_search: [200, { status: "ok", matches: [{ tag: "20FAKE_001_PV", description: "Факт. знач. - расход массы", unit: "л/час", kind: "analog" }] }] });
  render(<CalcPage api={api} onSignedOut={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Слова для поиска"), { target: { value: "расход" } });
  fireEvent.click(screen.getByRole("button", { name: "Найти тег" }));
  fireEvent.click(await screen.findByRole("button", { name: /20FAKE_001_PV — Факт\. знач\. - расход массы — л\/час/ }));
  expect((screen.getByLabelText("Тег") as HTMLInputElement).value).toBe("20FAKE_001_PV");
  expect(api.calc).toHaveBeenCalledWith("catalog_search", { query: "расход", limit: 20 });
});

test("calc_page_period_result", async () => {
  const api = calcApi({ tag_period: [200, PERIOD_OK] });
  render(<CalcPage api={api} onSignedOut={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Тег"), { target: { value: "20FAKE_001_PV" } });
  fireEvent.click(screen.getByRole("button", { name: "Посчитать" }));
  expect(await screen.findByText("12,4 л/час")).toBeTruthy();
  for (const t of ["Среднее", "Минимум", "Максимум", "Размах", PERIOD_OK.notes[0], PERIOD_OK.source, "вид не подтверждён сверкой"])
    expect(screen.getByText(t)).toBeTruthy();
  expect(screen.getByText(/Период: .* — .* \(UTC\+05:00\)/)).toBeTruthy();
  expect(api.calc).toHaveBeenCalledWith("tag_period", { tags: ["20FAKE_001_PV"], period: "prev_shift" });
});

const SERIES_OK = { tool: "tag_series", status: "ok", notes: [], points: 4,
  period: { start: "2026-10-01T04:00:00+05:00", end: "2026-10-01T12:00:00+05:00", tz: "UTC+05:00", preset: "last_8h" },
  items: [{ tag: "20FAKE_001_PV", status: "ok", unit: "л/час",
            points: [["2026-10-01T04:00:00+05:00", 10], ["2026-10-01T06:40:00+05:00", 15],
                     ["2026-10-01T09:20:00+05:00", null], ["2026-10-01T12:00:00+05:00", 12.5]],
            profile: { first: 10, first_time: "2026-10-01T04:00:00+05:00", last: 12.5, last_time: "2026-10-01T12:00:00+05:00",
                       change: 2.5, min: 10, min_time: "2026-10-01T04:00:00+05:00", max: 15, max_time: "2026-10-01T06:40:00+05:00" } }] };
const RUNTIME_OK = { tool: "calc_runtime", status: "ok", notes: [],
  period: { start: "2026-09-30T00:00:00+05:00", end: "2026-10-01T00:00:00+05:00", tz: "UTC+05:00", preset: "yesterday" },
  items: [{ tag: "25FAKE_007_CLS", status: "ok", covered_percent: 100, period_hours: 24,
            states: [{ value: 1, label: "закрыт (по суффиксу)", hours: 18, percent: 75, count: 3 },
                     { value: 0, label: "не закрыт (по суффиксу)", hours: 6, percent: 25, count: 3 }] }] };

async function run(api: ReturnType<typeof calcApi>, mode: string, tag: string) {
  render(<CalcPage api={api} onSignedOut={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Режим"), { target: { value: mode } });
  fireEvent.change(screen.getByLabelText("Тег"), { target: { value: tag } });
  fireEvent.click(screen.getByRole("button", { name: "Посчитать" }));
}

test("calc_page_profile_chart", async () => {
  const api = calcApi({ tag_series: [200, SERIES_OK] });
  await run(api, "profile", "20FAKE_001_PV");
  await screen.findByText("В начале");
  const lines = [...document.querySelectorAll("svg.profile polyline")].map((p) => p.getAttribute("points"));
  expect(lines).toEqual(profileSegments([10, 15, null, 12.5], 600, 160).filter((s) => s.includes(" ")));
  for (const t of ["10 л/час", "12,5 л/час", "2,5 л/час"]) expect(screen.getAllByText(t).length).toBeGreaterThan(0);
  expect(document.querySelectorAll("svg.profile circle").length).toBe(1);          // отрезок из одной точки — кружок
  expect(api.calc).toHaveBeenCalledWith("tag_series", { tags: ["20FAKE_001_PV"], period: "prev_shift", points: 24 });
});

test("calc_page_runtime_table", async () => {
  const api = calcApi({ calc_runtime: [200, RUNTIME_OK] });
  await run(api, "runtime", "25FAKE_007_CLS");
  const row = (await screen.findByText("закрыт (по суффиксу)")).closest("tr")!;
  expect([...row.querySelectorAll("td")].map((td) => td.textContent)).toEqual(["закрыт (по суффиксу)", "18", "75", "3"]);
  expect(screen.getByText("Данные покрывают 100 % периода")).toBeTruthy();
});

test("calc_page_refusal_and_signed_out", async () => {                            // Review Focus 4
  const onSignedOut = vi.fn();
  const api = calcApi({ tag_now: [[200, { status: "refused", message: "неверные аргументы: tags: нужен хотя бы один тег" }],
                                  [503, { name: "Unavailable", data: { message: "Калькуляторы недоступны — служба данных не подключена" } }],
                                  [401, { name: "Unauthorized" }]] });
  render(<CalcPage api={api} onSignedOut={onSignedOut} />);
  fireEvent.change(screen.getByLabelText("Режим"), { target: { value: "now" } });
  fireEvent.change(screen.getByLabelText("Тег"), { target: { value: "20FAKE_001_PV" } });
  for (const want of ["неверные аргументы: tags: нужен хотя бы один тег", "Калькуляторы недоступны — служба данных не подключена"]) {
    fireEvent.click(screen.getByRole("button", { name: "Посчитать" }));
    expect(await screen.findByText(want)).toBeTruthy();
  }
  fireEvent.click(screen.getByRole("button", { name: "Посчитать" }));
  await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
});

// --- сверх брифа: пустая форма, свой период, кнопка в пути, текущее значение и частичный ответ, CSP ---

test("calc_page_needs_tag_and_range", async () => {
  const api = calcApi({ tag_period: [200, PERIOD_OK] });
  render(<CalcPage api={api} onSignedOut={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Посчитать" }));
  expect(screen.getByText(T_CALC_NO_TAG)).toBeTruthy();
  expect(screen.queryByLabelText("С")).toBeNull();                                  // поля своего периода — только у «свой»
  fireEvent.change(screen.getByLabelText("Тег"), { target: { value: "20FAKE_001_PV" } });
  fireEvent.change(screen.getByLabelText("Период"), { target: { value: "custom" } });
  fireEvent.change(screen.getByLabelText("С"), { target: { value: "2026-10-01T08:00" } });
  fireEvent.click(screen.getByRole("button", { name: "Посчитать" }));
  expect(screen.getByText(T_CALC_NO_RANGE)).toBeTruthy();
  expect(api.calc).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("По"), { target: { value: "2026-10-01T12:00" } });
  fireEvent.click(screen.getByRole("button", { name: "Посчитать" }));
  await screen.findByText("12,4 л/час");
  expect(api.calc).toHaveBeenCalledWith("tag_period", { tags: ["20FAKE_001_PV"], start: "2026-10-01T08:00", end: "2026-10-01T12:00" });
});

test("calc_page_button_busy_while_in_flight", async () => {
  const api = calcApi({});
  let answer: (r: Reply) => void = () => {};
  api.calc.mockImplementation(() => new Promise<Reply>((resolve) => { answer = resolve; }));
  render(<CalcPage api={api} onSignedOut={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Тег"), { target: { value: "20FAKE_001_PV" } });
  fireEvent.click(screen.getByRole("button", { name: "Посчитать" }));
  const busy = await screen.findByRole("button", { name: T_CALC_BUSY }) as HTMLButtonElement;
  expect(busy.disabled).toBe(true);
  fireEvent.click(busy);
  expect(api.calc).toHaveBeenCalledTimes(1);                                       // второй вызов не ушёл
  answer({ status: 0, body: null });
  expect(await screen.findByText(T_DOWN)).toBeTruthy();                            // сеть — текст replyText
  expect((screen.getByRole("button", { name: "Посчитать" }) as HTMLButtonElement).disabled).toBe(false);
});

test("calc_page_now_value_and_partial", async () => {
  const api = calcApi({ tag_now: [200, { tool: "tag_now", status: "partial", notes: [], asof: "2026-10-01T12:00:00+05:00",
    tz: "UTC+05:00", source: "текущее значение из таблицы Live историана",
    items: [{ tag: "20FAKE_001_PV", status: "ok", description: "Факт. знач. - расход массы", unit: "л/час", kind: "analog",
              value: 1250.4567, time: "2026-10-01T11:59:40+05:00", age_s: 20, quality: "хорошее",
              item_notes: ["метка тега впереди часов историана на 61 с"] },
            { tag: "20FAKE_999_PV", status: "unknown", text: "такого тега нет в каталоге историана" }] }] });
  await run(api, "now", "20FAKE_001_PV");
  expect(await screen.findByText("1 250,457 л/час")).toBeTruthy();
  for (const t of ["Значение", "Время замера", "Возраст", "Качество", "01.10.2026 11:59:40", "20 с", "хорошее",
                   "метка тега впереди часов историана на 61 с", "такого тега нет в каталоге историана",
                   "текущее значение из таблицы Live историана"])
    expect(screen.getByText(t)).toBeTruthy();
  expect(screen.queryByText(/^Период:/)).toBeNull();                                // у текущего значения периода нет
});

test("calc_page_no_style_attributes", async () => {                              // CSP style-src 'self' (Д6-R41)
  const api = calcApi({ tag_series: [200, SERIES_OK] });
  await run(api, "profile", "20FAKE_001_PV");
  await screen.findByText("В начале");
  expect(document.querySelector("section.calc-result")).not.toBeNull();
  expect(document.querySelectorAll("[style]").length).toBe(0);
});

// --- доводка р1 (ревью А5): нет значения — «—», не ноль; заметки элемента; отказ и 401 поиска тега ---

test("calc_page_missing_values_dash_not_zero", async () => {                     // I-1: неуспех 2 дня — ложного числа нет
  // у элемента ok поле null, NaN, строкой или его нет вовсе (А2 назвала бы поле иначе) — «—», не «0»; заметки — дословно
  const QUALITY = "доля достоверных 90 % — ниже 99 %";
  const COVERAGE = "данные покрывают 97,5 % периода — часы состояний в сумме меньше периода";
  const api = calcApi({
    tag_period: [200, { ...PERIOD_OK, items: [{ tag: "20FAKE_001_PV", status: "ok", unit: "л/час", avg: null, min: "10",
                                                 max: Number.NaN, percent_good: null, quality_note: QUALITY }] }],
    tag_series: [200, { ...SERIES_OK, items: [{ tag: "20FAKE_001_PV", status: "ok", unit: "л/час" }] }],
    calc_runtime: [200, { ...RUNTIME_OK, items: [{ tag: "25FAKE_007_CLS", status: "ok", covered_percent: null, coverage_note: COVERAGE,
                                                   states: [{ value: 1, label: "закрыт (по суффиксу)", hours: null, percent: "75" }] }] }],
  });
  const nums = () => [...document.querySelectorAll("section.calc-result .calc-num")].map((e) => e.textContent);
  render(<CalcPage api={api} onSignedOut={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Тег"), { target: { value: "20FAKE_001_PV" } });
  fireEvent.click(screen.getByRole("button", { name: "Посчитать" }));
  expect(await screen.findByText(QUALITY)).toBeTruthy();
  expect(nums()).toEqual(["—", "—", "—", "—", "—"]);                              // среднее, минимум, максимум, размах, доля

  fireEvent.change(screen.getByLabelText("Режим"), { target: { value: "profile" } });
  fireEvent.click(screen.getByRole("button", { name: "Посчитать" }));
  await screen.findByText("В начале");
  expect(nums()).toEqual(["—", "—", "—", "—", "—"]);                              // профиля нет — ни одного числа
  expect(document.querySelectorAll("svg.profile polyline, svg.profile circle").length).toBe(0);

  fireEvent.change(screen.getByLabelText("Режим"), { target: { value: "runtime" } });
  fireEvent.click(screen.getByRole("button", { name: "Посчитать" }));
  const row = (await screen.findByText("закрыт (по суффиксу)")).closest("tr")!;
  expect([...row.querySelectorAll("td")].map((td) => td.textContent)).toEqual(["закрыт (по суффиксу)", "—", "—", "—"]);
  expect(screen.getByText(COVERAGE)).toBeTruthy();
  expect(screen.queryByText(/^Данные покрывают/)).toBeNull();                      // покрытия нет — строки с числом нет
});

test("calc_page_search_refusal_and_signed_out", async () => {                    // I-2: Review Focus 4, учение А8 (R90 п. 4)
  const onSignedOut = vi.fn();
  const api = calcApi({ catalog_search: [[200, { tool: "catalog_search", status: "error", message: "сбой службы данных — вызов записан в журнал",
                                                 notes: [] }],
                                         [401, { name: "Unauthorized" }]] });
  render(<CalcPage api={api} onSignedOut={onSignedOut} />);
  fireEvent.change(screen.getByLabelText("Слова для поиска"), { target: { value: "тест" } });
  fireEvent.click(screen.getByRole("button", { name: "Найти тег" }));
  expect(await screen.findByText("сбой службы данных — вызов записан в журнал")).toBeTruthy();
  expect(onSignedOut).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Найти тег" }));
  await waitFor(() => expect(onSignedOut).toHaveBeenCalledTimes(1));
  expect(api.calc.mock.calls).toEqual([["catalog_search", { query: "тест", limit: 20 }], ["catalog_search", { query: "тест", limit: 20 }]]);
});

// --- итоговая доводка Д9а: число точек профиля, заметка поиска, свой период назад, защиты inFlight и alive ---

test("calc_page_profile_shows_points_per_tag", async () => {                      // итоговое ревью, m-6: не «N точек»
  const api = calcApi({ tag_series: [200, { ...SERIES_OK, source: "равномерная сетка историана (Cyclic): N точек" }],
                        tag_period: [200, PERIOD_OK] });
  await run(api, "profile", "20FAKE_001_PV");
  const row = (await screen.findByText("Точек на тег")).closest("div")!;
  expect(row.querySelector("dd")!.textContent).toBe("4");                          // points ответа службы
  fireEvent.change(screen.getByLabelText("Режим"), { target: { value: "period" } });
  fireEvent.click(screen.getByRole("button", { name: "Посчитать" }));
  await screen.findByText("12,4 л/час");
  expect(screen.queryByText("Точек на тег")).toBeNull();                           // только у профиля
});

test("calc_page_search_similar_note_for_student", async () => {                  // ревью А5, m-3: заметка агента — своя
  const TRUNC = "описания в историане обрезаны на 50 символах";
  const api = calcApi({ catalog_search: [200, { status: "ok", notes: [SIMILAR_NOTE_CORE, TRUNC], similar: true,
    matches: [{ tag: "20FAKE_001_PV", description: "Факт. знач. - расход массы", unit: "л/час" }] }] });
  render(<CalcPage api={api} onSignedOut={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Слова для поиска"), { target: { value: "расход" } });
  fireEvent.click(screen.getByRole("button", { name: "Найти тег" }));
  expect(await screen.findByText(T_CALC_SIMILAR)).toBeTruthy();
  expect(screen.getByText(TRUNC)).toBeTruthy();                                    // прочие заметки — дословно
  expect(screen.queryByText(SIMILAR_NOTE_CORE)).toBeNull();
});

test("calc_page_backward_range_not_sent", async () => {                          // ревью А5, m-4
  const api = calcApi({ tag_period: [200, PERIOD_OK] });
  render(<CalcPage api={api} onSignedOut={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Тег"), { target: { value: "20FAKE_001_PV" } });
  fireEvent.change(screen.getByLabelText("Период"), { target: { value: "custom" } });
  fireEvent.change(screen.getByLabelText("С"), { target: { value: "2026-10-01T12:00" } });
  fireEvent.change(screen.getByLabelText("По"), { target: { value: "2026-10-01T08:00" } });
  fireEvent.click(screen.getByRole("button", { name: "Посчитать" }));
  expect(screen.getByText(T_CALC_BAD_RANGE)).toBeTruthy();
  expect(api.calc).not.toHaveBeenCalled();
});

test("calc_page_in_flight_flag_and_late_answer", async () => {                   // ревью А5, m-2: флаг, не disabled
  const api = calcApi({});
  const answers: ((r: Reply) => void)[] = [];
  api.calc.mockImplementation(() => new Promise<Reply>((resolve) => { answers.push(resolve); }));
  const onSignedOut = vi.fn();
  const { unmount } = render(<CalcPage api={api} onSignedOut={onSignedOut} />);
  fireEvent.change(screen.getByLabelText("Тег"), { target: { value: "20FAKE_001_PV" } });
  const form = screen.getByRole("button", { name: "Посчитать" }).closest("form")!;
  fireEvent.submit(form);
  fireEvent.submit(form);                                                          // мимо кнопки: держит флаг
  fireEvent.change(screen.getByLabelText("Слова для поиска"), { target: { value: "расход" } });
  const search = screen.getByRole("button", { name: "Найти тег" }).closest("form")!;
  fireEvent.submit(search);
  fireEvent.submit(search);
  expect(api.calc).toHaveBeenCalledTimes(2);                                       // по одному вызову на кнопку
  unmount();                                                                       // вкладку сняли, ответы — позже
  for (const answer of answers) answer({ status: 401, body: { name: "Unauthorized" } });
  await new Promise((r) => setTimeout(r, 0));
  expect(onSignedOut).not.toHaveBeenCalled();                                      // alive: снятая вкладка не решает
});

// --- итоговая доводка Д10б ---

test("calc_page_unknown_text_prop_is_every_fallback", async () => {    // итоговое ревью Д10б, m-4
  // запасной текст пропсом (у преподавателя — кабинетный): отказ без текста, ответ не той формы, поиск без message,
  // элемент не ok без text, расчёт без message; T_DOWN (502–504) и текст сервера — как есть
  const OWN = "свой запасной текст";
  const api = calcApi({
    catalog_search: [[418, { odd: true }], [200, "не объект"], [200, { status: "refused" }]],
    tag_now: [[200, { status: "partial", items: [{ tag: "20FAKE_999_PV", status: "unknown" }] }],
              [200, { status: "refused", items: [] }], [502, null],
              [400, { name: "BadRequest", data: { message: "текст сервера" } }]] });
  render(<CalcPage api={api} onSignedOut={vi.fn()} unknownText={OWN} />);
  fireEvent.change(screen.getByLabelText("Слова для поиска"), { target: { value: "расход" } });
  const find = screen.getByRole("button", { name: "Найти тег" }) as HTMLButtonElement;
  for (let i = 0; i < 3; i++) {
    fireEvent.click(find);
    expect(find.disabled).toBe(true);                                         // ответ в пути: прежний текст не в счёт
    await waitFor(() => expect(find.disabled).toBe(false));
    expect(api.calc).toHaveBeenCalledTimes(i + 1);
    expect(screen.getByRole("alert").textContent).toBe(OWN);
  }
  fireEvent.change(screen.getByLabelText("Режим"), { target: { value: "now" } });
  fireEvent.change(screen.getByLabelText("Тег"), { target: { value: "20FAKE_999_PV" } });
  const result = async (): Promise<string> => {
    fireEvent.click(screen.getByRole("button", { name: "Посчитать" }));
    await waitFor(() => expect(document.querySelector("section.calc-result")).not.toBeNull());
    return document.querySelector("section.calc-result")!.textContent!;
  };
  expect(await result()).toContain(OWN);                                      // элемент не ok без text
  expect(await result()).toContain(OWN);                                      // расчёт без message
  expect(await result()).toBe(T_DOWN);
  expect(await result()).toBe("текст сервера");
  expect(document.body.textContent).not.toMatch(/сообщите преподавателю/);
});
