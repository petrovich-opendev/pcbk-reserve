// Молчание в браузере (Д6-R51): чат сам замечает, что ответа или связи нет, и шлёт отчёт администратору.
// - Сторож хода (turn_silent): после начала хода отсчёт TURN_SILENT_S по часам Date.now(), а не таймером — фоновая
//   вкладка троттлит таймеры, и тики лишь проверяют часы (раз в секунду и на visibilitychange). Любой прирост
//   последнего ответа ассистента (текст, рассуждение, вызов инструмента) отсчёт сбрасывает, конец хода снимает.
//   Отчёт — один на ход.
// - Сон машины (preflight Н15): вкладка видна, а между тиками больше TICK_GAP_S — ноутбук спал. Ход мог кончиться,
//   пока поток стоял, поэтому отсчёт начинается заново, а не даёт ложный отчёт на первом тике. Скрытую вкладку
//   браузер троттлит законно — там разрыв тиков не сон. Известное ограничение: если вкладка была скрыта и ноутбук
//   уснул, первый тик после возврата отчёт даст.
// - Пока поток не "ok", отсчёт хода стоит на паузе (доводка 1, m4): без потока прироста ответа не видно, и при
//   лежащей связи владелец увидел бы «ответ модели не пришёл» вместо «браузеры теряют связь».
// - Сторож потока (stream_down): связь потеряна — last !== "ok", восстановлена — last === "ok" (по failures нельзя:
//   после 200 обёртка его не сбрасывает, Д6-R43). "too_many_tabs" — не потеря связи (доводка 1): шлюз ответил, а
//   забытая четвёртая вкладка не должна красить строку; эпизод она закрывает, как "ok". Отчёт — при восстановлении
//   после потери дольше STREAM_DOWN_S (пока связи нет, отчёт не дойдёт), один на эпизод. Известное ограничение
//   (preflight Н16): петлю коротких потоков (200 и сразу конец) сторож не видит — last остаётся "ok", хотя строка
//   «переподключаюсь» видна.
// createTurnWatch и createStreamWatch — чистые функции от часов; useTurnWatch связывает сторож хода с рантаймом.
import type { AssistantRuntime, ThreadMessage } from "@assistant-ui/react";
import { useEffect, useRef, useState } from "react";
import type { StreamState } from "./gateway";

export const TURN_SILENT_S = 180;         // «Ответа нет уже 3 минуты»
export const STREAM_DOWN_S = 120;
export const TICK_GAP_S = 30;             // разрыв тиков видимой вкладки длиннее — машина спала (Н15)
export const TICK_MS = 1000;

export interface TurnWatch {
  started(): void;
  progress(fingerprint: string): void;
  ended(): void;
  tick(): void;
  pause(): void;                          // поток не "ok": молчание не копится
  resume(): void;                         // поток снова "ok": отсчёт идёт с того же места
}

export function createTurnWatch(o: {
  now(): number;
  onSilent(): void;
  silentS?: number;
  isVisible?(): boolean;                  // без него разрыв тиков сном не считается
}): TurnWatch {
  const silentMs = (o.silentS ?? TURN_SILENT_S) * 1000;
  const gapMs = TICK_GAP_S * 1000;
  let running = false;
  let since = 0;                          // начало хода или последний прирост ответа
  let fingerprint: string | null = null;
  let reported = false;
  let lastTick = 0;
  let lastVisible = false;
  let pausedAt: number | null = null;     // начало паузы (поток не "ok")

  const visible = (): boolean => o.isVisible?.() ?? false;

  return {
    started() {
      running = true;
      reported = false;
      fingerprint = null;
      since = o.now();
      lastTick = since;
      lastVisible = visible();
    },
    progress(next) {
      if (!running || next === fingerprint) return;          // тот же отпечаток — не прогресс
      fingerprint = next;
      since = o.now();
    },
    pause() {
      pausedAt ??= o.now();
    },
    resume() {
      if (pausedAt === null) return;
      const t = o.now();
      since += t - Math.max(pausedAt, since);                 // время без потока не в счёт
      pausedAt = null;
      lastTick = t;                                           // разрыв тиков на паузе — не сон
    },
    ended() {
      running = false;
      fingerprint = null;
    },
    tick() {
      if (!running) return;
      const t = o.now();
      const shown = visible();
      if (o.isVisible !== undefined && shown && lastVisible && t - lastTick > gapMs) since = t;   // сон — отсчёт заново
      lastTick = t;
      lastVisible = shown;
      if (pausedAt !== null || reported || t - since < silentMs) return;
      reported = true;
      o.onSilent();
    },
  };
}

export function createStreamWatch(o: {
  now(): number;
  onRecoveredAfterLongDown(): void;
  downS?: number;
}): { onStream(s: StreamState): void } {
  const downMs = (o.downS ?? STREAM_DOWN_S) * 1000;
  let downSince: number | null = null;    // первая потеря связи в эпизоде
  return {
    onStream(s) {
      if (s.last !== "ok" && s.last !== "too_many_tabs") {
        downSince ??= o.now();
        return;
      }
      if (downSince !== null && o.now() - downSince > downMs) o.onRecoveredAfterLongDown();
      downSince = null;
    },
  };
}

type Part = ThreadMessage["content"][number];

// последняя часть ответа: у текста и рассуждения — длина, у вызова инструмента — имя и состояние (Н14: у части-вызова
// длина не растёт, растёт состояние «идёт» → «готово»). Прочие части — по типу; новая часть меняет их число. Так
// часть retry OpenCode (адаптер даёт её частью data opencode-retry) считается приростом: повторы модели сбрасывают
// отсчёт, и ход, который только повторяет запрос к модели, turn_silent не даст. Так ли ведёт себя живое место
// (пишет ли 1.18.33 часть retry на каждую попытку) — живая проверка Б4/Б6 (m3 ревью Б3), здесь не правится.
function partMark(part: Part | undefined): string {
  if (part === undefined) return "-";
  switch (part.type) {
    case "text":
    case "reasoning":
      return `${part.type}:${part.text.length}`;
    case "tool-call":
      return `tool:${part.toolName}:${part.result === undefined ? "running" : part.isError ? "error" : "done"}`;
    default:
      return part.type;
  }
}

// отпечаток прогресса: messageId последнего ответа ассистента, число его частей и последняя часть; "" — ответа нет
export function turnFingerprint(messages: readonly ThreadMessage[]): string {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i]!;
    if (m.role !== "assistant") continue;
    return `${m.id}|${m.content.length}|${partMark(m.content.at(-1))}`;
  }
  return "";
}

// Сторож хода на рантайме чата (preflight Ф11): onNew адаптер наружу не отдаёт, поэтому начало хода — переход
// isRunning false → true в подписке runtime.thread, конец — true → false (isRunning адаптера включает busy и retry
// места). Прирост — смена отпечатка. Тик — раз в tickMs и на visibilitychange. Возвращает: показывать ли строку
// «Ответа нет уже 3 минуты» (снимается приростом ответа, концом и началом хода).
export function useTurnWatch(runtime: AssistantRuntime, o: {
  onSilent(): void;
  now?(): number;
  tickMs?: number;
  streamOk?: boolean;                     // поток "ok" (по умолчанию да); нет — отсчёт на паузе (m4)
}): boolean {
  const [silent, setSilent] = useState(false);
  const opts = useRef(o);
  opts.current = o;
  const current = useRef<TurnWatch | null>(null);
  const streamOk = o.streamOk ?? true;

  useEffect(() => {
    const watch = createTurnWatch({
      now: () => opts.current.now?.() ?? Date.now(),
      onSilent: () => {
        setSilent(true);
        opts.current.onSilent();
      },
      isVisible: () => document.visibilityState !== "hidden",
    });
    if (opts.current.streamOk === false) watch.pause();
    current.current = watch;
    let running = false;
    let fingerprint = "";
    const check = (): void => {
      const state = runtime.thread.getState();
      const next = turnFingerprint(state.messages);
      if (state.isRunning !== running) {
        running = state.isRunning;
        if (running) watch.started();
        else watch.ended();
        setSilent(false);
      } else if (next !== fingerprint) {
        setSilent(false);                 // ответ пошёл — строка «ответа нет» больше не правда
      }
      fingerprint = next;
      if (running) watch.progress(next);  // в начале хода — точка отсчёта (прежний ответ), дальше — прирост
    };
    check();
    const unsubscribe = runtime.thread.subscribe(check);
    const timer = setInterval(() => watch.tick(), opts.current.tickMs ?? TICK_MS);
    const onVisibility = (): void => watch.tick();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      current.current = null;
      unsubscribe();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [runtime]);

  useEffect(() => {
    if (streamOk) current.current?.resume();
    else current.current?.pause();
  }, [streamOk]);

  return silent;
}
