// @vitest-environment node
// Баннер состояния (Д6-R44, решение F7): уровень и текст по /status.json и опрос по часам браузера.
import { afterEach, expect, test, vi } from "vitest";
import { startStatusPolling, statusView, TEACHER_ROW, TEACHER_STUDENT_FAIL, type StatusView } from "../src/status";
import { T_STATUS_UNKNOWN } from "../src/texts";

const st = (checks: object[], extra = {}) => ({ stale: false, stale_after_s: 30, checks, ...extra });
const c = (component: string, title: string, state: string) => ({ component, title, state, detail: "", note: "" });

test("status_view_filters_and_levels", () => {
  expect(statusView(st([c("edge", "Входной прокси", "ok")]), "03")).toEqual({ level: "ok", text: "Стенд работает" });
  expect(statusView(st([c("student-05", "Рабочее место 05", "fail")]), "03").level).toBe("ok");      // чужое место
  expect(statusView(st([c("student-03", "Рабочее место 03", "fail"), c("llm", "LLM-прокси и бюджет", "warn")]), "03"))
    .toEqual({ level: "fail", text: "Сбой: Рабочее место 03" });
  expect(statusView(st([c("a", "А", "warn"), c("b", "Б", "warn"), c("c", "В", "warn"), c("d", "Г", "warn")]), "03").text)
    .toBe("Внимание: А, Б, В и ещё 1");
  expect(statusView(st([c("web", "Веб-чат", "fail"), c("gateway", "Шлюз и вход", "ok")]), "03").level).toBe("ok");  // web — для владельца
  expect(statusView(st([], { stale: true }), "03").level).toBe("unknown");
  expect(statusView("<html>", "03").level).toBe("unknown");
  expect(statusView(st([c("x", "X", "absent")]), "03").level).toBe("ok");
  // F7: строка в unknown при живом стороже — названия строк, а не «сторож не отвечает»
  expect(statusView(st([c("historian", "Историан БДРВ", "unknown"), c("llm", "LLM-прокси и бюджет", "warn")]), "03"))
    .toEqual({ level: "unknown", text: "Состояние неизвестно: Историан БДРВ" });
  expect(statusView(st([c("student-03", "Рабочее место 03", "unknown"), c("sp-ro", "Прокси сокета сторожа", "unknown")]), "03"))
    .toEqual({ level: "unknown", text: "Состояние неизвестно: Рабочее место 03, Прокси сокета сторожа" });
  expect(statusView(st([c("historian", "Историан БДРВ", "unknown"), c("edge", "Входной прокси", "fail")]), "03"))
    .toEqual({ level: "fail", text: "Сбой: Входной прокси" });                   // fail > unknown, как в page.py
  expect(statusView(st([], { stale: true }), "03")).toEqual({ level: "unknown", text: T_STATUS_UNKNOWN });
  expect(statusView("<html>", "03").text).toBe("Состояние стенда неизвестно — сторож не отвечает");
  expect(statusView({ stale: false, checks: "x" }, "03").text).toBe(T_STATUS_UNKNOWN);  // не той формы
  expect(statusView(st([c("student-03", "Рабочее место 03", "fail")]), null).level).toBe("ok");  // без места — ни одного
  // доводка 1: незнакомое состояние строки — «неизвестно», не «норма»; ответ без stale — страница не по договору
  expect(statusView(st([c("edge", "Входной прокси", "ok"), c("core", "Служба данных", "непонятно")]), "03"))
    .toEqual({ level: "unknown", text: "Состояние неизвестно: Служба данных" });
  expect(statusView({ stale_after_s: 30, checks: [c("edge", "Входной прокси", "ok")] }, "03"))
    .toEqual({ level: "unknown", text: T_STATUS_UNKNOWN });
  // итоговое ревью Д6а, P1: warn общих строк владельца студенту не показывается, их fail и unknown — показываются
  const owners = [c("gateway", "Шлюз и вход", "warn"), c("edge", "Входной прокси", "warn"), c("memory", "Память сервера", "warn"),
    c("llm", "LLM-прокси и бюджет", "warn")];
  expect(statusView(st(owners), "03")).toEqual({ level: "ok", text: "Стенд работает" });
  for (const row of owners) expect(statusView(st([row]), "03").level).toBe("ok");          // каждая по отдельности
  expect(statusView(st([...owners, c("gateway", "Шлюз и вход", "fail")]), "03"))
    .toEqual({ level: "fail", text: "Сбой: Шлюз и вход" });
  expect(statusView(st([c("gateway", "Шлюз и вход", "fail")]), "03")).toEqual({ level: "fail", text: "Сбой: Шлюз и вход" });
  expect(statusView(st([c("edge", "Входной прокси", "fail")]), "03")).toEqual({ level: "fail", text: "Сбой: Входной прокси" });
  expect(statusView(st([c("llm", "LLM-прокси и бюджет", "fail")]), "03").level).toBe("fail");
  expect(statusView(st([c("memory", "Память сервера", "unknown")]), "03"))
    .toEqual({ level: "unknown", text: "Состояние неизвестно: Память сервера" });
  // warn своего места, историана и контейнера core студенту по-прежнему виден
  expect(statusView(st([...owners, c("historian", "Историан БДРВ", "warn")]), "03"))
    .toEqual({ level: "warn", text: "Внимание: Историан БДРВ" });
  expect(statusView(st([...owners, c("student-03", "Рабочее место 03", "warn")]), "03"))
    .toEqual({ level: "warn", text: "Внимание: Рабочее место 03" });
  expect(statusView(st([c("core-container", "Серверный слой — контейнер", "warn")]), "03").level).toBe("warn");
  // Д7а: «Агенты студентов» — общая строка владельца (счётчики по всем местам): warn студенту не показывается,
  // fail и unknown — показываются, как у прочих строк владельца
  expect(statusView(st([c("agents", "Агенты студентов", "warn")]), "03")).toEqual({ level: "ok", text: "Стенд работает" });
  expect(statusView(st([c("agents", "Агенты студентов", "fail")]), "03"))
    .toEqual({ level: "fail", text: "Сбой: Агенты студентов" });
  expect(statusView(st([c("agents", "Агенты студентов", "unknown")]), "03"))
    .toEqual({ level: "unknown", text: "Состояние неизвестно: Агенты студентов" });
  expect(statusView(st([c("agents", "Агенты студентов", "warn"), c("student-03", "Рабочее место 03", "warn")]), "03"))
    .toEqual({ level: "warn", text: "Внимание: Рабочее место 03" });
  // Д7б (А6): fail «настройка рабочего места сломана» (image_config, одно место) — только владельцу; прочие fail
  // строки агентов (каталог, учёт, молчит) студенту видны, как прежде
  const broken = { ...c("agents", "Агенты студентов", "fail"), detail: "настройка рабочего места сломана — место не поднимается" };
  expect(statusView(st([broken]), "03")).toEqual({ level: "ok", text: "Стенд работает" });
  expect(statusView(st([broken, c("student-03", "Рабочее место 03", "fail")]), "03"))
    .toEqual({ level: "fail", text: "Сбой: Рабочее место 03" });
  expect(statusView(st([{ ...broken, detail: "каталог агентов недоступен — агенты не сохраняются" }]), "03"))
    .toEqual({ level: "fail", text: "Сбой: Агенты студентов" });
  expect(statusView(st([{ ...broken, component: "gateway", title: "Шлюз и вход" }]), "03").level).toBe("fail");
});

test("teacher_row_hidden_except_proxy_closed", () => {      // Д10-R106, I-3: строка кабинета — владельцу; студенту — только «прокси закрыт»
  const t = (state: string, detail = "") => ({ ...c("teacher", "Кабинет преподавателя и доли", state), detail });
  for (const row of [t("warn"), t("unknown"), t("fail", "журнал действий преподавателя не пишется — кабинет закрыт"),
                     t("fail", "LLM-прокси считает доли не так, как сохранено")]) {
    expect(statusView(st([row]), "03")).toEqual({ level: "ok", text: "Стенд работает" });
  }
  expect(statusView(st([t("fail", TEACHER_STUDENT_FAIL)]), "03"))
    .toEqual({ level: "fail", text: "Сбой: Кабинет преподавателя и доли" });
  expect(TEACHER_STUDENT_FAIL).toBe("доли бюджета не загружены — LLM-прокси закрыт для студентов");
  // сверх брифа: id строки — как в components.json сторожа; прочие строки рядом видны как прежде; у преподавателя (без
  // места) — так же
  expect(TEACHER_ROW).toBe("teacher");
  expect(statusView(st([t("unknown"), c("historian", "Историан БДРВ", "warn")]), "03"))
    .toEqual({ level: "warn", text: "Внимание: Историан БДРВ" });
  expect(statusView(st([t("fail", TEACHER_STUDENT_FAIL), c("edge", "Входной прокси", "fail")]), null))
    .toEqual({ level: "fail", text: "Сбой: Кабинет преподавателя и доли, Входной прокси" });
  expect(statusView(st([t("ok", "доли сходятся, журнал действий пишется")]), "03").level).toBe("ok");
});

// --- опрос: часы и таймеры — поддельные (vi.useFakeTimers), fetch — управляемый ---

afterEach(() => { vi.useRealTimers(); });

type Answer = { status: number; body: unknown } | "hang";
function poller(answers: Answer[], extra: Partial<Parameters<typeof startStatusPolling>[0]> = {}) {
  vi.useFakeTimers();
  const views: StatusView[] = [];
  const resolvers: ((r: Response) => void)[] = [];
  const fetchImpl = vi.fn((url: string, _init?: RequestInit) => {
    expect(url).toBe("/status.json");
    const a = answers.shift() ?? "hang";
    if (a === "hang") return new Promise<Response>((resolve) => { resolvers.push(resolve); });   // висит, сигнал не слушает
    return Promise.resolve(new Response(typeof a.body === "string" ? a.body : JSON.stringify(a.body), { status: a.status }));
  });
  const stop = startStatusPolling({ onView: (v) => views.push(v), ownPlace: "03", fetchImpl, nowMs: () => Date.now(), ...extra });
  return { views, fetchImpl, stop, resolvers };
}

test("polling_goes_unknown_by_clock", async () => {
  const p = poller([{ status: 200, body: st([c("edge", "Входной прокси", "ok")]) }]);   // дальше fetch висит
  await vi.advanceTimersByTimeAsync(0);
  expect(p.views).toEqual([{ level: "ok", text: "Стенд работает" }]);             // без мигания «неизвестно» до ответа
  await vi.advanceTimersByTimeAsync(29_000);
  expect(p.views.at(-1)!.level).toBe("ok");
  expect(p.fetchImpl).toHaveBeenCalledTimes(3);                                   // раз в 10 с: 0, 10, 20
  await vi.advanceTimersByTimeAsync(2_000);                                       // 31 с по часам с последнего свежего
  expect(p.views.at(-1)).toEqual({ level: "unknown", text: T_STATUS_UNKNOWN });
  expect(p.views).toHaveLength(2);                                                // вид — только при смене
  p.stop();
  const n = p.fetchImpl.mock.calls.length;
  await vi.advanceTimersByTimeAsync(60_000);
  expect(p.fetchImpl).toHaveBeenCalledTimes(n);                                   // после остановки — ни запроса
  expect(p.views).toHaveLength(2);
});

test("late_answer_of_old_request_ignored", async () => {
  const p = poller(["hang", { status: 200, body: st([c("edge", "Входной прокси", "fail")]) }], { timeoutMs: 60_000 });
  await vi.advanceTimersByTimeAsync(10_000);                                      // второй запрос ответил первым
  expect(p.views.at(-1)).toEqual({ level: "fail", text: "Сбой: Входной прокси" });
  p.resolvers[0](new Response(JSON.stringify(st([c("edge", "Входной прокси", "ok")])), { status: 200 }));
  await vi.advanceTimersByTimeAsync(0);
  expect(p.views.at(-1)).toEqual({ level: "fail", text: "Сбой: Входной прокси" }); // ответ №1 после №2 не применён
  p.stop();
});

test("polling_bad_answers_are_unknown", async () => {
  const p = poller([
    { status: 200, body: st([c("historian", "Историан БДРВ", "warn")]) },         // warn, который студенту виден
    { status: 502, body: "<html>сторож не отвечает</html>" },                    // страница watchdog-down.html
    { status: 200, body: st([c("historian", "Историан БДРВ", "warn")]) },
    { status: 200, body: st([], { stale: true }) },
    { status: 200, body: "не json" },
    { status: 200, body: { stale_after_s: 30, checks: [c("edge", "Входной прокси", "ok")] } },   // без stale
    { status: 200, body: st([c("edge", "Входной прокси", "ok")], { stale_after_s: 60 }) },
  ]);
  const seen: string[] = [];
  for (let i = 0; i < 7; i++) { await vi.advanceTimersByTimeAsync(i === 0 ? 0 : 10_000); seen.push(p.views.at(-1)!.level); }
  expect(seen).toEqual(["warn", "unknown", "warn", "unknown", "unknown", "unknown", "ok"]);
  await vi.advanceTimersByTimeAsync(55_000);                                      // stale_after_s из ответа — 60 с
  expect(p.views.at(-1)!.level).toBe("ok");
  p.stop();
});

test("polling_request_timeout_aborts", async () => {
  const signals: AbortSignal[] = [];
  vi.useFakeTimers();
  const views: StatusView[] = [];
  const stop = startStatusPolling({ onView: (v) => views.push(v), ownPlace: "03", nowMs: () => Date.now(),
    fetchImpl: (_url: string, init?: RequestInit) => new Promise<Response>((_, reject) => {
      const s = init!.signal!; signals.push(s); s.addEventListener("abort", () => reject(s.reason));
    }) });
  await vi.advanceTimersByTimeAsync(3_900);
  expect(views).toEqual([]);                                                      // срок запроса 4 с ещё не вышел
  await vi.advanceTimersByTimeAsync(200);
  expect(signals[0].aborted).toBe(true);
  expect(views).toEqual([{ level: "unknown", text: T_STATUS_UNKNOWN }]);          // первый же запрос повис — неизвестно
  stop();
});

test("stop_aborts_request_in_flight", async () => {
  vi.useFakeTimers();
  const signals: AbortSignal[] = [];
  const views: StatusView[] = [];
  const stop = startStatusPolling({ onView: (v) => views.push(v), ownPlace: "03", nowMs: () => Date.now(), doc: null,
    fetchImpl: (_url: string, init?: RequestInit) => new Promise<Response>((_, reject) => {
      const s = init!.signal!; signals.push(s); s.addEventListener("abort", () => reject(s.reason));
    }) });
  await vi.advanceTimersByTimeAsync(1_000);
  expect(signals).toHaveLength(1); expect(signals[0].aborted).toBe(false);
  stop();
  expect(signals[0].aborted).toBe(true);                                          // запрос в пути отменён остановкой
  await vi.advanceTimersByTimeAsync(60_000);
  expect(signals).toHaveLength(1); expect(views).toEqual([]);                     // ни запроса, ни вида после остановки
});

test("visible_again_polls_at_once", async () => {
  // вкладка вернулась из фона — запрос сразу, не через 10 с; скрытие запроса не даёт; остановка снимает подписку
  const live = new Set<() => void>();                                             // подписки на visibilitychange
  const doc = { hidden: false,
    addEventListener: (_t: "visibilitychange", fn: () => void) => { live.add(fn); },
    removeEventListener: (_t: "visibilitychange", fn: () => void) => { live.delete(fn); },
    dispatchEvent: (_e: Event) => { for (const fn of live) fn(); } };
  const p = poller([], { doc });
  expect(live.size).toBe(1);
  await vi.advanceTimersByTimeAsync(0);
  expect(p.fetchImpl).toHaveBeenCalledTimes(1);
  doc.hidden = true; doc.dispatchEvent(new Event("visibilitychange"));
  expect(p.fetchImpl).toHaveBeenCalledTimes(1);
  doc.hidden = false; doc.dispatchEvent(new Event("visibilitychange"));
  expect(p.fetchImpl).toHaveBeenCalledTimes(2);
  p.stop();
  expect(live.size).toBe(0);                                                      // остановка сняла подписку
  doc.dispatchEvent(new Event("visibilitychange"));
  expect(p.fetchImpl).toHaveBeenCalledTimes(2);
});

test("default_doc_is_global_document", async () => {
  // в среде node document нет — подписки нет, опрос работает; подложенный globalThis.document подхватывается
  const doc = Object.assign(new EventTarget(), { hidden: false });
  vi.stubGlobal("document", doc);
  try {
    const p = poller([]);
    await vi.advanceTimersByTimeAsync(0);
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(p.fetchImpl).toHaveBeenCalledTimes(2);
    p.stop();
  } finally {
    vi.unstubAllGlobals();
  }
  const q = poller([]);
  await vi.advanceTimersByTimeAsync(0);
  expect(q.fetchImpl).toHaveBeenCalledTimes(1);
  q.stop();
});
