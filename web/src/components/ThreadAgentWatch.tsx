// Агент открытого разговора (Д7-R65, Review Focus 1 Д7б; preflight П14, m13). Сидит внутри провайдера рантайма и
// сообщает Chat одно из трёх:
//   - «agent» — info.agent первого сообщения студента в истории разговора (threadAgent);
//   - «none» — сообщений студента нет: новый разговор (у строки списка ещё нет сессии) или история загружена, а в ней
//     их нет. Агент хода — выбранный в панели (newAgent);
//   - «loading» — агент разговора ещё неизвестен: состояние адаптера не этой сессии (рантайм разговора ещё не привязан)
//     или история ни разу не загружена. Chat закрывает отправку: иначе ход ушёл бы выбранному агенту (по умолчанию —
//     помощнику), а не агенту разговора, и Конструктор потерял бы черновик.
// Фоновое перечитывание истории (loadState снова loading, сообщения на месте) агента не теряет и отправку не закрывает.
// Состояние — useOpenCodeThreadState() с запасным EMPTY (useOpenCodeRuntimeExtras() бросает вне extras, m13), сессия —
// из строки открытого разговора в списке (threads.threadItems): пока рантайм разговора не привязан, состояние адаптера —
// чужое или EMPTY, и сверка сессий не даёт принять его за «нет сообщений». Сообщение — в useLayoutEffect, до отрисовки:
// useEffect дал бы кадр, в котором открытый разговор ещё с открытой отправкой.
import { useAuiState } from "@assistant-ui/react";
import { useOpenCodeThreadState, type OpenCodeThreadState } from "@assistant-ui/react-opencode";
import { useLayoutEffect, useRef } from "react";
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

export function ThreadAgentWatch(p: { onAgent(seen: ThreadAgentSeen): void }) {
  const sessionId = useAuiState((s) => {
    const main = s.threads.threadItems.find((item) => item.id === s.threads.mainThreadId);
    return main?.externalId ?? main?.remoteId;
  });
  const seen = seenAgent(sessionId, useOpenCodeThreadState());
  const kind = seen.state;
  const name = seen.state === "agent" ? seen.name : "";
  const onAgent = useRef(p.onAgent);
  onAgent.current = p.onAgent;
  // сообщение — только при смене (вид, имя): Chat на каждое сообщение перестраивается
  useLayoutEffect(() => {
    onAgent.current(kind === "agent" ? { state: "agent", name } : { state: kind });
  }, [kind, name]);
  return null;
}
