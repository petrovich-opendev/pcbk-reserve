// «Учётные записи» кабинета преподавателя (Д10-R110, ручки R108): раздел под таблицей мест.
// - Форма «Завести студента»: #acc-login «Логин», #acc-place «Место» — только места с free == true обзора (без
//   закрепления, в том числе за удалённым логином, m-11), button.acc-add «Завести». Подсказки до запроса (логин не по
//   правилу учёток, место не выбрано) — без вызова: сервер проверяет те же правила сам.
// - Удача заведения и «Новый пароль» — окно .password-once: пароль в <code>, «Скопировать» и button.acc-done «Я записал
//   пароль». Пароль живёт только в состоянии useAccounts до «Я записал пароль»: ни localStorage, ни журнала браузера,
//   ни адреса, ни отчёта ui_crash (отчёт несёт только вид). Пока окно открыто, кнопки учёток выключены — новый пароль не
//   перебьёт незаписанный. Состояние живёт в TeacherPage (useAccounts): окно переживает и неудачный обзор после действия.
// - У строки места со студентом (AccountButtons в tr[data-place] таблицы мест): button.acc-passwd «Новый пароль»,
//   button.acc-disable «Отключить» или button.acc-enable «Включить» (по пометке disabled), button.acc-unlock «Снять
//   замок» (если locked); aria-label — «<действие> <логин>». «Отключить» и «Новый пароль» закрывают сессии студента —
//   сначала подтверждение окном в странице (не window.confirm: браузер теста CDP его не видит). Текст подтверждения не
//   обещает «сразу»: чат студента уходит на вход по ответу 401 — не позже его следующего действия (итоговое ревью Д10б,
//   m-1).
// - Форма — подсказка о закреплении навсегда; логин вида пробной учётки (dN-probe-…) — ещё строка .acc-probe: такое место
//   администратор может освободить (ревью Б1+Б2, M-4). Это подсказка, не запрет — сервер такой логин принимает.
// - «Скопировать» удалась — «Я записал пароль» ещё и затирает буфер обмена (без гарантии: история буфера ОС не наша;
//   ревью Б1+Б2, M-7).
// - После любого ответа, кроме 401, — обзор и журнал заново (m-8): строка нового студента и его кнопки появляются сразу.
//   Ответ не 2xx — текст ответа дословно, без текста — свой запасной кабинета (tchReplyText, как у прочих ручек
//   кабинета, Д10а А4 m-4: «…сообщите администратору стенда», не «…преподавателю»); 401 — на вход. Пока ответ в пути,
//   кнопки выключены.
// Учётки преподавателей обзор не показывает — кабинет их не видит и не трогает. Стили — классы chat.css, атрибутов style нет.
import { useEffect, useRef, useState, type FormEvent } from "react";
import type { Reply } from "../api";
import { tchReplyText, type Overview, type PlaceRow } from "../teacher";
import { isLogin, isProbeLogin, type TeacherApi } from "../teacherApi";
import { T_TCH_ACC_ADD, T_TCH_ACC_ADD_HINT, T_TCH_ACC_ADD_TITLE, T_TCH_ACC_ASK_DISABLE, T_TCH_ACC_ASK_PASSWD,
         T_TCH_ACC_CANCEL, T_TCH_ACC_COPIED, T_TCH_ACC_COPY, T_TCH_ACC_COPY_FAILED, T_TCH_ACC_DISABLE,
         T_TCH_ACC_DISABLED, T_TCH_ACC_DONE, T_TCH_ACC_ENABLE, T_TCH_ACC_ENABLED, T_TCH_ACC_LABEL, T_TCH_ACC_LOGIN,
         T_TCH_ACC_LOGIN_RULE, T_TCH_ACC_NO_FREE, T_TCH_ACC_PASSWD, T_TCH_ACC_PASSWORD_FOR, T_TCH_ACC_PASSWORD_ONCE,
         T_TCH_ACC_PICK_PLACE, T_TCH_ACC_PLACE, T_TCH_ACC_PROBE_LOGIN, T_TCH_ACC_UNLOCK, T_TCH_ACC_UNLOCKED,
         T_TCH_ACC_YES_DISABLE, T_TCH_ACC_YES_PASSWD, T_TCH_ACCOUNTS, T_TCH_PICK, T_TCH_UNKNOWN } from "../texts";

export type AccountAction = "passwd" | "disable" | "enable" | "unlock";

// действие с учёткой строки места: логин — из обзора
export interface AccountAsk { action: AccountAction; login: string; place: string }

// окно пароля: логин и место — для подписи; пароль — только здесь и только до «Я записал пароль»
interface Secret { login: string; place: string; password: string }

type Note = { kind: "ok" | "fail"; text: string } | null;

export interface AccountsCtl {
  locked: boolean;                       // ответ в пути или открыто окно пароля: кнопки учёток выключены
  confirm: AccountAsk | null;            // ждёт «Да» (отключение, новый пароль)
  secret: Secret | null;
  note: Note;
  ask(a: AccountAsk): void;              // кнопка строки: отключение и новый пароль — через подтверждение
  approve(): void;
  cancel(): void;
  add(login: string, place: string): Promise<boolean>;   // true — заведено, окно пароля открыто
  forget(): void;                        // «Я записал пароль»: пароль стёрт из состояния
}

export interface UseAccountsProps {
  api: TeacherApi;
  onChanged(): void;                     // обзор и журнал заново
  onSignedOut(): void;
}

const noAnswer = (): Reply => ({ status: 0, body: null });
const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);

// пароль из ответа 201 заведения или 200 нового пароля (R108); не та форма — null
function passwordOf(body: unknown): string | null {
  return isObject(body) && typeof body.password === "string" && body.password !== "" ? body.password : null;
}

const NEEDS_CONFIRM: readonly AccountAction[] = ["disable", "passwd"];

export function useAccounts(p: UseAccountsProps): AccountsCtl {
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<AccountAsk | null>(null);
  const [secret, setSecret] = useState<Secret | null>(null);
  const [note, setNote] = useState<Note>(null);
  const props = useRef(p);
  props.current = p;
  const inFlight = useRef(false);
  const shown = useRef(false);           // окно пароля открыто — тот же запрет, что у кнопок, без ожидания отрисовки
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  // вызов ручки учётки; done — разбор ответа 2xx (false — ответ не той формы). После ответа, кроме 401, — обзор заново.
  const run = async (call: () => Promise<Reply>, done: (r: Reply) => boolean): Promise<boolean> => {
    if (inFlight.current || shown.current) return false;
    inFlight.current = true;
    setBusy(true);
    setNote(null);
    const r = await call().catch(noAnswer);
    inFlight.current = false;
    if (!alive.current) return false;
    setBusy(false);
    if (r.status === 401) {
      props.current.onSignedOut();
      return false;
    }
    let ok = false;
    if (r.status >= 200 && r.status < 300) {
      ok = done(r);
      if (!ok) setNote({ kind: "fail", text: T_TCH_UNKNOWN });
    } else {
      setNote({ kind: "fail", text: tchReplyText(r) });
    }
    props.current.onChanged();
    return ok;
  };

  const showPassword = (login: string, place: string) => (r: Reply): boolean => {
    const password = passwordOf(r.body);
    if (password === null) return false;
    shown.current = true;
    setSecret({ login, place, password });
    return true;
  };

  const okNote = (text: string) => (): boolean => {
    setNote({ kind: "ok", text });
    return true;
  };

  const act = (a: AccountAsk): Promise<boolean> => {
    const { api } = props.current;
    switch (a.action) {
      case "passwd": return run(() => api.newPassword(a.login), showPassword(a.login, a.place));
      case "disable": return run(() => api.disable(a.login), okNote(T_TCH_ACC_DISABLED(a.login)));
      case "enable": return run(() => api.enable(a.login), okNote(T_TCH_ACC_ENABLED(a.login)));
      case "unlock": return run(() => api.unlock(a.login), okNote(T_TCH_ACC_UNLOCKED(a.login)));
    }
  };

  return {
    locked: busy || secret !== null,
    confirm,
    secret,
    note,
    ask: (a) => {
      if (inFlight.current || shown.current) return;
      if (NEEDS_CONFIRM.includes(a.action)) {
        setNote(null);
        setConfirm(a);
      } else {
        setConfirm(null);
        void act(a);
      }
    },
    approve: () => {
      if (confirm === null) return;
      setConfirm(null);
      void act(confirm);
    },
    cancel: () => setConfirm(null),
    add: (login, place) => {
      setConfirm(null);
      return run(() => props.current.api.addStudent(login, place), showPassword(login, place));
    },
    forget: () => {
      shown.current = false;
      setSecret(null);
    },
  };
}

// --- кнопки строки места со студентом (в таблице мест) ---

export function AccountButtons({ row, ctl }: { row: PlaceRow; ctl: AccountsCtl }) {
  const login = row.login;
  if (login === null) return null;                       // учётки нет — свободное или закреплённое за удалённым логином
  const button = (action: AccountAction, label: string) => (
    <button type="button" className={`acc-${action}`} aria-label={T_TCH_ACC_LABEL(label, login)} disabled={ctl.locked}
            onClick={() => ctl.ask({ action, login, place: row.place })}>
      {label}
    </button>
  );
  return (
    <div className="acc-actions">
      {button("passwd", T_TCH_ACC_PASSWD)}
      {row.disabled === true ? button("enable", T_TCH_ACC_ENABLE) : button("disable", T_TCH_ACC_DISABLE)}
      {row.locked === true && button("unlock", T_TCH_ACC_UNLOCK)}
    </div>
  );
}

// --- окно пароля: копия в буфер — только по кнопке; не вышло (нет буфера, http) — пароль выделен для Ctrl+C ---

function PasswordOnce({ secret, onDone }: { secret: Secret; onDone(): void }) {
  const [copied, setCopied] = useState<"ok" | "fail" | null>(null);
  const code = useRef<HTMLElement>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const copy = async (): Promise<void> => {
    let ok = false;
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard !== undefined) {
        await navigator.clipboard.writeText(secret.password);
        ok = true;
      }
    } catch {
      /* буфер не дали — ниже выделение */
    }
    if (!alive.current) return;
    if (!ok && code.current !== null) {
      try {
        window.getSelection()?.selectAllChildren(code.current);
      } catch {
        /* выделить не вышло — пароль виден, его можно выделить самому */
      }
    }
    setCopied(ok ? "ok" : "fail");
  };

  // «Я записал пароль»: пароль копировали в буфер — буфер затирается пустой строкой (без ожидания и без гарантии)
  const done = (): void => {
    if (copied === "ok") {
      try {
        void navigator.clipboard.writeText("").catch(() => undefined);
      } catch {
        /* буфер не дали — пароль в нём остаётся, как после любого копирования */
      }
    }
    onDone();
  };

  return (
    <section className="password-once" role="dialog" aria-label={T_TCH_ACC_PASSWORD_ONCE}>
      <p className="password-once-title">{T_TCH_ACC_PASSWORD_ONCE}</p>
      <p>{T_TCH_ACC_PASSWORD_FOR(secret.login, secret.place)}</p>
      <code ref={code} translate="no">{secret.password}</code>
      <div className="password-once-actions">
        <button type="button" className="acc-copy" autoFocus onClick={() => void copy()}>{T_TCH_ACC_COPY}</button>
        <button type="button" className="acc-done send" onClick={done}>{T_TCH_ACC_DONE}</button>
      </div>
      {copied !== null && (
        <p className="tch-note" data-kind={copied === "ok" ? "ok" : "fail"} role={copied === "fail" ? "alert" : undefined}>
          {copied === "ok" ? T_TCH_ACC_COPIED : T_TCH_ACC_COPY_FAILED}
        </p>
      )}
    </section>
  );
}

// --- подтверждение отключения и нового пароля ---

function Confirm({ ask, ctl }: { ask: AccountAsk; ctl: AccountsCtl }) {
  const disable = ask.action === "disable";
  const question = disable ? T_TCH_ACC_ASK_DISABLE(ask.login) : T_TCH_ACC_ASK_PASSWD(ask.login);
  return (
    <div className="acc-confirm" role="alertdialog" aria-label={question}>
      <p>{question}</p>
      <div className="password-once-actions">
        <button type="button" className="acc-yes stop" disabled={ctl.locked} onClick={ctl.approve}>
          {disable ? T_TCH_ACC_YES_DISABLE : T_TCH_ACC_YES_PASSWD}
        </button>
        <button type="button" className="acc-cancel" autoFocus onClick={ctl.cancel}>{T_TCH_ACC_CANCEL}</button>
      </div>
    </div>
  );
}

// --- форма «Завести студента» ---

function AddStudent({ overview, ctl }: { overview: Overview; ctl: AccountsCtl }) {
  const [login, setLogin] = useState("");
  const [place, setPlace] = useState("");
  const [hint, setHint] = useState<string | null>(null);
  const free = overview.places.filter((r) => r.free).map((r) => r.place);
  const chosen = free.includes(place) ? place : "";       // место заняли (новый обзор) — выбор снят

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    if (ctl.locked) return;
    const name = login.trim();
    if (!isLogin(name)) return setHint(T_TCH_ACC_LOGIN_RULE);
    if (chosen === "") return setHint(T_TCH_ACC_PICK_PLACE);
    setHint(null);
    if (await ctl.add(name, chosen)) {
      setLogin("");
      setPlace("");
    }
  };

  return (
    <form className="calc-row acc-add-form" aria-label={T_TCH_ACC_ADD_TITLE} onSubmit={(e) => void submit(e)}>
      <div className="calc-field">
        <label htmlFor="acc-login">{T_TCH_ACC_LOGIN}</label>
        <input id="acc-login" type="text" autoComplete="off" autoCapitalize="none" spellCheck={false} maxLength={32}
               value={login} onChange={(e) => setLogin(e.target.value)} />
      </div>
      <div className="calc-field">
        <label htmlFor="acc-place">{T_TCH_ACC_PLACE}</label>
        <select id="acc-place" value={chosen} onChange={(e) => setPlace(e.target.value)}>
          <option value="">{free.length === 0 ? T_TCH_ACC_NO_FREE : T_TCH_PICK}</option>
          {free.map((nn) => <option key={nn} value={nn}>{nn}</option>)}
        </select>
      </div>
      <button type="submit" className="acc-add send" disabled={ctl.locked}>{T_TCH_ACC_ADD}</button>
      {hint !== null && <p className="tch-note acc-hint" data-kind="fail" role="alert">{hint}</p>}
      {isProbeLogin(login.trim()) && <p className="tch-hint acc-hint acc-probe">{T_TCH_ACC_PROBE_LOGIN}</p>}
    </form>
  );
}

// --- раздел ---

export interface TeacherAccountsProps {
  ctl: AccountsCtl;
  overview: Overview | null;             // null — обзора нет (сбой): формы нет, окно пароля и итог остаются
}

export function TeacherAccounts({ ctl, overview }: TeacherAccountsProps) {
  if (overview === null && ctl.secret === null && ctl.confirm === null && ctl.note === null) return null;
  return (
    <section className="tch-accounts" aria-label={T_TCH_ACCOUNTS}>
      <h3>{T_TCH_ACCOUNTS}</h3>
      {ctl.secret !== null && <PasswordOnce secret={ctl.secret} onDone={ctl.forget} />}
      {ctl.confirm !== null && <Confirm ask={ctl.confirm} ctl={ctl} />}
      {ctl.note !== null && (
        <p className="tch-note" data-kind={ctl.note.kind} role={ctl.note.kind === "fail" ? "alert" : undefined}>
          {ctl.note.text}
        </p>
      )}
      {overview !== null && (
        <>
          <h4>{T_TCH_ACC_ADD_TITLE}</h4>
          <AddStudent overview={overview} ctl={ctl} />
          <p className="tch-hint">{T_TCH_ACC_ADD_HINT}</p>
        </>
      )}
    </section>
  );
}
