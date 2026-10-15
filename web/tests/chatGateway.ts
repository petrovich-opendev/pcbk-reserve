// Общие помощники тестов агентов уровня Chat (AgentPanel.test.tsx, ChatAgentsSwitch.test.tsx): встроенные агенты,
// двойник шлюза на глобальном fetch (список разговоров, сессии, история с придержкой и сбоем n-й загрузки,
// prompt_async, поток /event, GET /pcbk/api/agents) и действия студента. Не тест: vitest берёт только *.test.ts(x).
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { expect, vi } from "vitest";
import type { AgentItem } from "../src/agents";

export const POMOSHNIK: AgentItem = { name: "pomoshnik", title: "Помощник технолога КДМ-1",
                                      purpose: "диалог, данные БДРВ и подготовленные знания V1/V2", kind: "builtin", state: "ready" };
export const KONSTRUKTOR: AgentItem = { name: "konstruktor", title: "Конструктор агентов",
                                        purpose: "за один разговор собирает вашего агента по узлу", kind: "builtin", state: "ready" };
export const BUILTIN = [POMOSHNIK, KONSTRUKTOR];

export const SES_K = "ses_konstruktor0123456789abcd";      // разговор с Конструктором
export const SES_P = "ses_pomoshnik0123456789abcdef";      // разговор с помощником
export const session = (id: string, title = "") =>
  ({ id, slug: "s", projectID: "p", directory: "/work", title, version: "1.18.33", time: { created: 1, updated: 1 } });
export const ocJson = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
export const userMsg = (sid: string, mid: string, agent: string, text: string) => ({
  info: { id: mid, sessionID: sid, role: "user", time: { created: 1 }, agent, model: { providerID: "openrouter", modelID: "m" } },
  parts: [{ id: `prt_${mid}`, sessionID: sid, messageID: mid, type: "text", text }],
});
export const assistantMsg = (sid: string, mid: string, parentID: string, agent: string, text: string) => ({
  info: { id: mid, sessionID: sid, role: "assistant", time: { created: 2, completed: 3 }, parentID, modelID: "m",
          providerID: "openrouter", mode: agent, agent, path: { cwd: "/work", root: "/work" }, cost: 0,
          tokens: { input: 1, output: 1, reasoning: 0, cache: { read: 0, write: 0 } }, finish: "stop" },
  parts: [{ id: `prt_${mid}`, sessionID: sid, messageID: mid, type: "text", text }],
});
export const HISTORY: Record<string, unknown[]> = {
  [SES_K]: [userMsg(SES_K, "msg_k1", "konstruktor", "собери агента по узлу 3"),
            assistantMsg(SES_K, "msg_k2", "msg_k1", "konstruktor", "Какой узел и какие теги?")],
  [SES_P]: [userMsg(SES_P, "msg_p1", "pomoshnik", "что сейчас на T1"),
            assistantMsg(SES_P, "msg_p2", "msg_p1", "pomoshnik", "T1 = 12,5")],
};

export type Answer = Response | Promise<Response> | undefined;

// Двойник шлюза: список разговоров, сессии, история (можно придержать), prompt_async, поток /event и GET
// /pcbk/api/agents (agentCalls — число вызовов). Новый разговор — POST /session с id ses_newN.
// hold(sid, n) — придержать n-ю (с 1) загрузку истории разговора sid до release(sid); fail(sid, n) — ответить на неё 500
export function chatGateway(o: { agents?: () => Answer; list?: () => Answer; hold?: (sid: string, n: number) => boolean;
                          fail?: (sid: string, n: number) => boolean; prompt?: (sid: string) => Answer }) {
  const enc = new TextEncoder();
  let ctl: ReadableStreamDefaultController<Uint8Array> | null = null;
  const held = new Map<string, (r: Response) => void>();
  const loads = new Map<string, number>();
  const gw = {
    calls: [] as { method: string; path: string; body: string }[],
    prompts: [] as { session: string; body: Record<string, unknown> }[],
    agentCalls: 0,
    agentAt: [] as number[],                    // performance.now() каждого вызова ручки агентов
    idleFrom: 0,                                // performance.now() первого session.idle последней отправки
    created: 0,
    push(...events: object[]): void { for (const e of events) ctl?.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)); },
    release(sid: string): void { held.get(sid)?.(ocJson(200, HISTORY[sid] ?? [])); held.delete(sid); },
    asked: (path: string): boolean => gw.calls.some((c) => c.path === path),
    loads: (sid: string): number => gw.calls.filter((c) => c.path === `/pcbk/api/oc/session/${sid}/message`).length,
  };
  const answer = (method: string, path: string, body: string): Answer => {
    if (path === "/pcbk/api/agents") {
      gw.agentCalls += 1;
      gw.agentAt.push(performance.now());
      return o.agents ? o.agents() : ocJson(200, { agents: BUILTIN, own: 0, limit: 20 });
    }
    if (path === "/pcbk/api/oc/event") {
      return new Response(new ReadableStream<Uint8Array>({
        start(c) { ctl = c; c.enqueue(enc.encode(`data: ${JSON.stringify({ type: "server.connected", properties: {} })}\n\n`)); },
        cancel() { ctl = null; },
      }), { status: 200, headers: { "content-type": "text/event-stream" } });
    }
    if (path === "/pcbk/api/oc/experimental/session")
      return o.list ? o.list() : ocJson(200, [session(SES_K, "Агент по узлу 3"), session(SES_P, "Значение T1")]);
    if (method === "POST" && path === "/pcbk/api/oc/session") {
      gw.created += 1;
      return ocJson(200, session(`ses_new${gw.created}abcdefghijklmnopqr`));
    }
    const m = /^\/pcbk\/api\/oc\/session\/([^/]+)(\/.*)?$/.exec(path);
    if (m === null) return undefined;
    const [, sid, rest] = m;
    if (rest === undefined && method === "GET") return ocJson(200, session(sid!));
    if (rest === "/message" && method === "GET") {
      const n = (loads.get(sid!) ?? 0) + 1;
      loads.set(sid!, n);
      if (o.hold?.(sid!, n)) return new Promise<Response>((resolve) => { held.set(sid!, resolve); });
      if (o.fail?.(sid!, n)) return ocJson(500, { name: "UnknownError", data: { message: "История разговора не загрузилась" } });
      return ocJson(200, HISTORY[sid!] ?? []);
    }
    if (rest === "/summarize") return ocJson(200, true);                         // самозаголовок разговора
    if (rest === "/prompt_async") {
      gw.prompts.push({ session: sid!, body: JSON.parse(body) });
      return o.prompt?.(sid!) ?? new Response(null, { status: 204 });
    }
    return undefined;
  };
  vi.stubGlobal("fetch", vi.fn(async (input: Request | string, init?: RequestInit) => {
    const r = typeof input === "string" ? new Request(new URL(input, location.origin), init) : input;
    const path = new URL(r.url).pathname;
    const body = r.body ? await r.clone().text() : "";
    gw.calls.push({ method: r.method, path, body });
    return answer(r.method, path, body) ?? ocJson(404, { name: "NotFound", data: { message: "нет" } });
  }));
  return gw;
}


// срок теста уровня Chat — с запасом (обычно 0,3–2,5 с): при 5 с по умолчанию под нагрузкой тест падал по сроку, а его
// хвост продолжал жать «Отправить» по screen уже следующего теста
export const CHAT_TIMEOUT_MS = 20_000;

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
export const box = () => screen.getByRole("textbox", { name: "Ваш вопрос агенту" }) as HTMLTextAreaElement;
export const agentLine = (c: HTMLElement) => c.querySelector(".agent-line")?.textContent ?? null;

// отправка как у студента: поле и кнопка доступны → текст → «Отправить» → prompt_async ушёл → ход кончился (session.idle)
export async function send(gw: ReturnType<typeof chatGateway>, text: string) {
  const before = gw.prompts.length;
  await waitFor(() => expect(box().disabled).toBe(false));
  fireEvent.change(box(), { target: { value: text } });
  const send = await screen.findByRole("button", { name: "Отправить" }) as HTMLButtonElement;
  await waitFor(() => expect(send.disabled).toBe(false));
  fireEvent.click(send);
  await waitFor(() => expect(gw.prompts.length).toBe(before + 1));
  const sid = gw.prompts.at(-1)!.session;
  gw.idleFrom = performance.now();                                                // конец хода — не раньше этой метки
  await waitFor(() => {
    gw.push({ type: "session.idle", properties: { sessionID: sid } });
    expect(screen.queryByRole("button", { name: "Отправить" })).toBeTruthy();
  });
  return gw.prompts.at(-1)!;
}

export const pickAgent = async (name: RegExp) => fireEvent.click(await screen.findByRole("button", { name }));
export const openThread = (c: HTMLElement, sid: string) =>
  fireEvent.click(c.querySelector(`[data-session-id="${sid}"] .thread-open`)!);
