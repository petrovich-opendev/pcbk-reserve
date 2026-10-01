// Страница чата (Д6-R39, R42, R44, R49). Фазы: загрузка → ошибка /me | преподаватель | предупреждение →
// рабочее место → чат; из чата — «место спит» (кнопка «Разбудить», сам не будит: Д5-R27). Предупреждение при
// первом входе стоит между /me и местом: пока студент не нажал «Понятно», место не будится и чат не монтируется.
// Поля notice нет (core Д5) — как null (== null); номер без текста в NOTICES — ошибка без «Понятно» (Ф14).
// Конец сессии в любой фазе и «Выйти» — на /login. Шапка — во всех фазах, кроме загрузки. App помнит последний
// открытый разговор (onThreadIdChange) и после «Разбудить» монтирует чат с ним.
// Молчание в браузере (R51): старт отмечается загрузчику (window.__pcbkStarted) при монтировании App, ещё до ответа
// /me — экран места или предупреждения дольше 20 с не дают ложного boot_failed (preflight Ф9); без загрузчика (edge
// до Д6б, откат) App работает. Чат — внутри ErrorBoundary: сбой отрисовки — отчёт ui_crash и «Перезапустить чат».
// Отчёты чата (turn_silent, stream_down) уходят через api.report — только от нынешнего чата и не при уходе на вход.
import { useCallback, useEffect, useRef, useState, type ComponentType } from "react";
import { replyText, type Api, type ReportKind, type Reply } from "./api";
import { Chat, type ChatProps } from "./components/Chat";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Header } from "./components/Header";
import { NoticeScreen } from "./components/NoticeScreen";
import { WorkplaceScreen } from "./components/WorkplaceScreen";
import { startStatusPolling } from "./status";
import { NOTICES, T_ASLEEP, T_LOADING, T_NOTICE_UNKNOWN, T_RETRY, T_TEACHER, T_UNKNOWN, T_WAKE } from "./texts";
import { placeView, type Timers } from "./workplace";

export interface AppProps {
  api: Api;
  navigate(url: string): void;
  ChatImpl?: ComponentType<ChatProps>;
  startPolling?: typeof startStatusPolling;
  pollMs?: number;              // опрос места — для тестов (preflight M11)
  timers?: Timers;
}

type Phase =
  | { kind: "loading" }
  | { kind: "error"; text: string }
  | { kind: "teacher" }
  | { kind: "notice"; notice: string; text: string }   // предупреждение при первом входе (R49)
  | { kind: "workplace" }
  | { kind: "asleep" }
  | { kind: "chat"; mount: number; sessionId: string | undefined }
  | { kind: "leaving" };                                 // уход на /login: чат снят, поток закрыт

// notice — как пришло: номер редакции, null — подтверждено, undefined — поля нет (core Д5)
interface Who { login: string; role: "student" | "teacher"; place: string | null; notice: unknown }

declare global {
  interface Window {
    __pcbkStarted?: () => void;           // загрузчик boot.js (R51): чат запустился, boot_failed больше не нужен
  }
}

const LOGIN_URL = "/login";

const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object";

// ответ /me → учётка; null — ответ не той формы
function parseMe(r: Reply): Who | null {
  if (r.status !== 200 || !isObject(r.body)) return null;
  const { login, role, place, notice } = r.body;
  if (typeof login !== "string" || (role !== "student" && role !== "teacher")) return null;
  return { login, role, place: typeof place === "string" ? place : null, notice };
}

const noAnswer = (): Reply => ({ status: 0, body: null });

export function App(p: AppProps) {
  const ChatImpl = p.ChatImpl ?? Chat;
  const startPolling = p.startPolling ?? startStatusPolling;
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [who, setWho] = useState<Who | null>(null);
  const [attempt, setAttempt] = useState(0);            // «Повторить» после сбоя /me
  const props = useRef(p);
  props.current = p;
  const lastSession = useRef<string | undefined>(undefined);
  const mounts = useRef(0);                             // номер монтирования чата
  const leaving = useRef(false);
  const started = useRef(false);

  // старт — при первом показе App (фаза «загрузка»), один раз; функции нет — загрузчика нет, ничего не делаем
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    window.__pcbkStarted?.();
  }, []);

  // отчёт браузера: ответ не нужен, ошибка не повторяется; при уходе на вход сессии уже нет
  const report = useCallback((kind: ReportKind): void => {
    if (leaving.current) return;
    void props.current.api.report(kind).catch(() => undefined);
  }, []);

  const goLogin = useCallback((): void => {
    if (leaving.current) return;
    leaving.current = true;
    setPhase({ kind: "leaving" });
    props.current.navigate(LOGIN_URL);
  }, []);

  const openChat = useCallback((): void => {
    mounts.current += 1;
    setPhase({ kind: "chat", mount: mounts.current, sessionId: lastSession.current });
  }, []);

  // место студента: готово — чат, иначе экран места (он и будит); stale — ответ уже никому не нужен
  const toPlace = useCallback(async (stale: () => boolean): Promise<void> => {
    const place = await props.current.api.workplace().catch(noAnswer);
    if (stale()) return;
    if (place.status === 401) return goLogin();
    if (place.status === 200 && placeView(place.body).ok) openChat();
    else setPhase({ kind: "workplace" });
  }, [goLogin, openChat]);

  // вход: кто я → у студента предупреждение (если не подтверждено) → место (готово — чат, иначе будим)
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const me = await props.current.api.me().catch(noAnswer);
      if (cancelled) return;
      if (me.status === 401) return goLogin();
      const user = parseMe(me);
      if (user === null) {
        setPhase({ kind: "error", text: me.status === 200 ? T_UNKNOWN : replyText(me) });
        return;
      }
      setWho(user);
      if (user.role === "teacher") {                    // ни мест, ни чата, ни одного вызова /pcbk/api/oc
        setPhase({ kind: "teacher" });
        return;
      }
      const notice = user.notice;
      if (notice != null) {                             // == null: подтверждено или поля нет (core Д5) — дальше
        const text = typeof notice === "string" ? NOTICES.get(notice) : undefined;
        if (typeof notice === "string" && text !== undefined) setPhase({ kind: "notice", notice, text });
        else setPhase({ kind: "error", text: T_NOTICE_UNKNOWN });   // номер без текста не подтверждаем (Ф14)
        return;
      }
      await toPlace(() => cancelled || leaving.current);
    })();
    return () => { cancelled = true; };
  }, [attempt, goLogin, toPlace]);

  // «Понятно» (R49): подтверждение, затем место. null — принято (экран снимет смена фазы), строка — текст ошибки,
  // экран остаётся (404 core Д5, сеть, 503). Конец сессии — вход; «Выйти», пока ответ в пути, — дальше не идём.
  const ackNotice = async (notice: string): Promise<string | null> => {
    const r = await props.current.api.ackNotice(notice).catch(noAnswer);
    if (leaving.current) return null;
    if (r.status === 401) {
      goLogin();
      return null;
    }
    if (r.status < 200 || r.status >= 300) return replyText(r);
    await toPlace(() => leaving.current);
    return null;
  };

  const logout = async (): Promise<void> => {
    if (leaving.current) return;
    leaving.current = true;
    setPhase({ kind: "leaving" });                      // чат снят до выхода — поток закрыт
    await props.current.api.logout().catch(noAnswer);
    props.current.navigate(LOGIN_URL);
  };

  let body;
  switch (phase.kind) {
    case "loading":
      body = <p className="phase">{T_LOADING}</p>;
      break;
    case "error":
      body = (
        <section className="phase">
          <p data-kind="fail">{phase.text}</p>
          <button type="button" onClick={() => { setPhase({ kind: "loading" }); setAttempt((n) => n + 1); }}>
            {T_RETRY}
          </button>
        </section>
      );
      break;
    case "teacher":
      body = <p className="phase">{T_TEACHER}</p>;
      break;
    case "notice": {
      const notice = phase.notice;
      body = <NoticeScreen text={phase.text} onAck={() => ackNotice(notice)} />;
      break;
    }
    case "workplace":
      body = <WorkplaceScreen api={p.api} onReady={openChat} onSignedOut={goLogin} pollMs={p.pollMs} timers={p.timers} />;
      break;
    case "asleep":
      body = (
        <section className="phase">
          <p>{T_ASLEEP}</p>
          <button type="button" onClick={() => setPhase({ kind: "workplace" })}>{T_WAKE}</button>
        </section>
      );
      break;
    case "chat": {
      const mount = phase.mount;
      body = (
        <ErrorBoundary key={mount} onCrash={() => report("ui_crash")}>
          <ChatImpl
            initialSessionId={phase.sessionId}
            onThreadIdChange={(id) => { if (mount === mounts.current) lastSession.current = id; }}
            onHalt={(kind) => {
              if (mount !== mounts.current) return;     // снятый чат не решает за нынешний
              if (kind === "signed_out") goLogin();
              else setPhase({ kind: "asleep" });
            }}
            onReport={(kind) => { if (mount === mounts.current) report(kind); }}
          />
        </ErrorBoundary>
      );
      break;
    }
    case "leaving":
      body = null;
      break;
  }

  const header = phase.kind !== "loading" && !(phase.kind === "leaving" && who === null);
  return (
    <div className="app">
      {header && (
        <Header login={who?.login ?? null} ownPlace={who?.place ?? null} startPolling={startPolling}
                onLogout={() => void logout()} leaving={phase.kind === "leaving"} />
      )}
      <main className="main">{body}</main>
    </div>
  );
}
