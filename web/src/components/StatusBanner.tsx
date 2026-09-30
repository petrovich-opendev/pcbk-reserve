// Баннер состояния стенда (Д6-R44): вид — от startStatusPolling (опрос /status.json раз в 10 с, по своему
// месту), рядом — «Подробнее» на страницу состояния. До первого ответа (до 4 с) текста нет: опрос молчит,
// чтобы «неизвестно» не мигало на каждой загрузке.
import { useEffect, useState } from "react";
import type { startStatusPolling, StatusView } from "../status";
import { T_STATUS_DETAILS } from "../texts";

export interface StatusBannerProps {
  ownPlace: string | null;
  startPolling: typeof startStatusPolling;
}

export function StatusBanner({ ownPlace, startPolling }: StatusBannerProps) {
  const [view, setView] = useState<StatusView | null>(null);
  useEffect(() => startPolling({ onView: setView, ownPlace }), [startPolling, ownPlace]);
  return (
    <div className="banner" role="status" data-level={view?.level ?? "wait"}>
      <span className="banner-text">{view?.text ?? ""}</span>
      <a href="/status" target="_blank" rel="noopener noreferrer">{T_STATUS_DETAILS}</a>
    </div>
  );
}
