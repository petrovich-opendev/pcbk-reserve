// tests/integration/converse.js — разговор через API OpenCode места (1.18.33); Bun из бинарника OpenCode
// (BUN_BE_BUN=1), без импортов. Вход — /req.json {url, question, model, tools, timeout_s}, пароль места — /pw.
// Печатает один JSON: session, status (done | asked | timeout), asked, final_text, error, tools,
// assistant_messages. Вопросы модели и запросы разрешений своей сессии снимаются (reject): иначе ход с
// вопросом висел бы до срока. На сроке — abort и ожидание, пока сессия не уйдёт из /session/status (К25).
const req = JSON.parse(await Bun.file("/req.json").text())
const pw = (await Bun.file("/pw").text()).trim()
const headers = {authorization: "Basic " + btoa("opencode:" + pw), "content-type": "application/json"}
const REQUEST_TIMEOUT_MS = 10000     // CONVERSE_REQUEST_S в conftest.py: по нему считается срок docker run

async function api(method, path, body) {
  const r = await fetch(req.url + path, {method, headers, body: body === undefined ? undefined : JSON.stringify(body),
                                         signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS)})
  const text = await r.text()
  if (!r.ok) throw new Error(`${method} ${path}: HTTP ${r.status}`)   // тело ответа — не в вывод
  return text ? JSON.parse(text) : null
}

const session = (await api("POST", "/session", {})).id
const prompt = {parts: [{type: "text", text: req.question}]}
if (req.model) prompt.model = req.model
if (req.tools) prompt.tools = req.tools
await api("POST", `/session/${session}/prompt_async`, prompt)

const asked = []
let messages = []
const assistants = () => messages.filter(m => m.info.role === "assistant")
// имя инструмента снятого запроса — по callID части-инструмента; нет части — имя из запроса
const toolOf = (call, fallback) => {
  const part = call && messages.flatMap(m => m.parts).find(p => p.type === "tool" && p.callID === call.callID)
  return part ? part.tool : fallback
}
const finished = async () => {
  const busy = session in (await api("GET", "/session/status"))
  messages = await api("GET", `/session/${session}/message`)
  const last = assistants().at(-1)
  return !busy && last !== undefined && (last.info.time.completed !== undefined || last.info.error !== undefined)
}

let status = "timeout"
const deadline = Date.now() + req.timeout_s * 1000
while (Date.now() < deadline) {
  if (await finished()) {
    status = asked.length ? "asked" : "done"
    break
  }
  for (const q of await api("GET", "/question")) {
    if (q.sessionID !== session) continue
    await api("POST", `/question/${q.id}/reject`)
    asked.push({kind: "question", tool: toolOf(q.tool, "question")})
  }
  for (const p of await api("GET", "/permission")) {
    if (p.sessionID !== session) continue
    await api("POST", `/permission/${p.id}/reply`, {reply: "reject"})
    asked.push({kind: "permission", tool: toolOf(p.tool, p.permission)})
  }
  await Bun.sleep(1000)
}
if (status === "timeout") {
  // ход не должен идти дальше на месте: предел 3 одновременных вызова и деньги (К25)
  await api("POST", `/session/${session}/abort`)
  const stop = Date.now() + 10000
  while (Date.now() < stop && session in (await api("GET", "/session/status"))) await Bun.sleep(500)
  messages = await api("GET", `/session/${session}/message`)
}

const last = assistants().at(-1)
console.log(JSON.stringify({
  session,
  status,
  asked,
  final_text: last ? last.parts.filter(p => p.type === "text").map(p => p.text).join("\n") : "",
  error: last?.info.error ?? null,
  tools: assistants().flatMap(m => m.parts).filter(p => p.type === "tool")
    .map(p => ({tool: p.tool, status: p.state.status, output: p.state.output ?? p.state.error ?? null})),
  assistant_messages: assistants().length,
}))
