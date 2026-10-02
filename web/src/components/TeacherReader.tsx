// Разговор студента в кабинете (Д10-R106): только чтение. Сверху — постоянная строка «Только чтение…»; сообщения —
// div.msg[data-role], подпись «Студент» или «Агент «…»» и время; текст — элементом <p className="plain"> как есть (без
// markdown и HTML: тексты студента и модели могут нести разметку, она видна знаками); вызов инструмента — строка «Запрос к
// данным: … — …» и свёрнутые вход и вывод в <details><pre>; ошибка ответа — строкой data-kind="fail". Полей ввода,
// textarea и кнопок здесь нет вовсе: писать в разговор студента кабинет не может (сервер и не даст).
import type { Message, Part, Transcript } from "../teacher";
import { fmtWhen } from "../teacher";
import { BUILTIN_AGENT_TITLES, T_TCH_AGENT, T_TCH_AGENT_NONAME, T_TCH_CLIPPED, T_TCH_NO_MESSAGES, T_TCH_READ_ONLY,
         T_TCH_STUDENT, T_TCH_TOOL_UNKNOWN, T_TOOL_DONE, T_TOOL_ERROR, T_TOOL_LINE, T_TOOL_RUNNING, T_UNTITLED,
         TOOL_LABELS } from "../texts";

// состояние вызова (R102: pending|running|completed|error, иначе unknown) → слово строки, как в чате
const TOOL_STATES: Readonly<Record<string, string>> = {
  pending: T_TOOL_RUNNING, running: T_TOOL_RUNNING, completed: T_TOOL_DONE, error: T_TOOL_ERROR,
};

// подпись ассистента: встроенный агент образа — своим названием, свой агент студента — именем
function who(m: Message): string {
  if (m.role === "user") return T_TCH_STUDENT;
  if (m.agent === null) return T_TCH_AGENT_NONAME;
  return T_TCH_AGENT(BUILTIN_AGENT_TITLES.get(m.agent) ?? m.agent);
}

function PartView({ part }: { part: Part }) {
  if (part.kind === "text") return <p className="plain">{part.text}</p>;
  const what = Object.hasOwn(TOOL_LABELS, part.tool) ? TOOL_LABELS[part.tool]! : part.tool;
  const state = TOOL_STATES[part.status] ?? T_TCH_TOOL_UNKNOWN;
  return (
    <details className="tool" data-state={part.status === "error" ? "error" : part.status === "completed" ? "done" : "running"}>
      <summary>{T_TOOL_LINE(what, state)}</summary>
      <pre data-part="args">{part.input}</pre>
      {part.output !== "" && <pre data-part="output">{part.output}</pre>}
    </details>
  );
}

export function TeacherReader({ transcript }: { transcript: Transcript }) {
  const { session, messages, clipped } = transcript;
  return (
    <section className="transcript" aria-label={session.title || T_UNTITLED}>
      <p className="read-only">{T_TCH_READ_ONLY}</p>
      <h4>{session.title || T_UNTITLED}</h4>
      {messages.length === 0 && <p className="tch-hint">{T_TCH_NO_MESSAGES}</p>}
      {messages.map((m, i) => (
        <div key={i} className="msg" data-role={m.role}>
          <p className="msg-who">
            <span>{who(m)}</span>{" "}
            <span className="msg-when">{fmtWhen(m.at)}</span>
          </p>
          {m.parts.map((p, j) => <PartView key={j} part={p} />)}
          {m.error !== null && <p className="msg-error" data-kind="fail">{m.error}</p>}
        </div>
      ))}
      {clipped && <p className="tch-clipped">{T_TCH_CLIPPED}</p>}
    </section>
  );
}
