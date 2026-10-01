// Чат поверх шлюза (Д6-R42, R43): клиент OpenCode, обёртка fetch шлюза и рантайм — свои на каждое
// монтирование. Размонтирование (401, «место спит», «Выйти») закрывает поток адаптера (registry.dispose), а
// новое монтирование после «Разбудить» открывает последний разговор (initialSessionId от App).
// Без defaultAgent и defaultModel (агента выбирает default_agent места) и без adapters (нет вложений).
// Д6б: список разговоров слева (R52) и сторожа молчания (R51): ход без прироста ответа 3 мин — строка и отчёт
// turn_silent (один на ход), связь с потоком потеряна дольше 2 мин — отчёт stream_down при восстановлении (один на
// эпизод). Отчёт уходит через onReport, пока чат смонтирован и обёртка не остановлена (после 401 и «спит» — нет).
// Пока поток не "ok" (и при "too_many_tabs"), отсчёт хода на паузе (доводка 1, m4). Сон машины — тики сторожа потока
// раз в секунду и на visibilitychange (итоговая доводка Д6б, m4; правило — в watch.ts).
// initialSessionId не уходит в рантайм (доводка 1, I1): switchToThread библиотеки по умолчанию снимает архив
// (PATCH time.archived = null — против R52), а разговор мог уйти в архив в другой вкладке или до перезапуска. Чат
// открывает его сам — на первой успешной загрузке списка (список упал при монтировании — на той, что библиотека
// перечитает позже: online, возврат вкладки; итоговая доводка Д6б, Б3-m5), только если он есть среди неархивных и
// студент ещё не начал новый разговор, и с unarchive: false. Иначе — новый разговор, а App забывает прежний
// (onThreadIdChange(undefined)).
import {
  createOpencodeClient, useOpenCodePermissions, useOpenCodeQuestions, useOpenCodeRuntime,
  useOpenCodeRuntimeExtras, type OpenCodePermissionRequest, type OpenCodeQuestionRequest,
} from "@assistant-ui/react-opencode";
import { useEffect, useRef, useState } from "react";
import { createGatewayFetch, explainError, streamLine } from "../gateway";
import { T_TURN_SILENT } from "../texts";
import { TICK_MS, createStreamWatch, useTurnWatch } from "../watch";
import { ChatView } from "./ChatView";
import { PendingInteractions } from "./PendingInteractions";
import { ThreadSidebar } from "./ThreadSidebar";

export interface ChatProps {
  onHalt(kind: "signed_out" | "asleep"): void;
  initialSessionId?: string;
  onThreadIdChange?(id: string | undefined): void;
  onReport?(kind: "turn_silent" | "stream_down"): void;   // отчёт браузера о молчании (R51)
}

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

  const runtime = useOpenCodeRuntime({
    client,
    onError,
    onThreadIdChange: (id) => {
      if (halted.current || !mounted.current) return;    // остановленный или снятый чат молчит
      setError("");                        // другой разговор — прежняя ошибка к нему не относится
      props.current.onThreadIdChange?.(id);
    },
  });

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
      if (list.isLoading || list.loadError !== undefined) return;
      done = true;
      unsubscribe();
      if (list.mainThreadId !== list.newThreadId) return;  // студент уже начал новый разговор — не уводим
      if (!list.threadIds.includes(want)) {                // в архиве или удалён — новый разговор
        props.current.onThreadIdChange?.(undefined);
        return;
      }
      runtime.threads.switchToThread(want, { unarchive: false }).catch(onError);
    };
    unsubscribe = runtime.threads.subscribe(() => queueMicrotask(tryOpen));   // не внутри уведомления библиотеки
    tryOpen();
    return () => {
      done = true;
      unsubscribe();
    };
  }, [runtime, onError]);

  // новый ход пошёл — ошибка прошлого действия больше не нужна
  useEffect(() => {
    let running = runtime.thread.getState().isRunning;
    return runtime.thread.subscribe(() => {
      const now = runtime.thread.getState().isRunning;
      if (now && !running) setError("");
      running = now;
    });
  }, [runtime]);

  // ход без прироста ответа: строка над полем ввода и отчёт (Ф11 — начало хода по isRunning, не по onNew)
  const silent = useTurnWatch(runtime, { onSilent: () => report("turn_silent"), streamOk });

  return (
    <ChatView
      runtime={runtime}
      streamLine={stream ?? undefined}
      silentLine={silent ? T_TURN_SILENT : undefined}
      error={error || undefined}
      pending={<RuntimePending onError={onError} />}
      sidebar={<ThreadSidebar runtime={runtime} onError={onError} />}
    />
  );
}
