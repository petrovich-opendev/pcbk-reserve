// «Основания» ответа аналитики КДМ-1 (план КДМ1, Task 6; мастер-промпт §6, §8): карточки источников под каждым
// разделом ответа — название/локатор, ограничение применимости и версия корпуса; по запросу — сам сохранённый
// фрагмент (GET /pcbk/api/analytics/evidence/{id}) текстом, без markdown (это цитата источника, не ответ модели:
// лишнего форматирования ей не добавляем). Сети вне AnalyticsApi здесь нет — отказ ручки общим текстом.
import { useState } from "react";
import type { AnalyticsApi } from "../analyticsApi";
import type { EvidenceRef, Locator } from "../analytics";
import { parseEvidenceItem } from "../analytics";
import {
  T_AN_CORPUS_VERSION, T_AN_FRAGMENT_ERROR, T_AN_HIDE_FRAGMENT, T_AN_LIMITATION, T_AN_LOC_IMAGE, T_AN_LOC_PDF,
  T_AN_LOC_PUBLIC, T_AN_LOC_XLSX, T_AN_NO_SOURCES, T_AN_SHOW_FRAGMENT, T_AN_SOURCE_CHECKED, T_AN_SOURCE_CODE,
  T_AN_SOURCES,
} from "../texts";

export interface AnalyticsSourcesProps {
  evidenceRefs: EvidenceRef[];
  api: AnalyticsApi;
}

function locatorText(l: Locator): string {
  switch (l.type) {
    case "pdf_page": return T_AN_LOC_PDF(l.page);
    case "xlsx_cell": return T_AN_LOC_XLSX(l.sheet, l.cell);
    case "image_label": return T_AN_LOC_IMAGE(l.file, l.label);
    case "public_section": return T_AN_LOC_PUBLIC(l.section);
  }
}

// раскрытая карточка (после успешного GET evidence/{id}): название/тип/дата источника — из EvidenceItem, их
// нет до раскрытия (мастер-промпт §6, правка итогового ревью п.1 F2-brief); локатор берём из самого item (тот
// же, что у evidenceRef — сервер не меняет локатор между секцией и пакетом, но это его собственное поле)
type FragmentState =
  | { kind: "hidden" }
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "text"; text: string; title: string | null; type: string | null; date: string | null; locator: Locator };

function SourceItem({ item: evidenceRef, api }: { item: EvidenceRef; api: AnalyticsApi }) {
  const [fragment, setFragment] = useState<FragmentState>({ kind: "hidden" });
  const fragmentId = evidenceRef.fragmentId;
  const revealed = fragment.kind === "text";

  const toggle = (): void => {
    if (fragment.kind === "text" || fragment.kind === "error") { setFragment({ kind: "hidden" }); return; }
    if (fragment.kind === "loading") return;
    if (fragmentId === null) return;
    setFragment({ kind: "loading" });
    void (async () => {
      try {
        const r = await api.evidence(fragmentId);
        if (r.status !== 200) { setFragment({ kind: "error" }); return; }
        const item = parseEvidenceItem(r.body);
        setFragment(item === null ? { kind: "error" } : {
          kind: "text", text: item.text, title: item.sourceTitle, type: item.sourceType, date: item.sourceDate,
          locator: item.locator,
        });
      } catch {
        setFragment({ kind: "error" });
      }
    })();
  };

  return (
    <li className="an-source">
      {revealed ? (
        <>
          {/* название вместо кода (вместо одного "PUB-01"); код — мелко рядом, своим узлом (правка итогового
              ревью, п.1 F2-brief). Текст — только текстом (JSX экранирует сам), XSS-строка в названии остаётся
              текстом; название и код — отдельные элементы (не один узел с конкатенацией), чтобы название
              оставалось отдельным текстовым узлом как написано, без хвоста код а рядом */}
          <span className="an-source-title-row">
            <span className="an-source-title">{fragment.title ?? evidenceRef.sourceId}</span>
            <span className="an-source-code">{T_AN_SOURCE_CODE(evidenceRef.sourceId)}</span>
          </span>
          {fragment.type !== null && <span className="an-source-type">{fragment.type}</span>}
          {fragment.date !== null && <span className="an-source-date">{T_AN_SOURCE_CHECKED(fragment.date)}</span>}
          <span className="an-source-locator">{locatorText(fragment.locator)}</span>
        </>
      ) : (
        <span className="an-source-locator">{evidenceRef.sourceId} — {locatorText(evidenceRef.locator)}</span>
      )}
      {evidenceRef.limitation !== "" && (
        <span className="an-source-limitation">{T_AN_LIMITATION}: {evidenceRef.limitation}</span>
      )}
      <span className="an-source-version">{T_AN_CORPUS_VERSION(evidenceRef.corpusVersion)}</span>
      {fragmentId !== null && (
        <>
          <button type="button" className="an-source-toggle" onClick={toggle} disabled={fragment.kind === "loading"}
                  aria-expanded={fragment.kind === "text" || fragment.kind === "error"}>
            {fragment.kind === "text" || fragment.kind === "error" ? T_AN_HIDE_FRAGMENT : T_AN_SHOW_FRAGMENT}
          </button>
          {fragment.kind === "error" && <p className="an-msg" role="alert">{T_AN_FRAGMENT_ERROR}</p>}
          {revealed && <p className="an-source-fragment">{fragment.text}</p>}
        </>
      )}
    </li>
  );
}

export function AnalyticsSources(p: AnalyticsSourcesProps) {
  if (p.evidenceRefs.length === 0) return <p className="an-note">{T_AN_NO_SOURCES}</p>;
  return (
    <div className="an-sources">
      <h4>{T_AN_SOURCES}</h4>
      <ul className="an-source-list">
        {p.evidenceRefs.map((r, i) => <SourceItem key={i} item={r} api={p.api} />)}
      </ul>
    </div>
  );
}
