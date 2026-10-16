// Все строки людям веб-чата (Д6-R39…R44, таблица Д6-R43). Тексты, общие с соседями, сверяют тесты:
// STAND_WARNING — с login.html, тексты места — с viewFor login.js (tests/workplace.test.ts), MAX_TEXT и MAX_TITLE —
// с rules.py шлюза, номер предупреждения NOTICES — с NOTICE accounts.py, запасные тексты отказов — с proxy.py и edge
// (tests/texts.test.ts).

// предупреждение «учебный стенд» — тот же текст, что .stand на login.html (R44)
export const STAND_WARNING =
  "Учебный стенд: не для ведения технологического процесса — данные и выводы только для разбора и обучения.";

export const MAX_TEXT = 20000;                          // поле ввода — MAX_TEXT правил шлюза Д5б (R42)

// имена инструментов в строке вызова (R42); прочие — своим именем. pcbk_save_agent — инструмент Конструктора (Д7б,
// итоговая доводка, замечание Б4): системное имя студенту не показывается. Ряд и наработка — с Д9а (Д9-R88).
// pcbk_my_agents и pcbk_report_event — Д13: Конструктор (правка агента) и агенты студента (запись событий)
export const TOOL_LABELS: Readonly<Record<string, string>> = {
  pcbk_tag_now: "текущее значение",
  pcbk_tag_period: "сводка за период",
  pcbk_catalog_search: "поиск тегов",
  pcbk_save_agent: "сохранение агента",
  pcbk_tag_series: "ряд и профиль",
  pcbk_calc_runtime: "наработка",
  pcbk_my_agents: "мои агенты",
  pcbk_report_event: "запись события",
  pcbk_calc_spc: "карта Шухарта",
  pcbk_calc_flow_total: "суммарный расход",                // Д26
  pcbk_calc_formula: "расчёт по формуле",                  // Д28
  read: "чтение базы знаний",                         // Д25: read в чате — только у Помощника, база V1/V2
  kb_corrections: "поправки базы знаний",
  kb_correction_add: "запись поправки в базу знаний",
  kb_correction_withdraw: "отзыв поправки",
};

// --- шапка и фазы чата (R39) ---
export const T_LOADING = "Чат загружается…";
export const T_SIGNED_IN = (login: string): string => `Вы вошли как ${login}`;
export const T_LOGOUT = "Выйти";
export const T_RETRY = "Повторить";

// --- предупреждение при первом входе (R49) ---
// Номер редакции живёт в core (NOTICE accounts.py), текст — здесь. Новый текст — новый номер в обоих местах: иначе
// студент «подтвердит» текст, которого не видел. Номер, которого здесь нет, не подтверждается (preflight Ф14).
export const NOTICES: ReadonlyMap<string, string> = new Map([
  ["teacher-reads-1",
   "Преподаватель видит ваши разговоры и агентов на этом стенде (только чтение). Не пишите сюда пароли и личные данные."],
]);
export const T_NOTICE_ACK = "Понятно";
export const T_NOTICE_UNKNOWN =
  "Стенд просит подтвердить предупреждение, которого эта версия чата не знает — обновите страницу или сообщите преподавателю.";

// --- рабочее место: те же тексты, что viewFor страницы входа (R39) ---
export const T_PLACE_PREPARING = "Готовлю рабочее место…";
export const T_PLACE_READY = "Рабочее место готово";
export const T_PLACE_FAILED = (detail: string): string => `Не удалось подготовить рабочее место: ${detail}`;
export const T_PLACE_FAILED_NO_DETAIL = "причина неизвестна";
export const T_PLACE_GAVE_UP =
  "Рабочее место не ответило за полторы минуты — обновите страницу или сообщите преподавателю.";
export const T_PLACE_ODD = "Непонятный ответ о рабочем месте — обновите страницу или сообщите преподавателю.";
export const T_ASLEEP = "Рабочее место спит";          // экран после 503 WorkplaceNotReady (R43)
export const T_WAKE = "Разбудить";

// --- разговор (R42) ---
export const T_INPUT = "Ваш вопрос агенту";           // подсказка и подпись поля ввода
export const T_SEND = "Отправить";
export const T_STOP = "Стоп";
export const T_NEW_THREAD = "Новый разговор";
export const T_IMAGE = (alt: string): string => `[картинка: ${alt}]`;
export const T_TOOL_RUNNING = "идёт…";
export const T_TOOL_DONE = "готово";
export const T_TOOL_ERROR = "ошибка";
export const T_TOOL_LINE = (what: string, state: string): string => `Запрос к данным: ${what} — ${state}`;
// --- карта Шухарта под строкой pcbk_calc_spc (Д24): рисует чат из чисел службы ---
export const T_SPC_CHART = (chart: string, tag: string, unit: string | null): string =>
  `${chart === "I-MR" ? "Карта I — индивидуальные значения" : "Карта X̄ — средние подгрупп"}: ${tag}` +
  (unit ? `, ${unit}` : "");
export const T_SPC_SECONDARY = (chart: string): string =>
  chart === "MR" ? "Карта MR — скользящий размах" : chart === "R" ? "Карта R — размах подгрупп" : `Карта ${chart}`;
export const T_SPC_OK = "Нарушений правил нет";
export const T_SPC_BAD = (n: number): string => `Нарушения правил: ${n} ${n % 10 === 1 && n % 100 !== 11 ? "точка"
  : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "точки" : "точек"} (красным)`;
export const T_PENDING = "Агент ждёт ответа, которого этот чат пока не умеет дать";
export const T_REJECT = "Отклонить";

// --- список разговоров слева (R52): «В архив» и «Переименовать»; «Из архива» и «Удалить» нет ---
export const MAX_TITLE = 200;                           // имя разговора — 1–200 знаков, MAX_TITLE правил шлюза (Н20)
export const T_THREADS = "Разговоры";                   // подпись списка для чтения с экрана
export const T_UNTITLED = "Без названия";
export const T_ARCHIVE = "В архив";
export const T_RENAME = "Переименовать";
export const T_RENAME_FIELD = "Новое имя разговора";
export const T_RENAME_SAVE = "Сохранить";
export const T_RENAME_CANCEL = "Отмена";
export const T_RENAME_HINT = `Имя разговора — от 1 до ${MAX_TITLE} знаков`;

// --- панель «Агенты» над списком разговоров и агент разговора (Д7-R65) ---
// «Мои агенты» — так панель называет и Конструктор в ответе ok («… появится в списке „Мои агенты“ …», Д7-R63)
export const T_AGENTS = "Агенты";
export const T_MY_AGENTS = "Мои агенты";
export const T_AGENTS_EMPTY = "Пока нет — соберите агента вместе с Конструктором";
export const T_AGENT_PENDING = "появится после ответа Конструктора";      // state pending — кнопка неактивна
export const T_AGENT_BROKEN = "не загрузился — сообщите преподавателю";   // state broken — кнопка неактивна
export const T_AGENTS_DOWN = "Список агентов недоступен — чат работает с помощником по данным";   // ответ не 200
export const T_AGENT_LINE = (title: string): string => `Агент: ${title}`;  // над полем ввода
// названия встроенных агентов места — для строки «Агент: …», когда списка нет (404 старого core, сбой) или агента в нём
// нет (доводка р1, m3); источник правды — ответ ручки (IMAGE_AGENTS шлюза, Д7-R64), эти — запасные и с ним совпадают
export const BUILTIN_AGENT_TITLES: ReadonlyMap<string, string> = new Map([
  ["pomoshnik", "Помощник технолога КДМ-1"],
  ["konstruktor", "Конструктор агентов"],
]);

// --- молчание в браузере (R51): строка хода и сбой интерфейса; отчёт уходит администратору ---
export const T_TURN_SILENT =
  "Ответа нет уже 3 минуты. Можно нажать «Стоп» и спросить снова — сбой отмечен для администратора.";
export const T_UI_CRASH = "Чат столкнулся с ошибкой. Сбой отмечен для администратора.";
export const T_UI_RESTART = "Перезапустить чат";

// --- вкладки шапки (Д9-R88, Д10-R110, Д13): у студента «Чат», «Калькуляторы» и «События», у преподавателя —
// «Кабинет» и «Калькуляторы» (без «События»); калькуляторы — те же инструменты службы, что у агентов ---
export const T_TABS = "Разделы";                       // подпись группы вкладок для чтения с экрана
export const T_TAB_CHAT = "Чат";
export const T_TAB_CABINET = "Кабинет";
export const T_TAB_CALC = "Калькуляторы";
export const T_CALC_TITLE = "Калькуляторы БДРВ — те же расчёты, что у агентов";
export const T_CALC_FIND = "Найти тег";
export const T_CALC_RUN = "Посчитать";
export const T_CALC_BUSY = "Считаю…";
export const T_CALC_NO_TAG = "Выберите тег — найдите его по словам или впишите имя";
export const T_CALC_NO_RANGE = "Укажите начало и конец своего периода";
export const T_CALC_BAD_RANGE = "Конец своего периода — позже начала";
export const T_CALC_SIMILAR = "Точного совпадения нет — вот похожие теги";   // вместо заметки службы для агента
export const T_CALC_NOTHING = "Ничего не найдено — попробуйте другие слова";
export const T_CALC_PERIOD_LINE = (start: string, end: string, tz: string): string => `Период: ${start} — ${end} (${tz})`;
export const T_CALC_ASOF_LINE = (asof: string, tz: string): string => `Прочитано: ${asof} (${tz})`;   // у текущего значения
export const T_CALC_COVERED = (percent: string): string => `Данные покрывают ${percent} % периода`;
export const T_CALC_SECONDS = (s: string): string => `${s} с`;
export const T_CALC_PERCENT = (p: string): string => `${p} %`;
export const T_CALC_STATE_VALUE = (v: string): string => `значение ${v}`;   // состояние без подписи по суффиксу
// сбой вкладки — своя граница ошибок (m5); тексты — не чата (m-3)
export const T_CALC_CRASH = "Вкладка калькуляторов столкнулась с ошибкой. Сбой отмечен для администратора.";
export const T_CALC_RESTART = "Перезапустить калькуляторы";

// --- вкладка «События» (Д13, DESIGN п.7): только у студента; GET при открытии и по кнопке «Обновить» ---
export const T_TAB_EVENTS = "События";
export const T_EVENTS_TITLE = "События агентов";
export const T_EVENTS_REFRESH = "Обновить";
// текст пустого списка — дословно (DESIGN п.7)
export const T_EVENTS_EMPTY =
  "Событий пока нет. Их записывают агенты контроля, например при проверке контрольной точки за смену.";
// вид события (kind report_event) — дословно (DESIGN п.7)
export const EVENT_KIND_LABELS: Readonly<Record<string, string>> = {
  deviation: "Отклонение от задания",
  note: "Заметка",
};
// заголовки столбцов таблицы (DESIGN п.7): время (местное браузера), агент, вид, заголовок, теги, период,
// значение и задание (если есть), текст
export const EVENTS_COLUMNS = {
  time: "Время", agent: "Агент", kind: "Вид", title: "Заголовок", tags: "Теги", period: "Период",
  value: "Значение и задание", text: "Текст",
} as const;
// сбой вкладки — своя граница ошибок, как у калькуляторов (m5)
export const T_EVENTS_CRASH = "Вкладка событий столкнулась с ошибкой. Сбой отмечен для администратора.";
export const T_EVENTS_RESTART = "Перезапустить события";

// --- вкладка «Уроки» (Д15а, DESIGN): только у студента; список — при открытии, текст урока — по выбору ---
export const T_TAB_LESSONS = "Уроки";
export const T_LESSONS_TITLE = "Уроки";
export const T_LESSONS_EMPTY = "Уроки пока не добавлены";
// один общий текст отказа (503, сеть, ответ не по договору; 401 — отдельно, onSignedOut): причина студенту не важна
export const T_LESSONS_ERROR = "Уроки сейчас не открываются — попробуйте позже";
// сбой вкладки — своя граница ошибок, как у событий (m5)
export const T_LESSONS_CRASH = "Вкладка уроков столкнулась с ошибкой. Сбой отмечен для администратора.";
export const T_LESSONS_RESTART = "Перезапустить уроки";
// подписи полей, режимов, периодов и чисел вкладки (R88). Ключи режимов — CalcMode, ключи периодов — PRESETS службы
// (periods.py) и custom; значения полей и порядок — calc.ts
export const CALC_LABELS = {
  query: "Слова для поиска",
  tag: "Тег",
  mode: "Режим",
  period: "Период",
  from: "С",
  to: "По",
  points: "Точек на графике",
  modes: {
    now: "Текущее значение",
    period: "Сводка за период: среднее, минимум, максимум, размах",
    profile: "Профиль: как менялся параметр",
    runtime: "Наработка по тегу состояния",
  },
  periods: {
    last_hour: "Последний час",
    last_8h: "Последние 8 часов",
    last_24h: "Последние сутки",
    current_shift: "Текущая смена",
    prev_shift: "Прошлая смена",
    today: "Сегодня",
    yesterday: "Вчера",
    last_7d: "Последние 7 суток",
    last_30d: "Последние 30 суток",
    custom: "Свой период (время историана)",
  },
  matches: "Найденные теги",
  value: "Значение",
  time: "Время замера",
  age: "Возраст",
  quality: "Качество",
  avg: "Среднее",
  min: "Минимум",
  max: "Максимум",
  range: "Размах",
  good: "Доля достоверных",
  first: "В начале",
  last: "В конце",
  change: "Изменение",
  gridMin: "Минимум сетки",
  gridMax: "Максимум сетки",
  chart: "График профиля",
  state: "Состояние",
  hours: "Часов",
  percent: "% периода",
  count: "Переходов",
  source: "Источник",
  pointsPerTag: "Точек на тег",                       // у профиля — рядом с источником (его текст: «N точек»)
  avgKind: "Вид среднего",
} as const;

// --- вкладка «Аналитический чат технолога» (план КДМ1, Task 6; мастер-промпт §6): у студента и преподавателя
// (справка преподавателю — без LLM), сразу после «Чат»/«Кабинет» и перед «Калькуляторы». Узкий экран — короткое
// название вкладки, доступное имя (aria-label) — полное (мастер-промпт §6: «„Аналитика“ с полным доступным
// названием»).
export const T_TAB_ANALYTICS = "Аналитический чат технолога";
export const T_TAB_ANALYTICS_SHORT = "Аналитика";
export const T_AN_TITLE = "Аналитический чат технолога";
export const T_AN_SCOPE = "Область: КДМ-1";
export const T_AN_CRASH = "Вкладка аналитики столкнулась с ошибкой. Сбой отмечен для администратора.";
export const T_AN_RESTART = "Перезапустить аналитику";
export const T_AN_LOADING = "Открываю аналитику…";
// отказ capabilities/conversations (404 — выключатель, 503 — не готова, сеть, чужая форма ответа): один общий
// текст, причина студенту не важна (приём T_LESSONS_ERROR); 401 — onSignedOut отдельно
export const T_AN_UNAVAILABLE = "Аналитика КДМ-1 сейчас не отвечает — попробуйте позже";
export const T_AN_NAV = "Разделы аналитики";
export const T_AN_NAV_CHAT = "Диалог";
export const T_AN_START_HINT = "Выберите диалог слева или начните новый";

// --- диалоги слева (история — мастер-промпт §6) ---
export const T_AN_CONVERSATIONS = "Диалоги";
export const T_AN_NEW_CONVERSATION = "Новый диалог";
export const T_AN_NO_CONVERSATIONS = "Диалогов пока нет — задайте первый вопрос";
export const T_AN_CONVERSATION_ITEM = (when: string): string => `Диалог от ${when}`;

// --- режим (мастер-промпт §6: «Справка по документам / Разбор отклонения») ---
export const T_AN_MODE = "Вид разбора";
export const AN_MODE_LABELS: Readonly<Record<string, string>> = {
  reference: "Справка по документам",
  deviation_review: "Разбор отклонения",
};

// --- составление вопроса ---
export const T_AN_QUESTION = "Ваш вопрос технологу КДМ-1";
export const T_AN_PROCESS = "Участок (необязательно)";
export const T_AN_PERIOD = "Период (необязательно)";
export const T_AN_PERIOD_FROM = "С";
export const T_AN_PERIOD_TO = "По";
export const T_AN_PERIOD_TZ = "Часовой пояс периода";
export const AN_TZ_CHOICES: ReadonlyArray<{ id: string; label: string; offset: string }> = [
  { id: "utc", label: "UTC", offset: "Z" },
  { id: "msk", label: "Москва (UTC+3)", offset: "+03:00" },
];
export const T_AN_UNIT = "Единица измерения (необязательно)";
export const T_AN_SEND = "Спросить";
export const T_AN_SENDING = "Отправляю…";
export const T_AN_CANCEL = "Отмена";
export const T_AN_RETRY = "Повторить";
export const T_AN_NO_QUESTION = "Введите вопрос";
export const T_AN_BAD_PERIOD = "Укажите обе границы периода, конец — не раньше начала";
// подсказки участков КДМ-1 (мастер-промпт §5: обогащаемые процессы) — свободный ввод, не каталог ID
export const AN_PROCESS_HINTS: readonly string[] = [
  "Подготовка массы", "Сортирование", "Сгущение/водооборот", "Напорные ящики", "Формование", "Прессование",
  "Сушка", "Пароконденсатная система", "Вентиляция", "Поверхностная проклейка", "Измерение качества",
];

// --- ход задания (JobState) ---
export const AN_STATE_LABELS: Readonly<Record<string, string>> = {
  queued: "в очереди", preanalysis: "предварительный анализ", retrieving: "поиск оснований",
  generating: "формирование ответа", validating: "проверка ответа", completed: "готово",
  needs_clarification: "нужно уточнение", limited_answer: "ограниченный ответ", cancelled: "отменено",
  failed: "сбой",
};

// --- уточнения (ClarificationKey) ---
export const AN_CLARIFICATION_LABELS: Readonly<Record<string, string>> = {
  quantity: "количество или величина", process: "участок процесса КДМ-1", unit: "единица измерения",
  basis: "база (например, по массе или по объёму)", period: "период", timezone: "часовой пояс",
  grade: "сорт или режим", measurement_quality: "качество/достоверность измерения",
  source_applicability: "применимость источника к КДМ-1",
};
export const T_AN_CLARIFY_TITLE = "Нужны уточнения, прежде чем продолжить:";
export const T_AN_CLARIFY_HINT = "Уточните вопрос ниже и отправьте снова — прежний вопрос остался рядом для сверки";
export const T_AN_CLARIFY_RESEND = "Уточнить и спросить снова";
export const T_AN_ORIGINAL_QUESTION = "Исходный вопрос";

// --- разделы ответа (SectionKind) и подписи карточек ---
export const AN_SECTION_LABELS: Readonly<Record<string, string>> = {
  document_observation: "Наблюдение по документу", public_definition: "Публичное определение",
  calculation: "Расчёт", qualified_hypothesis: "Гипотеза с условиями проверки", limitation: "Ограничение",
  clarification: "Уточнение", telemetry_observation: "Данные историана",
};

// --- отказ задания (ErrorCode) ---
export const AN_ERROR_LABELS: Readonly<Record<string, string>> = {
  unavailable: "Аналитика КДМ-1 сейчас недоступна — попробуйте позже",
  invalid_input: "Вопрос не принят — проверьте текст и попробуйте снова",
  policy_refused: "Управляющее действие и вопросы вне области КДМ-1 не выполняются — ниже только допустимая справка",
  source_conflict: "В основаниях найден конфликт — его можно отправить на согласование",
  validation_failed: "Ответ не прошёл проверку — попробуйте переформулировать вопрос",
  interrupted: "Разбор был прерван",
  busy: "Слишком много одновременных запросов — повторите позже",
};

// --- основания (AnalyticsSources.tsx) ---
export const T_AN_SOURCES = "Основания";
export const T_AN_NO_SOURCES = "Без оснований — общая справка";
// раунд правки 2 (живая проверка на сервере): секция telemetry_observation не имеет evidence_refs по контракту
// (источник значения — служба данных, не корпус документов, Д17 Task 1/DRIFT.md), но «Без оснований — общая
// справка» для неё неверно — у значения ЕСТЬ источник, просто не из документов. AnalyticsPage.tsx показывает
// эту строку вместо AnalyticsSources специально для telemetry_observation (остальные виды секций — без правки)
export const T_AN_TELEMETRY_SOURCE =
  "Источник: служба данных историана — тот же канал, что у калькуляторов, не документы КДМ-1";
export const T_AN_LIMITATION = "Ограничение";
export const T_AN_CORPUS_VERSION = (v: string): string => `Версия корпуса: ${v}`;
// название/тип/дата источника при раскрытии (правка итогового ревью, п.1 F2-brief; мастер-промпт §6: «название,
// страница/ячейка/снимок, дата, тип, фрагмент и ограничения»): название заменяет код в заголовке, код — мелко
// рядом (T_AN_SOURCE_CODE); нет записи источника — поля null, строка не показывается (см. AnalyticsSources.tsx)
export const T_AN_SOURCE_CODE = (code: string): string => `код ${code}`;
export const T_AN_SOURCE_CHECKED = (date: string): string => `проверено ${date}`;
export const T_AN_SHOW_FRAGMENT = "Показать фрагмент источника";
export const T_AN_HIDE_FRAGMENT = "Скрыть фрагмент источника";
export const T_AN_FRAGMENT_ERROR = "Фрагмент источника сейчас не открывается";
export const T_AN_LOC_PDF = (page: number): string => `страница ${page} (документ в предоставленной модели)`;
export const T_AN_LOC_XLSX = (sheet: string, cell: string): string => `лист «${sheet}», ячейка ${cell}`;
export const T_AN_LOC_IMAGE = (file: string, label: string): string => `снимок «${file}», метка «${label}»`;
export const T_AN_LOC_PUBLIC = (section: string): string => `публичный источник, раздел «${section}»`;
export const T_AN_LOC_INTERNAL_SECTION = (section: string): string => `внутренний источник, раздел «${section}»`;

// --- отправка конфликта на согласование (мастер-промпт §6: «конфликт виден и может быть отправлен») ---
export const T_AN_SEND_TO_REVIEW = "Отправить на согласование";
export const T_AN_SEND_TO_REVIEW_DONE = "Заявка отправлена на согласование";
export const T_AN_SEND_TO_REVIEW_RATIONALE = "Пояснение для проверяющего";

// --- назначенное согласование (AnalyticsReviews.tsx) — видно только при праве, сообщённом сервером ---
export const T_AN_REVIEWS = "Назначенное согласование";
export const T_AN_REVIEWS_EMPTY = "Заявок на согласование нет";
export const T_AN_REVIEWS_ERROR = "Заявки на согласование сейчас не открываются";
export const T_AN_REVIEW_OLD = "Было";
export const T_AN_REVIEW_NEW = "Предлагается";
export const T_AN_REVIEW_SCOPE = "Область";
export const T_AN_REVIEW_PERIOD = "Срок";
export const T_AN_REVIEW_RATIONALE = "Основание";
export const T_AN_REVIEW_COMMENT = "Комментарий";
export const T_AN_REVIEW_ACCEPT = "Принять";
export const T_AN_REVIEW_REJECT = "Отклонить";
export const T_AN_REVIEW_INFO = "Запросить сведения";
export const T_AN_REVIEW_REFRESH = "Обновить";
// отказ решения заявки (правка итогового ревью, п.2 F2-brief): проверяющий раньше не видел отказ decide() вовсе —
// теперь понятный текст (role="alert") на 403/404/409, прочее (503 и незнакомый код) — общий; 401 — выход, как
// раньше (AnalyticsReviews.tsx)
export const T_AN_REVIEW_DECIDE_CONFLICT = "Заявка уже решена другим проверяющим";       // 409
export const T_AN_REVIEW_DECIDE_NOT_FOUND = "Заявка недоступна";                         // 404
export const T_AN_REVIEW_DECIDE_FORBIDDEN = "Нет права решать эту заявку";               // 403
export const T_AN_REVIEW_DECIDE_ERROR = "Решение не сохранилось — попробуйте ещё раз";   // прочее (503 и т.п.)
export const AN_REVIEW_KIND_LABELS: Readonly<Record<string, string>> = {
  local_claim: "местное утверждение", equipment_link: "связь с оборудованием", local_norm: "местный норматив",
  conflict: "конфликт источников",
};
export const AN_REVIEW_STATUS_LABELS: Readonly<Record<string, string>> = {
  pending: "на рассмотрении", accepted: "принято", rejected: "отклонено", info_requested: "запрошены сведения",
};
// подпись связи с оборудованием в заявке equipment_link (AnalyticsReviews.tsx) — вместо внутреннего id, когда
// подпись найдена в уже загруженном списке связей (план Д17, Task 2)
export const T_AN_REVIEW_EQUIPMENT = "Оборудование";

// --- раздел «Связи с историаном» (план Д17, Task 2; мастер-промпт §8): подпись оборудования из документов
// КДМ-1 ↔ тег историана. Видно всем вошедшим с доступом к вкладке — предложить связь может любой, решает тот,
// у кого право согласования (экран выше, AnalyticsReviews.tsx; AnalyticsLinks.tsx) ---
export const T_AN_LINKS = "Связи с историаном";
export const T_AN_LINKS_EMPTY = "Связей пока нет";
export const T_AN_LINKS_ERROR = "Связи с историаном сейчас не открываются";
// честная строка (мастер-промпт §8), когда служба данных не подключена — подбор тега не работает, сам список
// связей и их состояния при этом показываются
export const T_AN_LINKS_NO_BRIDGE = "Служба данных недоступна — подбор тега сейчас не работает";
export const T_AN_LINK_UNBOUND = "не связана";
export const T_AN_LINK_PENDING = "на согласовании";
export const T_AN_LINK_LINKED = (tag: string, approvedBy: string, approvedAt: string): string =>
  `связана: ${tag} (согласовал ${approvedBy}, ${approvedAt})`;
export const T_AN_LINK_PICK = "Подобрать тег";
export const T_AN_LINK_LOADING = "Ищу подходящие теги…";
// пустой список кандидатов (дословно) — не отказ службы данных, а честное «такого тега в разрешённом списке нет»
export const T_AN_LINK_NO_CANDIDATES = "В разрешённом списке нет тегов с этой подписью";
export const T_AN_LINK_CANDIDATES_TITLE = "Подходящие теги";
export const T_AN_LINK_CANDIDATE_LABEL = (tag: string, description: string, unit: string | null): string =>
  unit !== null ? `${tag} — ${description}, ${unit}` : `${tag} — ${description}`;

// --- отказы шлюза (таблица R43) ---
export const T_SIGNED_OUT = "Сессия закончилась — войдите снова";
// запасные тексты: в ответе шлюза или edge свой data.message, эти — когда его нет (равны текстам шлюза)
export const T_TOO_MANY_TABS = "Открыто слишком много вкладок чата — закройте лишние";   // proxy.py streams
export const T_BUSY = "Слишком много одновременных запросов — повторите позже";           // proxy.py busy, edge busy.json
export const T_DOWN = "Серверный слой не отвечает";                                        // edge 503.json
export const T_OFFLINE = "Нет связи с сервером — проверьте сеть";
export const T_UNKNOWN = "Неожиданный ответ сервера — сообщите преподавателю.";
export const T_STREAM_RETRY = (s: number): string =>
  `Связь с рабочим местом прервалась — переподключаюсь через ${s} с`;

// --- баннер состояния (R44, решение F7) ---
export const T_STATUS_OK = "Стенд работает";
export const T_STATUS_WARN = "Внимание";               // «Внимание: <названия>»
export const T_STATUS_FAIL = "Сбой";                   // «Сбой: <названия>»
export const T_STATUS_ROWS_UNKNOWN = "Состояние неизвестно";   // строки в unknown при живом стороже (F7)
export const T_STATUS_UNKNOWN = "Состояние стенда неизвестно — сторож не отвечает";   // сама страница молчит
export const T_STATUS_MORE = (n: number): string => ` и ещё ${n}`;
export const T_STATUS_DETAILS = "Подробнее";           // ссылка на /status

// --- кабинет преподавателя (Д10-R106): места и доли, разговоры и агенты студентов только на чтение, журнал действий ---
// Отказы ручек кабинета (TEACHER_TEXTS шлюза, Д10-R100) и правил долей (Д10-R97) приходят в ответе и показываются
// дословно (replyText) — здесь только свои строки страницы.
export const T_TCH_TITLE = "Кабинет преподавателя";
export const T_TCH_REFRESH = "Обновить";
export const T_TCH_LOADING = "Загружаю…";
export const T_TCH_BUDGET = (spent: string, total: string, allocated: string, free: string, ops: string): string =>
  `Общий бюджет: потрачено ${spent} из ${total}; распределено по местам ${allocated}, свободно ${free}; отбор и проверки ${ops}`;
export const T_TCH_NO_SHARES = "Доли бюджета недоступны — перевод закрыт";   // обзор без LLM-прокси или без долей (R101)
// кабинет говорит с преподавателем: «…сообщите преподавателю» из текстов чата здесь не к месту (ревью А4, m-4)
export const T_TCH_UNKNOWN = "Неожиданный ответ сервера — сообщите администратору стенда.";
export const T_TCH_PLACE_GAVE_UP =
  "Рабочее место не ответило за полторы минуты — повторите или сообщите администратору стенда.";
export const T_TCH_PLACE_ODD = "Непонятный ответ о рабочем месте — повторите или сообщите администратору стенда.";
// обзор после изменения не пришёл: прежний остаётся на экране, причина — строкой над ним (ревью А4, m-1)
export const T_TCH_OVERVIEW_FAILED = (why: string): string => `Обзор не обновился: ${why}`;
export const T_TCH_NONE = "—";                         // нет числа (доли не загружены)
export const T_TCH_BAD_PART = "Неверный номер места, разговора или имя агента";   // вызов не ушёл: часть пути не по шаблону
// таблица мест
export const T_TCH_PLACES = "Места студентов";
export const TCH_COLUMNS = {
  place: "Место", student: "Студент", state: "Состояние", share: "Доля", spent: "Потрачено", agents: "Агентов",
  account: "Учётная запись",
} as const;
export const T_TCH_FREE = "свободно";                  // у места нет закрепления (free, m-11)
export const T_TCH_NO_ACCOUNT = "без учётки";           // закреплено за удалённым логином: учётки нет, но место не свободно
export const T_TCH_MARK_DISABLED = "отключена";
export const T_TCH_MARK_LOCKED = "замок";
export const T_TCH_MARK_NOT_NOTICED = "не подтвердил предупреждение";
export const T_TCH_OPEN = "Открыть";
export const T_TCH_OPEN_LABEL = (nn: string): string => `Открыть место ${nn}`;
// состояние места (workplaces.view, Д5): незнакомое — как пришло
export const PLACE_STATE_LABELS: Readonly<Record<string, string>> = {
  sleeping: "спит", starting: "просыпается", ready: "работает", failed: "сбой",
};
// открытое место: вкладки «Разговоры» и «Агенты»
export const T_TCH_PLACE_TITLE = (nn: string, login: string): string => `Место ${nn} — ${login}`;
export const T_TCH_PLACE_TABS = "Разговоры и агенты места";
export const T_TCH_TAB_SESSIONS = "Разговоры";
export const T_TCH_TAB_AGENTS = "Агенты";
export const T_TCH_WAKE = "Разбудить для просмотра";
export const T_TCH_RELOAD_LIST = "Обновить список";
export const T_TCH_NO_SESSIONS = "Разговоров нет";
export const T_TCH_ARCHIVED = "в архиве";
// разговор студента (TeacherReader): только текст, ни полей, ни кнопок отправки
export const T_TCH_READ_ONLY = "Только чтение: писать в разговор студента нельзя";
export const T_TCH_STUDENT = "Студент";
export const T_TCH_AGENT = (title: string): string => `Агент «${title}»`;
export const T_TCH_AGENT_NONAME = "Агент";
export const T_TCH_CLIPPED = "Разговор показан не целиком — он слишком длинный";
export const T_TCH_NO_MESSAGES = "В разговоре нет сообщений";
export const T_TCH_TOOL_UNKNOWN = "состояние неизвестно";
// агенты места (TeacherAgents): карточки из учёта и текст файла
export const T_TCH_NO_AGENTS = "Своих агентов у студента нет";
export const T_TCH_SHOW_TEXT = "Показать инструкцию";
export const T_TCH_HIDE_TEXT = "Скрыть инструкцию";
export const TCH_AGENT_LABELS = {
  node: "Узел", purpose: "Назначение", tools: "Инструменты", tags: "Теги", state: "Состояние",
} as const;
// состояния учёта агентов (AgentState, Д7-R60; архивных в ответе нет): незнакомое — как пришло
export const AGENT_STATE_LABELS: Readonly<Record<string, string>> = {
  saved: "сохранён — появится после перезагрузки места",
  visible: "работает",
  not_visible: "не появился в рабочем месте",
  quarantined: "в карантине — файл не загрузился",
};
// доли бюджета на модель (TeacherShares)
export const T_TCH_SHARES = "Доли бюджета на модель";
export const T_TCH_FROM = "С места";
export const T_TCH_TO = "На место";
export const T_TCH_AMOUNT = "Сумма, $";
export const T_TCH_PICK = "выберите место";
export const T_TCH_MOVE = "Перевести";
export const T_TCH_FLOOR = "Остановить место";
export const T_TCH_FLOOR_HINT =
  "«Остановить место» сводит долю места «С места» к уже оплаченному, разницу отдаёт месту «На место»: следующий вызов модели студента получит отказ «доля израсходована».";
export const T_TCH_SHARES_DONE = "Доли обновлены";
export const T_TCH_SAME_PLACE = "Выберите два разных места";
export const T_TCH_ZERO = "Сумма должна быть больше нуля";
export const T_TCH_BAD_AMOUNT = "Сумма — в долларах, не больше двух знаков после запятой";
export const T_TCH_TAKE_MAX = (nn: string, money: string): string => `У места ${nn} можно взять не больше ${money}`;
// учётные записи студентов (TeacherAccounts, Д10-R110): отказы ручек учёток (R108) — текстом ответа дословно
export const T_TCH_ACCOUNTS = "Учётные записи";
export const T_TCH_ACC_ADD_TITLE = "Завести студента";
export const T_TCH_ACC_LOGIN = "Логин";
export const T_TCH_ACC_PLACE = "Место";
export const T_TCH_ACC_ADD = "Завести";
// заведение закрепляет место за логином навсегда (Д5а); смены студента в занятом месте нет ни здесь, ни у команды
// администратора — хранилище отказывает (итоговое ревью Д10б, m-2 и ревью Б1+Б2, M-5)
export const T_TCH_ACC_ADD_HINT =
  "Только на свободное место. Логин закрепляется за местом навсегда — проверьте написание. Сменить студента в занятом " +
  "месте сейчас нельзя; учётки преподавателей и удаление учёток — команда администратора.";
// логин вида пробных учёток проверок (PROBE_LOGIN учёток шлюза): такое место администратор может освободить
// (release-place) — живому студенту нужен обычный логин (ревью Б1+Б2, M-4); подсказка не запрещает: сервер их принимает
export const T_TCH_ACC_PROBE_LOGIN =
  "Логин вида dN-probe-… — для проверок стенда: такое место администратор может освободить. Студенту дайте обычный логин.";
export const T_TCH_ACC_NO_FREE = "Свободных мест нет";
export const T_TCH_ACC_PICK_PLACE = "Выберите свободное место";
// правило логина — то же, что _LOGIN_RULE учёток шлюза (LOGIN_RE): подсказка до запроса
export const T_TCH_ACC_LOGIN_RULE = "Логин — 3…32 знака из a-z, 0-9, «.», «_», «-», первый — буква a-z";
// окно пароля (.password-once): пароль только в состоянии окна, «Я записал пароль» его стирает
export const T_TCH_ACC_PASSWORD_ONCE = "Пароль показан один раз — запишите его и передайте студенту";
export const T_TCH_ACC_PASSWORD_FOR = (login: string, nn: string): string => `Учётка ${login}, место ${nn}`;
export const T_TCH_ACC_COPY = "Скопировать";
export const T_TCH_ACC_COPIED = "Скопировано";
export const T_TCH_ACC_COPY_FAILED = "Скопировать не удалось — пароль выделен, нажмите Ctrl+C";
export const T_TCH_ACC_DONE = "Я записал пароль";
// кнопки строки места со студентом; aria-label — «<действие> <логин>»
export const T_TCH_ACC_PASSWD = "Новый пароль";
export const T_TCH_ACC_DISABLE = "Отключить";
export const T_TCH_ACC_ENABLE = "Включить";
export const T_TCH_ACC_UNLOCK = "Снять замок";
export const T_TCH_ACC_LABEL = (action: string, login: string): string => `${action} ${login}`;
// подтверждение — окно в странице (не window.confirm): отключение и новый пароль закрывают сессии студента. Чат узнаёт об
// этом по ответу 401: при следующем действии студента, а при открытом разговоре — и сам, за перепроверку потока; уйти
// «сразу» он не может (Б3, сомнение 1; итоговое ревью Д10б, m-1) — один честный текст на оба подтверждения
export const T_TCH_ACC_SIGNS_OUT = "Студент не сможет продолжить: чат закроется не позже его следующего действия";
export const T_TCH_ACC_ASK_DISABLE = (login: string): string => `Отключить учётку ${login}? ${T_TCH_ACC_SIGNS_OUT}`;
export const T_TCH_ACC_YES_DISABLE = "Да, отключить";
export const T_TCH_ACC_ASK_PASSWD = (login: string): string =>
  `Выдать учётке ${login} новый пароль? Старый перестанет работать. ${T_TCH_ACC_SIGNS_OUT}`;
export const T_TCH_ACC_YES_PASSWD = "Да, выдать новый пароль";
export const T_TCH_ACC_CANCEL = "Отмена";
export const T_TCH_ACC_DISABLED = (login: string): string => `Учётка ${login} отключена`;
export const T_TCH_ACC_ENABLED = (login: string): string => `Учётка ${login} включена`;
export const T_TCH_ACC_UNLOCKED = (login: string): string => `Замок входа ${login} снят`;
// вызов не ушёл: логин в пути ручки учётки не по LOGIN_RE учёток
export const T_TCH_ACC_BAD_LOGIN = "Неверный логин учётки";
// журнал действий (TeacherJournal): последние строки, новые первыми
export const T_TCH_JOURNAL = "Журнал действий";
export const T_TCH_JOURNAL_RELOAD = "Обновить журнал";
export const T_TCH_JOURNAL_EMPTY = "Записей пока нет";
export const T_TCH_JOURNAL_PLACE = (nn: string): string => `место ${nn}`;
export const T_TCH_JOURNAL_SHARE = (nn: string, from: string, to: string): string => `${nn}: ${from} → ${to}`;
// подписи действий журнала (ACTIONS, Д10-R99; действия учёток — Д10б, R108); незнакомое — кодом как есть
export const ACTION_LABELS: Readonly<Record<string, string>> = {
  sessions: "открыл список разговоров",
  read: "прочитал разговор",
  agents: "открыл список агентов",
  agent: "прочитал инструкцию агента",
  wake: "разбудил место",
  shares: "перевёл долю",
  account_add: "завёл учётку студента",
  account_passwd: "выдал новый пароль",
  account_disable: "отключил учётку",
  account_enable: "включил учётку",
  account_unlock: "снял замок входа",
};
// итоги строк журнала (OUTCOMES, Д10-R99)
export const OUTCOME_LABELS: Readonly<Record<string, string>> = {
  requested: "запрошено", ok: "выполнено", refused: "отказ", error: "ошибка", started: "начато",
};
// сбой отрисовки кабинета — своя граница ошибок, тексты — не чата (Д9 m-3)
export const T_TCH_CRASH = "Кабинет преподавателя столкнулся с ошибкой. Сбой отмечен для администратора.";
export const T_TCH_RESTART = "Перезапустить кабинет";
