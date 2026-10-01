// Чат поверх шлюза (Д6-R42, R43): клиент OpenCode, обёртка fetch шлюза и рантайм — свои на каждое
// монтирование. Размонтирование (401, «место спит», «Выйти») закрывает поток адаптера (registry.dispose), а
// новое монтирование после «Разбудить» открывает последний разговор (initialSessionId от App).
// Без defaultAgent и defaultModel (агента выбирает default_agent места) и без adapters (нет вложений).
// Д6б: список разговоров слева (R52) и сторожа молчания (R51): ход без прироста ответа 3 мин — строка и отчёт
// turn_silent (один на ход), связь с потоком потеряна дольше 2 мин — отчёт stream_down при восстановлении (один на
// эпизод). Отчёт уходит через onReport, пока чат смонтирован и обёртка не остановлена (после 401 и «спит» — нет).
import {
  createOpencodeClient, useOpenCodePermissions, useOpenCodeQuestions, useOpenCodeRuntime,
  useOpenCodeRuntimeExtras, type OpenCodePermissionRequest, type OpenCodeQuestionRequest,
} from "@assistant-ui/react-opencode";
import { useEffect, useRef, useState } from "react";
import { createGatewayFetch, explainError, streamLine } from "../gateway";
import { T_TURN_SILENT } from "../texts";
import { createStreamWatch, useTurnWatch } from "../watch";
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

  // клиент и обёртка — один раз на монтирование: useOpenCodeRuntime держит реестр потока по клиенту
  const [client] = useState(() => {
    const streamWatch = createStreamWatch({ now: () => Date.now(), onRecoveredAfterLongDown: () => report("stream_down") });
    const gatewayFetch = createGatewayFetch({
      onHalt: (kind) => {
        halted.current = true;
        props.current.onHalt(kind);
      },
      onStream: (s) => {
        setStream(streamLine(s));
        streamWatch.onStream(s);
      },
    });
    // SDK зовёт fetch всегда с готовым Request (обёртка другого не принимает)
    return createOpencodeClient({ baseUrl: `${location.origin}/pcbk/api/oc`, fetch: gatewayFetch as typeof fetch });
  });

  // до рантайма: при снятии чата флаг гаснет раньше, чем рантайм закрывает поток и разговоры
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const runtime = useOpenCodeRuntime({
    client,
    initialSessionId: p.initialSessionId,
    onError,
    onThreadIdChange: (id) => {
      if (halted.current || !mounted.current) return;    // остановленный или снятый чат молчит
      setError("");                        // другой разговор — прежняя ошибка к нему не относится
      props.current.onThreadIdChange?.(id);
    },
  });

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
  const silent = useTurnWatch(runtime, { onSilent: () => report("turn_silent") });

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
