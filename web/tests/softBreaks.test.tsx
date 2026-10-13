// Плагин remark: одиночные переносы строк в markdown (13.10 Д15а) — показываются переносом, а не пробелом.
// Используется в MarkdownTextPrimitive как для чата (MessageParts.tsx), так и для уроков (LessonMarkdown.tsx).
import { render } from "@testing-library/react";
import { expect, test } from "vitest";
import { LessonMarkdown } from "../src/components/LessonMarkdown";

test("soft_break_renders_br", () => {
  const markdown = "**А** — 20FAKE_001\n**Б** — 20FAKE_002";
  const { container } = render(<LessonMarkdown markdown={markdown} />);

  const p = container.querySelector("p");
  expect(p).toBeTruthy();

  const breaks = p!.querySelectorAll("br");
  expect(breaks).toHaveLength(1);

  const strongs = p!.querySelectorAll("strong");
  expect(strongs).toHaveLength(2);
  expect(strongs[0].textContent).toBe("А");
  expect(strongs[1].textContent).toBe("Б");
});

test("code_block_keeps_newlines_without_br", () => {
  const markdown = "```\nline1\nline2\nline3\n```";
  const { container } = render(<LessonMarkdown markdown={markdown} />);

  const pre = container.querySelector("pre");
  expect(pre).toBeTruthy();

  const codeBlock = container.querySelector("code");
  expect(codeBlock).toBeTruthy();
  expect(codeBlock!.textContent).toContain("line1");
  expect(codeBlock!.textContent).toContain("line2");
  expect(codeBlock!.textContent).toContain("line3");

  const breaks = container.querySelectorAll("br");
  expect(breaks).toHaveLength(0);
});

test("inline_code_untouched", () => {
  const markdown = "`code with newlines`";
  const { container } = render(<LessonMarkdown markdown={markdown} />);

  const code = container.querySelector("code");
  expect(code).toBeTruthy();
  expect(code!.textContent).toContain("code with newlines");

  // inline code не должен иметь br элементов
  const breaks = container.querySelectorAll("br");
  expect(breaks).toHaveLength(0);
});

test("list_items_unaffected", () => {
  const markdown = "- а\n- б";
  const { container } = render(<LessonMarkdown markdown={markdown} />);

  const items = container.querySelectorAll("li");
  expect(items).toHaveLength(2);

  const breaks = container.querySelectorAll("br");
  expect(breaks).toHaveLength(0);
});

test("crlf_is_one_break", () => {
  const markdown = "**А**\r\n**Б**";
  const { container } = render(<LessonMarkdown markdown={markdown} />);

  const breaks = container.querySelectorAll("br");
  expect(breaks).toHaveLength(1);
});

test("lesson_markdown_soft_break", () => {
  const markdown = "**Насос 1-й ступени** — текст1\n**Насос 2-й ступени** — текст2";
  const { container } = render(<LessonMarkdown markdown={markdown} />);

  const p = container.querySelector("p");
  expect(p).toBeTruthy();

  const breaks = p!.querySelectorAll("br");
  expect(breaks).toHaveLength(1);

  const strongs = p!.querySelectorAll("strong");
  expect(strongs).toHaveLength(2);
  expect(strongs[0].textContent).toBe("Насос 1-й ступени");
  expect(strongs[1].textContent).toBe("Насос 2-й ступени");
});
