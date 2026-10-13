// Части сообщения (Д6-R42). Текст модели и вывод инструментов приходят из историана и описаний тегов и
// могут нести что угодно (Review Focus 3): всё выводится текстом.
// - Текст ответа — markdown (remark-gfm) без rehype-raw: сырой HTML react-markdown показывает текстом.
// - Картинки — текстом «[картинка: <alt>]», без <img>: ни запроса, ни нарушения CSP img-src.
// - Ссылки — только http:, https:, относительные и якоря; иначе ссылка без href.
// - Вызов инструмента — строка «Запрос к данным: <что> — <состояние>» и свёрнутые аргументы и вывод,
//   каждый обрезан до MAX_TOOL_TEXT знаков.
import type {
  ImageMessagePartComponent, TextMessagePartComponent, ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive, type MarkdownTextPrimitiveProps } from "@assistant-ui/react-markdown";
import type { ComponentPropsWithoutRef } from "react";
import remarkGfm from "remark-gfm";
import { remarkSoftBreaks } from "../remarkSoftBreaks";
import {
  T_IMAGE, T_TOOL_DONE, T_TOOL_ERROR, T_TOOL_LINE, T_TOOL_RUNNING, TOOL_LABELS,
} from "../texts";

export const MAX_TOOL_TEXT = 2000;                    // аргументы и вывод инструмента под строкой (R42)
// http(s), путь своего сайта и якорь; «//хост» и «/\хост» браузер понял бы как чужой хост без схемы
const SAFE_HREF = /^(https?:|\/(?![\/\\])|#)/;

type MdProps<T extends "a" | "img"> = ComponentPropsWithoutRef<T> & { node?: unknown };

// ссылка: href — только безопасный (react-markdown уже вырезал javascript: и прочие схемы, это второй
// рубеж и отказ от mailto:, data:, file: и хоста без схемы); без Referer. Якорь (сноски GFM) — на этой же
// странице: новая вкладка открыла бы вторую копию чата и тратила бы пределы шлюза на cookie
// Экспортирован (Д15а, Ruling 2): та же ссылка — в markdown вкладки «Уроки» (LessonMarkdown), без копии логики.
export function Link({ href, title, children }: MdProps<"a">) {
  const safe = typeof href === "string" && SAFE_HREF.test(href) ? href : undefined;
  const target = safe === undefined || safe.startsWith("#") ? undefined : "_blank";
  return <a href={safe} title={title} rel="noopener noreferrer" target={target}>{children}</a>;
}

// картинка markdown — только текстом: браузер не идёт ни по какому адресу
function ImageText({ alt }: MdProps<"img">) {
  return <span className="img-text">{T_IMAGE(alt ?? "")}</span>;
}

// постоянные объекты: MarkdownTextPrimitive и MessagePrimitive.Parts сравнивают их по ссылке (memo)
const MARKDOWN_COMPONENTS: MarkdownTextPrimitiveProps["components"] = { a: Link, img: ImageText };
export const REMARK_PLUGINS: MarkdownTextPrimitiveProps["remarkPlugins"] = [remarkGfm, remarkSoftBreaks];

export const MarkdownText: TextMessagePartComponent = () => (
  <MarkdownTextPrimitive className="md" remarkPlugins={REMARK_PLUGINS} components={MARKDOWN_COMPONENTS} />
);

// текст студента — как написан, с переводами строк (без markdown)
export const PlainText: TextMessagePartComponent = ({ text }) => <p className="plain">{text}</p>;

// часть-картинка сообщения (файл image/* в ответе места) — тоже текстом
export const ImagePartText: ImageMessagePartComponent = ({ filename }) => (
  <p className="img-text">{T_IMAGE(filename ?? "")}</p>
);

function clip(text: string): string {
  return text.length > MAX_TOOL_TEXT ? text.slice(0, MAX_TOOL_TEXT) + "…" : text;
}

function asText(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);                               // цикл или BigInt — как есть
  }
}

// строка вызова инструмента: «идёт…», пока нет вывода и ход идёт; «ошибка» — отказ инструмента или ход
// прерван без вывода; иначе «готово»
export const ToolLine: ToolCallMessagePartComponent = ({ toolName, args, argsText, result, isError, status }) => {
  const what = Object.hasOwn(TOOL_LABELS, toolName) ? TOOL_LABELS[toolName]! : toolName;
  const state = isError || status.type === "incomplete" ? T_TOOL_ERROR
    : status.type === "running" || status.type === "requires-action" ? T_TOOL_RUNNING
    : T_TOOL_DONE;
  return (
    <details className="tool" data-state={state === T_TOOL_DONE ? "done" : state === T_TOOL_ERROR ? "error" : "running"}>
      <summary>{T_TOOL_LINE(what, state)}</summary>
      <pre data-part="args">{clip(argsText || asText(args))}</pre>
      {result !== undefined && <pre data-part="output">{clip(asText(result))}</pre>}
    </details>
  );
};
