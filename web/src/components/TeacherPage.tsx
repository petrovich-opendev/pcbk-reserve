// Кабинет преподавателя (Д10-R106): страница чата для роли teacher вместо заглушки Д6.
// - Сверху — строка бюджета «Общий бюджет: …» и «Обновить». Обзор сам не опрашивается: только кнопкой и после изменений.
// - Таблица мест table.places (tr[data-place]): место, студент с пометками, состояние, доля, потрачено, агентов; у места со
//   студентом — button.open-place «Открыть».
// - Открытое место — справа, вкладки «Разговоры» и «Агенты». Разговоры читаются только у готового места: открытие
//   спрашивает список, 503 WorkplaceNotReady — текст и button.wake-read «Разбудить для просмотра»; нажатие — wake, затем
//   опрос place(nn) раз в секунду до «готово» или 90 с (wakeAndWait, как WorkplaceScreen) и список заново. Само место
//   кабинет не будит. Список — .sessions button[data-session-id]; выбор — TeacherReader (только чтение). Агенты —
//   TeacherAgents: место для них не нужно.
// - Ниже — «Доли бюджета на модель» (TeacherShares) и «Журнал действий» (TeacherJournal).
// 401 любой ручки — onSignedOut (один раз); прочие отказы — текст ответа (replyText). Роль решает сервер: страница только
// показывает. Стили — классы chat.css, атрибутов style нет (CSP style-src 'self').
import { useCallback, useEffect, useRef, useState } from "react";
import { replyText, type Api, type Reply } from "../api";
import { fmtCents, fmtUsd, fmtWhen, parseOverview, parseSessions, parseTranscript, type Overview, type PlaceRow,
         type SessionRow, type Transcript } from "../teacher";
import type { TeacherApi } from "../teacherApi";
import { PLACE_STATE_LABELS, T_PLACE_PREPARING, T_TCH_ARCHIVED, T_TCH_BUDGET, T_TCH_FREE, T_TCH_LOADING,
         T_TCH_MARK_DISABLED, T_TCH_MARK_LOCKED, T_TCH_MARK_NOT_NOTICED, T_TCH_NO_ACCOUNT, T_TCH_NO_SESSIONS,
         T_TCH_NO_SHARES, T_TCH_NONE, T_TCH_OPEN, T_TCH_OPEN_LABEL, T_TCH_PLACE_TABS, T_TCH_PLACE_TITLE, T_TCH_PLACES,
         T_TCH_REFRESH, T_TCH_RELOAD_LIST, T_TCH_TAB_AGENTS, T_TCH_TAB_SESSIONS, T_TCH_TITLE, T_TCH_WAKE, T_UNKNOWN,
         T_UNTITLED, TCH_COLUMNS as C } from "../texts";
import { wakeAndWait, type Timers } from "../workplace";
import { TeacherAgents } from "./TeacherAgents";
import { TeacherJournal } from "./TeacherJournal";
import { TeacherReader } from "./TeacherReader";
import { TeacherShares } from "./TeacherShares";

export interface TeacherPageProps {
  api: TeacherApi;
  onSignedOut(): void;
  pollMs?: number;              // опрос пробуждения — для тестов; по умолчанию раз в секунду
  timers?: Timers;
}

const noAnswer = (): Reply => ({ status: 0, body: null });
const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object";
// место спит (R102, R100 шаг 9): чтение разговоров его не будит
const asleep = (r: Reply): boolean => r.status === 503 && isObject(r.body) && r.body.name === "WorkplaceNotReady";
const money = (cents: number | null): string => (cents === null ? T_TCH_NONE : fmtCents(cents));

// --- таблица мест ---

function marks(row: PlaceRow): string[] {
  const out: string[] = [];
  if (row.disabled === true) out.push(T_TCH_MARK_DISABLED);
  if (row.locked === true) out.push(T_TCH_MARK_LOCKED);
  if (row.noticed === false) out.push(T_TCH_MARK_NOT_NOTICED);
  return out;
}

function PlacesTable({ overview, open, onOpen }: { overview: Overview; open: string | null; onOpen(nn: string): void }) {
  return (
    <table className="places" aria-label={T_TCH_PLACES}>
      <thead>
        <tr>
          <th scope="col">{C.place}</th><th scope="col">{C.student}</th><th scope="col">{C.state}</th>
          <th scope="col">{C.share}</th><th scope="col">{C.spent}</th><th scope="col">{C.agents}</th><th scope="col" />
        </tr>
      </thead>
      <tbody>
        {overview.places.map((row) => (
          <tr key={row.place} data-place={row.place} data-open={open === row.place ? "true" : undefined}>
            <td>{row.place}</td>
            <td>
              {row.login ?? (row.free ? T_TCH_FREE : T_TCH_NO_ACCOUNT)}
              {marks(row).map((m) => <span key={m} className="tch-mark">{" "}{m}</span>)}
            </td>
            <td data-state={row.state}>{PLACE_STATE_LABELS[row.state] ?? row.state}</td>
            <td>{money(row.share_cents)}</td>
            <td>{row.spent_usd === null ? T_TCH_NONE : fmtUsd(row.spent_usd)}</td>
            <td>{row.agents === null ? T_TCH_NONE : String(row.agents)}</td>
            <td>
              {row.login !== null && (
                <button type="button" className="open-place" aria-label={T_TCH_OPEN_LABEL(row.place)}
                        aria-pressed={open === row.place} onClick={() => onOpen(row.place)}>
                  {T_TCH_OPEN}
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function BudgetLine({ overview }: { overview: Overview }) {
  const b = overview.budget;
  return (
    <>
      {b !== null && (
        <p className="tch-budget">
          {T_TCH_BUDGET(fmtUsd(b.spent_usd), fmtCents(b.total_cents), fmtCents(b.allocated_cents), fmtCents(b.free_cents),
                        fmtUsd(b.ops_usd))}
        </p>
      )}
      {(b === null || !overview.shares_ok) && <p className="tch-budget" data-kind="fail">{T_TCH_NO_SHARES}</p>}
    </>
  );
}

// --- открытое место: разговоры (только у готового места) ---

type Sessions =
  | { kind: "loading" }
  | { kind: "asleep"; text: string }               // 503 WorkplaceNotReady — кнопка «Разбудить для просмотра»
  | { kind: "waking"; text: string; stuck: boolean }   // ждём «готово»; stuck — не удалось или не дождались
  | { kind: "text"; text: string }
  | { kind: "list"; sessions: SessionRow[] };

type Reading = { kind: "none" } | { kind: "loading"; id: string } | { kind: "text"; text: string } | { kind: "transcript"; t: Transcript };

interface PlaceProps {
  api: TeacherApi;
  row: PlaceRow;
  onSignedOut(): void;
  pollMs?: number;
  timers?: Timers;
}

function PlaceSessions(p: PlaceProps) {
  const [list, setList] = useState<Sessions>({ kind: "loading" });
  const [reading, setReading] = useState<Reading>({ kind: "none" });
  const props = useRef(p);
  props.current = p;
  const alive = useRef(true);
  const waking = useRef<AbortController | null>(null);
  const listSeq = useRef(0);                                // ответ старого запроса не перебивает нового
  const readSeq = useRef(0);

  const load = useCallback(async (): Promise<void> => {
    const { api, row } = props.current;
    const my = ++listSeq.current;
    setList({ kind: "loading" });
    const r = await api.sessions(row.place).catch(noAnswer);
    if (!alive.current || my !== listSeq.current) return;
    if (r.status === 401) return props.current.onSignedOut();
    if (asleep(r)) return setList({ kind: "asleep", text: replyText(r) });
    if (r.status !== 200) return setList({ kind: "text", text: replyText(r) });
    const sessions = parseSessions(r.body);
    setList(sessions === null ? { kind: "text", text: T_UNKNOWN } : { kind: "list", sessions });
  }, []);

  useEffect(() => {
    alive.current = true;
    void load();
    return () => {
      alive.current = false;
      waking.current?.abort();
    };
  }, [load]);

  // «Разбудить для просмотра»: обычный запуск места (R102) и ожидание готовности — те же ветки, что у экрана места
  // студента; ручки места кабинета вместо места учётки
  const wake = async (): Promise<void> => {
    if (waking.current !== null) return;
    const { api, row, pollMs, timers } = props.current;
    const ctl = new AbortController();
    waking.current = ctl;
    readSeq.current += 1;                                   // чтение в пути больше не нужно
    listSeq.current += 1;
    setReading({ kind: "none" });
    setList({ kind: "waking", text: T_PLACE_PREPARING, stuck: false });
    // wakeAndWait зовёт только wake и workplace — им и отвечают ручки места кабинета
    const place: Pick<Api, "wake" | "workplace"> = { wake: () => api.wake(row.place), workplace: () => api.place(row.place) };
    let last = T_PLACE_PREPARING;
    const result = await wakeAndWait({
      api: place as Api, pollMs, timers, signal: ctl.signal,
      onView: (v) => {
        last = v.text;
        if (alive.current) setList({ kind: "waking", text: v.text, stuck: false });
      },
    });
    waking.current = null;
    if (!alive.current || result === "aborted") return;
    if (result === "signed_out") return props.current.onSignedOut();
    if (result === "ready") return void load();
    setList({ kind: "waking", text: last, stuck: true });             // не удалось или не дождались — можно ещё раз
  };

  const read = async (id: string): Promise<void> => {
    const { api, row } = props.current;
    const my = ++readSeq.current;
    setReading({ kind: "loading", id });
    const r = await api.session(row.place, id).catch(noAnswer);
    if (!alive.current || my !== readSeq.current) return;
    if (r.status === 401) return props.current.onSignedOut();
    if (asleep(r)) {                                                  // место уснуло, пока список был открыт
      listSeq.current += 1;
      setReading({ kind: "none" });
      return setList({ kind: "asleep", text: replyText(r) });
    }
    if (r.status !== 200) return setReading({ kind: "text", text: replyText(r) });
    const t = parseTranscript(r.body);
    setReading(t === null ? { kind: "text", text: T_UNKNOWN } : { kind: "transcript", t });
  };

  const current = reading.kind === "loading" ? reading.id : reading.kind === "transcript" ? reading.t.session.id : null;
  return (
    <div className="tch-sessions">
      {list.kind === "loading" && <p className="tch-hint">{T_TCH_LOADING}</p>}
      {list.kind === "text" && (
        <>
          <p data-kind="fail" role="alert">{list.text}</p>
          <button type="button" onClick={() => void load()}>{T_TCH_RELOAD_LIST}</button>
        </>
      )}
      {(list.kind === "asleep" || (list.kind === "waking" && list.stuck)) && (
        <>
          <p data-kind={list.kind === "asleep" ? undefined : "fail"}>{list.text}</p>
          <button type="button" className="wake-read" onClick={() => void wake()}>{T_TCH_WAKE}</button>
        </>
      )}
      {list.kind === "waking" && !list.stuck && <p data-kind="wait" aria-live="polite">{list.text}</p>}
      {list.kind === "list" && (
        <>
          <div className="tch-head">
            <span className="tch-hint">{list.sessions.length === 0 ? T_TCH_NO_SESSIONS : ""}</span>
            <button type="button" onClick={() => void load()}>{T_TCH_RELOAD_LIST}</button>
          </div>
          {list.sessions.length > 0 && (
            <div className="sessions">
              {list.sessions.map((s) => (
                <button key={s.id} type="button" data-session-id={s.id} aria-pressed={current === s.id}
                        onClick={() => void read(s.id)}>
                  <span className="session-title">{s.title || T_UNTITLED}</span>{" "}
                  <span className="session-when">{fmtWhen(s.updated ?? s.created)}</span>
                  {s.archived && <>{" "}<span className="session-archived">{T_TCH_ARCHIVED}</span></>}
                </button>
              ))}
            </div>
          )}
        </>
      )}
      {reading.kind === "loading" && <p className="tch-hint">{T_TCH_LOADING}</p>}
      {reading.kind === "text" && <p data-kind="fail" role="alert">{reading.text}</p>}
      {reading.kind === "transcript" && <TeacherReader transcript={reading.t} />}
    </div>
  );
}

type Tab = "sessions" | "agents";

function PlacePanel(p: PlaceProps) {
  const [tab, setTab] = useState<Tab>("sessions");
  const [agentsOpened, setAgentsOpened] = useState(false);   // вкладка агентов смонтирована с первого открытия
  const show = (t: Tab): void => {
    setTab(t);
    if (t === "agents") setAgentsOpened(true);
  };
  return (
    <section className="tch-place" aria-label={T_TCH_PLACE_TITLE(p.row.place, p.row.login ?? "")}>
      <h3>{T_TCH_PLACE_TITLE(p.row.place, p.row.login ?? "")}</h3>
      <nav className="tabs" aria-label={T_TCH_PLACE_TABS}>
        <button type="button" aria-pressed={tab === "sessions"} onClick={() => show("sessions")}>{T_TCH_TAB_SESSIONS}</button>
        <button type="button" aria-pressed={tab === "agents"} onClick={() => show("agents")}>{T_TCH_TAB_AGENTS}</button>
      </nav>
      {/* обе вкладки живут, пока место открыто: переход не спрашивает место и журнал заново */}
      <div hidden={tab !== "sessions"}><PlaceSessions {...p} /></div>
      {agentsOpened && (
        <div hidden={tab !== "agents"}>
          <TeacherAgents api={p.api} nn={p.row.place} onSignedOut={p.onSignedOut} />
        </div>
      )}
    </section>
  );
}

// --- страница ---

type Load = { kind: "loading" } | { kind: "text"; text: string } | { kind: "ready"; overview: Overview };

export function TeacherPage(p: TeacherPageProps) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [open, setOpen] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const props = useRef(p);
  props.current = p;
  const alive = useRef(true);
  const seq = useRef(0);                                     // ответ старого обзора не перебивает нового
  const signedOut = useRef(false);

  const signOut = useCallback((): void => {
    if (signedOut.current || !alive.current) return;
    signedOut.current = true;
    props.current.onSignedOut();
  }, []);

  // обзор; прежний остаётся на экране, пока новый в пути
  const fetchOverview = useCallback(async (): Promise<void> => {
    const my = ++seq.current;
    const r = await props.current.api.overview().catch(noAnswer);
    if (!alive.current || my !== seq.current) return;
    if (r.status === 401) return signOut();
    if (r.status !== 200) return setLoad({ kind: "text", text: replyText(r) });
    const overview = parseOverview(r.body);
    setLoad(overview === null ? { kind: "text", text: T_UNKNOWN } : { kind: "ready", overview });
  }, [signOut]);

  useEffect(() => {
    alive.current = true;
    void fetchOverview();
    return () => { alive.current = false; };
  }, [fetchOverview]);

  // «Обновить» и после изменения долей: обзор и журнал заново (журнал сам читается и при открытии)
  const refresh = (): void => {
    setReloadKey((n) => n + 1);
    void fetchOverview();
  };

  const overview = load.kind === "ready" ? load.overview : null;
  const row = overview?.places.find((r) => r.place === open && r.login !== null) ?? null;
  return (
    <section className="teacher">
      <div className="tch-head">
        <h2>{T_TCH_TITLE}</h2>
        <button type="button" onClick={refresh}>{T_TCH_REFRESH}</button>
      </div>
      {load.kind === "loading" && <p className="tch-hint">{T_TCH_LOADING}</p>}
      {load.kind === "text" && <p data-kind="fail" role="alert">{load.text}</p>}
      {overview !== null && (
        <>
          <BudgetLine overview={overview} />
          <div className="tch-main">
            <PlacesTable overview={overview} open={row?.place ?? null} onOpen={setOpen} />
            {row !== null && (
              <PlacePanel key={row.place} api={p.api} row={row} onSignedOut={signOut} pollMs={p.pollMs} timers={p.timers} />
            )}
          </div>
          <TeacherShares api={p.api} overview={overview} onChanged={refresh} onSignedOut={signOut} />
        </>
      )}
      <TeacherJournal api={p.api} reloadKey={reloadKey} onSignedOut={signOut} />
    </section>
  );
}
