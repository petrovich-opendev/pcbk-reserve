// Шапка (Д6-R44): предупреждение «учебный стенд» (тот же текст и роль, что .stand на login.html), баннер
// состояния, вкладки (aria-pressed): у студента в фазах места и чата — «Чат», «Калькуляторы» (Д9-R88) и «События»
// (Д13, только у студента), у преподавателя в фазе кабинета — «Кабинет» и «Калькуляторы» (Д10-R110); «Вы вошли
// как …» и «Выйти». Закреплена сверху и видна всегда.
import type { startStatusPolling } from "../status";
import { STAND_WARNING, T_LOGOUT, T_SIGNED_IN, T_TAB_CABINET, T_TAB_CALC, T_TAB_CHAT, T_TAB_EVENTS, T_TABS } from "../texts";
import { StatusBanner } from "./StatusBanner";

// main — своя вкладка роли: чат студента или кабинет преподавателя; calc — «Калькуляторы»; events — «События» (Д13)
export type View = "main" | "calc" | "events";

export interface HeaderProps {
  login: string | null;         // null — /me ещё не ответил как следует
  ownPlace: string | null;      // своё место для баннера; null — места не учитываются
  startPolling: typeof startStatusPolling;
  onLogout(): void;
  leaving: boolean;             // выход уже идёт
  view: View | null;            // открытая вкладка; null — вкладок нет (загрузка, ошибка, предупреждение, выход)
  teacher: boolean;             // своя вкладка — «Кабинет», а не «Чат»
  showEvents: boolean;          // вкладка «События» — только у студента (Д13)
  onView(v: View): void;
}

export function Header(p: HeaderProps) {
  return (
    <header className="top">
      <div className="stand" role="note">{STAND_WARNING}</div>
      <div className="bar">
        <StatusBanner ownPlace={p.ownPlace} startPolling={p.startPolling} />
        {p.view !== null && (
          <nav className="tabs" aria-label={T_TABS}>
            <button type="button" aria-pressed={p.view === "main"} onClick={() => p.onView("main")}>
              {p.teacher ? T_TAB_CABINET : T_TAB_CHAT}
            </button>
            <button type="button" aria-pressed={p.view === "calc"} onClick={() => p.onView("calc")}>{T_TAB_CALC}</button>
            {p.showEvents && (
              <button type="button" aria-pressed={p.view === "events"} onClick={() => p.onView("events")}>
                {T_TAB_EVENTS}
              </button>
            )}
          </nav>
        )}
        <div className="who">
          {p.login !== null && <span>{T_SIGNED_IN(p.login)}</span>}
          <button type="button" onClick={p.onLogout} disabled={p.leaving}>{T_LOGOUT}</button>
        </div>
      </div>
    </header>
  );
}
