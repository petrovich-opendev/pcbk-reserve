// Шапка (Д6-R44): предупреждение «учебный стенд» (тот же текст и роль, что .stand на login.html), баннер
// состояния, «Вы вошли как …» и «Выйти». Закреплена сверху и видна всегда.
import type { startStatusPolling } from "../status";
import { STAND_WARNING, T_LOGOUT, T_SIGNED_IN } from "../texts";
import { StatusBanner } from "./StatusBanner";

export interface HeaderProps {
  login: string | null;         // null — /me ещё не ответил как следует
  ownPlace: string | null;      // своё место для баннера; null — места не учитываются
  startPolling: typeof startStatusPolling;
  onLogout(): void;
  leaving: boolean;             // выход уже идёт
}

export function Header(p: HeaderProps) {
  return (
    <header className="top">
      <div className="stand" role="note">{STAND_WARNING}</div>
      <div className="bar">
        <StatusBanner ownPlace={p.ownPlace} startPolling={p.startPolling} />
        <div className="who">
          {p.login !== null && <span>{T_SIGNED_IN(p.login)}</span>}
          <button type="button" onClick={p.onLogout} disabled={p.leaving}>{T_LOGOUT}</button>
        </div>
      </div>
    </header>
  );
}
