// Список разговоров слева (Д6-R52): «Новый разговор» и разговоры без архивных — ThreadListPrimitive (New, Items),
// ThreadListItemPrimitive (Root, Trigger, Title, Archive), без Radix-меню. У строки — data-session-id (id разговора
// OpenCode, по нему её находят тесты) и всегда видные кнопки (не по наведению):
//   - имя разговора (Trigger) — открыть;
//   - «Переименовать» — имя сменяет поле «Новое имя разговора» (input[name=title]) с нынешним именем, в фокусе и
//     выделенным: ввод заменяет имя целиком. Enter или «Сохранить» —
//     ThreadListItemRuntime.rename(имя), Escape или «Отмена» — без вызова. Имя — 1–MAX_TITLE знаков после среза
//     пробелов по краям; пустое и длиннее не уходят, под полем — подсказка;
//   - «В архив» — ThreadListItemPrimitive.Archive (открытый разговор библиотека сначала меняет на новый).
// «Из архива» и «Удалить» нет: time.archived = null место 1.18.33 принимает с 200, но архив не снимает; удаление
// закрыто шлюзом. Имя разговора даёт OpenCode; адаптер кладёт session.updated в разговор, а не в список, поэтому
// список перечитывается, когда имя открытого разговора (useOpenCodeSession) отличается от имени в списке, — только
// при смене имени, не на каждое session.updated (preflight Ф13).
import {
  ThreadListItemPrimitive, ThreadListPrimitive, useAuiState, type AssistantRuntime,
} from "@assistant-ui/react";
import { useOpenCodeSession } from "@assistant-ui/react-opencode";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  MAX_TITLE, T_ARCHIVE, T_NEW_THREAD, T_RENAME, T_RENAME_CANCEL, T_RENAME_FIELD, T_RENAME_HINT, T_RENAME_SAVE,
  T_THREADS, T_UNTITLED,
} from "../texts";

export interface ThreadSidebarProps {
  runtime: AssistantRuntime;
  onError?(error: unknown): void;         // отказ переименования — строкой ошибки чата
}

interface RowContext {
  runtime: AssistantRuntime;
  onError(error: unknown): void;
}

const Row = createContext<RowContext | null>(null);

// Имя открытого разговора от OpenCode (session — useOpenCodeSession()) → список, если там другое (Ф13). Сверка — после
// загрузки списка, что уже в пути: иначе ответ списка, запрошенный до смены имени, затёр бы имя уже после сверки, а
// пока прежний ответ в пути, нового запроса нет. Ответ пришёл, когда открыт уже другой разговор, — не перечитывать.
// Своё переименование список не перечитывает: имя в строке уже новое, а session.updated приносит то же имя.
export function useTitleFollowsSession(runtime: AssistantRuntime,
                                       session: { id: string; title: string } | null | undefined): void {
  const sessionId = session?.id;
  const title = session?.title;
  useEffect(() => {
    if (!sessionId || !title) return;
    let stale = false;
    void runtime.threads.getLoadThreadsPromise().catch(() => undefined).then(() => {
      if (stale) return;                                            // имя сменилось ещё раз — сверит следующий запуск
      const main = runtime.threads.mainItem.getState();
      if ((main.externalId ?? main.remoteId) !== sessionId) return; // открыт уже другой разговор
      if (main.title === title) return;                             // список уже знает это имя
      void runtime.threads.reload().catch(() => undefined);         // сбой списка библиотека пишет сама
    });
    return () => { stale = true; };
  }, [runtime, sessionId, title]);
}

function ThreadRow() {
  const ctx = useContext(Row)!;
  const item = useAuiState((s) => s.threadListItem);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [hint, setHint] = useState(false);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const startEdit = (): void => {
    setDraft(item.title ?? "");
    setHint(false);
    setEditing(true);
  };
  const cancel = (): void => {
    setEditing(false);
    setHint(false);
  };
  const save = (): void => {
    const title = draft.trim();
    if (title.length < 1 || title.length > MAX_TITLE) {       // пустое и длиннее не отправляются
      setHint(true);
      return;
    }
    setEditing(false);
    setHint(false);
    // имя в строке меняется сразу; отказ шлюза возвращает прежнее и уходит строкой ошибки чата
    ctx.runtime.threads.getItemById(item.id).rename(title).catch((error: unknown) => {
      if (mounted.current) ctx.onError(error);
    });
  };
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === "Enter") {
      e.preventDefault();
      save();
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancel();
    }
  };

  return (
    <ThreadListItemPrimitive.Root className="thread-item" data-session-id={item.externalId ?? item.remoteId}>
      {editing ? (
        <span className="thread-edit">
          <input className="thread-title-input" name="title" aria-label={T_RENAME_FIELD} value={draft}
                 maxLength={MAX_TITLE} autoFocus onFocus={(e) => e.currentTarget.select()}
                 onChange={(e) => setDraft(e.target.value)} onKeyDown={onKeyDown} />
          <button type="button" onClick={save}>{T_RENAME_SAVE}</button>
          <button type="button" onClick={cancel}>{T_RENAME_CANCEL}</button>
        </span>
      ) : (
        <>
          <ThreadListItemPrimitive.Trigger className="thread-open">
            <ThreadListItemPrimitive.Title fallback={T_UNTITLED} />
          </ThreadListItemPrimitive.Trigger>
          <span className="thread-actions">
            <button type="button" className="thread-rename" onClick={startEdit}>{T_RENAME}</button>
            <ThreadListItemPrimitive.Archive className="thread-archive">{T_ARCHIVE}</ThreadListItemPrimitive.Archive>
          </span>
        </>
      )}
      {hint && <p className="thread-hint" aria-live="polite">{T_RENAME_HINT}</p>}
    </ThreadListItemPrimitive.Root>
  );
}

// постоянная функция: ThreadListPrimitive.Items сравнивает её по ссылке (memo)
const renderRow = () => <ThreadRow />;

export function ThreadSidebar(p: ThreadSidebarProps) {
  const onError = useRef(p.onError);
  onError.current = p.onError;
  const ctx = useMemo<RowContext>(() => ({
    runtime: p.runtime,
    onError: (error) => onError.current?.(error),
  }), [p.runtime]);
  useTitleFollowsSession(p.runtime, useOpenCodeSession());
  return (
    <nav className="threads" aria-label={T_THREADS}>
      <ThreadListPrimitive.New className="new-thread">{T_NEW_THREAD}</ThreadListPrimitive.New>
      <div className="thread-items">
        <Row.Provider value={ctx}>
          <ThreadListPrimitive.Items>{renderRow}</ThreadListPrimitive.Items>
        </Row.Provider>
      </div>
    </nav>
  );
}
