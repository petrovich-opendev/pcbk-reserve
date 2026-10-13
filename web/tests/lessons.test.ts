// @vitest-environment node
// Вкладка «Уроки» (Д15а, DESIGN): договор ответов GET /pcbk/api/lessons (список) и GET /pcbk/api/lessons/{id}
// (текст урока) — строгий разбор, как у «Событий» (events.ts). Сервер (lessons.py: _lesson_meta) сам требует
// у элемента индекса ровно набор ключей {id, title, summary} — лишние или пропущенные здесь тоже отказ.
import { expect, test } from "vitest";
import { parseLesson, parseLessonIndex, type Lesson, type LessonMeta } from "../src/lessons";

test("parse_lesson_index_contract", () => {
  expect(parseLessonIndex({ lessons: [{ id: "01-vhod", title: "Вход", summary: "Как войти в стенд" }] }))
    .toEqual([{ id: "01-vhod", title: "Вход", summary: "Как войти в стенд" }] satisfies LessonMeta[]);
  expect(parseLessonIndex({ lessons: [] })).toEqual([]);   // пакета нет — пустой список, не отказ
  for (const bad of [null, undefined, 7, "<html>502</html>", [], {}, { lessons: {} }, { lessons: "x" },
                     { lessons: [{ id: "01-vhod", title: "Вход", summary: "Как войти" }], extra: 1 }])
    expect(parseLessonIndex(bad), JSON.stringify(bad)).toBeNull();
});

test("parse_lesson_index_item_field_checks", () => {
  const item = (over: Partial<Record<string, unknown>> = {}) =>
    ({ id: "01-vhod", title: "Вход", summary: "Как войти в стенд", ...over });
  for (const bad of [
    item({ id: 7 }), item({ id: "" }), item({ title: 7 }), item({ title: "" }),
    item({ summary: 7 }), item({ summary: "" }),
    { id: "01-vhod", title: "Вход" },                       // пропущено summary
    { ...item(), note: "лишнее" },                          // лишнее поле элемента
    "строка", 7, null, [],
  ]) expect(parseLessonIndex({ lessons: [bad] }), JSON.stringify(bad)).toBeNull();
});

test("parse_lesson_contract", () => {
  expect(parseLesson({ id: "01-vhod", title: "Вход", markdown: "# Урок 1\n\nТекст." }))
    .toEqual({ id: "01-vhod", title: "Вход", markdown: "# Урок 1\n\nТекст." } satisfies Lesson);
  for (const bad of [
    null, undefined, 7, "<html>502</html>", [], {},
    { id: "01-vhod", title: "Вход" },                                     // пропущено markdown
    { id: "01-vhod", title: "Вход", markdown: "т", extra: 1 },            // лишнее поле
    { id: 7, title: "Вход", markdown: "т" },                              // id не строка
    { id: "01-vhod", title: 7, markdown: "т" },                           // title не строка
    { id: "01-vhod", title: "Вход", markdown: 7 },                        // markdown не строка
    { id: "", title: "Вход", markdown: "т" },
    { id: "01-vhod", title: "", markdown: "т" },
    { id: "01-vhod", title: "Вход", markdown: "" },
  ]) expect(parseLesson(bad), JSON.stringify(bad)).toBeNull();
});
