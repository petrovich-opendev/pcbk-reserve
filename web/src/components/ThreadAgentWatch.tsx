// Агент открытого разговора (Д7-R65, Review Focus 1 Д7б; preflight П14, m13). Сидит внутри провайдера рантайма и
// сообщает Chat одно из трёх:
//   - «agent» — info.agent первого сообщения студента в истории разговора (threadAgent);
//   - «none» — сообщений студента нет: новый разговор (у строки списка ещё нет сессии) или история загружена, а в ней
//     их нет. Агент хода — выбранный в панели (newAgent);
//   - «loading» — агент разговора ещё неизвестен: состояние адаптера не этой сессии (рантайм разговора ещё не привязан)
//     или история ни разу не загружена. Chat закрывает отправку: иначе ход ушёл бы выбранному агенту (по умолчанию —
//     помощнику), а не агенту разговора, и Конструктор потерял бы черновик.
// Фоновое перечитывание истории (loadState снова loading, сообщения на месте) агента не теряет и отправку не закрывает.
// Сбой первой загрузки истории (loadState error, ни одной удачной) держит «loading»: отправлять не тому агенту хуже, чем
// не отправлять. Адаптер держит экземпляр разговора и его контроллер до снятия чата и load() сам больше не зовёт —
// поэтому каждое повторное открытие такого разговора перечитывает историю один раз (RetryHistory, доводка р1, I1):
// нагрузка — по кликам студента, без таймеров. Ошибку перечитывания показывает строка ошибки чата (onError).
// Состояние — useOpenCodeThreadState() с запасным EMPTY (useOpenCodeRuntimeExtras() бросает вне extras, m13), сессия —
// из строки открытого разговора в списке (threads.threadItems): пока рантайм разговора не привязан, состояние адаптера —
// чужое или EMPTY, и сверка сессий не даёт принять его за «нет сообщений». Сообщение — в useLayoutEffect, до отрисовки:
// useEffect дал бы кадр, в котором открытый разговор ещё с открытой отправкой.
import { useAuiState } from "@assistant-ui/react";
import { useOpenCodeRuntimeExtras, useOpenCodeThreadState, type OpenCodeThreadState } from "@assistant-ui/react-opencode";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { threadAgent } from "../agents";

export type ThreadAgentSeen = { state: "loading" } | { state: "none" } | { state: "agent"; name: string };

export function seenAgent(sessionId: string | undefined, state: OpenCodeThreadState): ThreadAgentSeen {
  if (sessionId === undefined) return { state: "none" };
  if (state.sessionId !== sessionId) return { state: "loading" };
  const name = threadAgent(state);
  if (name !== undefined) return { state: "agent", name };
  if (state.loadState.type === "ready" || state.sync.lastHistoryLoadAt !== undefined) return { state: "none" };
  return { state: "loading" };
}

// Смонтирован, пока открыт разговор с ни разу не загруженной историей (ключ — сессия: каждое открытие — новое
// монтирование). Застал первую загрузку упавшей — перечитывает один раз: extras.refresh() = controller.refresh() =
// load(true) адаптера 0.2.25. Первое открытие (idle или loading) грузит сам адаптер; повтор на том же открытии не
// делается — иначе 500 давал бы петлю загрузок. useOpenCodeRuntimeExtras() здесь не бросает: состояние адаптера —
// этой сессии, значит, extras есть.
function RetryHistory(p: { failed: boolean; onError?(error: unknown): void }) {
  const { refresh } = useOpenCodeRuntimeExtras();
  const [failedAtMount] = useState(p.failed);
  const onError = useRef(p.onError);
  onError.current = p.onError;
  useEffect(() => {
    if (failedAtMount) refresh().catch((error: unknown) => onError.current?.(error));
  }, []);   // только на монтировании: одно перечитывание на открытие
  return null;
}

export function ThreadAgentWatch(p: { onAgent(seen: ThreadAgentSeen): void; onError?(error: unknown): void }) {
  const sessionId = useAuiState((s) => {
    const main = s.threads.threadItems.find((item) => item.id === s.threads.mainThreadId);
    return main?.externalId ?? main?.remoteId;
  });
  const state = useOpenCodeThreadState();
  const seen = seenAgent(sessionId, state);
  const kind = seen.state;
  const name = seen.state === "agent" ? seen.name : "";
  const onAgent = useRef(p.onAgent);
  onAgent.current = p.onAgent;
  // сообщение — только при смене (вид, имя): Chat на каждое сообщение перестраивается
  useLayoutEffect(() => {
    onAgent.current(kind === "agent" ? { state: "agent", name } : { state: kind });
  }, [kind, name]);
  const neverLoaded = sessionId !== undefined && state.sessionId === sessionId && state.sync.lastHistoryLoadAt === undefined;
  return neverLoaded
    ? <RetryHistory key={sessionId} failed={state.loadState.type === "error"} onError={p.onError} />
    : null;
}
