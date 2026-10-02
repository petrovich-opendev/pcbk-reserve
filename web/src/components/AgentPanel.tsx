// Панель «Агенты» над списком разговоров (Д7-R65): встроенные агенты, подзаголовок «Мои агенты» и свои агенты места.
// Только отображение: список и выбор — у Chat. Примитивы — обычные кнопки, без Radix (Д6-R41: CSP без <style>).
// Строка агента — кнопка с названием и назначением; pending и broken — неактивны, с подписью состояния; агент
// текущего разговора — aria-current. Разметка — договор с Б4 (preflight П12): section.agents,
// button[data-agent][data-state][aria-current] с span.agent-title и span.agent-purpose.
// items: undefined — ответа ещё нет (только заголовок), null — ответ не 200 (строка «недоступен»).
import { Fragment } from "react";
import type { AgentItem } from "../agents";
import { T_AGENT_BROKEN, T_AGENT_PENDING, T_AGENTS, T_AGENTS_DOWN, T_AGENTS_EMPTY, T_MY_AGENTS } from "../texts";

export interface AgentPanelProps {
  items: AgentItem[] | null | undefined;
  current: string;                        // агент текущего разговора ("" — ещё неизвестен)
  onPick(name: string): void;             // клик по ready — новый разговор с этим агентом
}

const STATE_TEXT: Readonly<Record<AgentItem["state"], string>> = { ready: "", pending: T_AGENT_PENDING, broken: T_AGENT_BROKEN };

function AgentRow(p: { agent: AgentItem; current: boolean; onPick(name: string): void }) {
  const a = p.agent;
  const ready = a.state === "ready";
  // пробелы между частями — для имени кнопки при чтении с экрана; в колонке flex они не видны
  return (
    <button type="button" className="agent" data-agent={a.name} data-state={a.state}
            aria-current={p.current ? "true" : undefined} disabled={!ready}
            onClick={() => { if (ready) p.onPick(a.name); }}>
      <span className="agent-title">{a.title}</span>{" "}
      <span className="agent-purpose">{a.purpose}</span>
      {!ready && <Fragment>{" "}<span className="agent-state">{STATE_TEXT[a.state]}</span></Fragment>}
    </button>
  );
}

export function AgentPanel(p: AgentPanelProps) {
  const row = (a: AgentItem) =>
    <AgentRow key={`${a.kind}:${a.name}`} agent={a} current={a.name === p.current} onPick={p.onPick} />;
  let body;
  if (p.items === undefined) body = null;
  else if (p.items === null) body = <p className="agents-note">{T_AGENTS_DOWN}</p>;
  else {
    const own = p.items.filter((a) => a.kind === "own");
    body = (
      <>
        {p.items.filter((a) => a.kind === "builtin").map(row)}
        <h3 className="agents-sub">{T_MY_AGENTS}</h3>
        {own.length === 0 ? <p className="agents-note">{T_AGENTS_EMPTY}</p> : own.map(row)}
      </>
    );
  }
  return (
    <section className="agents" aria-label={T_AGENTS}>
      <h2 className="agents-head">{T_AGENTS}</h2>
      {body}
    </section>
  );
}
