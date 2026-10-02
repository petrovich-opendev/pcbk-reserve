// Чистые помощники вкладки «Калькуляторы» (Д9-R88): форма → запрос шлюза, числа ru-RU, время историана, ломаные
// профиля. Вкладка считает не сама: числа — значения службы как есть, только в формате ru-RU (не больше 3 знаков).
import type { CalcTool } from "./api";
import { CALC_LABELS } from "./texts";

export type CalcMode = "now" | "period" | "profile" | "runtime";
// готовые периоды службы (PRESETS periods.py) и свой период — два поля datetime-local (сверка — tests/texts.test.ts)
export type PeriodKey = keyof typeof CALC_LABELS.periods;

export interface CalcForm {
  mode: CalcMode;
  tag: string;
  period: string;              // ключ PERIOD_CHOICES
  start: string;                // свой период: строка datetime-local как есть («2026-10-01T08:00», время историана)
  end: string;
  points: number;               // точки профиля на тег
}

export const MODE_CHOICES: readonly { mode: CalcMode; tool: CalcTool; label: string }[] = [
  { mode: "now", tool: "tag_now", label: CALC_LABELS.modes.now },
  { mode: "period", tool: "tag_period", label: CALC_LABELS.modes.period },
  { mode: "profile", tool: "tag_series", label: CALC_LABELS.modes.profile },
  { mode: "runtime", tool: "calc_runtime", label: CALC_LABELS.modes.runtime },
];

const PERIOD_KEYS: readonly PeriodKey[] = ["last_hour", "last_8h", "last_24h", "current_shift", "prev_shift", "today",
                                           "yesterday", "last_7d", "last_30d", "custom"];
export const PERIOD_CHOICES: readonly { key: PeriodKey; label: string }[] =
  PERIOD_KEYS.map((key) => ({ key, label: CALC_LABELS.periods[key] }));
export const CUSTOM_PERIOD: PeriodKey = "custom";

export const POINT_CHOICES: readonly number[] = [12, 24, 48, 96];   // в пределах службы 2–96 (Д9-R84)
export const CALC_SEARCH_LIMIT = 20;                                 // совпадений поиска тега за вызов (≤ SEARCH_LIMIT службы)

// режим по умолчанию — сводка за прошлую смену (m14), 24 точки профиля
export const DEFAULT_FORM: CalcForm = { mode: "period", tag: "", period: "prev_shift", start: "", end: "", points: 24 };

// форма → инструмент и аргументы шлюза; null — нет тега или свой период без начала или конца
export function buildRequest(form: CalcForm): { tool: CalcTool; args: Record<string, unknown> } | null {
  const tag = form.tag.trim();
  const choice = MODE_CHOICES.find((c) => c.mode === form.mode);
  if (tag === "" || choice === undefined) return null;
  const args: Record<string, unknown> = { tags: [tag] };
  if (form.mode !== "now") {                            // у текущего значения периода нет
    if (form.period === CUSTOM_PERIOD) {
      if (form.start.trim() === "" || form.end.trim() === "") return null;
      args.start = form.start;
      args.end = form.end;
    } else {
      args.period = form.period;
    }
    if (form.mode === "profile") args.points = form.points;
  }
  return { tool: choice.tool, args };
}

const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);

const NUMBER = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });

// число службы → ru-RU, не больше 3 знаков после запятой; не число (null, строка, NaN) — «—». «-0» от округления
// крошечного отрицательного (изменение 12,5 − 12,5000001) пишется «0»
export function fmtNum(x: unknown): string {
  if (!isNum(x)) return "—";
  const text = NUMBER.format(x);
  return text === "-0" ? "0" : text;
}

const ISO_LOCAL = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/;

// время ISO службы → «ДД.ММ.ГГГГ ЧЧ:ММ[:СС]» по часам историана, без пересчёта в пояс браузера (пояс — строкой периода);
// не той формы — как есть, не строка — «—»
export function fmtTime(iso: unknown): string {
  if (typeof iso !== "string") return "—";
  const m = ISO_LOCAL.exec(iso);
  if (m === null) return iso;
  const [, y, mo, d, h, mi, s] = m;
  return `${d}.${mo}.${y} ${h}:${mi}` + (s !== undefined && s !== "00" ? `:${s}` : "");
}

const round1 = (v: number): string => String(Math.round(v * 10) / 10);      // String(-0) — «0»

// значения сетки → ломаные SVG («x,y x,y …») по отрезкам без пропусков: null рвёт линию. x_i = i · w / (n − 1),
// y = h − (v − min) / (max − min) · h, ровный ряд — посередине (h / 2); числа — до 0,1 без лишних нулей
export function profileSegments(values: (number | null)[], width: number, height: number): string[] {
  const known = values.filter(isNum);
  if (known.length === 0) return [];
  const min = Math.min(...known);
  const max = Math.max(...known);
  const n = values.length;
  const segments: string[] = [];
  let current: string[] = [];
  values.forEach((v, i) => {
    if (!isNum(v)) {
      if (current.length) segments.push(current.join(" "));
      current = [];
      return;
    }
    const x = n > 1 ? (i * width) / (n - 1) : width / 2;
    const y = max === min ? height / 2 : height - ((v - min) / (max - min)) * height;
    current.push(`${round1(x)},${round1(y)}`);
  });
  if (current.length) segments.push(current.join(" "));
  return segments;
}
