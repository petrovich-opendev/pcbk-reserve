// Карта Шухарта в чате (Д24): под строкой вызова pcbk_calc_spc чат рисует карту сам — из поля plot ответа службы
// (числа службы, не текст модели). Ответ разбирается строго: любое поле не того вида — графика нет, строка вызова и
// вывод остаются как были. Текст из ответа (тег, единица) — только текстом React, без разметки. Без новых API
// браузера (Chrome 109) и без style-атрибутов: геометрия — атрибутами SVG, цвета — классами chat.css.
import { T_SPC_BAD, T_SPC_CHART, T_SPC_OK, T_SPC_SECONDARY } from "../texts";

export const SPC_TOOL = "pcbk_calc_spc";
export const SPC_MAX_POINTS = 500;                    // служба отдаёт до 96; больше — не наш ответ

export type SpcLimits = { cl: number; ucl: number; lcl: number };
export type SpcPlot = SpcLimits & {
  chart: "I-MR" | "X̄-R"; tag: string; unit: string | null;
  points: number[]; flagged: number[]; lsl: number | null; usl: number | null;
  start: string | null; end: string | null;
  secondary: SpcLimits & { chart: string; points: number[] };
};

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const numOrNull = (v: unknown): v is number | null => v === null || isNum(v);
const strOrNull = (v: unknown): v is string | null => v === null || typeof v === "string";
const nums = (v: unknown, min: number): v is number[] =>
  Array.isArray(v) && v.length >= min && v.length <= SPC_MAX_POINTS && v.every(isNum);
const limits = (o: Obj): boolean => isNum(o.cl) && isNum(o.ucl) && isNum(o.lcl);

// ответ инструмента — строка JSON (вывод MCP в OpenCode) или уже объект; не карта — null
export function spcPlotOf(toolName: string, result: unknown): SpcPlot | null {
  if (toolName !== SPC_TOOL) return null;
  let r: unknown = result;
  if (typeof r === "string") {
    try { r = JSON.parse(r); } catch { return null; }   // обрезанный или чужой вывод — без графика
  }
  if (!isObj(r) || r.status !== "ok" || (r.chart !== "I-MR" && r.chart !== "X̄-R") || !limits(r)) return null;
  const p = r.plot, sec = r.secondary;
  if (!isObj(p) || !isObj(sec) || !limits(sec) || typeof sec.chart !== "string") return null;
  if (typeof r.tag !== "string" || !strOrNull(r.unit)) return null;
  if (!nums(p.points, 2) || !nums(p.secondary, 1) || !numOrNull(p.lsl) || !numOrNull(p.usl)) return null;
  if (!strOrNull(p.start) || !strOrNull(p.end)) return null;
  const points = p.points, shift = points.length - p.secondary.length;
  if (shift !== 0 && shift !== 1) return null;           // MR — на одну точку меньше, R — столько же
  if (!Array.isArray(p.flagged) || !p.flagged.every((i) => Number.isInteger(i) && i >= 0 && i < points.length))
    return null;
  return {
    chart: r.chart, tag: r.tag, unit: r.unit, cl: r.cl as number, ucl: r.ucl as number, lcl: r.lcl as number,
    points, flagged: p.flagged as number[], lsl: p.lsl, usl: p.usl, start: p.start, end: p.end,
    secondary: { chart: sec.chart, cl: sec.cl as number, ucl: sec.ucl as number, lcl: sec.lcl as number,
                 points: p.secondary },
  };
}

// подписи чисел — до 4 значащих цифр, запятая (как в ответах на русском)
export const fmt = (v: number): string => v.toLocaleString("ru-RU", { maximumSignificantDigits: 4 });

// время точки из ISO службы — как записано (время завода), без пересчёта в пояс браузера: «16.10 02:03»
export function shortTime(iso: string | null): string {
  const m = iso === null ? null : /^\d{4}-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  return m ? `${m[2]}.${m[1]} ${m[3]}:${m[4]}` : "";
}

// подписи линий справа не наезжают друг на друга: сверху вниз не ближе gap
export function spread(ys: number[], gap: number): number[] {
  const order = ys.map((y, i) => [y, i] as const).sort((a, b) => a[0] - b[0]);
  const out = ys.slice();
  let last = -Infinity;
  for (const [y, i] of order) { last = Math.max(y, last + gap); out[i] = last; }
  return out;
}

const W = 640, LEFT = 8, RIGHT = 92, MAIN_TOP = 24, MAIN_H = 170, SEC_TOP = MAIN_TOP + MAIN_H + 30, SEC_H = 70;
const H = SEC_TOP + SEC_H + 22, PLOT_W = W - LEFT - RIGHT, LABEL_GAP = 11;

type Line = { y: number; cls: string; label: string };

function scale(values: number[], top: number, height: number): (v: number) => number {
  let lo = Math.min(...values), hi = Math.max(...values);
  if (hi - lo < 1e-12) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.06;
  lo -= pad; hi += pad;
  return (v) => top + height - ((v - lo) / (hi - lo)) * height;
}

function Panel(p: { points: number[]; xs: number[]; lines: Line[]; top: number; height: number;
                    flagged?: ReadonlySet<number>; cls: string }) {
  const path = p.points.map((v, i) => `${i ? "L" : "M"}${p.xs[i]!.toFixed(1)} ${v.toFixed(1)}`).join(" ");
  const labelYs = spread(p.lines.map((l) => l.y), LABEL_GAP);
  return (
    <g className={p.cls}>
      <rect className="spc-frame" x={LEFT} y={p.top} width={PLOT_W} height={p.height} />
      {p.lines.map((l, i) => (
        <g key={i}>
          <line className={l.cls} x1={LEFT} x2={LEFT + PLOT_W} y1={l.y} y2={l.y} />
          <text className="spc-label" x={LEFT + PLOT_W + 4} y={labelYs[i]! + 4}>{l.label}</text>
        </g>
      ))}
      <path className="spc-path" d={path} />
      {p.points.map((y, i) => (
        <circle key={i} className={p.flagged?.has(i) ? "spc-pt spc-bad" : "spc-pt"} cx={p.xs[i]} cy={y}
                r={p.flagged?.has(i) ? 3.5 : 2.2} />
      ))}
    </g>
  );
}

export function SpcChart({ plot }: { plot: SpcPlot }) {
  const n = plot.points.length;
  const x = (i: number): number => LEFT + (n > 1 ? (i * PLOT_W) / (n - 1) : PLOT_W / 2);
  const spec = [plot.lsl, plot.usl].filter((v): v is number => v !== null);
  const y = scale([...plot.points, plot.ucl, plot.lcl, ...spec], MAIN_TOP, MAIN_H);
  const mainLines: Line[] = [
    { y: y(plot.ucl), cls: "spc-limit", label: `UCL ${fmt(plot.ucl)}` },
    { y: y(plot.cl), cls: "spc-cl", label: `CL ${fmt(plot.cl)}` },
    { y: y(plot.lcl), cls: "spc-limit", label: `LCL ${fmt(plot.lcl)}` },
    ...(plot.usl !== null ? [{ y: y(plot.usl), cls: "spc-spec", label: `USL ${fmt(plot.usl)}` }] : []),
    ...(plot.lsl !== null ? [{ y: y(plot.lsl), cls: "spc-spec", label: `LSL ${fmt(plot.lsl)}` }] : []),
  ];
  const sec = plot.secondary, shift = n - sec.points.length;
  const y2 = scale([...sec.points, sec.ucl, sec.lcl], SEC_TOP, SEC_H);
  const secLines: Line[] = [
    { y: y2(sec.ucl), cls: "spc-limit", label: `UCL ${fmt(sec.ucl)}` },
    { y: y2(sec.cl), cls: "spc-cl", label: `CL ${fmt(sec.cl)}` },
    ...(sec.lcl > 0 ? [{ y: y2(sec.lcl), cls: "spc-limit", label: `LCL ${fmt(sec.lcl)}` }] : []),
  ];
  const flagged = new Set(plot.flagged);
  const title = T_SPC_CHART(plot.chart, plot.tag, plot.unit);
  const verdict = flagged.size ? T_SPC_BAD(flagged.size) : T_SPC_OK;
  return (
    <figure className="spc">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title}. ${verdict}`}>
        <text className="spc-title" x={LEFT} y={MAIN_TOP - 8}>{title}</text>
        <Panel cls="spc-main" points={plot.points.map(y)} xs={plot.points.map((_, i) => x(i))} lines={mainLines}
               top={MAIN_TOP} height={MAIN_H} flagged={flagged} />
        <text className="spc-title" x={LEFT} y={SEC_TOP - 8}>{T_SPC_SECONDARY(sec.chart)}</text>
        <Panel cls="spc-sec" points={sec.points.map(y2)} xs={sec.points.map((_, i) => x(i + shift))}
               lines={secLines} top={SEC_TOP} height={SEC_H} />
        <text className="spc-label" x={LEFT} y={H - 6}>{shortTime(plot.start)}</text>
        <text className="spc-label" x={LEFT + PLOT_W} y={H - 6} textAnchor="end">{shortTime(plot.end)}</text>
      </svg>
      <figcaption className={flagged.size ? "spc-caption spc-caption-bad" : "spc-caption"}>{verdict}</figcaption>
    </figure>
  );
}
