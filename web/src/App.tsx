// Страница чата (Д6-R39, R42, R44, R49). Фазы: загрузка → ошибка /me | преподаватель (кабинет) | предупреждение →
// рабочее место → чат; из чата — «место спит» (кнопка «Разбудить», сам не будит: Д5-R27). Предупреждение при
// первом входе стоит между /me и местом: пока студент не нажал «Понятно», место не будится и чат не монтируется.
// Поля notice нет (core Д5) — как null (== null); номер без текста в NOTICES — ошибка без «Понятно» (Ф14).
// Конец сессии в любой фазе и «Выйти» — на /login. Шапка — во всех фазах, кроме загрузки. App помнит последний
// открытый разговор (onThreadIdChange) и после «Разбудить» монтирует чат с ним.
// Молчание в браузере (R51): старт отмечается загрузчику (window.__pcbkStarted) при монтировании App, ещё до ответа
// /me — экран места или предупреждения дольше 20 с не дают ложного boot_failed (preflight Ф9); без загрузчика (edge
// до Д6б, откат) App работает. Чат — внутри ErrorBoundary: сбой отрисовки — отчёт ui_crash и «Перезапустить чат» —
// новое монтирование с последним открытым разговором (lastSession), а не с тем, что был при прошлом монтировании.
// Сбой чата, смонтированного перезапуском, — второй подряд: разговор мог ронять чат своим содержимым, и заход с ним
// снова дал бы петлю ui_crash, поэтому следующий перезапуск открывает новый разговор, а прежний App забывает
// (итоговая доводка Д6б, Б3-m6). Сбой вне границы (шапка, фазы до чата) ловит корень — mount.tsx.
// Отчёты чата (turn_silent, stream_down) уходят через api.report — только от нынешнего чата и не при уходе на вход.
// Список агентов места (Д7-R65) чат читает сам — App отдаёт ему загрузчик api.agents (preflight П11).
// Вкладки (Д9-R88): у студента в фазах места, «спит» и чата шапка показывает «Чат» и «Калькуляторы». Чат при этом не
// снимается: тело фаз лежит в обёртке с атрибутом hidden, поток /event и разговор живы. Вкладка калькуляторов
// монтируется при первом открытии и остаётся (форма и итог переживают переход в чат), снимается только с уходом из фаз
// вкладок; у неё своя ErrorBoundary (m5) со своими текстами: сбой — отчёт ui_crash, «Перезапустить калькуляторы»
// перемонтирует только её, чат не трогается.
// Вкладка «События» (Д13, DESIGN п.7): только у студента (showEvents = tabs && !teacher), рядом с «Чат» и
// «Калькуляторы». Устроена так же, как «Калькуляторы»: монтируется при первом открытии, остаётся смонтированной,
// своя ErrorBoundary. Основная область (чат/кабинет) прячется, пока открыта любая вкладка кроме «main».
// Вкладка «Уроки» (Д15а, DESIGN): только у студента (showLessons = tabs && !teacher), рядом с «События». Та же
// схема — монтируется при первом открытии, остаётся смонтированной, своя ErrorBoundary.
// Преподаватель (Д10-R106): фаза teacher рисует кабинет (TeacherPage) в своей ErrorBoundary с текстами кабинета; ни места
// студента, ни чата, ни одного вызова /pcbk/api/oc; вызовы кабинета — teacherApi (по умолчанию createTeacherApi()).
// Вкладки преподавателя (Д10-R110): в фазе teacher — «Кабинет» и «Калькуляторы». Кабинет, как чат студента, не снимается:
// вкладка калькуляторов его только прячет (окно пароля учётки и открытое место живы). «Калькуляторы» — тот же CalcPage
// Д9 через api.calc; вызывающего (teacher, канал web) шлюз определяет сам по роли сессии (Д10-R109). Запасной текст
// вкладки у преподавателя — кабинетный T_TCH_UNKNOWN, не «…сообщите преподавателю» (итоговое ревью Д10б, m-4).
// Один существующий Chat: история, инструменты БДРВ и текстовое общение.
import { useCallback, useEffect, useMemo, useRef, useState, type ComponentType } from "react";
import type { AnalyticsApi } from "./analyticsApi";
import { replyText, type Api, type ReportKind, type Reply } from "./api";
import type { AnalyticsPageProps } from "./components/AnalyticsPage";
import { CalcPage, type CalcPageProps } from "./components/CalcPage";
import { Chat, type ChatProps } from "./components/Chat";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { EventsPage, type EventsPageProps } from "./components/EventsPage";
import { Header, type View } from "./components/Header";
import { LessonsPage, type LessonsPageProps } from "./components/LessonsPage";
import { NoticeScreen } from "./components/NoticeScreen";
import { TeacherPage } from "./components/TeacherPage";
import { WorkplaceScreen } from "./components/WorkplaceScreen";
import { startStatusPolling } from "./status";
import { createTeacherApi, type TeacherApi } from "./teacherApi";
import { NOTICES, T_ASLEEP, T_CALC_CRASH, T_CALC_RESTART, T_EVENTS_CRASH,
         T_EVENTS_RESTART, T_LESSONS_CRASH, T_LESSONS_RESTART, T_LOADING, T_NOTICE_UNKNOWN, T_RETRY, T_TCH_CRASH,
         T_TCH_RESTART, T_TCH_UNKNOWN, T_UNKNOWN, T_WAKE } from "./texts";
import { placeView, type Timers } from "./workplace";


export interface AppProps {
  api: Api;
  teacherApi?: TeacherApi;                                // кабинет преподавателя (Д10-R106); по умолчанию createTeacherApi()
  analyticsApi?: AnalyticsApi; // Устаревшая инъекция, не используется.
  navigate(url: string): void;
  ChatImpl?: ComponentType<ChatProps>;
  CalcImpl?: ComponentType<CalcPageProps>;                // подмена вкладки «Калькуляторы» в тестах, как ChatImpl
  EventsImpl?: ComponentType<EventsPageProps>;            // подмена вкладки «События» в тестах, как CalcImpl (Д13)
  LessonsImpl?: ComponentType<LessonsPageProps>;          // подмена вкладки «Уроки» в тестах, как EventsImpl (Д15а)
  AnalyticsImpl?: ComponentType<AnalyticsPageProps>; // Устаревшая инъекция, не используется.
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
  | { kind: "chat"; mount: number; sessionId: string | undefined; restarted: boolean }   // restarted — «Перезапустить чат»
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
  const CalcImpl = p.CalcImpl ?? CalcPage;
  const EventsImpl = p.EventsImpl ?? EventsPage;
  const LessonsImpl = p.LessonsImpl ?? LessonsPage;
  const startPolling = p.startPolling ?? startStatusPolling;
  const teacherApi = useMemo(() => p.teacherApi ?? createTeacherApi(), [p.teacherApi]);
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [who, setWho] = useState<Who | null>(null);
  const [attempt, setAttempt] = useState(0);            // «Повторить» после сбоя /me
  const [view, setView] = useState<View>("main");        // открытая вкладка (Д9-R88, Д10-R110, Д13)
  const [calcOpened, setCalcOpened] = useState(false);   // вкладку калькуляторов уже открывали — она смонтирована
  const [eventsOpened, setEventsOpened] = useState(false);   // вкладку «События» уже открывали (Д13)
  const [lessonsOpened, setLessonsOpened] = useState(false);  // вкладку «Уроки» уже открывали (Д15а)
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
    setPhase({ kind: "chat", mount: mounts.current, sessionId: lastSession.current, restarted: false });
  }, []);

  // «Перезапустить чат»: с последним открытым разговором; упал чат прошлого перезапуска — с новым (Б3-m6)
  const restartChat = useCallback((crashedWasRestart: boolean): void => {
    if (crashedWasRestart) lastSession.current = undefined;
    mounts.current += 1;
    setPhase({ kind: "chat", mount: mounts.current, sessionId: lastSession.current, restarted: true });
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
      if (user.role === "teacher") {                    // кабинет: ни мест, ни чата, ни одного вызова /pcbk/api/oc
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
      body = (
        <ErrorBoundary onCrash={() => report("ui_crash")} crashText={T_TCH_CRASH} restartText={T_TCH_RESTART}>
          <TeacherPage api={teacherApi} onSignedOut={goLogin} pollMs={p.pollMs} timers={p.timers} />
        </ErrorBoundary>
      );
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
      const { mount, restarted } = phase;
      body = (
        <ErrorBoundary key={mount} onCrash={() => report("ui_crash")} onRestart={() => restartChat(restarted)}>
          <ChatImpl
            initialSessionId={phase.sessionId}
            onThreadIdChange={(id) => { if (mount === mounts.current) lastSession.current = id; }}
            onHalt={(kind) => {
              if (mount !== mounts.current) return;     // снятый чат не решает за нынешний
              if (kind === "signed_out") goLogin();
              else setPhase({ kind: "asleep" });
            }}
            onReport={(kind) => { if (mount === mounts.current) report(kind); }}
            loadAgents={p.api.agents}
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
  // вкладки — у студента в фазах места и чата, у преподавателя в фазе кабинета (не в предупреждении, ошибке и уходе):
  // калькуляторы ходят в шлюз, не на место
  const teacher = who?.role === "teacher";
  const tabs = (who?.role === "student" && (phase.kind === "workplace" || phase.kind === "asleep" || phase.kind === "chat"))
    || (teacher && phase.kind === "teacher");
  const showAnalytics = false; // Отдельная аналитика убрана: технолог работает в обычном чате.
  const showEvents = tabs && !teacher;                   // «События» — только у студента (Д13)
  const showLessons = tabs && !teacher;                  // «Уроки» — только у студента (Д15а)
  const showView = (v: View): void => {
    setView(v === "analytics" ? "main" : v);
    if (v === "calc") setCalcOpened(true);
    if (v === "events") setEventsOpened(true);
    if (v === "lessons") setLessonsOpened(true);
  };
  return (
    <div className="app">
      {header && (
        <Header login={who?.login ?? null} ownPlace={who?.place ?? null} startPolling={startPolling}
                onLogout={() => void logout()} leaving={phase.kind === "leaving"} view={tabs ? view : null}
                teacher={teacher} showAnalytics={showAnalytics} showEvents={showEvents} showLessons={showLessons}
                onView={showView} />
      )}
      <main className="main">
        {/* обёртка — во всех фазах: смена фазы не перемонтирует чат, прочие вкладки его только прячут */}
        <div className="view" hidden={tabs && view !== "main"}>{body}</div>
        {tabs && calcOpened && (
          <div className="view" hidden={view !== "calc"}>
            <ErrorBoundary onCrash={() => report("ui_crash")} crashText={T_CALC_CRASH} restartText={T_CALC_RESTART}>
              <CalcImpl api={p.api} onSignedOut={goLogin} unknownText={teacher ? T_TCH_UNKNOWN : undefined} />
            </ErrorBoundary>
          </div>
        )}
        {showEvents && eventsOpened && (
          <div className="view" hidden={view !== "events"}>
            <ErrorBoundary onCrash={() => report("ui_crash")} crashText={T_EVENTS_CRASH} restartText={T_EVENTS_RESTART}>
              <EventsImpl api={p.api} onSignedOut={goLogin} />
            </ErrorBoundary>
          </div>
        )}
        {showLessons && lessonsOpened && (
          <div className="view" hidden={view !== "lessons"}>
            <ErrorBoundary onCrash={() => report("ui_crash")} crashText={T_LESSONS_CRASH} restartText={T_LESSONS_RESTART}>
              <LessonsImpl api={p.api} onSignedOut={goLogin} />
            </ErrorBoundary>
          </div>
        )}

      </main>
    </div>
  );
}
