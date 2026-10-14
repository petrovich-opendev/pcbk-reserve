// Вкладка «Аналитический чат технолога» (план КДМ1, Task 6; мастер-промпт §6): диалоги, два режима («Справка
// по документам» / «Разбор отклонения»), участок/период/единица — необязательные уточнения вопроса, отправка/
// отмена/опрос хода задания, адресные уточнения, раскрытие оснований (AnalyticsSources), раздел «Связи с
// историаном» (AnalyticsLinks, план Д17 Task 2 — доступен любому вошедшему с доступом к вкладке) и — только
// при праве, которое сообщил сервер (`capabilities.reviewAuthority`) — экран назначенного согласования
// (AnalyticsReviews). Ответ рисуется только из серверных полей JobResponse, включая секцию телеметрии
// `telemetry_observation` (Д17 Task 1 — «Данные историана», AN_SECTION_LABELS): текст разделов — markdown тем
// же безопасным рендером, что у уроков (LessonMarkdown), сырой ответ модели сюда не попадает ни при каком
// состоянии (сервер его и не отдаёт — только rendered_by:"server").
//
// Идентичность — только серверная сессия (как у calc/lessons): эта страница не посылает ни role, ни owner, ни
// place. Повтор после сетевого сбоя посылки вопроса — тот же client_request_id (ОСОБЫЙ ФОКУС ПРОВЕРКИ №3 плана:
// без повторной оплаты неизвестно оплаченного вызова); новый вопрос — новый id (newClientRequestId).
// История диалога (GET conversations/{id}) отдаёт вопрос вместе с ответом — {question, response} на каждый
// элемент requests (доработка по решению контроллера Ruling 15: Task 4, коммит ed32131, обновил форму под это;
// исходно JobResponse сам вопрос не нёс — см. docs/kdm1/INTERFACES.md, «Правка раунд 1», п.5). Вопрос своей же
// только что завершённой отправки (finishResponse) берётся из lastAttempt — переживать круг на сервер и обратно
// за тем же текстом незачем. Показан он всегда только текстом (QuestionBubble, JSX-узлом, без markdown/HTML) —
// это ввод технолога, не проверенный ответ сервера.
import { Fragment, useEffect, useRef, useState } from "react";
import type { AnalyticsApi, AskInput, QuestionContext } from "../analyticsApi";
import { newClientRequestId } from "../analyticsApi";
import {
  JOB_TERMINAL_STATES, parseCapabilities, parseConversationDetail, parseConversationList,
  parseCreatedConversation, parseJobResponse, parseQuestionAccepted, QUESTION_MAX, type AnalysisMode,
  type Capabilities, type ConversationDetail, type ConversationMeta, type JobResponse,
} from "../analytics";
import { replyText, type Reply } from "../api";
import { fmtLocal } from "../events";
import type { VisibilityDoc } from "../status";
import type { Timers } from "../workplace";
import { LessonMarkdown } from "./LessonMarkdown";
import { AnalyticsSources } from "./AnalyticsSources";
import { AnalyticsReviews } from "./AnalyticsReviews";
import { AnalyticsLinks } from "./AnalyticsLinks";
import {
  AN_CLARIFICATION_LABELS, AN_ERROR_LABELS, AN_MODE_LABELS, AN_PROCESS_HINTS, AN_SECTION_LABELS, AN_STATE_LABELS,
  AN_TZ_CHOICES, T_AN_BAD_PERIOD, T_AN_CANCEL, T_AN_CLARIFY_HINT, T_AN_CLARIFY_RESEND, T_AN_CLARIFY_TITLE,
  T_AN_CONVERSATION_ITEM, T_AN_CONVERSATIONS, T_AN_LINKS, T_AN_LOADING, T_AN_MODE, T_AN_NAV, T_AN_NAV_CHAT,
  T_AN_NEW_CONVERSATION, T_AN_NO_CONVERSATIONS, T_AN_NO_QUESTION, T_AN_ORIGINAL_QUESTION, T_AN_PERIOD,
  T_AN_PERIOD_FROM, T_AN_PERIOD_TO, T_AN_PERIOD_TZ, T_AN_PROCESS, T_AN_QUESTION, T_AN_RETRY, T_AN_REVIEWS,
  T_AN_SCOPE, T_AN_SEND, T_AN_SEND_TO_REVIEW, T_AN_SEND_TO_REVIEW_DONE, T_AN_SEND_TO_REVIEW_RATIONALE,
  T_AN_SENDING, T_AN_START_HINT, T_AN_TITLE, T_AN_UNAVAILABLE, T_AN_UNIT, T_UNKNOWN,
} from "../texts";

export interface AnalyticsPageProps {
  api: AnalyticsApi;
  onSignedOut(): void;
  pollMs?: number;            // опрос хода задания — для тестов (приём WorkplaceScreen/TeacherPage)
  timers?: Timers;
  doc?: VisibilityDoc | null;  // visibilitychange — для тестов (приём startStatusPolling); по умолчанию globalThis.document
}

const DEFAULT_POLL_MS = 1500;
const defaultTimers: Timers = { setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: (id) => clearTimeout(id as ReturnType<typeof setTimeout>) };
const noAnswer = (): Reply => ({ status: 0, body: null });
const errorText = (r: Reply): string => { const t = replyText(r); return t === T_UNKNOWN ? T_AN_UNAVAILABLE : t; };

type CapsState = { kind: "loading" } | { kind: "error"; text: string } | { kind: "ready"; caps: Capabilities };
type ListState = { kind: "loading" } | { kind: "error"; text: string } | { kind: "ready"; items: ConversationMeta[] };
type DetailState = { kind: "none" } | { kind: "loading" } | { kind: "error"; text: string } | { kind: "ready"; detail: ConversationDetail };

type JobPhase =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "send_error"; text: string }
  | { kind: "polling"; requestId: string; response: JobResponse | null }
  | { kind: "cancelling"; requestId: string; response: JobResponse | null };

interface Attempt { conversationId: string; input: AskInput; questionText: string }

function emptyContext(): { process: string; from: string; to: string; tz: string; unit: string } {
  return { process: "", from: "", to: "", tz: AN_TZ_CHOICES[0]!.id, unit: "" };
}

function buildContext(f: ReturnType<typeof emptyContext>): { context: QuestionContext | null; error: string | null } {
  const hasPeriod = f.from !== "" || f.to !== "";
  if (hasPeriod && (f.from === "" || f.to === "" || f.to < f.from)) return { context: null, error: T_AN_BAD_PERIOD };
  const tz = AN_TZ_CHOICES.find((t) => t.id === f.tz) ?? AN_TZ_CHOICES[0]!;
  const context: QuestionContext = {};
  if (f.process.trim() !== "") context.processId = f.process.trim();
  if (hasPeriod) context.period = { start: `${f.from}:00${tz.offset}`, end: `${f.to}:00${tz.offset}` };
  if (f.unit.trim() !== "") context.unitHint = f.unit.trim();
  return { context: Object.keys(context).length > 0 ? context : null, error: null };
}

// вопрос пользователя в истории диалога (доработка: Task 4, ed32131 — GET conversations/{id} отдаёт его как
// {question, response}) — приём обычного чата, класс .msg[data-role="user"] chat.css (своего класса не
// заводим). Текст JSX-узлом, не markdown и не dangerouslySetInnerHTML: вопрос — ввод технолога, не ответ
// сервера, разметку/HTML в нём не исполняем, React сам экранирует.
function QuestionBubble({ text }: { text: string }) {
  return <div className="msg" data-role="user">{text}</div>;
}

function SectionCard({ s, api }: { s: JobResponse["sections"][number]; api: AnalyticsApi }) {
  return (
    <div className="an-section" data-kind={s.kind}>
      <h5 className="an-section-kind">{AN_SECTION_LABELS[s.kind] ?? s.kind}</h5>
      <div className="an-section-text"><LessonMarkdown markdown={s.text} /></div>
      <AnalyticsSources evidenceRefs={s.evidenceRefs} api={api} />
    </div>
  );
}

// Отправка конфликта на согласование (мастер-промпт §6: «конфликт виден и может быть отправлен на согласование»).
// JobResponse не даёт отдельного id конфликта (контракт Task 0 — только errorCode="source_conflict"), поэтому
// предмет заявки — сам requestId этого ответа: ReviewStore.create_request (Task 7) не требует особого права на
// создание заявки, только на решение по ней (reviews.py: active_grant проверяет decide, не create_request) —
// её может отправить любой увидевший конфликт, не только обладатель review_authority.
type ConflictState =
  | { kind: "idle" }
  | { kind: "open"; rationale: string }
  | { kind: "sending"; rationale: string }
  | { kind: "done" }
  | { kind: "error"; rationale: string; text: string };

function ConflictFlag({ requestId, api, onSignedOut }: {
  requestId: string; api: AnalyticsApi; onSignedOut: () => void;
}) {
  const [state, setState] = useState<ConflictState>({ kind: "idle" });
  if (state.kind === "idle") {
    return (
      <button type="button" onClick={() => setState({ kind: "open", rationale: "" })}>{T_AN_SEND_TO_REVIEW}</button>
    );
  }
  if (state.kind === "done") return <p className="an-note">{T_AN_SEND_TO_REVIEW_DONE}</p>;

  const sending = state.kind === "sending";
  const send = (): void => {
    if (sending) return;
    const text = state.rationale.trim();
    if (text === "") return;
    setState({ kind: "sending", rationale: state.rationale });
    void (async () => {
      const r = await api.createReview({
        kind: "conflict", subjectId: requestId, newValue: "Рассмотреть конфликт источников в ответе", scope: "КДМ-1",
        rationale: text,
      }).catch(() => ({ status: 0, body: null }));
      if (r.status === 401) { onSignedOut(); return; }
      if (r.status !== 200 && r.status !== 201) {
        const t = replyText(r);
        setState({ kind: "error", rationale: text, text: t === T_UNKNOWN ? T_AN_UNAVAILABLE : t });
        return;
      }
      setState({ kind: "done" });
    })();
  };
  return (
    <div className="an-conflict-flag">
      <label className="an-field">
        {T_AN_SEND_TO_REVIEW_RATIONALE}
        <textarea value={state.rationale} disabled={sending}
                  onChange={(e) => setState({ kind: "open", rationale: e.target.value })} />
      </label>
      <button type="button" onClick={send} disabled={sending || state.rationale.trim() === ""}>
        {T_AN_SEND_TO_REVIEW}
      </button>
      {state.kind === "error" && <p className="an-msg" role="alert">{state.text}</p>}
    </div>
  );
}

function ResponseCard({ r, api, onCancel, cancelling, onSignedOut }: {
  r: JobResponse; api: AnalyticsApi; onCancel: (() => void) | null; cancelling: boolean; onSignedOut: () => void;
}) {
  const stateLabel = AN_STATE_LABELS[r.state] ?? r.state;
  const inProgress = !JOB_TERMINAL_STATES.has(r.state);
  return (
    <div className="an-answer" data-state={r.state}>
      <div className="an-answer-head">
        <span className="an-answer-state">{r.stageLabel || stateLabel}</span>
        {inProgress && onCancel !== null && (
          <button type="button" onClick={onCancel} disabled={cancelling}>{T_AN_CANCEL}</button>
        )}
      </div>
      {r.state === "needs_clarification" && (
        <div className="an-clarify">
          <p className="an-clarify-title">{T_AN_CLARIFY_TITLE}</p>
          <ul>
            {r.clarificationKeys.map((k) => <li key={k}>{AN_CLARIFICATION_LABELS[k] ?? k}</li>)}
          </ul>
          <p className="an-note">{T_AN_CLARIFY_HINT}</p>
        </div>
      )}
      {r.errorCode !== null && <p className="an-msg" role="alert">{AN_ERROR_LABELS[r.errorCode] ?? r.errorCode}</p>}
      {r.errorCode === "source_conflict" && <ConflictFlag requestId={r.requestId} api={api} onSignedOut={onSignedOut} />}
      {r.sections.map((s, i) => <SectionCard key={i} s={s} api={api} />)}
    </div>
  );
}

export function AnalyticsPage(p: AnalyticsPageProps) {
  const props = useRef(p);
  props.current = p;
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const [caps, setCaps] = useState<CapsState>({ kind: "loading" });
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [composingMode, setComposingMode] = useState<AnalysisMode | null>(null);    // не null — форма нового диалога
  const [detail, setDetail] = useState<DetailState>({ kind: "none" });
  const [job, setJob] = useState<JobPhase>({ kind: "idle" });
  const [innerView, setInnerView] = useState<"chat" | "reviews" | "links">("chat");
  const [question, setQuestion] = useState("");
  const [form, setForm] = useState(emptyContext());

  const lastAttempt = useRef<Attempt | null>(null);
  const pollTimer = useRef<unknown>(null);
  const pollToken = useRef(0);               // новая отправка/переключение — старый опрос сам себя не пишет в state
  // токен цикла, для которого сейчас в полёте запрос api.request() — null, если нет (правка раунд 3: простой
  // boolean раунда 2 давал «призрачный» true навсегда, если токен менялся пока старый запрос был в полёте —
  // см. doTick/stopPolling ниже)
  const inFlight = useRef<number | null>(null);
  const requestedConv = useRef<string | null>(null);   // последний запрошенный диалог — старый ответ не перебивает новый

  const timers = p.timers ?? defaultTimers;
  const pollMs = p.pollMs ?? DEFAULT_POLL_MS;

  const stopPolling = (): void => {
    pollToken.current += 1;
    if (pollTimer.current !== null) { timers.clearTimeout(pollTimer.current); pollTimer.current = null; }
    inFlight.current = null;   // старый цикл больше не владеет меткой «в полёте» — новый токен уже не совпадёт
                                // с ним сам по себе (см. doTick), это просто немедленная уборка, не подстраховка
  };
  useEffect(() => () => stopPolling(), []);

  // последний job — в ref: эффект размонтирования ниже регистрируется один раз (deps []), и без ref его замыкание
  // видело бы job таким, каким он был при первой отрисовке (idle), а не текущим на момент настоящего ухода
  const jobRef = useRef<JobPhase>(job);
  useEffect(() => { jobRef.current = job; }, [job]);

  // выход студента со страницы (размонтирование) отменяет своё незавершённое задание наилучшим образом —
  // мастер-промпт §6: «при выходе отменить задания»; ответа не ждём, вкладка уже снята
  useEffect(() => () => {
    const j = jobRef.current;
    if ((j.kind === "polling" || j.kind === "cancelling") && !(j.response !== null && JOB_TERMINAL_STATES.has(j.response.state))) {
      void props.current.api.cancel(j.requestId).catch(() => undefined);
    }
  }, []);

  const loadCapabilities = async (): Promise<void> => {
    setCaps({ kind: "loading" });
    const r = await props.current.api.capabilities().catch(noAnswer);
    if (!alive.current) return;
    if (r.status === 401) { props.current.onSignedOut(); return; }
    if (r.status !== 200) { setCaps({ kind: "error", text: errorText(r) }); return; }
    const parsed = parseCapabilities(r.body);
    setCaps(parsed === null ? { kind: "error", text: T_AN_UNAVAILABLE } : { kind: "ready", caps: parsed });
  };

  const loadList = async (): Promise<void> => {
    setList({ kind: "loading" });
    const r = await props.current.api.conversations().catch(noAnswer);
    if (!alive.current) return;
    if (r.status === 401) { props.current.onSignedOut(); return; }
    if (r.status !== 200) { setList({ kind: "error", text: errorText(r) }); return; }
    const items = parseConversationList(r.body);
    setList(items === null ? { kind: "error", text: T_AN_UNAVAILABLE } : { kind: "ready", items });
  };

  useEffect(() => { void loadCapabilities(); void loadList(); }, []);

  const loadDetail = async (id: string): Promise<DetailState> => {
    const r = await props.current.api.conversation(id).catch(noAnswer);
    if (r.status === 401) { props.current.onSignedOut(); return { kind: "none" }; }
    if (r.status !== 200) return { kind: "error", text: errorText(r) };
    const parsed = parseConversationDetail(r.body);
    return parsed === null ? { kind: "error", text: T_AN_UNAVAILABLE } : { kind: "ready", detail: parsed };
  };

  const selectConversation = (id: string): void => {
    stopPolling();
    requestedConv.current = id;
    setSelected(id);
    setComposingMode(null);
    setJob({ kind: "idle" });
    setQuestion("");
    setForm(emptyContext());
    setDetail({ kind: "loading" });
    void (async () => {
      const d = await loadDetail(id);
      if (!alive.current || requestedConv.current !== id) return;    // ушли на другой диалог — ответ уже не нужен
      setDetail(d);
    })();
  };

  const startNewConversation = (mode: AnalysisMode): void => {
    stopPolling();
    requestedConv.current = null;
    setSelected(null);
    setComposingMode(mode);
    setJob({ kind: "idle" });
    setQuestion("");
    setForm(emptyContext());
    setDetail({ kind: "none" });
  };

  // терминальный ответ — в историю диалога (detail.requests) и job обратно в idle: без этого activeResponse
  // показал бы тот же ответ второй раз, рядом с только что добавленной карточкой истории. Вопрос — из lastAttempt
  // (сервер уже отдаёт его и сам, Task 4 ed32131, но здесь это свежий локальный ответ собственной отправки —
  // переживать круг на сервер и обратно незачем; lastAttempt гарантированно принадлежит этому requestId: его
  // сбрасывает только submit() следующего вопроса, не ход этого)
  const finishResponse = (parsed: JobResponse): void => {
    const question = lastAttempt.current?.questionText ?? "";
    setDetail((d) => d.kind === "ready"
      ? { kind: "ready",
         detail: { conversation: d.detail.conversation, requests: [...d.detail.requests, { question, response: parsed }] } }
      : d);
    setJob({ kind: "idle" });
  };

  // doc.hidden читаем заново при каждом вызове (не кэшируем) — правка раунд 2: гонка «скрылись, пока api.request()
  // в полёте» требовала проверки актуального состояния в момент планирования следующего тика, не только в
  // обработчике visibilitychange
  const isDocHidden = (): boolean => {
    const doc = p.doc !== undefined ? p.doc : (globalThis.document ?? null);
    return doc !== null && doc.hidden;
  };

  // один опрос; poll() планирует следующий по pollMs (или не планирует вовсе — скрыто), visible-обработчик ниже
  // зовёт doTick сразу, без ожидания. inFlight — токен цикла, чей запрос опроса сейчас в полёте: без него
  // быстрый hidden→visible→hidden→visible во время полёта второго параллельного запроса (видимый —
  // pollTimer===null, пока первый не вернулся) запустил бы ещё один doTick тем же requestId. Привязка к токену
  // (не простой boolean, правка раунд 3) — иначе устаревший ответ (отмена/переключение диалога случились, пока
  // старый запрос был в полёте) оставлял бы «призрачный» признак «в полёте» навсегда: visible-обработчик сверяет
  // inFlight.current именно с ТЕКУЩИМ pollToken.current, и значение от старого токена для этого сравнения уже
  // не значит «в полёте» — новый тик стартует как положено.
  const doTick = (requestId: string, token: number): void => {
    if (!alive.current || token !== pollToken.current) return;
    inFlight.current = token;
    void (async () => {
      const r = await props.current.api.request(requestId).catch(noAnswer);
      // снимаем метку только если она ещё наша: если за время запроса начался новый цикл и уже поставил свою
      // (другой токен), трогать её нельзя — иначе чужой «в полёте» потерялся бы и позволил бы второй параллельный
      // doTick того цикла
      if (inFlight.current === token) inFlight.current = null;
      if (!alive.current || token !== pollToken.current) return;    // устарело — дальше ничего не решаем
      if (r.status === 401) { props.current.onSignedOut(); return; }
      if (r.status !== 200) { poll(requestId, token); return; }     // временный сбой опроса — прежний ответ остаётся
      const parsed = parseJobResponse(r.body);
      if (parsed === null) { setJob({ kind: "send_error", text: T_AN_UNAVAILABLE }); return; }
      if (JOB_TERMINAL_STATES.has(parsed.state)) { finishResponse(parsed); return; }
      setJob({ kind: "polling", requestId, response: parsed });
      poll(requestId, token);
    })();
  };

  const poll = (requestId: string, token: number): void => {
    if (pollTimer.current !== null) { timers.clearTimeout(pollTimer.current); pollTimer.current = null; }
    if (isDocHidden()) return;   // скрыта именно сейчас (после сетевого ответа, не раньше) — тик не планируем
    pollTimer.current = timers.setTimeout(() => { pollTimer.current = null; doTick(requestId, token); }, pollMs);
  };

  // скрытая вкладка (правило: пауза опроса, не отмена задания — приём status.ts startStatusPolling, visibilitychange):
  // hidden — гасим таймер следующего тика, если он ждёт (ни job, ни pollToken не трогаем — задание на сервере
  // идёт своим чередом; запрос, уже ушедший в сеть, не отменяем — его продолжение само остановится на isDocHidden()
  // внутри poll()); visible — если опрос был на паузе (job всё ещё "polling", таймера нет и нет запроса в полёте
  // ДЛЯ ТЕКУЩЕГО токена — ghost-значение от уже сброшенного цикла инлайн не совпадёт с pollToken.current), один
  // тик сразу — normal-интервал восстанавливается сам (doTick → poll() у непоследнего ответа)
  useEffect(() => {
    const doc = p.doc !== undefined ? p.doc : (globalThis.document ?? null);
    if (doc === null) return;
    const onVisibilityChange = (): void => {
      if (doc.hidden) {
        if (pollTimer.current !== null) { timers.clearTimeout(pollTimer.current); pollTimer.current = null; }
        return;
      }
      const j = jobRef.current;
      if (j.kind === "polling" && pollTimer.current === null && inFlight.current !== pollToken.current) {
        doTick(j.requestId, pollToken.current);
      }
    };
    doc.addEventListener("visibilitychange", onVisibilityChange);
    return () => doc.removeEventListener("visibilitychange", onVisibilityChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- doTick/timers читают только рефы и props.current (см. jobRef выше)
  }, []);

  const sendAttempt = async (attempt: Attempt): Promise<void> => {
    lastAttempt.current = attempt;
    setJob({ kind: "sending" });
    const r = await props.current.api.ask(attempt.conversationId, attempt.input).catch(noAnswer);
    if (!alive.current) return;
    if (r.status === 401) { props.current.onSignedOut(); return; }
    if (r.status !== 202 && r.status !== 200) { setJob({ kind: "send_error", text: errorText(r) }); return; }
    const requestId = parseQuestionAccepted(r.body);
    if (requestId === null) { setJob({ kind: "send_error", text: T_AN_UNAVAILABLE }); return; }
    setQuestion("");                 // ушло успешно — поле свободно для следующего вопроса (не раньше: см. submit)
    stopPolling();
    const token = pollToken.current;
    setJob({ kind: "polling", requestId, response: null });
    poll(requestId, token);
  };

  const submit = (): void => {
    const text = question.trim();
    if (text === "") { setJob({ kind: "send_error", text: T_AN_NO_QUESTION }); return; }
    const { context, error } = buildContext(form);
    if (error !== null) { setJob({ kind: "send_error", text: error }); return; }
    const input: AskInput = {
      clientRequestId: newClientRequestId(), question: text,
      mode: composingMode ?? (detail.kind === "ready" ? detail.detail.conversation.mode : "reference"),
      context,
    };
    // не отданный вопрос ещё не считается «отправленным»: lastAttempt (и, значит, кнопка «Повторить») появляется
    // только после реального POST .../questions в sendAttempt — иначе сбой создания диалога показал бы повтор
    // чужой прежней попытки, а текст вопроса, очищенный здесь заранее, было бы нечем вернуть пользователю
    lastAttempt.current = null;
    if (selected !== null) { void sendAttempt({ conversationId: selected, input, questionText: text }); return; }
    if (composingMode === null) return;
    void (async () => {
      setJob({ kind: "sending" });
      const createR = await props.current.api.createConversation(composingMode).catch(noAnswer);
      if (!alive.current) return;
      if (createR.status === 401) { props.current.onSignedOut(); return; }
      const newId = createR.status === 201 || createR.status === 200 ? parseCreatedConversation(createR.body) : null;
      if (newId === null) { setJob({ kind: "send_error", text: errorText(createR) }); return; }
      requestedConv.current = newId;
      setSelected(newId);
      setComposingMode(null);
      const d = await loadDetail(newId);
      if (!alive.current || requestedConv.current !== newId) return;
      setDetail(d);
      await loadList();
      if (!alive.current || requestedConv.current !== newId) return;
      await sendAttempt({ conversationId: newId, input, questionText: text });
    })();
  };

  const retry = (): void => { if (lastAttempt.current !== null) void sendAttempt(lastAttempt.current); };

  const cancelActive = (): void => {
    if (job.kind !== "polling") return;
    const { requestId, response } = job;
    stopPolling();                 // гасит текущий цикл опроса и его токен — своя продолжение ниже получает новый
    const token = pollToken.current;
    setJob({ kind: "cancelling", requestId, response });
    void (async () => {
      const r = await props.current.api.cancel(requestId).catch(noAnswer);
      if (!alive.current || token !== pollToken.current) return;
      if (r.status === 401) { props.current.onSignedOut(); return; }
      if (r.status !== 200) { setJob({ kind: "polling", requestId, response }); poll(requestId, token); return; }
      const parsed = parseJobResponse(r.body);
      if (parsed === null) { setJob({ kind: "polling", requestId, response }); poll(requestId, token); return; }
      if (JOB_TERMINAL_STATES.has(parsed.state)) { finishResponse(parsed); return; }
      setJob({ kind: "polling", requestId, response: parsed });
      poll(requestId, token);
    })();
  };

  if (caps.kind === "error") return <section className="an"><p className="an-msg" role="alert">{caps.text}</p></section>;
  if (caps.kind === "loading") return <section className="an"><p className="an-note">{T_AN_LOADING}</p></section>;

  const conversationMode = selected !== null && detail.kind === "ready" ? detail.detail.conversation.mode : composingMode;
  const canCompose = selected !== null || composingMode !== null;
  const sending = job.kind === "sending";
  const activeResponse = job.kind === "polling" || job.kind === "cancelling" ? job.response : null;
  const activeInProgress = activeResponse !== null && !JOB_TERMINAL_STATES.has(activeResponse.state);
  // терминальный «нужно уточнение» уже лежит в истории (finishResponse переносит его туда и возвращает job в
  // idle, чтобы не показать тот же ответ дважды) — «исходный вопрос» и подсказку переспросить берём из конца
  // истории, не из activeResponse (он к этому моменту уже null)
  const lastHistoryItem = detail.kind === "ready" && detail.detail.requests.length > 0
    ? detail.detail.requests[detail.detail.requests.length - 1]! : null;
  const needsClarification = activeResponse?.state === "needs_clarification"
    || lastHistoryItem?.response.state === "needs_clarification";

  return (
    <section className="an">
      <div className="an-head">
        <h2>{T_AN_TITLE}</h2>
        <span className="an-scope">{T_AN_SCOPE}</span>
      </div>
      {/* «Диалог» и «Связи с историаном» — всем вошедшим с доступом к вкладке (предложить связь может любой,
          план Д17 Task 2); «Назначенное согласование» — только при праве, которое сообщил сервер */}
      <nav className="an-inner-nav" aria-label={T_AN_NAV}>
        <button type="button" aria-pressed={innerView === "chat"} onClick={() => setInnerView("chat")}>{T_AN_NAV_CHAT}</button>
        <button type="button" aria-pressed={innerView === "links"} onClick={() => setInnerView("links")}>{T_AN_LINKS}</button>
        {caps.caps.reviewAuthority && (
          <button type="button" aria-pressed={innerView === "reviews"} onClick={() => setInnerView("reviews")}>{T_AN_REVIEWS}</button>
        )}
      </nav>
      {innerView === "reviews" && caps.caps.reviewAuthority ? (
        <AnalyticsReviews api={props.current.api} onSignedOut={props.current.onSignedOut} />
      ) : innerView === "links" ? (
        <AnalyticsLinks api={props.current.api} onSignedOut={props.current.onSignedOut}
                         dataBridgeAvailable={caps.caps.dataBridgeAvailable} />
      ) : (
        <div className="an-body">
          <nav className="an-list" aria-label={T_AN_CONVERSATIONS}>
            <div className="an-list-head"><h3>{T_AN_CONVERSATIONS}</h3></div>
            <div className="an-new-modes">
              {(Object.keys(AN_MODE_LABELS) as AnalysisMode[]).map((m) => (
                <button key={m} type="button" aria-pressed={composingMode === m && selected === null}
                        onClick={() => startNewConversation(m)}>
                  {T_AN_NEW_CONVERSATION}: {AN_MODE_LABELS[m]}
                </button>
              ))}
            </div>
            {list.kind === "error" && <p className="an-msg" role="alert">{list.text}</p>}
            {list.kind === "ready" && (
              list.items.length === 0
                ? <p className="an-note">{T_AN_NO_CONVERSATIONS}</p>
                : (
                  <div className="an-list-items">
                    {list.items.map((c) => (
                      <button key={c.id} type="button" aria-pressed={selected === c.id} onClick={() => selectConversation(c.id)}>
                        <span className="an-list-item-title">{T_AN_CONVERSATION_ITEM(fmtLocal(c.lastActivityAt))}</span>
                        <span className="an-list-item-mode">{AN_MODE_LABELS[c.mode] ?? c.mode}</span>
                      </button>
                    ))}
                  </div>
                )
            )}
          </nav>
          <div className="an-main">
            {!canCompose && <p className="an-note">{T_AN_START_HINT}</p>}
            {detail.kind === "error" && <p className="an-msg" role="alert">{detail.text}</p>}
            {detail.kind === "ready" && (
              <div className="an-thread">
                {detail.detail.requests.map((turn) => (
                  <Fragment key={turn.response.requestId}>
                    <QuestionBubble text={turn.question} />
                    <ResponseCard r={turn.response} api={props.current.api} onCancel={null} cancelling={false}
                                  onSignedOut={props.current.onSignedOut} />
                  </Fragment>
                ))}
              </div>
            )}
            {activeResponse !== null && (
              <ResponseCard r={activeResponse} api={props.current.api}
                            onCancel={activeInProgress ? cancelActive : null} cancelling={job.kind === "cancelling"}
                            onSignedOut={props.current.onSignedOut} />
            )}
            {canCompose && (
              <form className="an-compose" onSubmit={(e) => { e.preventDefault(); submit(); }}>
                {needsClarification && lastAttempt.current !== null && (
                  <p className="an-note">{T_AN_ORIGINAL_QUESTION}: {lastAttempt.current.questionText}</p>
                )}
                <label className="an-field an-field-wide">
                  {T_AN_QUESTION}
                  <textarea value={question} maxLength={QUESTION_MAX} disabled={sending}
                            onChange={(e) => setQuestion(e.target.value)} />
                </label>
                <div className="an-row">
                  <label className="an-field">
                    {T_AN_PROCESS}
                    <input list="an-process-hints" value={form.process} disabled={sending}
                           onChange={(e) => setForm((f) => ({ ...f, process: e.target.value }))} />
                  </label>
                  <label className="an-field">
                    {T_AN_UNIT}
                    <input value={form.unit} disabled={sending}
                           onChange={(e) => setForm((f) => ({ ...f, unit: e.target.value }))} />
                  </label>
                </div>
                <datalist id="an-process-hints">
                  {AN_PROCESS_HINTS.map((h) => <option key={h} value={h} />)}
                </datalist>
                <fieldset className="an-row">
                  <legend>{T_AN_PERIOD}</legend>
                  <label className="an-field">
                    {T_AN_PERIOD_FROM}
                    <input type="datetime-local" value={form.from} disabled={sending}
                           onChange={(e) => setForm((f) => ({ ...f, from: e.target.value }))} />
                  </label>
                  <label className="an-field">
                    {T_AN_PERIOD_TO}
                    <input type="datetime-local" value={form.to} disabled={sending}
                           onChange={(e) => setForm((f) => ({ ...f, to: e.target.value }))} />
                  </label>
                  <label className="an-field">
                    {T_AN_PERIOD_TZ}
                    <select value={form.tz} disabled={sending} onChange={(e) => setForm((f) => ({ ...f, tz: e.target.value }))}>
                      {AN_TZ_CHOICES.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
                    </select>
                  </label>
                </fieldset>
                <div className="an-row">
                  <span className="an-mode-note">{T_AN_MODE}: {conversationMode !== null ? AN_MODE_LABELS[conversationMode] : ""}</span>
                  <button type="submit" disabled={sending}>{sending ? T_AN_SENDING : T_AN_SEND}</button>
                  {job.kind === "send_error" && lastAttempt.current !== null && (
                    <button type="button" onClick={retry}>{T_AN_RETRY}</button>
                  )}
                </div>
                {job.kind === "send_error" && <p className="an-msg" role="alert">{job.text}</p>}
                {needsClarification && job.kind !== "send_error" && <p className="an-note">{T_AN_CLARIFY_RESEND}</p>}
              </form>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

export default AnalyticsPage;
