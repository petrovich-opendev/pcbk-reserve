// Чат поверх шлюза (Д6-R42, R43): клиент OpenCode, обёртка fetch шлюза и рантайм — свои на каждое
// монтирование. Размонтирование (401, «место спит», «Выйти») закрывает поток адаптера (registry.dispose), а
// новое монтирование после «Разбудить» открывает последний разговор (initialSessionId от App).
// Без defaultModel и без adapters (нет вложений). defaultAgent — агент хода (Д7-R65, ниже).
// Д6б: список разговоров слева (R52) и сторожа молчания (R51): ход без прироста ответа 3 мин — строка и отчёт
// turn_silent (один на ход), связь с потоком потеряна дольше 2 мин — отчёт stream_down при восстановлении (один на
// эпизод). Отчёт уходит через onReport, пока чат смонтирован и обёртка не остановлена (после 401 и «спит» — нет).
// Пока поток не "ok" (и при "too_many_tabs"), отсчёт хода на паузе (доводка 1, m4). Сон машины — тики сторожа потока
// раз в секунду и на visibilitychange (итоговая доводка Д6б, m4; правило — в watch.ts).
// initialSessionId не уходит в рантайм (доводка 1, I1): switchToThread библиотеки по умолчанию снимает архив
// (PATCH time.archived = null — против R52), а разговор мог уйти в архив в другой вкладке или до перезапуска. Чат
// открывает его сам — на первой успешной загрузке списка (список упал при монтировании — на той, что библиотека
// перечитает позже: online, возврат вкладки; итоговая доводка Д6б, Б3-m5), только если он есть среди неархивных и
// студент ещё не начал новый разговор и не выбрал агента в панели (m15), и с unarchive: false. Иначе — новый
// разговор, а App забывает прежний (onThreadIdChange(undefined)).
// Д7б (Д7-R65, Review Focus 1): панель «Агенты» над списком разговоров — только с loadAgents (App отдаёт api.agents;
// без него ни панели, ни вызова — preflight П11). Агент хода: у начатого разговора — из его истории (ThreadAgentWatch),
// у нового — выбранный в панели (newAgent, по умолчанию помощник; «Новый разговор» его сохраняет); defaultAgent =
// sendAgent(агент). Пока агент открытого разговора неизвестен (история ещё грузится — после перезагрузки, «Разбудить»
// или переключения), отправка закрыта: isDisabled — поле, isSendDisabled — сама отправка (canSend assistant-ui 0.15
// isDisabled не смотрит, и кнопка с текстом в поле ушла бы выбранному агенту). Список агентов читает опрос
// createAgentsPoll (agents.ts): при монтировании, через 1 с после конца хода, пока есть pending — раз в 3 с до 60 с;
// остановка — при снятии чата и остановке обёртки.
// С панелью агентов после перезагрузки или «Разбудить» (initialSessionId) отправка закрыта и до решения, открывать ли
// прежний разговор (доводка р1, m1): иначе ход ушёл бы выбранному агенту (помощнику) в новый разговор мимо прежнего
// разговора Конструктора. Решение — прежний открыт или его нет в списке; ещё открывают отправку сбой списка (решать
// нечего, студент пишет в новый разговор) и выбор агента в панели (m15). Без панели (тесты Д6) — поведение Д6б: студент
// может начать новый разговор сразу, и прежний поверх него не откроется.
import {
  createOpencodeClient, useOpenCodePermissions, useOpenCodeQuestions, useOpenCodeRuntime,
  useOpenCodeRuntimeExtras, type OpenCodePermissionRequest, type OpenCodeQuestionRequest,
} from "@assistant-ui/react-opencode";
import { useEffect, useRef, useState } from "react";
import { DEFAULT_AGENT, createAgentsPoll, parseAgents, sendAgent, type AgentItem, type AgentsPoll } from "../agents";
import type { Reply } from "../api";
import { createGatewayFetch, explainError, streamLine } from "../gateway";
import { BUILTIN_AGENT_TITLES, T_AGENT_LINE, T_TURN_SILENT } from "../texts";
import { TICK_MS, createStreamWatch, useTurnWatch } from "../watch";
import { AgentPanel } from "./AgentPanel";
import { ChatView } from "./ChatView";
import { PendingInteractions } from "./PendingInteractions";
import { ThreadAgentWatch, type ThreadAgentSeen } from "./ThreadAgentWatch";
import { ThreadSidebar } from "./ThreadSidebar";

export interface ChatProps {
  onHalt(kind: "signed_out" | "asleep"): void;
  initialSessionId?: string;
  onThreadIdChange?(id: string | undefined): void;
  onReport?(kind: "turn_silent" | "stream_down"): void;   // отчёт браузера о молчании (R51)
  loadAgents?: () => Promise<Reply>;                      // GET /pcbk/api/agents (Д7-R64); без него — ни панели, ни вызова
}

// список агентов: items — последний ответ (undefined — ответа ещё нет, null — не 200); known — последний ответ 200,
// по нему название в строке «Агент: …» (агента нет в списке — имя, m14)
interface AgentsView { items: AgentItem[] | null | undefined; known: AgentItem[] }

const sameSeen = (a: ThreadAgentSeen, b: ThreadAgentSeen): boolean =>
  a.state === b.state && (a.state !== "agent" || a.name === (b as { name: string }).name);

// Висящие разрешения и вопросы — из хуков адаптера. Хуки с запасным значением работают и до того, как у
// разговора появился рантайм; rejectQuestion есть только в полных extras — их берёт вложенный компонент,
// который монтируется, лишь когда есть что показывать (значит, extras уже есть).
function RuntimePending(p: { onError(error: unknown): void }) {
  const { pending, reply } = useOpenCodePermissions();
  const questions = useOpenCodeQuestions();
  if (pending.length === 0 && questions.length === 0) return null;
  return <PendingWithReject permissions={pending} questions={questions} reply={reply} onError={p.onError} />;
}

function PendingWithReject(p: {
  permissions: OpenCodePermissionRequest[];
  questions: OpenCodeQuestionRequest[];
  reply(id: string, response: "reject"): Promise<void>;
  onError(error: unknown): void;
}) {
  const { rejectQuestion } = useOpenCodeRuntimeExtras();
  return (
    <PendingInteractions
      permissions={p.permissions}
      questions={p.questions}
      onRejectPermission={(id) => void p.reply(id, "reject").catch(p.onError)}
      onRejectQuestion={(id) => void rejectQuestion(id).catch(p.onError)}
    />
  );
}

export function Chat(p: ChatProps) {
  const [stream, setStream] = useState<string | null>(null);
  const [streamOk, setStreamOk] = useState(true);       // поток "ok" — молчание хода копится
  const [error, setError] = useState("");
  const props = useRef(p);
  props.current = p;
  const halted = useRef(false);            // обёртка остановлена (401, «место спит»)
  const mounted = useRef(false);
  const [withAgents] = useState(() => p.loadAgents !== undefined);   // панель — на всё монтирование
  const [agents, setAgents] = useState<AgentsView>({ items: undefined, known: [] });
  const [newAgent, setNewAgent] = useState(DEFAULT_AGENT);            // агент нового разговора — выбор в панели
  const [seen, setSeen] = useState<ThreadAgentSeen>({ state: "none" });   // агент открытого разговора
  const picked = useRef(false);            // студент выбрал агента — прежний разговор поверх выбора не открывается (m15)
  const [awaitingOpen, setAwaitingOpen] =   // решение по прежнему разговору ещё не принято (m1 доводки р1)
    useState(() => p.loadAgents !== undefined && p.initialSessionId !== undefined);
  const poll = useRef<AgentsPoll | null>(null);

  // Ошибка действия → строка над полем ввода. Остановку (signed_out, asleep) показывает App, своя отмена
  // и отвергнутые после остановки вызовы (halted) не показываются вовсе.
  const onError = useRef((e: unknown): void => {
    const { kind, text } = explainError(e);
    if (kind === "halted" || kind === "signed_out" || kind === "asleep" || text === "") return;
    setError(text);
  }).current;

  // отчёт — пока чат смонтирован и обёртка не остановлена: снятому или остановленному чату сессии уже нет
  const report = useRef((kind: "turn_silent" | "stream_down"): void => {
    if (halted.current || !mounted.current) return;
    props.current.onReport?.(kind);
  }).current;

  // клиент, обёртка и сторож потока — один раз на монтирование: useOpenCodeRuntime держит реестр потока по клиенту
  const [{ client, streamWatch }] = useState(() => {
    const streamWatch = createStreamWatch({ now: () => Date.now(), onRecoveredAfterLongDown: () => report("stream_down") });
    const gatewayFetch = createGatewayFetch({
      onHalt: (kind) => {
        halted.current = true;
        poll.current?.stop();              // остановленному чату список агентов не нужен
        props.current.onHalt(kind);
      },
      onStream: (s) => {
        setStream(streamLine(s));
        setStreamOk(s.last === "ok");
        streamWatch.onStream(s);
      },
    });
    // SDK зовёт fetch всегда с готовым Request (обёртка другого не принимает)
    return {
      client: createOpencodeClient({ baseUrl: `${location.origin}/pcbk/api/oc`, fetch: gatewayFetch as typeof fetch }),
      streamWatch,
    };
  });

  // тики сторожа потока: разрыв тиков (сон машины) — эпизод потери связи до него не в счёт (m4)
  useEffect(() => {
    streamWatch.tick();
    const timer = setInterval(() => streamWatch.tick(), TICK_MS);
    const onVisibility = (): void => streamWatch.tick();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [streamWatch]);

  // до рантайма: при снятии чата флаг гаснет раньше, чем рантайм закрывает поток и разговоры
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  // список агентов (R65): опрос на монтирование; ответ снятому или остановленному чату не нужен
  useEffect(() => {
    const load = props.current.loadAgents;
    if (load === undefined) return;
    const agentsPoll = createAgentsPoll({
      load: async () => {
        const r = await load();
        return r.status === 200 ? parseAgents(r.body) : null;
      },
      onItems: (items) => {
        if (halted.current || !mounted.current) return;
        setAgents((prev) => ({ items, known: items ?? prev.known }));
      },
      now: () => Date.now(),
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
    });
    poll.current = agentsPoll;
    if (!halted.current) agentsPoll.start();
    return () => {
      agentsPoll.stop();
      if (poll.current === agentsPoll) poll.current = null;
    };
  }, []);

  // агент хода: открытого разговора — из истории, нового — выбранный; пока агент разговора неизвестен — не отправлять
  const locked = seen.state === "loading" || awaitingOpen;
  const agent = seen.state === "agent" ? seen.name : newAgent;
  const runtime = useOpenCodeRuntime({
    client,
    onError,
    onThreadIdChange: (id) => {
      if (halted.current || !mounted.current) return;    // остановленный или снятый чат молчит
      setError("");                        // другой разговор — прежняя ошибка к нему не относится
      props.current.onThreadIdChange?.(id);
    },
    defaultAgent: sendAgent(agent),
    isDisabled: locked,
    isSendDisabled: locked,
  });

  // ThreadAgentWatch сообщает только смену; тот же вид — без перерисовки
  const onAgent = useRef((next: ThreadAgentSeen): void => {
    setSeen((prev) => sameSeen(prev, next) ? prev : next);
  }).current;

  // прежний разговор (I1): открыть, только если он в неархивном списке, — на первой успешной загрузке списка (Б3-m5).
  // Пока список грузится или упал, неизвестно, в архиве ли разговор: не открывать и не забывать — ждать следующей
  // загрузки (библиотека перечитывает упавший список сама: online, возврат вкладки). Решение — одно на монтирование
  useEffect(() => {
    const want = props.current.initialSessionId;           // разговор на момент монтирования
    if (want === undefined) return;
    let done = false;
    let unsubscribe = (): void => {};
    const tryOpen = (): void => {
      if (done) return;
      if (halted.current || !mounted.current) return;
      const list = runtime.threads.getState();
      if (list.loadError !== undefined) setAwaitingOpen(false);   // список упал — решать нечего, отправка открыта
      if (list.isLoading || list.loadError !== undefined) return;
      done = true;
      unsubscribe();
      if (list.mainThreadId !== list.newThreadId || picked.current || !list.threadIds.includes(want)) {
        setAwaitingOpen(false);
        if (list.mainThreadId !== list.newThreadId) return;  // студент уже начал новый разговор — не уводим
        if (picked.current) return;                          // или выбрал агента — новый разговор с ним (m15)
        props.current.onThreadIdChange?.(undefined);         // в архиве или удалён — новый разговор
        return;
      }
      // отправка откроется, когда разговор уже открыт: дальше её держит агент разговора (история ещё грузится)
      runtime.threads.switchToThread(want, { unarchive: false }).catch(onError).finally(() => setAwaitingOpen(false));
    };
    unsubscribe = runtime.threads.subscribe(() => queueMicrotask(tryOpen));   // не внутри уведомления библиотеки
    tryOpen();
    return () => {
      done = true;
      unsubscribe();
    };
  }, [runtime, onError]);

  // новый ход пошёл — ошибка прошлого действия больше не нужна; ход кончился — список агентов через 1 с (R65)
  useEffect(() => {
    let running = runtime.thread.getState().isRunning;
    return runtime.thread.subscribe(() => {
      const now = runtime.thread.getState().isRunning;
      if (now && !running) setError("");
      if (!now && running && !halted.current) poll.current?.turnEnded();
      running = now;
    });
  }, [runtime]);

  // клик по агенту в панели (только ready): новый разговор с ним (R65)
  const pick = (name: string): void => {
    if (halted.current) return;
    picked.current = true;
    setAwaitingOpen(false);                // студент выбрал сам — прежний разговор не ждём
    setNewAgent(name);
    runtime.threads.switchToNewThread().catch(onError);
  };

  // ход без прироста ответа: строка над полем ввода и отчёт (Ф11 — начало хода по isRunning, не по onNew)
  const silent = useTurnWatch(runtime, { onSilent: () => report("turn_silent"), streamOk });

  // строка «Агент: …» и подсветка в панели — агент, которому уйдёт ход; пока он неизвестен — ни строки, ни подсветки
  const shown = locked ? undefined : agent;
  // название — из списка, у встроенных без списка — запасное (m3), прочих нет в списке — имя (m14)
  const title = shown === undefined ? undefined
    : agents.known.find((a) => a.name === shown)?.title ?? BUILTIN_AGENT_TITLES.get(shown) ?? shown;
  const threads = <ThreadSidebar runtime={runtime} onError={onError} />;
  const watch = <ThreadAgentWatch onAgent={onAgent} onError={onError} />;   // внутри провайдера, ничего не рисует
  return (
    <ChatView
      runtime={runtime}
      streamLine={stream ?? undefined}
      silentLine={silent ? T_TURN_SILENT : undefined}
      error={error || undefined}
      pending={<RuntimePending onError={onError} />}
      sidebar={withAgents ? (
        <div className="side">
          <AgentPanel items={agents.items} current={shown ?? ""} onPick={pick} />
          {threads}
          {watch}
        </div>
      ) : <>{threads}{watch}</>}
      agentLine={withAgents && agents.items !== undefined && title !== undefined ? T_AGENT_LINE(title) : undefined}
    />
  );
}
