// Вкладка «Уроки» (Д15а, DESIGN): договор ответов GET /pcbk/api/lessons (список) и GET /pcbk/api/lessons/{id}
// (текст урока). Сети и React здесь нет — их дают LessonsPage и createApi.
//
// Ответ индекса (lessons_api.py: lessons_json): {"lessons": [{"id", "title", "summary"}]}; пакета нет — пустой
// список, не отказ. Ответ урока: {"id", "title", "markdown"}. Сервер сам требует у элемента индекса ровно набор
// ключей {id, title, summary} (lessons.py: _lesson_meta) — здесь тот же разбор: лишние или пропущенные поля,
// значения не той формы — null (как и у ответа урока).

export interface LessonMeta {
  id: string;
  title: string;
  summary: string;
}

export interface Lesson {
  id: string;
  title: string;
  markdown: string;
}

const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
const isText = (v: unknown): v is string => typeof v === "string" && v !== "";

// ровно эти ключи, не больше и не меньше — как set(item) != {...} в lessons.py
function hasExactKeys(v: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(v);
  return actual.length === keys.length && keys.every((k) => Object.hasOwn(v, k));
}

function parseLessonMeta(item: unknown): LessonMeta | null {
  if (!isObject(item) || !hasExactKeys(item, ["id", "title", "summary"])) return null;
  const { id, title, summary } = item;
  if (!isText(id) || !isText(title) || !isText(summary)) return null;
  return { id, title, summary };
}

// ответ ручки {"lessons": [...]} → список; не по договору (HTML прокси, чужая форма) — null. Пустой список —
// не отказ (вкладка покажет «Уроки пока не добавлены»).
export function parseLessonIndex(body: unknown): LessonMeta[] | null {
  if (!isObject(body) || !hasExactKeys(body, ["lessons"]) || !Array.isArray(body.lessons)) return null;
  const items: LessonMeta[] = [];
  for (const raw of body.lessons) {
    const meta = parseLessonMeta(raw);
    if (meta === null) return null;
    items.push(meta);
  }
  return items;
}

// ответ ручки {"id", "title", "markdown"} → урок; не по договору — null
export function parseLesson(body: unknown): Lesson | null {
  if (!isObject(body) || !hasExactKeys(body, ["id", "title", "markdown"])) return null;
  const { id, title, markdown } = body;
  if (!isText(id) || !isText(title) || !isText(markdown)) return null;
  return { id, title, markdown };
}
