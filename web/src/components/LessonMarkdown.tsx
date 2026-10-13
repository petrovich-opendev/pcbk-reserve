// Текст урока (Д15а, DESIGN): тот же разбор markdown, что у чата (MessageParts.tsx) — MarkdownTextPrimitive +
// remark-gfm, без rehype-raw (сырой HTML — текстом), безопасный Link (экспортирован оттуда, без копии логики, Ruling 2).
// Текст урока не приходит частью сообщения чата — своего MessagePart-контекста у него нет, поэтому он подставлен
// TextMessagePartProvider (@assistant-ui/react): тот же приём, что используют сами примитивы assistant-ui для
// показа текста вне разговора.
// Картинки — <img> только на свои: src вида img/<имя> по шаблону пакета уроков (IMAGE_NAME_RE lessons.py), адрес —
// GET /pcbk/api/lessons/img/<имя>. Внешний адрес или выход за каталог урока (../, абсолютный путь, чужой хост) —
// текстом, как ImageText чата (T_IMAGE): браузер по такому адресу не идёт.
import { TextMessagePartProvider } from "@assistant-ui/react";
import { MarkdownTextPrimitive, type MarkdownTextPrimitiveProps } from "@assistant-ui/react-markdown";
import type { ComponentPropsWithoutRef } from "react";
import remarkGfm from "remark-gfm";
import { T_IMAGE } from "../texts";
import { Link } from "./MessageParts";

export interface LessonMarkdownProps {
  markdown: string;
}

type ImgProps = ComponentPropsWithoutRef<"img"> & { node?: unknown };

// имя — ровно как IMAGE_NAME_RE пакета уроков (lessons.py), с префиксом «img/» (относительный путь markdown)
const LESSON_IMG_SRC_RE = /^img\/[a-z0-9-]{1,60}\.(?:png|jpg|jpeg|webp)$/;

function LessonImage({ src, alt }: ImgProps) {
  if (typeof src === "string" && LESSON_IMG_SRC_RE.test(src)) {
    return <img src={`/pcbk/api/lessons/${src}`} alt={alt ?? ""} loading="lazy" />;
  }
  return <span className="img-text">{T_IMAGE(alt ?? "")}</span>;
}

// постоянные объекты: MarkdownTextPrimitive сравнивает их по ссылке (memo), как в MessageParts.tsx
const MARKDOWN_COMPONENTS: MarkdownTextPrimitiveProps["components"] = { a: Link, img: LessonImage };
const REMARK_PLUGINS: MarkdownTextPrimitiveProps["remarkPlugins"] = [remarkGfm];

export function LessonMarkdown(p: LessonMarkdownProps) {
  return (
    <TextMessagePartProvider text={p.markdown}>
      <MarkdownTextPrimitive className="md" remarkPlugins={REMARK_PLUGINS} components={MARKDOWN_COMPONENTS} />
    </TextMessagePartProvider>
  );
}
