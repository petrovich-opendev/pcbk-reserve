// «Доли бюджета на модель» в кабинете (Д10-R104, R106): поля #share-from и #share-to (места), #share-amount (сумма, $) и
// две кнопки.
// - «Перевести» (button.share-move) — два изменения со сверкой «было» по обзору (transferChanges): 200 — «Доли обновлены»
//   и новый обзор; 409 (доли изменились в другой вкладке) — текст и обзор заново; 400 и 503 — текст ответа дословно.
// - «Остановить место» (button.share-floor) — одна серверная операция (I-1 р2): доля места «С места» сводится к уже
//   оплаченному, разница уходит месту «На место». Пол считает сервер под замком перевода, числа пола в вызове нет. 200 —
//   «Доли обновлены» и новый обзор; 400 и 409 — текст ответа и обзор заново (m-5): пол мог вырасти; 503 — текст.
// Подсказки до запроса (одно место, ноль, больше «доля − пол») — без вызова: сервер проверяет те же правила сам.
// 401 — на вход. Пока ответ в пути, кнопки выключены: второе нажатие не уходит.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { replyText, type Reply } from "../api";
import { parseMoney, transferChanges, type Overview } from "../teacher";
import type { TeacherApi } from "../teacherApi";
import { T_TCH_AMOUNT, T_TCH_BAD_AMOUNT, T_TCH_FLOOR, T_TCH_FLOOR_HINT, T_TCH_FREE, T_TCH_FROM, T_TCH_MOVE, T_TCH_PICK,
         T_TCH_SAME_PLACE, T_TCH_SHARES, T_TCH_SHARES_DONE, T_TCH_TO } from "../texts";

export interface TeacherSharesProps {
  api: TeacherApi;
  overview: Overview;
  onChanged(): void;                    // обзор (и журнал) заново
  onSignedOut(): void;
}

type Note = { kind: "ok" | "fail"; text: string } | null;

const noAnswer = (): Reply => ({ status: 0, body: null });

export function TeacherShares(p: TeacherSharesProps) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const inFlight = useRef(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const open = p.overview.budget !== null && p.overview.shares_ok;

  // вызов; reloadOn — коды, после которых обзор заново (кроме 200 — после него всегда)
  const send = async (call: () => Promise<Reply>, reloadOn: readonly number[]): Promise<void> => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setNote(null);
    const r = await call().catch(noAnswer);
    inFlight.current = false;
    if (!alive.current) return;
    setBusy(false);
    if (r.status === 401) return p.onSignedOut();
    if (r.status === 200) {
      setNote({ kind: "ok", text: T_TCH_SHARES_DONE });
      setAmount("");
      p.onChanged();
      return;
    }
    setNote({ kind: "fail", text: replyText(r) });
    if (reloadOn.includes(r.status)) p.onChanged();
  };

  const move = (e: FormEvent): void => {
    e.preventDefault();
    if (inFlight.current) return;
    const cents = parseMoney(amount);
    if (cents === null) return setNote({ kind: "fail", text: T_TCH_BAD_AMOUNT });
    const changes = transferChanges(p.overview, from, to, cents);
    if (typeof changes === "string") return setNote({ kind: "fail", text: changes });
    void send(() => p.api.shares(changes), [409]);
  };

  const stop = (): void => {
    if (inFlight.current) return;
    if (from === "" || to === "" || from === to) return setNote({ kind: "fail", text: T_TCH_SAME_PLACE });
    void send(() => p.api.stopPlace(from, to), [400, 409]);
  };

  const options = p.overview.places.map((row) => (
    <option key={row.place} value={row.place}>{`${row.place} — ${row.login ?? T_TCH_FREE}`}</option>
  ));
  return (
    <section className="tch-shares" aria-label={T_TCH_SHARES}>
      <h3>{T_TCH_SHARES}</h3>
      <form className="calc-row" onSubmit={move}>
        <div className="calc-field">
          <label htmlFor="share-from">{T_TCH_FROM}</label>
          <select id="share-from" value={from} onChange={(e) => setFrom(e.target.value)}>
            <option value="">{T_TCH_PICK}</option>
            {options}
          </select>
        </div>
        <div className="calc-field">
          <label htmlFor="share-to">{T_TCH_TO}</label>
          <select id="share-to" value={to} onChange={(e) => setTo(e.target.value)}>
            <option value="">{T_TCH_PICK}</option>
            {options}
          </select>
        </div>
        <div className="calc-field">
          <label htmlFor="share-amount">{T_TCH_AMOUNT}</label>
          <input id="share-amount" type="text" inputMode="decimal" autoComplete="off" value={amount} maxLength={16}
                 onChange={(e) => setAmount(e.target.value)} />
        </div>
        <button type="submit" className="share-move send" disabled={busy || !open}>{T_TCH_MOVE}</button>
        <button type="button" className="share-floor" disabled={busy || !open} onClick={stop}>{T_TCH_FLOOR}</button>
      </form>
      <p className="tch-hint">{T_TCH_FLOOR_HINT}</p>
      {note !== null && (
        <p className="tch-note" data-kind={note.kind} role={note.kind === "fail" ? "alert" : undefined}>{note.text}</p>
      )}
    </section>
  );
}
