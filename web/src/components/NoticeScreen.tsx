// Предупреждение при первом входе (Д6-R49): текст редакции и «Понятно». Пока студент не подтвердил, App не
// монтирует чат и не будит место. onAck — подтверждение: null — принято (App ведёт дальше), строка — текст ошибки,
// экран остаётся и кнопку можно нажать снова. Пока подтверждение в пути, кнопка выключена: второе не уходит.
import { useEffect, useRef, useState } from "react";
import { T_NOTICE_ACK } from "../texts";

export interface NoticeScreenProps {
  text: string;
  onAck(): Promise<string | null>;
}

export function NoticeScreen(p: NoticeScreenProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const ack = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    const failed = await p.onAck();
    if (!mounted.current) return;                       // принято — экран уже снят
    setBusy(false);
    setError(failed);
  };

  return (
    <section className="phase notice">
      <p className="notice-text">{p.text}</p>
      {error !== null && <p data-kind="fail">{error}</p>}
      <button type="button" disabled={busy} onClick={() => void ack()}>{T_NOTICE_ACK}</button>
    </section>
  );
}
