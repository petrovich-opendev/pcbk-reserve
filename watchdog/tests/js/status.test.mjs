// status.test.mjs — node --test; fetch, часы и таймеры поддельные (mock.timers)
import { test, mock, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { startPolling } from '../../pcbk_watchdog/static/status.js';

const STALE_AFTER_S = 30;

class FakeEl {
  constructor(tag, attrs = {}) {
    this.tagName = tag;
    this.hidden = false;
    this.textContent = '';
    this.className = '';
    this.children = [];
    this.attrs = { ...attrs };
  }
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  append(...els) { this.children.push(...els); }
  replaceChildren(...els) { this.children = els; }
}

// страница в том виде, в каком её отдал сервер: свежая — полоса скрыта
function fakeDoc({ hidden = true, ageS = '2' } = {}) {
  const els = {
    silence: Object.assign(new FakeEl('div'), { hidden }),
    age: new FakeEl('span', { 'data-age-s': ageS }),
    overall: new FakeEl('span'),
    'checked-at': new FakeEl('time'),
    checks: new FakeEl('ul'),
  };
  return { els, getElementById: (id) => els[id] ?? null, createElement: (tag) => new FakeEl(tag) };
}

const body = (stale) => ({
  stale, stale_after_s: STALE_AFTER_S, age_s: 1, overall: stale ? 'unknown' : 'warn',
  checked_at: '2026-09-29T17:00:05.123456+05:00',
  checks: [{ component: 'memory', title: 'Память сервера', state: 'warn', detail: 'свободно меньше 2048 МиБ' }],
});
const reply = (status, json) => ({ status, json: async () => json });
const fresh = () => Promise.resolve(reply(200, body(false)));
const staleJson = () => Promise.resolve(reply(200, body(true)));
const http502 = () => Promise.resolve(reply(502, null));
const hang = () => new Promise(() => {});   // не завершается и не слушает отмену

beforeEach(() => mock.timers.enable({ apis: ['setInterval', 'setTimeout', 'Date'], now: 0 }));
afterEach(() => mock.timers.reset());

const flush = () => new Promise((resolve) => setImmediate(resolve));

// часы идут по секунде, после каждой — дать разрешиться промисам fetch
async function advance(ms) {
  for (let t = 0; t < ms; t += 1000) {
    mock.timers.tick(1000);
    await flush();
  }
}

async function start(doc, fetchImpl) {
  const calls = [];
  const poller = startPolling({
    fetchImpl: (url, opts) => { calls.push({ url, opts }); return fetchImpl(url, opts); },
    doc, nowMs: () => Date.now(), setIntervalImpl: (fn, ms) => setInterval(fn, ms),
    staleAfterS: STALE_AFTER_S,
  });
  await flush();
  return { poller, calls };
}

test('no banner while fresh', async () => {
  const doc = fakeDoc();
  const { calls } = await start(doc, fresh);
  for (let i = 0; i < 12; i++) {
    await advance(5000);
    assert.equal(doc.els.silence.hidden, true);
  }
  assert.ok(calls.length >= 12);
  assert.equal(calls[0].url, '/status.json');
  // список перерисован из ответа, текст — через textContent
  const row = doc.els.checks.children[0];
  assert.equal(row.className, 'check s-warn');
  assert.deepEqual(row.children.map((c) => c.textContent),
    ['Память сервера', 'внимание', 'свободно меньше 2048 МиБ']);
  assert.equal(doc.els['checked-at'].textContent, '29.09.2026 17:00:05 UTC+05:00');
  assert.match(doc.els.age.textContent, /^обновлено \d+ с назад$/);
});

test('banner on stale json', async () => {
  const doc = fakeDoc();
  await start(doc, staleJson);
  await advance(5000);
  assert.equal(doc.els.silence.hidden, false);
});

test('banner on http error', async () => {
  const doc = fakeDoc();
  await start(doc, http502);
  await advance(5000);
  assert.equal(doc.els.silence.hidden, false);
});

test('banner on network error', async () => {
  const doc = fakeDoc();
  await start(doc, () => Promise.reject(new TypeError('Failed to fetch')));
  await advance(5000);
  assert.equal(doc.els.silence.hidden, false);
});

test('banner when fetch hangs', async () => {
  const doc = fakeDoc();
  await start(doc, hang);
  await advance(STALE_AFTER_S * 1000);
  assert.equal(doc.els.silence.hidden, true);       // срок ещё не вышел
  await advance(1000);
  assert.equal(doc.els.silence.hidden, false);      // staleAfterS + 1 с по часам браузера
});

test('banner hides after recovery', async () => {
  const doc = fakeDoc();
  let answer = http502;
  await start(doc, (...a) => answer(...a));
  assert.equal(doc.els.silence.hidden, false);
  answer = fresh;
  await advance(5000);
  assert.equal(doc.els.silence.hidden, true);
});

test('banner stays on a page rendered stale until a fresh answer', async () => {
  const doc = fakeDoc({ hidden: false, ageS: '90' });
  await start(doc, hang);
  await advance(3000);
  assert.equal(doc.els.silence.hidden, false);
  assert.equal(doc.els.age.textContent, 'обновлено 93 с назад');   // возраст идёт и без ответов
});

test('request is aborted after timeoutMs', async () => {
  const doc = fakeDoc();
  const { calls } = await start(doc, (url, { signal }) => new Promise((_, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
  }));
  assert.equal(calls[0].opts.signal.aborted, false);
  await advance(4000);
  assert.equal(calls[0].opts.signal.aborted, true);
  assert.equal(doc.els.silence.hidden, false);
});
