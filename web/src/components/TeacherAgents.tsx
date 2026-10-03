// Агенты места в кабинете (Д10-R103, R106): карточки из учёта агентов службы — название, узел, назначение, инструменты,
// теги, состояние — и «Показать инструкцию»: текст файла агента в <pre>, только текстом. Место для этого не нужно и не
// будится: учёт и каталог агентов — у службы. Встроенных агентов образа здесь нет — они одинаковы у всех.
// 401 — на вход; прочие отказы — текст ответа (tchReplyText). Инструкция не прочлась — текст отказа и снова «Показать
// инструкцию» (повтор); «Скрыть» — только у показанной (ревью А4, m-7).
import { useEffect, useRef, useState } from "react";
import type { Reply } from "../api";
import { label, parseAgents, parseAgentText, tchReplyText, type AgentCard } from "../teacher";
import type { TeacherApi } from "../teacherApi";
import { AGENT_STATE_LABELS, T_TCH_HIDE_TEXT, T_TCH_LOADING, T_TCH_NO_AGENTS, T_TCH_SHOW_TEXT, T_TCH_UNKNOWN,
         TCH_AGENT_LABELS as L, TOOL_LABELS } from "../texts";

export interface TeacherAgentsProps {
  api: TeacherApi;
  nn: string;
  onSignedOut(): void;
}

type List = { kind: "loading" } | { kind: "text"; text: string } | { kind: "list"; agents: AgentCard[] };
type FileView = { kind: "loading" } | { kind: "text"; text: string } | { kind: "file"; text: string };

const noAnswer = (): Reply => ({ status: 0, body: null });
// инструменты в учёте — без префикса MCP (tag_now, agents/draft.py), в TOOL_LABELS — с ним (pcbk_tag_now); незнакомый — именем
const toolName = (t: string): string => {
  const key = Object.hasOwn(TOOL_LABELS, t) ? t : `pcbk_${t}`;
  return Object.hasOwn(TOOL_LABELS, key) ? TOOL_LABELS[key]! : t;
};

function Card({ api, nn, card, onSignedOut }: TeacherAgentsProps & { card: AgentCard }) {
  const [file, setFile] = useState<FileView | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const toggle = async (): Promise<void> => {
    if (file?.kind === "loading") return;
    if (file?.kind === "file") return setFile(null);    // «Скрыть инструкцию»
    setFile({ kind: "loading" });                         // первое чтение или повтор после отказа
    const r = await api.agent(nn, card.name).catch(noAnswer);
    if (!alive.current) return;
    if (r.status === 401) return onSignedOut();
    if (r.status !== 200) return setFile({ kind: "text", text: tchReplyText(r) });
    const text = parseAgentText(r.body);
    setFile(text === null ? { kind: "text", text: T_TCH_UNKNOWN } : { kind: "file", text });
  };

  return (
    <article className="agent-card" data-agent={card.name}>
      <h5>{card.title}</h5>
      <dl className="agent-facts">
        <div><dt>{L.node}</dt><dd>{card.node}</dd></div>
        <div><dt>{L.purpose}</dt><dd>{card.purpose}</dd></div>
        <div><dt>{L.tools}</dt><dd>{card.tools.map(toolName).join(", ") || "—"}</dd></div>
        <div><dt>{L.tags}</dt><dd>{card.tags.join(", ") || "—"}</dd></div>
        <div><dt>{L.state}</dt><dd>{label(AGENT_STATE_LABELS, card.state)}</dd></div>
      </dl>
      <button type="button" disabled={file?.kind === "loading"} onClick={() => void toggle()}>
        {file?.kind === "file" ? T_TCH_HIDE_TEXT : T_TCH_SHOW_TEXT}
      </button>
      {file?.kind === "text" && <p data-kind="fail" role="alert">{file.text}</p>}
      {file?.kind === "file" && <pre className="agent-file">{file.text}</pre>}
    </article>
  );
}

export function TeacherAgents(p: TeacherAgentsProps) {
  const [list, setList] = useState<List>({ kind: "loading" });
  const props = useRef(p);
  props.current = p;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const { api, nn } = props.current;
      const r = await api.agents(nn).catch(noAnswer);
      if (cancelled) return;
      if (r.status === 401) return props.current.onSignedOut();
      if (r.status !== 200) return setList({ kind: "text", text: tchReplyText(r) });
      const agents = parseAgents(r.body);
      setList(agents === null ? { kind: "text", text: T_TCH_UNKNOWN } : { kind: "list", agents });
    })();
    return () => { cancelled = true; };
  }, []);

  if (list.kind === "loading") return <p className="tch-hint">{T_TCH_LOADING}</p>;
  if (list.kind === "text") return <p data-kind="fail" role="alert">{list.text}</p>;
  if (list.agents.length === 0) return <p className="tch-hint">{T_TCH_NO_AGENTS}</p>;
  return (
    <div className="agent-cards">
      {list.agents.map((a) => <Card key={a.name} api={p.api} nn={p.nn} card={a} onSignedOut={p.onSignedOut} />)}
    </div>
  );
}
