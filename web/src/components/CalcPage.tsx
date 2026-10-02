// Вкладка «Калькуляторы» (Д9-R88): те же инструменты службы данных, что у агентов, через шлюз POST /pcbk/api/calc/<tool>
// (Д9-R87). При открытии ничего не спрашивает — только по кнопкам «Найти тег» и «Посчитать». Числа — значения ответа
// службы как есть (fmtNum: ru-RU, не больше 3 знаков, малое ненулевое — 3 значащие цифры, не «0»; нет значения — «—»);
// под числами всегда — период с поясом, источник, вид среднего и заметки дословно. Отказ службы — её message, элемент
// не ok — его text; 401 — конец сессии (onSignedOut), прочие не 200 — replyText. Разметка — договор с интеграцией А7:
// поля #calc-query, #calc-tag, #calc-mode, #calc-period, #calc-from, #calc-to, #calc-points, список .calc-matches,
// график svg.profile, итог section.calc-result.
// Стили — только классы chat.css: атрибутов style нет (CSP style-src 'self').
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { replyText, type Api, type CalcTool, type Reply } from "../api";
import { buildRequest, CALC_SEARCH_LIMIT, CUSTOM_PERIOD, DEFAULT_FORM, fmtNum, fmtTime, MODE_CHOICES, PERIOD_CHOICES,
         POINT_CHOICES, profileSegments, type CalcForm, type CalcMode } from "../calc";
import { CALC_LABELS as L, T_CALC_ASOF_LINE, T_CALC_BUSY, T_CALC_COVERED, T_CALC_FIND, T_CALC_NO_RANGE, T_CALC_NO_TAG,
         T_CALC_NOTHING, T_CALC_PERCENT, T_CALC_PERIOD_LINE, T_CALC_RUN, T_CALC_SECONDS, T_CALC_STATE_VALUE,
         T_CALC_TITLE, T_UNKNOWN } from "../texts";

export interface CalcPageProps {
  api: Api;
  onSignedOut(): void;
}

type Obj = Record<string, unknown>;
const isObject = (v: unknown): v is Obj => v !== null && typeof v === "object" && !Array.isArray(v);
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x !== "") : []);
const objects = (v: unknown): Obj[] => (Array.isArray(v) ? v.filter(isObject) : []);
const noAnswer = (): Reply => ({ status: 0, body: null });

const CHART_W = 600;
const CHART_H = 160;
const QUERY_CHARS = 200;        // QUERY_CHARS службы
const TAG_CHARS = 128;          // TAG_CHARS службы

// ответ шлюза → тело службы, текст для человека или конец сессии
type Outcome = { kind: "body"; body: Obj } | { kind: "text"; text: string } | { kind: "signed_out" };

function outcomeOf(r: Reply): Outcome {
  if (r.status === 401) return { kind: "signed_out" };
  if (r.status !== 200) return { kind: "text", text: replyText(r) };
  return isObject(r.body) ? { kind: "body", body: r.body } : { kind: "text", text: T_UNKNOWN };
}

const answered = (body: Obj): boolean => body.status === "ok" || body.status === "partial";

interface Match { tag: string; label: string }
type Search = { kind: "none" } | { kind: "text"; text: string } | { kind: "matches"; matches: Match[]; notes: string[] };
type Result = { kind: "none" } | { kind: "text"; text: string } | { kind: "answer"; tool: CalcTool; body: Obj };

// ответ catalog_search → строки «тег — описание — единица» (I2)
function searchOf(o: Outcome): Search {
  if (o.kind !== "body") return { kind: "text", text: o.kind === "text" ? o.text : T_UNKNOWN };
  if (!answered(o.body)) return { kind: "text", text: str(o.body.message) ?? T_UNKNOWN };
  const matches: Match[] = [];
  for (const m of objects(o.body.matches)) {
    const tag = str(m.tag);
    if (tag !== null) matches.push({ tag, label: [tag, str(m.description), str(m.unit)].filter((s) => s !== null).join(" — ") });
  }
  return { kind: "matches", matches, notes: strings(o.body.notes) };
}

const withUnit = (x: unknown, unit: unknown): string => {
  const n = fmtNum(x);
  return n === "—" || str(unit) === null ? n : `${n} ${unit as string}`;
};

interface Row { label: string; value: string; when?: string }

function Numbers({ rows }: { rows: Row[] }) {
  return (
    <dl className="calc-numbers">
      {rows.map((r) => (
        <div key={r.label}>
          <dt>{r.label}</dt>
          <dd>
            <span className="calc-num">{r.value}</span>
            {r.when !== undefined && <span className="calc-when">{r.when}</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Notes({ notes }: { notes: string[] }) {
  if (notes.length === 0) return null;
  return <ul className="calc-notes">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>;
}

// график профиля: ломаные по отрезкам без пропусков, отрезок из одной точки — кружок
function Chart({ points }: { points: unknown }) {
  const rows = Array.isArray(points) ? points.filter(Array.isArray) : [];
  const values = rows.map((p) => (isNum(p[1]) ? p[1] : null));
  const segments = profileSegments(values, CHART_W, CHART_H);
  return (
    <figure className="calc-chart">
      <svg className="profile" viewBox={`0 0 ${CHART_W} ${CHART_H}`} width={CHART_W} height={CHART_H} role="img"
           aria-label={L.chart}>
        <rect className="frame" x={0} y={0} width={CHART_W} height={CHART_H} />
        {segments.map((s, i) => {
          if (s.includes(" ")) return <polyline key={i} points={s} />;
          const [cx, cy] = s.split(",");
          return <circle key={i} cx={cx} cy={cy} r={3} />;
        })}
      </svg>
      {rows.length > 0 && (
        <figcaption className="calc-axis">
          <span>{fmtTime(rows[0]![0])}</span>
          <span>{fmtTime(rows[rows.length - 1]![0])}</span>
        </figcaption>
      )}
    </figure>
  );
}

function numbersOf(tool: CalcTool, item: Obj): ReactNode {
  const unit = item.unit;
  switch (tool) {
    case "tag_now": {
      const state = str(item.state_label);
      return (
        <Numbers rows={[
          { label: L.value, value: state !== null ? `${fmtNum(item.value)} — ${state}` : withUnit(item.value, unit) },
          { label: L.time, value: fmtTime(item.time) },
          { label: L.age, value: isNum(item.age_s) ? T_CALC_SECONDS(fmtNum(item.age_s)) : "—" },
          { label: L.quality, value: str(item.quality) ?? "—" },
        ]} />
      );
    }
    case "tag_period":
      return (
        <Numbers rows={[
          { label: L.avg, value: withUnit(item.avg, unit) },
          { label: L.min, value: withUnit(item.min, unit) },
          { label: L.max, value: withUnit(item.max, unit) },
          { label: L.range, value: withUnit(item.range, unit) },
          { label: L.good, value: isNum(item.percent_good) ? T_CALC_PERCENT(fmtNum(item.percent_good)) : "—" },
        ]} />
      );
    case "tag_series": {
      const p = isObject(item.profile) ? item.profile : {};
      return (
        <>
          <Chart points={item.points} />
          <Numbers rows={[
            { label: L.first, value: withUnit(p.first, unit), when: fmtTime(p.first_time) },
            { label: L.last, value: withUnit(p.last, unit), when: fmtTime(p.last_time) },
            { label: L.change, value: withUnit(p.change, unit) },
            { label: L.gridMin, value: withUnit(p.min, unit), when: fmtTime(p.min_time) },
            { label: L.gridMax, value: withUnit(p.max, unit), when: fmtTime(p.max_time) },
          ]} />
        </>
      );
    }
    case "calc_runtime":
      return (
        <>
          <table className="calc-table">
            <thead><tr><th>{L.state}</th><th>{L.hours}</th><th>{L.percent}</th><th>{L.count}</th></tr></thead>
            <tbody>
              {objects(item.states).map((s, i) => (
                <tr key={i}>
                  <td>{str(s.label) ?? T_CALC_STATE_VALUE(fmtNum(s.value))}</td>
                  <td>{fmtNum(s.hours)}</td>
                  <td>{fmtNum(s.percent)}</td>
                  <td>{fmtNum(s.count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {isNum(item.covered_percent) && <p className="calc-covered">{T_CALC_COVERED(fmtNum(item.covered_percent))}</p>}
        </>
      );
    default:
      return null;
  }
}

function Item({ tool, item }: { tool: CalcTool; item: Obj }) {
  const tag = str(item.tag) ?? "—";
  if (item.status !== "ok") {
    return (
      <p className="calc-off">
        <span className="calc-tag">{tag}</span>
        <span>{str(item.text) ?? T_UNKNOWN}</span>
      </p>
    );
  }
  const description = str(item.description);
  const notes = [str(item.quality_note), str(item.coverage_note)].filter((n) => n !== null).concat(strings(item.item_notes));
  return (
    <article className="calc-item">
      <h3>{description !== null ? `${tag} — ${description}` : tag}</h3>
      {numbersOf(tool, item)}
      <Notes notes={notes} />
    </article>
  );
}

// под числами: период с поясом (у текущего значения — момент чтения), источник, вид среднего, заметки ответа
function About({ body }: { body: Obj }) {
  const period = isObject(body.period) ? body.period : null;
  const source = str(body.source);
  const avgKind = isObject(body.avg_kind) ? str(body.avg_kind.text) : null;
  return (
    <>
      {period !== null && (
        <p className="calc-period">{T_CALC_PERIOD_LINE(fmtTime(period.start), fmtTime(period.end), str(period.tz) ?? "—")}</p>
      )}
      {period === null && str(body.asof) !== null && (
        <p className="calc-period">{T_CALC_ASOF_LINE(fmtTime(body.asof), str(body.tz) ?? "—")}</p>
      )}
      {(source !== null || avgKind !== null) && (
        <dl className="calc-about">
          {source !== null && <div><dt>{L.source}</dt><dd>{source}</dd></div>}
          {avgKind !== null && <div><dt>{L.avgKind}</dt><dd>{avgKind}</dd></div>}
        </dl>
      )}
      <Notes notes={strings(body.notes)} />
    </>
  );
}

function Answer({ tool, body }: { tool: CalcTool; body: Obj }) {
  return (
    <>
      {!answered(body) && <p className="calc-msg" role="alert">{str(body.message) ?? T_UNKNOWN}</p>}
      {objects(body.items).map((item, i) => <Item key={i} tool={tool} item={item} />)}
      <About body={body} />
    </>
  );
}

export function CalcPage(p: CalcPageProps) {
  const [form, setForm] = useState<CalcForm>(DEFAULT_FORM);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<Search>({ kind: "none" });
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const [result, setResult] = useState<Result>({ kind: "none" });
  const props = useRef(p);
  props.current = p;
  const alive = useRef(true);
  const inFlight = useRef({ search: false, run: false });       // второе нажатие, пока ответ в пути, не уходит

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const set = (patch: Partial<CalcForm>): void => setForm((f) => ({ ...f, ...patch }));

  // вызов шлюза; null — вкладку сняли, пока ответ был в пути: состояние не трогаем
  const call = async (tool: CalcTool, args: Record<string, unknown>): Promise<Outcome | null> => {
    const r = await props.current.api.calc(tool, args).catch(noAnswer);
    return alive.current ? outcomeOf(r) : null;
  };

  const find = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    const q = query.trim();
    if (q === "" || inFlight.current.search) return;
    inFlight.current.search = true;
    setSearching(true);
    const o = await call("catalog_search", { query: q, limit: CALC_SEARCH_LIMIT });
    inFlight.current.search = false;
    if (o === null) return;
    setSearching(false);
    if (o.kind === "signed_out") return props.current.onSignedOut();
    setSearch(searchOf(o));
  };

  const run = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    if (inFlight.current.run) return;
    const request = buildRequest(form);
    if (request === null) {
      setHint(form.tag.trim() === "" ? T_CALC_NO_TAG : T_CALC_NO_RANGE);
      return;
    }
    inFlight.current.run = true;
    setHint(null);
    setResult({ kind: "none" });                        // прежний итог не висит под «Считаю…»
    setBusy(true);
    const o = await call(request.tool, request.args);
    inFlight.current.run = false;
    if (o === null) return;
    setBusy(false);
    if (o.kind === "signed_out") return props.current.onSignedOut();
    setResult(o.kind === "text" ? { kind: "text", text: o.text } : { kind: "answer", tool: request.tool, body: o.body });
  };

  const custom = form.mode !== "now" && form.period === CUSTOM_PERIOD;
  return (
    <section className="calc">
      <h2>{T_CALC_TITLE}</h2>
      <form className="calc-row" onSubmit={(e) => void find(e)}>
        <div className="calc-field calc-wide">
          <label htmlFor="calc-query">{L.query}</label>
          <input id="calc-query" type="text" value={query} maxLength={QUERY_CHARS} autoComplete="off"
                 onChange={(e) => setQuery(e.target.value)} />
        </div>
        <button type="submit" disabled={searching || query.trim() === ""}>{T_CALC_FIND}</button>
      </form>
      {search.kind === "text" && <p className="calc-msg" role="alert">{search.text}</p>}
      {search.kind === "matches" && (
        <>
          {search.matches.length === 0
            ? <p className="calc-hint">{T_CALC_NOTHING}</p>
            : (
              <ul className="calc-matches" aria-label={L.matches}>
                {search.matches.map((m, i) => (
                  <li key={i}><button type="button" onClick={() => set({ tag: m.tag })}>{m.label}</button></li>
                ))}
              </ul>
            )}
          <Notes notes={search.notes} />
        </>
      )}
      <form className="calc-row" onSubmit={(e) => void run(e)}>
        <div className="calc-field calc-wide">
          <label htmlFor="calc-tag">{L.tag}</label>
          <input id="calc-tag" type="text" value={form.tag} maxLength={TAG_CHARS} autoComplete="off" spellCheck={false}
                 onChange={(e) => set({ tag: e.target.value })} />
        </div>
        <div className="calc-field">
          <label htmlFor="calc-mode">{L.mode}</label>
          <select id="calc-mode" value={form.mode} onChange={(e) => set({ mode: e.target.value as CalcMode })}>
            {MODE_CHOICES.map((c) => <option key={c.mode} value={c.mode}>{c.label}</option>)}
          </select>
        </div>
        {form.mode !== "now" && (
          <div className="calc-field">
            <label htmlFor="calc-period">{L.period}</label>
            <select id="calc-period" value={form.period} onChange={(e) => set({ period: e.target.value })}>
              {PERIOD_CHOICES.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
            </select>
          </div>
        )}
        {custom && (
          <>
            <div className="calc-field">
              <label htmlFor="calc-from">{L.from}</label>
              <input id="calc-from" type="datetime-local" value={form.start} onChange={(e) => set({ start: e.target.value })} />
            </div>
            <div className="calc-field">
              <label htmlFor="calc-to">{L.to}</label>
              <input id="calc-to" type="datetime-local" value={form.end} onChange={(e) => set({ end: e.target.value })} />
            </div>
          </>
        )}
        {form.mode === "profile" && (
          <div className="calc-field">
            <label htmlFor="calc-points">{L.points}</label>
            <select id="calc-points" value={String(form.points)} onChange={(e) => set({ points: Number(e.target.value) })}>
              {POINT_CHOICES.map((n) => <option key={n} value={String(n)}>{n}</option>)}
            </select>
          </div>
        )}
        <button type="submit" className="send" disabled={busy}>{busy ? T_CALC_BUSY : T_CALC_RUN}</button>
      </form>
      {hint !== null && <p className="calc-hint" role="alert">{hint}</p>}
      {result.kind !== "none" && (
        <section className="calc-result" aria-live="polite">
          {result.kind === "text"
            ? <p className="calc-msg" role="alert">{result.text}</p>
            : <Answer tool={result.tool} body={result.body} />}
        </section>
      )}
    </section>
  );
}
