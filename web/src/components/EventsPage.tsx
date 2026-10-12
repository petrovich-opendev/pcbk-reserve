// Вкладка «События» (Д13, DESIGN п.7): только у студента. Данные — GET /pcbk/api/events при открытии вкладки
// (первом монтировании — она, как «Калькуляторы», открывается раз и остаётся смонтированной) и по кнопке
// «Обновить»; опроса по таймеру нет. Все поля — только текстом (без dangerouslySetInnerHTML и разбора markdown).
// Отказ ручки — тем же способом, что у панели агентов (AgentPanel: ответ не 200 — текст) и калькуляторов
// (CalcPage: replyText с запасным текстом, 401 — onSignedOut).
import { useEffect, useRef, useState } from "react";
import { replyText, type Api, type Reply } from "../api";
import { fmtNum, fmtTime } from "../calc";
import { EVENT_KIND_LABELS, EVENTS_COLUMNS as L, T_EVENTS_EMPTY, T_EVENTS_REFRESH, T_EVENTS_TITLE, T_UNKNOWN }
  from "../texts";
import { fmtLocal, parseEvents, type EventItem } from "../events";

export interface EventsPageProps {
  api: Api;
  onSignedOut(): void;
}

const noAnswer = (): Reply => ({ status: 0, body: null });

type State = { kind: "loading" } | { kind: "error"; text: string } | { kind: "data"; items: EventItem[] };

// «значение и задание»: нет ни того ни другого — «—»; иначе числа службы как есть (fmtNum — та же форма, что
// у вкладки «Калькуляторы»)
function valueCell(e: EventItem): string {
  if (e.value === null && e.setpoint === null) return "—";
  return `${fmtNum(e.value)} / ${fmtNum(e.setpoint)}`;
}

// период — обе границы или «—»; время каждой границы — своим поясом (fmtTime, как в «Калькуляторах»), не браузера
function periodCell(e: EventItem): string {
  if (e.periodStart === null && e.periodEnd === null) return "—";
  return `${fmtTime(e.periodStart)} — ${fmtTime(e.periodEnd)}`;
}

function Row({ e }: { e: EventItem }) {
  return (
    <tr>
      <td>{fmtLocal(e.ts)}</td>
      <td>{e.agentTitle}</td>
      <td>{EVENT_KIND_LABELS[e.kind] ?? e.kind}</td>
      <td>{e.title}</td>
      <td>{e.tags.join(", ")}</td>
      <td>{periodCell(e)}</td>
      <td>{valueCell(e)}</td>
      <td>{e.text}</td>
    </tr>
  );
}

export function EventsPage(p: EventsPageProps) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const props = useRef(p);
  props.current = p;
  const alive = useRef(true);
  const inFlight = useRef(false);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const load = async (): Promise<void> => {
    if (inFlight.current) return;
    inFlight.current = true;
    setState({ kind: "loading" });
    const r = await props.current.api.events().catch(noAnswer);
    inFlight.current = false;
    if (!alive.current) return;
    if (r.status === 401) { props.current.onSignedOut(); return; }
    if (r.status !== 200) {
      setState({ kind: "error", text: replyText(r) });
      return;
    }
    const items = parseEvents(r.body);
    setState(items === null ? { kind: "error", text: T_UNKNOWN } : { kind: "data", items });
  };

  // загрузка при открытии вкладки (первом монтировании) — без опроса по таймеру (DESIGN п.7)
  useEffect(() => { void load(); }, []);

  return (
    <section className="events">
      <div className="events-head">
        <h2>{T_EVENTS_TITLE}</h2>
        <button type="button" onClick={() => void load()} disabled={state.kind === "loading"}>{T_EVENTS_REFRESH}</button>
      </div>
      {state.kind === "error" && <p className="events-msg" role="alert">{state.text}</p>}
      {state.kind === "data" && (
        state.items.length === 0 ? (
          <p className="events-note">{T_EVENTS_EMPTY}</p>
        ) : (
          <table className="events-table">
            <thead>
              <tr>
                <th>{L.time}</th><th>{L.agent}</th><th>{L.kind}</th><th>{L.title}</th><th>{L.tags}</th>
                <th>{L.period}</th><th>{L.value}</th><th>{L.text}</th>
              </tr>
            </thead>
            <tbody>
              {state.items.map((e) => <Row key={e.id} e={e} />)}
            </tbody>
          </table>
        )
      )}
    </section>
  );
}
