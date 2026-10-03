// «Журнал действий» в кабинете (Д10-R101, I-1; R106): последние строки журнала действий преподавателя (не больше 50,
// новые первыми) — время (местное браузера), кто, действие словами, место, итог; у перевода доли — места и центы «было →
// стало». Текстов разговоров и агентов в журнале нет (R99). Чтение журнала само в журнал не пишется. Список — .journal li.
// Читается при открытии, по кнопке «Обновить журнал» и при каждом новом обзоре (reloadKey): после перевода доли строка
// уже в журнале. 401 — на вход; прочие отказы — текст ответа.
import { useEffect, useRef, useState } from "react";
import type { Reply } from "../api";
import { fmtCents, fmtWhen, label, parseJournal, tchReplyText, type JournalRow } from "../teacher";
import type { TeacherApi } from "../teacherApi";
import { ACTION_LABELS, OUTCOME_LABELS, T_TCH_JOURNAL, T_TCH_JOURNAL_EMPTY, T_TCH_JOURNAL_PLACE, T_TCH_JOURNAL_RELOAD,
         T_TCH_JOURNAL_SHARE, T_TCH_LOADING, T_TCH_UNKNOWN } from "../texts";

export interface TeacherJournalProps {
  api: TeacherApi;
  reloadKey: number;
  onSignedOut(): void;
}

type View = { kind: "loading" } | { kind: "text"; text: string } | { kind: "rows"; rows: JournalRow[] };

const noAnswer = (): Reply => ({ status: 0, body: null });

function Row({ row }: { row: JournalRow }) {
  const parts = [label(ACTION_LABELS, row.action)];
  if (row.place !== null) parts.push(T_TCH_JOURNAL_PLACE(row.place));
  for (const [nn, was, now] of row.detail ?? []) parts.push(T_TCH_JOURNAL_SHARE(nn, fmtCents(was), fmtCents(now)));
  return (
    <li data-action={row.action} data-outcome={row.outcome}>
      <span className="journal-when">{fmtWhen(row.at)}</span>
      <span className="journal-who">{row.login}</span>
      <span className="journal-what">{parts.join("; ")}</span>
      <span className="journal-outcome">{label(OUTCOME_LABELS, row.outcome)}</span>
    </li>
  );
}

export function TeacherJournal(p: TeacherJournalProps) {
  const [view, setView] = useState<View>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const props = useRef(p);
  props.current = p;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const r = await props.current.api.journal().catch(noAnswer);
      if (cancelled) return;
      if (r.status === 401) return props.current.onSignedOut();
      if (r.status !== 200) return setView({ kind: "text", text: tchReplyText(r) });
      const rows = parseJournal(r.body);
      setView(rows === null ? { kind: "text", text: T_TCH_UNKNOWN } : { kind: "rows", rows });
    })();
    return () => { cancelled = true; };
  }, [p.reloadKey, attempt]);

  return (
    <section className="tch-journal" aria-label={T_TCH_JOURNAL}>
      <div className="tch-head">
        <h3>{T_TCH_JOURNAL}</h3>
        <button type="button" onClick={() => setAttempt((n) => n + 1)}>{T_TCH_JOURNAL_RELOAD}</button>
      </div>
      {view.kind === "loading" && <p className="tch-hint">{T_TCH_LOADING}</p>}
      {view.kind === "text" && <p data-kind="fail" role="alert">{view.text}</p>}
      {view.kind === "rows" && view.rows.length === 0 && <p className="tch-hint">{T_TCH_JOURNAL_EMPTY}</p>}
      {view.kind === "rows" && view.rows.length > 0 && (
        <ul className="journal">{view.rows.map((row) => <Row key={row.id} row={row} />)}</ul>
      )}
    </section>
  );
}
