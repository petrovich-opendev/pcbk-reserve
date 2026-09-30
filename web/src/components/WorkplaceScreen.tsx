// Экран рабочего места (Д6-R39): будит место и ждёт готовности (wakeAndWait — те же ветки и тексты, что у
// страницы входа). Готово — чат; 401 — вход; не удалось или не дождались — текст и «Повторить».
// Размонтирование отменяет ожидание (signal): опрос не идёт в пустоту.
import { useEffect, useRef, useState } from "react";
import type { Api } from "../api";
import { T_PLACE_PREPARING, T_RETRY } from "../texts";
import { wakeAndWait, type PlaceView, type Timers } from "../workplace";

export interface WorkplaceScreenProps {
  api: Api;
  onReady(): void;
  onSignedOut(): void;
  pollMs?: number;              // для тестов (preflight M11); по умолчанию — раз в секунду
  timers?: Timers;
}

const PREPARING: PlaceView = { text: T_PLACE_PREPARING, poll: true, ok: false };

export function WorkplaceScreen(p: WorkplaceScreenProps) {
  const [view, setView] = useState<PlaceView>(PREPARING);
  const [stuck, setStuck] = useState(false);            // не удалось или не дождались
  const [attempt, setAttempt] = useState(0);
  const props = useRef(p);
  props.current = p;

  useEffect(() => {
    const ctl = new AbortController();
    const { api, pollMs, timers } = props.current;
    setView(PREPARING);
    setStuck(false);
    void wakeAndWait({ api, onView: setView, pollMs, timers, signal: ctl.signal }).then((result) => {
      if (ctl.signal.aborted) return;
      if (result === "ready") props.current.onReady();
      else if (result === "signed_out") props.current.onSignedOut();
      else if (result === "failed" || result === "gave_up") setStuck(true);
    });
    return () => ctl.abort();
  }, [attempt]);

  return (
    <section className="place">
      <p className="place-state" aria-live="polite" data-kind={view.ok ? "ok" : stuck ? "fail" : "wait"}>{view.text}</p>
      {stuck && <button type="button" onClick={() => setAttempt((n) => n + 1)}>{T_RETRY}</button>}
    </section>
  );
}
