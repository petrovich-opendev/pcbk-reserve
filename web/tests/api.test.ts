// @vitest-environment node
// Вызовы шлюза вне OpenCode (Д6-R39, доводка 1): путь, метод и init запроса, сеть и не-JSON ответа.
import { expect, test } from "vitest";
import { callJson, createApi, type JsonFetch } from "../src/api";

function recorder(respond: () => Promise<Response>) {
  const seen: { path: string; init: RequestInit }[] = [];
  const fetchImpl: JsonFetch = (path, init) => { seen.push({ path, init }); return respond(); };
  return { seen, fetchImpl };
}
const ok = () => Promise.resolve(new Response('{"state":"starting"}', { status: 202 }));

test("api_paths_methods_and_init", async () => {
  const { seen, fetchImpl } = recorder(ok);
  const api = createApi(fetchImpl);
  await api.me(); await api.logout(); await api.wake(); await api.workplace();
  expect(seen.map((s) => `${s.init.method} ${s.path}`)).toEqual([
    "GET /pcbk/api/auth/me", "POST /pcbk/api/auth/logout", "POST /pcbk/api/workplace/wake", "GET /pcbk/api/workplace",
  ]);
  for (const { init } of seen) {
    expect(init.credentials).toBe("same-origin");                                  // cookie сессии своего сайта
    expect(init.headers).toEqual({ Accept: "application/json" });                  // без тела — без Content-Type
    expect(init.body).toBeUndefined();
    expect(init.mode).toBeUndefined();                                             // cors по умолчанию — браузер шлёт Origin
  }
});

test("call_json_body_is_json", async () => {
  const { seen, fetchImpl } = recorder(ok);
  const r = await callJson("POST", "/pcbk/api/x", { login: "s", n: 1 }, fetchImpl);
  expect(r).toEqual({ status: 202, body: { state: "starting" } });
  expect(seen[0].init.headers).toEqual({ Accept: "application/json", "Content-Type": "application/json" });
  expect(seen[0].init.body).toBe('{"login":"s","n":1}');
});

test("call_json_network_and_html", async () => {
  const net = recorder(() => Promise.reject(new TypeError("Failed to fetch")));
  expect(await callJson("GET", "/pcbk/api/auth/me", undefined, net.fetchImpl)).toEqual({ status: 0, body: null });
  const html = recorder(() => Promise.resolve(new Response("<html>502 Bad Gateway</html>", { status: 502 })));
  expect(await callJson("GET", "/pcbk/api/auth/me", undefined, html.fetchImpl)).toEqual({ status: 502, body: "<html>502 Bad Gateway</html>" });
  const cut = recorder(() => Promise.resolve(new Response(new ReadableStream({ start(c) { c.error(new TypeError("reset")); } }), { status: 200 })));
  expect(await callJson("GET", "/pcbk/api/workplace", undefined, cut.fetchImpl)).toEqual({ status: 0, body: null });  // обрыв тела — как сеть
});

test("ack_notice_posts_exact_body", async () => {                                // Д6-R49: тело ровно {"notice": N}
  const { seen, fetchImpl } = recorder(() => Promise.resolve(new Response(null, { status: 204 })));
  const r = await createApi(fetchImpl).ackNotice("teacher-reads-1");
  expect(r.status).toBe(204);
  expect(seen.map((s) => `${s.init.method} ${s.path}`)).toEqual(["POST /pcbk/api/auth/notice"]);
  expect(seen[0].init.credentials).toBe("same-origin");
  expect(seen[0].init.headers).toEqual({ Accept: "application/json", "Content-Type": "application/json" });
  expect(seen[0].init.body).toBe('{"notice":"teacher-reads-1"}');
});
