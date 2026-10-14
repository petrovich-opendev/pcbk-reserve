// Раздел «Связи с историаном» вкладки «Аналитический чат технолога» (план Д17, Task 2; мастер-промпт §8):
// подпись оборудования из документов КДМ-1 ↔ тег историана. Список связей и их состояние видит любой вошедший
// с доступом к вкладке (мастер-промпт §8: «связь показателя с КДМ-1 согласуется отдельно, подпись PNG её не
// устанавливает») — предложить связь (подобрать тег и отправить заявку на согласование) может любой, решает —
// тот, у кого право согласования (экран «Назначенное согласование», AnalyticsReviews.tsx, как у ConflictFlag:
// создание заявки не требует особого права, только решение по ней). Подбор тега — прокси одного
// catalog_search по подписи (ручка кандидатов, Task 1 плана); отказ/пустой список службы данных показывается
// как есть, не прячется (приём ConflictFlag/AnalyticsSources — api+onSignedOut, без сети мимо AnalyticsApi).
import { useEffect, useRef, useState } from "react";
import type { AnalyticsApi } from "../analyticsApi";
import {
  parseEquipmentLinkList, parseLinkCandidates, parseReviewRequestItemResponse, type EquipmentLink,
  type LinkCandidate,
} from "../analytics";
import { replyText, type Reply } from "../api";
import { fmtLocal } from "../events";
import {
  T_AN_CANCEL, T_AN_LINK_CANDIDATE_LABEL, T_AN_LINK_CANDIDATES_TITLE, T_AN_LINK_LINKED, T_AN_LINK_LOADING,
  T_AN_LINK_NO_CANDIDATES, T_AN_LINK_PENDING, T_AN_LINK_PICK, T_AN_LINK_UNBOUND, T_AN_LINKS, T_AN_LINKS_EMPTY,
  T_AN_LINKS_ERROR, T_AN_LINKS_NO_BRIDGE, T_AN_REVIEW_REFRESH, T_AN_SEND_TO_REVIEW, T_AN_SEND_TO_REVIEW_DONE,
  T_AN_SEND_TO_REVIEW_RATIONALE, T_UNKNOWN,
} from "../texts";

export interface AnalyticsLinksProps {
  api: AnalyticsApi;
  onSignedOut(): void;
  dataBridgeAvailable: boolean;
}

const noAnswer = (): Reply => ({ status: 0, body: null });
const errorText = (r: Reply): string => { const t = replyText(r); return t === T_UNKNOWN ? T_AN_LINKS_ERROR : t; };

// форма подбора тега одной строки связи — свой стейт на карточку (приём ReviewRow.comment/ConflictFlag)
type PickState =
  | { kind: "closed" }
  | { kind: "loading" }
  | { kind: "error"; text: string }                   // отказ самой ручки кандидатов (сеть/сбой шлюза)
  | { kind: "refused"; text: string }                  // служба данных отказала (message ответа, как есть)
  | { kind: "empty" }                                  // список кандидатов пуст (не отказ)
  | { kind: "done" }
  | { kind: "choices"; candidates: LinkCandidate[]; picked: string; rationale: string; sending: boolean;
     sendError: string | null };

function LinkRow({ link, api, onSignedOut, dataBridgeAvailable, onLinked }: {
  link: EquipmentLink; api: AnalyticsApi; onSignedOut: () => void; dataBridgeAvailable: boolean;
  // правка раунда 1 ревью: раньше звала общий load() родителя — целиком размонтировал список и стирал
  // открытые/незаполненные формы подбора у ДРУГИХ строк (I1); теперь отдаёт id созданной заявки — родитель
  // точечно обновляет только эту строку (markPending), соседние LinkRow не перерисовываются вовсе
  onLinked(pendingRequestId: string | null): void;
}) {
  const [pick, setPick] = useState<PickState>({ kind: "closed" });

  const stateText = (): string => {
    if (link.state === "unbound") return T_AN_LINK_UNBOUND;
    if (link.state === "pending") return T_AN_LINK_PENDING;
    return T_AN_LINK_LINKED(link.historianTag ?? "", link.approvedBy ?? "", fmtLocal(link.approvedAt));
  };

  const openPicker = (): void => {
    setPick({ kind: "loading" });
    void (async () => {
      const r = await api.linkCandidates(link.id).catch(noAnswer);
      if (r.status === 401) { onSignedOut(); return; }
      if (r.status !== 200) { setPick({ kind: "error", text: errorText(r) }); return; }
      const parsed = parseLinkCandidates(r.body);
      if (parsed === null) { setPick({ kind: "error", text: T_AN_LINKS_ERROR }); return; }
      if (parsed.message !== null) { setPick({ kind: "refused", text: parsed.message }); return; }
      if (parsed.candidates.length === 0) { setPick({ kind: "empty" }); return; }
      setPick({
        kind: "choices", candidates: parsed.candidates, picked: "", rationale: "", sending: false, sendError: null,
      });
    })();
  };

  const closePicker = (): void => setPick({ kind: "closed" });

  const send = (): void => {
    if (pick.kind !== "choices" || pick.sending) return;
    const { picked } = pick;
    const rationale = pick.rationale.trim();
    if (picked === "" || rationale === "") return;
    setPick({ ...pick, sending: true, sendError: null });
    void (async () => {
      const r = await api.createReview({
        kind: "equipment_link", subjectId: link.id, newValue: picked, scope: "КДМ-1", rationale,
      }).catch(noAnswer);
      if (r.status === 401) { onSignedOut(); return; }
      if (r.status !== 200 && r.status !== 201) {
        const text = errorText(r);
        setPick((cur) => cur.kind === "choices" ? { ...cur, sending: false, sendError: text } : cur);
        return;
      }
      const created = parseReviewRequestItemResponse(r.body);
      setPick({ kind: "done" });
      onLinked(created?.id ?? null);
    })();
  };

  return (
    <li className="an-link" data-state={link.state}>
      <div className="an-link-head">
        <span className="an-link-label">{link.displayLabel}</span>
        <span className="an-link-state">{stateText()}</span>
      </div>
      {link.documentContext !== "" && <p className="an-link-context">{link.documentContext}</p>}
      {link.state === "unbound" && dataBridgeAvailable && (
        // кнопка-переключатель (приём AnalyticsSources — toggle/aria-expanded), не только триггер открытия:
        // так подбор можно закрыть и из состояний «пусто»/«отказ»/«ошибка», где своей кнопки «Отмена» нет
        <button type="button" onClick={pick.kind === "closed" ? openPicker : closePicker}
                aria-expanded={pick.kind !== "closed"} disabled={pick.kind === "choices" && pick.sending}>
          {T_AN_LINK_PICK}
        </button>
      )}
      {pick.kind === "loading" && <p className="an-note">{T_AN_LINK_LOADING}</p>}
      {(pick.kind === "error" || pick.kind === "refused") && <p className="an-msg" role="alert">{pick.text}</p>}
      {pick.kind === "empty" && <p className="an-note">{T_AN_LINK_NO_CANDIDATES}</p>}
      {pick.kind === "done" && <p className="an-note">{T_AN_SEND_TO_REVIEW_DONE}</p>}
      {pick.kind === "choices" && (
        <div className="an-link-picker">
          <fieldset className="an-link-candidates">
            <legend>{T_AN_LINK_CANDIDATES_TITLE}</legend>
            {pick.candidates.map((c) => (
              <label key={c.tag} className="an-link-candidate">
                <input type="radio" name={`an-link-tag-${link.id}`} value={c.tag} checked={pick.picked === c.tag}
                       disabled={pick.sending}
                       onChange={() => setPick((cur) => cur.kind === "choices" ? { ...cur, picked: c.tag } : cur)} />
                <span>{T_AN_LINK_CANDIDATE_LABEL(c.tag, c.description, c.unit)}</span>
              </label>
            ))}
          </fieldset>
          <label className="an-field">
            {T_AN_SEND_TO_REVIEW_RATIONALE}
            <textarea value={pick.rationale} disabled={pick.sending}
                      onChange={(e) => {
                        const value = e.target.value;
                        setPick((cur) => cur.kind === "choices" ? { ...cur, rationale: value } : cur);
                      }} />
          </label>
          <div className="an-row">
            <button type="button" onClick={send}
                    disabled={pick.sending || pick.picked === "" || pick.rationale.trim() === ""}>
              {T_AN_SEND_TO_REVIEW}
            </button>
            <button type="button" onClick={closePicker} disabled={pick.sending}>{T_AN_CANCEL}</button>
          </div>
          {pick.sendError !== null && <p className="an-msg" role="alert">{pick.sendError}</p>}
        </div>
      )}
    </li>
  );
}

export function AnalyticsLinks(p: AnalyticsLinksProps) {
  const [state, setState] = useState<{ kind: "loading" } | { kind: "error"; text: string }
    | { kind: "data"; items: EquipmentLink[] }>({ kind: "loading" });
  const props = useRef(p);
  props.current = p;
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const load = async (): Promise<void> => {
    setState({ kind: "loading" });
    const r = await props.current.api.links().catch(noAnswer);
    if (!alive.current) return;
    if (r.status === 401) { props.current.onSignedOut(); return; }
    if (r.status !== 200) { setState({ kind: "error", text: errorText(r) }); return; }
    const items = parseEquipmentLinkList(r.body);
    setState(items === null ? { kind: "error", text: T_AN_LINKS_ERROR } : { kind: "data", items });
  };

  useEffect(() => { void load(); }, []);

  // точечное обновление ОДНОЙ строки после успешной отправки заявки (правка раунда 1 ревью, I1): меняет только
  // свою запись в state.items, минуя "loading" — список `<ul>` не размонтируется ни на миг, каждый `LinkRow`
  // остаётся тем же React-узлом (ключ не меняется), и его локальный useState<PickState> у СОСЕДНИХ строк не
  // сбрасывается (раньше — полный load() стирал открытые/незаполненные формы подбора у всех строк сразу)
  const markPending = (linkId: string, pendingRequestId: string | null): void => {
    setState((cur) => cur.kind !== "data" ? cur : {
      kind: "data",
      items: cur.items.map((item) => item.id === linkId ? { ...item, state: "pending", pendingRequestId } : item),
    });
  };

  return (
    <section className="an-links">
      <div className="an-links-head">
        <h3>{T_AN_LINKS}</h3>
        <button type="button" onClick={() => void load()} disabled={state.kind === "loading"}>
          {T_AN_REVIEW_REFRESH}
        </button>
      </div>
      {!p.dataBridgeAvailable && <p className="an-note">{T_AN_LINKS_NO_BRIDGE}</p>}
      {state.kind === "error" && <p className="an-msg" role="alert">{state.text}</p>}
      {state.kind === "data" && (
        state.items.length === 0 ? (
          <p className="an-note">{T_AN_LINKS_EMPTY}</p>
        ) : (
          <ul className="an-link-list">
            {state.items.map((l) => (
              <LinkRow key={l.id} link={l} api={p.api} onSignedOut={p.onSignedOut}
                       dataBridgeAvailable={p.dataBridgeAvailable}
                       onLinked={(pendingRequestId) => markPending(l.id, pendingRequestId)} />
            ))}
          </ul>
        )
      )}
    </section>
  );
}

export default AnalyticsLinks;
