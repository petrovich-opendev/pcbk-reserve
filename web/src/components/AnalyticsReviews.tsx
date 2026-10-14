// Экран назначенного согласования КДМ-1 (план КДМ1, Task 6 — только интерфейс; сервер Task 7): заявки на
// согласование спорных местных сокращений, связей с историаном, местной применимости и нормативов —
// основания, старое/новое значение, область/срок, решение (принять/отклонить/запросить сведения), комментарий
// (мастер-промпт §6). Показывается только когда сервер сообщил право (`Capabilities.reviewAuthority` — сам
// компонент этого не проверяет, решение — AnalyticsPage, как источник права; дублировать проверку здесь нечем:
// без него ручки `reviews/*` и так ответят отказом). Роль `teacher` сама по себе не даёт этого права (мастер-
// промпт §6) — здесь это тоже не предполагается, право только то, что отдал `capabilities`.
//
// Д17 Task 2: для заявок `equipment_link` вместо внутреннего id связи показывается подпись оборудования
// (`display_label`) — список связей грузится здесь же, параллельно со списком заявок (equipmentLabel ниже);
// предложить связь и подобрать тег — отдельный раздел «Связи с историаном» (AnalyticsLinks.tsx), решение по
// заявке остаётся только здесь.
import { useEffect, useRef, useState } from "react";
import type { AnalyticsApi } from "../analyticsApi";
import { parseEquipmentLinkList, parseReviewList, type EquipmentLink, type ReviewDecisionKind,
         type ReviewRequestItem } from "../analytics";
import { replyText, type Reply } from "../api";
import {
  AN_REVIEW_KIND_LABELS, AN_REVIEW_STATUS_LABELS, T_AN_REVIEW_ACCEPT, T_AN_REVIEW_COMMENT,
  T_AN_REVIEW_DECIDE_CONFLICT, T_AN_REVIEW_DECIDE_ERROR, T_AN_REVIEW_DECIDE_FORBIDDEN,
  T_AN_REVIEW_DECIDE_NOT_FOUND, T_AN_REVIEW_EQUIPMENT, T_AN_REVIEW_INFO, T_AN_REVIEW_NEW, T_AN_REVIEW_OLD,
  T_AN_REVIEW_PERIOD, T_AN_REVIEW_RATIONALE, T_AN_REVIEW_REFRESH, T_AN_REVIEW_REJECT, T_AN_REVIEW_SCOPE,
  T_AN_REVIEWS, T_AN_REVIEWS_EMPTY, T_AN_REVIEWS_ERROR, T_UNKNOWN,
} from "../texts";

export interface AnalyticsReviewsProps {
  api: AnalyticsApi;
  onSignedOut(): void;
}

const noAnswer = (): Reply => ({ status: 0, body: null });

type State = { kind: "loading" } | { kind: "error"; text: string } | { kind: "data"; items: ReviewRequestItem[] };

// отказ decide() (правка итогового ревью, п.2 F2-brief): раньше проверяющий не видел отказ вовсе — тексты по
// коду, 401 — выход (как раньше), прочее (503 и незнакомый код) — общий текст, не молчание
const DECIDE_ERROR_TEXTS: Readonly<Record<number, string>> = {
  403: T_AN_REVIEW_DECIDE_FORBIDDEN, 404: T_AN_REVIEW_DECIDE_NOT_FOUND, 409: T_AN_REVIEW_DECIDE_CONFLICT,
};
const decideErrorText = (status: number): string => DECIDE_ERROR_TEXTS[status] ?? T_AN_REVIEW_DECIDE_ERROR;

function periodText(r: ReviewRequestItem): string | null {
  if (r.periodStart === null && r.periodEnd === null) return null;
  return `${r.periodStart ?? "…"} — ${r.periodEnd ?? "…"}`;
}

// подпись оборудования вместо внутреннего id заявки equipment_link (план Д17, Task 2): ищем subject_id в уже
// загруженном списке связей (load() ниже грузит его параллельно со списком заявок) — нет в списке (отказ
// загрузки, устаревший кэш) — id как раньше, не отказ разбора всей строки
function equipmentLabel(subjectId: string, links: readonly EquipmentLink[]): string {
  return links.find((l) => l.id === subjectId)?.displayLabel ?? subjectId;
}

function ReviewRow({ r, links, onDecide, busy }: {
  r: ReviewRequestItem;
  links: readonly EquipmentLink[];
  onDecide(id: string, decision: ReviewDecisionKind, comment: string): void;
  busy: boolean;
}) {
  const [comment, setComment] = useState("");
  const pending = r.status === "pending";
  const period = periodText(r);
  return (
    <li className="an-review" data-status={r.status}>
      <div className="an-review-head">
        <span className="an-review-kind">{AN_REVIEW_KIND_LABELS[r.kind] ?? r.kind}</span>
        <span className="an-review-status">{AN_REVIEW_STATUS_LABELS[r.status] ?? r.status}</span>
      </div>
      <dl className="an-review-facts">
        {r.kind === "equipment_link" && (
          <div><dt>{T_AN_REVIEW_EQUIPMENT}</dt><dd>{equipmentLabel(r.subjectId, links)}</dd></div>
        )}
        {r.oldValue !== null && (
          <div><dt>{T_AN_REVIEW_OLD}</dt><dd>{r.oldValue}</dd></div>
        )}
        <div><dt>{T_AN_REVIEW_NEW}</dt><dd>{r.newValue}</dd></div>
        <div><dt>{T_AN_REVIEW_SCOPE}</dt><dd>{r.scope}</dd></div>
        {period !== null && <div><dt>{T_AN_REVIEW_PERIOD}</dt><dd>{period}</dd></div>}
        <div><dt>{T_AN_REVIEW_RATIONALE}</dt><dd>{r.rationale}</dd></div>
        {r.comment !== null && <div><dt>{T_AN_REVIEW_COMMENT}</dt><dd>{r.comment}</dd></div>}
      </dl>
      {pending && (
        <div className="an-review-decide">
          <label className="an-review-comment-field">
            {T_AN_REVIEW_COMMENT}
            <textarea value={comment} onChange={(e) => setComment(e.target.value)} disabled={busy} />
          </label>
          <div className="an-review-actions">
            <button type="button" disabled={busy} onClick={() => onDecide(r.id, "accepted", comment)}>
              {T_AN_REVIEW_ACCEPT}
            </button>
            <button type="button" disabled={busy} onClick={() => onDecide(r.id, "rejected", comment)}>
              {T_AN_REVIEW_REJECT}
            </button>
            <button type="button" disabled={busy} onClick={() => onDecide(r.id, "info_requested", comment)}>
              {T_AN_REVIEW_INFO}
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

export function AnalyticsReviews(p: AnalyticsReviewsProps) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  // список связей — только для подписи equipment_link (equipmentLabel выше); отказ его загрузки не должен
  // прятать список заявок — он просто не обновляется, подпись остаётся резервным id (план Д17, Task 2)
  const [links, setLinks] = useState<EquipmentLink[]>([]);
  // отказ последнего decide() — отдельно от state (правка итогового ревью, п.2 F2-brief): load() ниже
  // перезагружает список после отказа (другой проверяющий мог уже решить заявку), и это не должно стирать
  // текст, который проверяющий должен увидеть
  const [decideError, setDecideError] = useState<string | null>(null);
  const props = useRef(p);
  props.current = p;
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const load = async (): Promise<void> => {
    setState({ kind: "loading" });
    const [r, linksR] = await Promise.all([
      props.current.api.reviewRequests().catch(noAnswer), props.current.api.links().catch(noAnswer),
    ]);
    if (!alive.current) return;
    if (linksR.status === 200) {
      const parsedLinks = parseEquipmentLinkList(linksR.body);
      if (parsedLinks !== null) setLinks(parsedLinks);
    }
    if (r.status === 401) { props.current.onSignedOut(); return; }
    if (r.status !== 200) { setState({ kind: "error", text: replyText(r) === T_UNKNOWN ? T_AN_REVIEWS_ERROR : replyText(r) }); return; }
    const items = parseReviewList(r.body);
    setState(items === null ? { kind: "error", text: T_AN_REVIEWS_ERROR } : { kind: "data", items });
  };

  useEffect(() => { void load(); }, []);

  const decide = async (id: string, decision: ReviewDecisionKind, comment: string): Promise<void> => {
    setDecideError(null);
    setBusy(true);
    const r = await props.current.api.decideReview(id, decision, comment).catch(noAnswer);
    setBusy(false);
    if (!alive.current) return;
    if (r.status === 401) { props.current.onSignedOut(); return; }
    if (r.status === 200) { await load(); return; }
    // отказ (403/404/409/503/прочее) — понятный текст проверяющему и перезагрузка списка: заявка могла уже
    // поменять статус (другой проверяющий), список должен это показать, а не застыть на старых данных
    setDecideError(decideErrorText(r.status));
    await load();
  };

  return (
    <section className="an-reviews">
      <div className="an-reviews-head">
        <h3>{T_AN_REVIEWS}</h3>
        <button type="button" onClick={() => void load()} disabled={state.kind === "loading"}>
          {T_AN_REVIEW_REFRESH}
        </button>
      </div>
      {decideError !== null && <p className="an-msg" role="alert">{decideError}</p>}
      {state.kind === "error" && <p className="an-msg" role="alert">{state.text}</p>}
      {state.kind === "data" && (
        state.items.length === 0 ? (
          <p className="an-note">{T_AN_REVIEWS_EMPTY}</p>
        ) : (
          <ul className="an-review-list">
            {state.items.map((r) => (
              <ReviewRow key={r.id} r={r} links={links} onDecide={(id, d, c) => void decide(id, d, c)} busy={busy} />
            ))}
          </ul>
        )
      )}
    </section>
  );
}
