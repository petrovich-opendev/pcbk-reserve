// Вкладка «Уроки» (Д15а, DESIGN): список уроков и текст выбранного — список слева (≥ 900 px) / сверху (уже, m6
// chat.css). Данные — GET /pcbk/api/lessons при открытии вкладки (первом монтировании — она, как «Калькуляторы» и
// «События», открывается раз и остаётся смонтированной, без опроса по таймеру); первый урок в списке открывается
// сам. Выбор другого урока — GET /pcbk/api/lessons/{id} (DESIGN: сервер читает файл на каждый запрос, без кеша —
// то же здесь: выбор того же урока снова перечитывает его). Отказ ручки (индекса или текста урока) — один общий
// текст T_LESSONS_ERROR: причина (401 здесь не разбирается отдельно, 503, сеть, чужая форма ответа) студенту не
// важна. 401 — onSignedOut, как у «Событий». Все поля списка — только текстом; текст урока — markdown (LessonMarkdown).
import { useEffect, useRef, useState } from "react";
import type { Api, Reply } from "../api";
import { parseLesson, parseLessonIndex, type Lesson, type LessonMeta } from "../lessons";
import { T_LESSONS_EMPTY, T_LESSONS_ERROR, T_LESSONS_TITLE } from "../texts";
import { LessonMarkdown } from "./LessonMarkdown";

export interface LessonsPageProps {
  api: Api;
  onSignedOut(): void;
}

const noAnswer = (): Reply => ({ status: 0, body: null });

type IndexState =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "ready"; metas: LessonMeta[] };

type LessonState =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "data"; lesson: Lesson };

export function LessonsPage(p: LessonsPageProps) {
  const [index, setIndex] = useState<IndexState>({ kind: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [lesson, setLesson] = useState<LessonState | null>(null);   // null — урок ещё не выбирался (пустой индекс)
  const props = useRef(p);
  props.current = p;
  const alive = useRef(true);
  const requestedId = useRef<string | null>(null);    // последний запрошенный урок — старый ответ не перебивает новый

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const select = (id: string): void => {
    setSelected(id);
    requestedId.current = id;
    setLesson({ kind: "loading" });
    void (async () => {
      const r = await props.current.api.lesson(id).catch(noAnswer);
      if (!alive.current || requestedId.current !== id) return;   // ушли на другой урок — ответ уже не нужен
      if (r.status === 401) { props.current.onSignedOut(); return; }
      if (r.status !== 200) { setLesson({ kind: "error" }); return; }
      const parsed = parseLesson(r.body);
      setLesson(parsed === null ? { kind: "error" } : { kind: "data", lesson: parsed });
    })();
  };

  // загрузка индекса при открытии вкладки (первом монтировании) — без опроса по таймеру, как «События»
  useEffect(() => {
    void (async () => {
      const r = await props.current.api.lessons().catch(noAnswer);
      if (!alive.current) return;
      if (r.status === 401) { props.current.onSignedOut(); return; }
      if (r.status !== 200) { setIndex({ kind: "error" }); return; }
      const metas = parseLessonIndex(r.body);
      if (metas === null) { setIndex({ kind: "error" }); return; }
      setIndex({ kind: "ready", metas });
      if (metas.length > 0) select(metas[0]!.id);         // первый урок — сам
    })();
  }, []);

  return (
    <section className="lessons">
      <div className="lessons-head"><h2>{T_LESSONS_TITLE}</h2></div>
      {index.kind === "error" && <p className="lessons-msg" role="alert">{T_LESSONS_ERROR}</p>}
      {index.kind === "ready" && (
        index.metas.length === 0 ? (
          <p className="lessons-note">{T_LESSONS_EMPTY}</p>
        ) : (
          <div className="lessons-body">
            <nav className="lessons-list" aria-label={T_LESSONS_TITLE}>
              {index.metas.map((m) => (
                <button key={m.id} type="button" aria-pressed={m.id === selected} onClick={() => select(m.id)}>
                  <span className="lessons-item-title">{m.title}</span>
                  <span className="lessons-item-summary">{m.summary}</span>
                </button>
              ))}
            </nav>
            <div className="lessons-content">
              {lesson?.kind === "error" && <p className="lessons-msg" role="alert">{T_LESSONS_ERROR}</p>}
              {lesson?.kind === "data" && <LessonMarkdown markdown={lesson.lesson.markdown} />}
            </div>
          </div>
        )
      )}
    </section>
  );
}
