// Опрос /status.json и полоса молчания по часам браузера.
// Полоса видна, если ответ stale, ошибка, HTTP ≠ 200 или с последнего
// удачного ответа прошло больше staleAfterS (запрос повис, сторож замолчал).

// подписи состояний — те же, что в page.py
const LABELS = {
  ok: 'норма', warn: 'внимание', fail: 'сбой', unknown: 'неизвестно', absent: 'ещё не установлен',
};

// «2026-09-29T17:00:05.123+05:00» → «29.09.2026 17:00:05 UTC+05:00», как fmt_time в page.py
function fmtTime(iso) {
  const m = /^(\d{4})-(\d\d)-(\d\d)T(\d\d:\d\d:\d\d)(?:\.\d+)?([+-]\d\d:\d\d)$/.exec(iso || '');
  return m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]} UTC${m[5]}` : String(iso);
}

function span(doc, cls, text) {
  const el = doc.createElement('span');
  el.className = cls;
  el.textContent = text;   // только textContent: detail может нести текст Docker
  return el;
}

export function startPolling({ fetchImpl, doc, nowMs, setIntervalImpl, staleAfterS,
                               pollMs = 5000, timeoutMs = 4000 }) {
  const silence = doc.getElementById('silence');
  const age = doc.getElementById('age');
  // страница пришла свежей — отсчёт от её загрузки; иначе ждём удачного ответа
  let lastOkMs = silence.hidden ? nowMs() : null;
  let failing = !silence.hidden;
  // возраст показанных данных: с сервера на момент ageBaseMs, дальше — по часам браузера
  const ageAttr = age ? age.getAttribute('data-age-s') : null;
  let serverAgeS = ageAttr === null ? null : Number(ageAttr);
  let ageBaseMs = nowMs();
  let sent = 0;
  let applied = 0;

  function update() {
    const now = nowMs();
    const silent = failing || lastOkMs === null || now - lastOkMs > staleAfterS * 1000;
    silence.hidden = !silent;
    if (age && serverAgeS !== null) {
      age.textContent = `обновлено ${Math.round(serverAgeS + (now - ageBaseMs) / 1000)} с назад`;
    }
  }

  function render(data) {
    const overall = doc.getElementById('overall');
    if (overall) {
      overall.className = `s-${data.overall}`;
      overall.textContent = LABELS[data.overall] ?? data.overall;
    }
    const at = doc.getElementById('checked-at');
    if (at) at.textContent = fmtTime(data.checked_at);
    const list = doc.getElementById('checks');
    if (!list) return;
    list.replaceChildren(...(data.checks || []).map((c) => {
      const label = LABELS[c.state] ?? c.state;
      const li = doc.createElement('li');
      li.className = `check s-${c.state}`;
      li.append(span(doc, 'name', c.title), span(doc, 'state', label),
        span(doc, 'detail', c.detail === label ? '' : c.detail));   // как в page.py
      return li;
    }));
  }

  async function poll() {
    update();   // часы браузера — до нового запроса
    const seq = ++sent;
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    let data = null;
    try {
      const resp = await fetchImpl('/status.json', { signal: ctl.signal, cache: 'no-store' });
      if (resp.status === 200) data = await resp.json();
    } catch {
      data = null;   // сеть, отмена по таймауту, не JSON
    } finally {
      clearTimeout(timer);
    }
    if (seq < applied) return;   // запоздавший ответ старого запроса
    applied = seq;
    if (data && data.stale === false) {
      failing = false;
      lastOkMs = nowMs();
      ageBaseMs = lastOkMs;
      serverAgeS = Number(data.age_s) || 0;
      render(data);
    } else {
      failing = true;
    }
    update();
  }

  setIntervalImpl(poll, pollMs);
  setIntervalImpl(update, 1000);   // полоса по часам — даже когда fetch повис
  poll();
  return { poll, update };
}

// в браузере — сам, со значениями страницы; в Node (тесты) — нет
if (typeof document !== 'undefined') {
  const poller = startPolling({
    fetchImpl: (url, opts) => fetch(url, opts),
    doc: document,
    nowMs: () => Date.now(),
    setIntervalImpl: (fn, ms) => setInterval(fn, ms),
    staleAfterS: Number(document.body.dataset.staleAfterS) || 30,
  });
  // вкладка вернулась из фона — таймеры были придушены, спросить сразу
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) poller.poll();
  });
}
