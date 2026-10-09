// Страница входа: вход, пробуждение рабочего места и ожидание его готовности.
// Предупреждение при первом входе (Д6-R49) показывает чат: пока /me несёт notice, страница входа место не будит и
// сразу ведёт в чат. Ответ входа предупреждения не несёт — после входа страница спрашивает /me.
// Преподаватель (Д10-R106, B-2) места не имеет: страница его не будит и сразу ведёт в кабинет — он живёт на странице чата.
// Чистые функции — для тестов (node --test edge/tests/*.test.mjs); DOM подключается только в браузере.
// Ни пароль, ни сессия нигде не хранятся: cookie сессии — HttpOnly, её ставит и стирает шлюз.

export const GIVE_UP_S = 90;          // дольше не ждём (холодный старт места — секунды); срок — своим таймером
const POLL_MS = 1000;               // опрос готовности — раз в секунду

const PREPARING = 'Готовлю рабочее место…';
const NO_ANSWER = 'Серверный слой не отвечает';
const OPENING_CHAT = 'Открываю чат…';
export const OPENING_CABINET = 'Открываю кабинет преподавателя…';

// ответ места ({state, detail}) → текст строки состояния; poll — спрашивать ли дальше
export function viewFor(reply) {
  const state = reply && typeof reply === 'object' ? reply.state : undefined;
  switch (state) {
    case 'sleeping':
    case 'starting':
      return { text: PREPARING, poll: true, ok: false };
    case 'ready':
      return { text: 'Рабочее место готово', poll: false, ok: true };
    case 'failed':
      return { text: `Не удалось подготовить рабочее место: ${reply.detail || 'причина неизвестна'}`,
               poll: false, ok: false };
    case 'gave_up':
      return { text: 'Рабочее место не ответило за полторы минуты — обновите страницу или сообщите преподавателю.',
               poll: false, ok: false };
    default:   // незнакомое состояние — не опрашиваем вечно
      return { text: 'Непонятный ответ о рабочем месте — обновите страницу или сообщите преподавателю.',
               poll: false, ok: false };
  }
}

function isObject(body) {
  return body !== null && typeof body === 'object';
}

// ошибка ответа шлюза или edge ({"name", "data": {"message"}}) → текст для человека
export function loginError(status, body) {
  const message = isObject(body) && isObject(body.data) ? body.data.message : undefined;
  if (typeof message === 'string' && message !== '') return message;
  if (!isObject(body) || (status >= 502 && status <= 504)) return NO_ANSWER;   // не JSON, сеть, шлюз edge
  return 'Неожиданный ответ сервера — сообщите преподавателю.';
}

// ответ GET /pcbk/api/auth/me → показывать ли форму и что написать
export function meView(status, body) {
  if (status === 200 && isObject(body) && typeof body.login === 'string') {
    return { form: false, text: `Вы вошли как ${body.login}` };
  }
  if (status === 401) return { form: true, text: '' };
  return { form: true, text: loginError(status, body) };
}

export const CHAT_URL = '/chat/';   // чат (Д6-R39): после «Рабочее место готово» — туда

// navigate — переход на другую страницу; в браузере location.assign, в тестах — своя запись (в Node нет location)
export function startPage({ doc, fetchImpl,
                            setTimeoutImpl = (fn, ms) => setTimeout(fn, ms),
                            clearTimeoutImpl = (id) => clearTimeout(id),
                            navigate = (url) => location.assign(url) }) {
  const el = (id) => doc.getElementById(id);
  const form = el('login-form');
  const loginInput = el('login');
  const passwordInput = el('password');
  const submit = el('submit');
  const who = el('who');
  const state = el('state');
  const logout = el('logout');
  const toChat = el('to-chat');   // запасная ссылка «Открыть чат» — если переход не случился

  let run = 0;               // номер хода ожидания места: выход и новый ход гасят прежний
  let navigatedRun = -1;     // ход, в котором уже был переход в чат: за ход — не больше одного
  let pollTimer = null;
  let giveUpTimer = null;
  let busy = false;

  // режим fetch — cors по умолчанию: так браузер шлёт Origin и при Referrer-Policy no-referrer
  async function call(method, path, body) {
    const init = { method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    try {
      const res = await fetchImpl(path, init);
      const text = await res.text();
      try {
        return { status: res.status, body: JSON.parse(text) };
      } catch {
        return { status: res.status, body: text };        // не JSON — HTML или текст прокси
      }
    } catch {
      return { status: 0, body: null };                   // сеть оборвалась
    }
  }

  function say(text, kind = '') {
    state.textContent = text;
    if (kind) state.setAttribute('data-kind', kind);
    else state.removeAttribute('data-kind');
  }

  function stopWaiting() {
    run += 1;
    if (pollTimer !== null) clearTimeoutImpl(pollTimer);
    if (giveUpTimer !== null) clearTimeoutImpl(giveUpTimer);
    pollTimer = null;
    giveUpTimer = null;
  }

  function showForm(text, kind = text ? 'fail' : '') {
    stopWaiting();
    form.hidden = false;
    who.hidden = true;
    who.textContent = '';
    logout.hidden = true;
    toChat.hidden = true;
    say(text, kind);
  }

  function showWho(account) {
    form.hidden = true;
    who.textContent = meView(200, account).text;
    who.hidden = false;
    logout.hidden = false;
  }

  // account — ответ /me: преподаватель — в кабинет; у студента с notice != null (не подтвердил; поле есть только с Д6б) —
  // в чат без пробуждения, предупреждение покажет чат; null или поля нет (core Д5) — как в Д6а
  function showSignedIn(account) {
    showWho(account);
    if (account.role === 'teacher') toCabinet();
    else if (account.notice != null) toNotice();
    else wake();
  }

  // кабинет преподавателя — страница чата (B-2): места нет, будить нечего; переход — один, ссылка «Открыть чат» — запасная
  function toCabinet() {
    stopWaiting();
    say(OPENING_CABINET, 'wait');
    toChat.hidden = false;
    navigate(CHAT_URL);
  }

  function toNotice() {
    stopWaiting();
    say(OPENING_CHAT, 'wait');
    toChat.hidden = false;                               // запасная ссылка, если переход не случился
    navigate(CHAT_URL);
  }

  // после удачного входа: предупреждение знает только /me. Нет ответа или он не по договору — текст и ссылка
  // «Открыть чат», место не будим: решит чат своим экраном «Повторить» (раунд 2, В4); 401 — снова форма (Н5)
  async function afterLogin(account) {
    const r = await call('GET', '/pcbk/api/auth/me');
    if (r.status === 401) showForm(loginError(r.status, r.body));
    else if (meView(r.status, r.body).form === false) showSignedIn(r.body);
    else {
      stopWaiting();
      showWho(account);
      say(loginError(r.status, r.body), 'fail');
      toChat.hidden = false;
    }
  }

  function showPlace(my, reply) {
    const view = viewFor(reply);
    say(view.text, view.ok ? 'ok' : view.poll ? 'wait' : 'fail');
    if (view.poll) pollTimer = setTimeoutImpl(() => poll(my), POLL_MS);
    else stopWaiting();
    if (view.ok && navigatedRun !== my) {              // место готово — в чат; у преподавателя сюда не доходит
      navigatedRun = my;
      toChat.hidden = false;
      navigate(CHAT_URL);
    }
  }

  async function wake() {
    stopWaiting();
    const my = run;
    toChat.hidden = true;
    say(PREPARING, 'wait');
    giveUpTimer = setTimeoutImpl(() => {
      if (my !== run) return;
      stopWaiting();
      say(viewFor({ state: 'gave_up' }).text, 'fail');
    }, GIVE_UP_S * 1000);
    const r = await call('POST', '/pcbk/api/workplace/wake');
    if (my !== run) return;
    if (r.status === 401) showForm(loginError(r.status, r.body));
    else if (r.status === 200 || r.status === 202) showPlace(my, r.body);
    else {                                               // пробуждение не принято — ждать нечего
      stopWaiting();
      say(loginError(r.status, r.body), 'fail');
    }
  }

  async function poll(my) {
    pollTimer = null;
    const r = await call('GET', '/pcbk/api/workplace');
    if (my !== run) return;
    if (r.status === 401) showForm(loginError(r.status, r.body));
    else if (r.status === 200) showPlace(my, r.body);
    else if (r.status === 0 || r.status >= 500) {        // core перезапускается — спрашиваем дальше до срока
      say(loginError(r.status, r.body), 'wait');
      pollTimer = setTimeoutImpl(() => poll(my), POLL_MS);
    } else {
      stopWaiting();
      say(loginError(r.status, r.body), 'fail');
    }
  }

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();                                 // и CSP form-action 'none': форма никуда не уходит
    if (busy) return;
    const login = loginInput.value.trim();
    const password = passwordInput.value;
    if (!login || !password) {
      say('Введите логин и пароль', 'fail');
      return;
    }
    busy = true;
    submit.disabled = true;
    passwordInput.value = '';                            // пароль не остаётся в поле
    say('Вхожу…', 'wait');
    const r = await call('POST', '/pcbk/api/auth/login', { login, password });
    if (r.status === 200 && meView(r.status, r.body).form === false) await afterLogin(r.body);   // форма занята до ответа /me
    else say(loginError(r.status, r.body), 'fail');
    busy = false;
    submit.disabled = false;
  });

  logout.addEventListener('click', async () => {
    stopWaiting();
    logout.disabled = true;
    const r = await call('POST', '/pcbk/api/auth/logout');
    logout.disabled = false;
    if (r.status === 200 || r.status === 401) showForm('Вы вышли', 'ok');
    else say(loginError(r.status, r.body), 'fail');
  });

  (async () => {
    const r = await call('GET', '/pcbk/api/auth/me');
    const view = meView(r.status, r.body);
    if (view.form) showForm(view.text);
    else showSignedIn(r.body);
  })();
}

if (typeof document !== 'undefined') {
  startPage({ doc: document, fetchImpl: (path, init) => fetch(path, init) });
}
