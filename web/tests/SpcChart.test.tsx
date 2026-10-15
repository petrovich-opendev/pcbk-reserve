// Карта Шухарта в чате (Д24): разбор ответа pcbk_calc_spc строгий (не тот вид — графика нет), числа рисуются из
// plot службы, нарушения — красным, допуск — только если был, текст ответа — только текстом.
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { SPC_MAX_POINTS, SpcChart, shortTime, spcPlotOf, spread, type SpcPlot } from "../src/components/SpcChart";
import { T_SPC_BAD, T_SPC_OK } from "../src/texts";

// ответ службы в том виде, как его отдаёт MCP (JSON с отступами), — вымышленный тег
function answer(over: Record<string, unknown> = {}, plot: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    tool: "calc_spc", status: "ok", tag: "20FAKE_001_PV", unit: "л/час", chart: "I-MR", in_control: false,
    cl: 10, ucl: 12.5, lcl: 7.5,
    secondary: { chart: "MR", cl: 0.9, ucl: 2.94, lcl: 0 },
    plot: { points: [10.1, 9.8, 10.3, 14.0, 10.0, 9.9], secondary: [0.3, 0.5, 3.7, 4.0, 0.1], flagged: [3],
            lsl: null, usl: null, start: "2026-10-02T00:00:00+05:00", end: "2026-10-02T06:00:00+05:00", ...plot },
    ...over,
  };
}
const text = (o: unknown): string => JSON.stringify(o, null, 2);
const plotOf = (o: unknown): SpcPlot => spcPlotOf("pcbk_calc_spc", text(o))!;

test("spc_plot_parses_mcp_json_text_and_object", () => {
  const p = plotOf(answer());
  expect(p).not.toBeNull();
  expect([p.chart, p.tag, p.unit, p.cl, p.ucl, p.lcl]).toEqual(["I-MR", "20FAKE_001_PV", "л/час", 10, 12.5, 7.5]);
  expect([p.points.length, p.secondary.points.length, p.flagged]).toEqual([6, 5, [3]]);
  expect(spcPlotOf("pcbk_calc_spc", answer())).toEqual(p);                   // уже объект — тот же результат
});

test("spc_plot_null_for_anything_else", () => {
  const bad: unknown[] = [
    text(answer()).slice(0, 200),                                   // обрезанный вывод
    "Карта: всё хорошо",                                            // не JSON
    text(answer({ status: "partial", plot: null })),                // данных мало — карты нет
    text(answer({ chart: "XYZ" })),
    text(answer({ cl: "10" })),
    text(answer({ tag: 5 })),
    text(answer({ secondary: null })),
    text(answer({}, { points: [1] })),                              // меньше двух точек
    text(answer({}, { points: [1, 2, "3", 4, 5, 6] })),
    text(answer({}, { secondary: [1, 2, 3] })),                     // длина вторичной не n-1 и не n
    text(answer({}, { flagged: [6] })),                             // индекс вне ряда
    text(answer({}, { flagged: [1.5] })),
    text(answer({}, { lsl: "5" })),
    text(answer({}, { start: 1 })),
    text(answer({}, { points: Array(SPC_MAX_POINTS + 1).fill(1), secondary: Array(SPC_MAX_POINTS).fill(0) })),
  ];
  for (const b of bad) expect(spcPlotOf("pcbk_calc_spc", b), String(b).slice(0, 80)).toBeNull();
  expect(spcPlotOf("pcbk_tag_series", text(answer()))).toBeNull();          // чужой инструмент
  expect(spcPlotOf("pcbk_calc_spc", undefined)).toBeNull();
  // X̄-R: вторичная R — столько же точек, сколько подгрупп
  expect(plotOf(answer({ chart: "X̄-R", secondary: { chart: "R", cl: 1, ucl: 2, lcl: 0 } },
                       { secondary: [1, 1, 1, 1, 1, 1] }))).not.toBeNull();
});

test("spc_chart_draws_points_limits_and_flagged_red", () => {
  const { container } = render(<SpcChart plot={plotOf(answer())} />);
  const main = container.querySelector("g.spc-main")!, sec = container.querySelector("g.spc-sec")!;
  expect(main.querySelectorAll("circle.spc-pt").length).toBe(6);
  expect(main.querySelectorAll("circle.spc-bad").length).toBe(1);
  expect(sec.querySelectorAll("circle.spc-pt").length).toBe(5);
  expect(sec.querySelectorAll("circle.spc-bad").length).toBe(0);
  const labels = [...main.querySelectorAll("text.spc-label")].map((t) => t.textContent);
  expect(labels).toEqual(["UCL 12,5", "CL 10", "LCL 7,5"]);                 // допуска не было — его линий нет
  expect(main.querySelectorAll("line.spc-spec").length).toBe(0);
  // MR — со второй точки: первая точка MR под второй точкой карты I
  const mainX = [...main.querySelectorAll("circle")].map((c) => c.getAttribute("cx"));
  const secX = [...sec.querySelectorAll("circle")].map((c) => c.getAttribute("cx"));
  expect(secX).toEqual(mainX.slice(1));
  // LCL MR = 0 — линии нет, только UCL и CL
  expect([...sec.querySelectorAll("text.spc-label")].map((t) => t.textContent)).toEqual(["UCL 2,94", "CL 0,9"]);
  expect(screen.getByText("02.10 00:00")).toBeTruthy();
  expect(screen.getByText("02.10 06:00")).toBeTruthy();
  expect(screen.getByText(T_SPC_BAD(1))).toBeTruthy();
  expect(container.querySelector("svg")!.getAttribute("role")).toBe("img");
});

test("spc_chart_tolerance_lines_and_no_violations", () => {
  const { container } = render(<SpcChart plot={plotOf(answer({}, { flagged: [], lsl: 5, usl: 15 }))} />);
  const main = container.querySelector("g.spc-main")!;
  expect(main.querySelectorAll("line.spc-spec").length).toBe(2);
  expect([...main.querySelectorAll("text.spc-label")].map((t) => t.textContent))
    .toEqual(["UCL 12,5", "CL 10", "LCL 7,5", "USL 15", "LSL 5"]);
  expect(main.querySelectorAll("circle.spc-bad").length).toBe(0);
  expect(screen.getByText(T_SPC_OK)).toBeTruthy();
});

test("spc_chart_text_from_answer_is_text", () => {
  const { container } = render(<SpcChart plot={plotOf(answer({ tag: "<img src=x onerror=alert(1)>", unit: null }))} />);
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("text.spc-title")!.textContent)
    .toBe("Карта I — индивидуальные значения: <img src=x onerror=alert(1)>");
});

test("spc_chart_flat_series_does_not_divide_by_zero", () => {
  const p = plotOf(answer({ cl: 5, ucl: 5, lcl: 5, secondary: { chart: "MR", cl: 0, ucl: 0, lcl: 0 } },
                          { points: [5, 5, 5], secondary: [0, 0], flagged: [] }));
  const { container } = render(<SpcChart plot={p} />);
  for (const c of container.querySelectorAll("circle")) expect(Number.isFinite(Number(c.getAttribute("cy")))).toBe(true);
});

test("spc_helpers", () => {
  expect(shortTime("2026-10-16T02:03:00+05:00")).toBe("16.10 02:03");
  expect(shortTime(null)).toBe("");
  expect(shortTime("вчера")).toBe("");
  expect(spread([50, 52, 100], 11)).toEqual([50, 61, 100]);                 // близкие подписи расходятся
  expect(spread([100, 52, 50], 11)).toEqual([100, 61, 50]);                 // порядок входа сохраняется
  expect(spread([190, 192, 194], 11, 194)).toEqual([172, 183, 194]);       // у нижнего края — поджаты вверх
  expect([T_SPC_BAD(1), T_SPC_BAD(3), T_SPC_BAD(5), T_SPC_BAD(11), T_SPC_BAD(21), T_SPC_BAD(22)]).toEqual([
    "Нарушения правил: 1 точка (красным)", "Нарушения правил: 3 точки (красным)",
    "Нарушения правил: 5 точек (красным)", "Нарушения правил: 11 точек (красным)",
    "Нарушения правил: 21 точка (красным)", "Нарушения правил: 22 точки (красным)"]);
});
