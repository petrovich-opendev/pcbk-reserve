// Кабинет преподавателя (Д10-R106): api — заглушка TeacherApi с очередями ответов (teacherFixtures.ts, как api() в
// App.test.tsx); опрос пробуждения — pollMs.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { TeacherPage } from "../src/components/TeacherPage";
import { AGENTS_03, JOURNAL_ROWS, OVERVIEW_READY_03, OVERVIEW_SLEEPING_05, SESSION, SESSION_ARCHIVED, TRANSCRIPT_WITH_HTML,
         teacherApi } from "./teacherFixtures";

test("overview_then_reader_is_read_only", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03], sessions: [200, { sessions: [SESSION] }],
                         session: [200, TRANSCRIPT_WITH_HTML] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: /Открыть.*03/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Значение 20FAKE_001_PV/ }));
  // waitFor с колбэком, который не бросает, вернулся бы сразу с null: ждём явной проверкой (правка теста брифа)
  await waitFor(() => expect(container.querySelector("section.transcript")).not.toBeNull());
  const t = container.querySelector("section.transcript")!;
  expect(t.textContent).toContain("<img src=x onerror=alert(1)>");            // текстом, не разметкой
  expect(t.querySelector("img, textarea, input, [contenteditable]")).toBeNull();
  expect(screen.getByText("Только чтение: писать в разговор студента нельзя")).toBeTruthy();
  expect(a.shares).not.toHaveBeenCalled();
  // сверх брифа: разметка стенограммы — договор с А5; вызовы — по месту и разговору из обзора
  expect(a.sessions).toHaveBeenCalledWith("03");
  expect(a.session).toHaveBeenCalledWith("03", SESSION.id);
  expect([...t.querySelectorAll("div.msg")].map((m) => m.getAttribute("data-role"))).toEqual(["user", "assistant", "assistant"]);
  expect(t.querySelector("script, b")).toBeNull();
  expect(t.textContent).toContain("<script>alert(2)</script>");
  expect(t.textContent).toContain("Запрос к данным: текущее значение — готово");
  expect(t.textContent).toContain("Агент «Помощник по данным»");
  expect(t.querySelector('[data-kind="fail"]')!.textContent).toBe("Ваша доля бюджета на модель израсходована");
  expect(t.textContent).toContain("Разговор показан не целиком — он слишком длинный");
  expect(t.querySelectorAll("button")).toHaveLength(0);                         // и кнопок отправки нет
});

test("sleeping_place_wakes_by_button_and_polls", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_SLEEPING_05],
                         sessions: [[503, { name: "WorkplaceNotReady", data: { message: "Рабочее место студента спит — разбудите его для просмотра" } }],
                                    [200, { sessions: [] }]],
                         wake: [202, { place: "05", state: "starting", text: "Готовлю рабочее место…" }],
                         place: [[200, { place: "05", state: "starting", text: "…" }], [200, { place: "05", state: "ready", text: "…" }]] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} pollMs={20} />);
  fireEvent.click(await screen.findByRole("button", { name: /Открыть.*05/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Разбудить для просмотра" }));
  expect(await screen.findByText("Разговоров нет")).toBeTruthy();
  expect(a.wake).toHaveBeenCalledTimes(1);
  // сверх брифа: опрос — по месту, до «готово»; список — заново после пробуждения
  expect(a.wake).toHaveBeenCalledWith("05");
  expect(a.place).toHaveBeenCalledTimes(2);
  expect(a.place).toHaveBeenCalledWith("05");
  expect(a.sessions).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: "Разбудить для просмотра" })).toBeNull();
});

test("transfer_sends_exact_body_and_shows_conflict", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03],
                         shares: [[409, { name: "Conflict", data: { message: "Доли изменились, пока страница была открыта, — обновите её" } }]] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  await screen.findByRole("table");
  fireEvent.change(screen.getByLabelText("С места"), { target: { value: "03" } });
  fireEvent.change(screen.getByLabelText("На место"), { target: { value: "05" } });
  fireEvent.change(screen.getByLabelText("Сумма, $"), { target: { value: "20" } });
  fireEvent.click(screen.getByRole("button", { name: "Перевести" }));
  expect(await screen.findByText(/Доли изменились/)).toBeTruthy();
  expect(a.shares).toHaveBeenCalledWith([{ place: "03", from_cents: 10000, to_cents: 8000 },
                                         { place: "05", from_cents: 10000, to_cents: 12000 }]);
  expect(a.overview).toHaveBeenCalledTimes(2);                               // обзор заново после 409
});

test("stop_place_button_calls_server_and_reloads", async () => {      // I-1, m-5 р2: пол считает сервер
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03],
                         stopPlace: [[400, { name: "BadRequest", data: { message: "Доля места 03 уже не выше оплаченного: 12,34 $" } }],
                                     [200, { places: OVERVIEW_READY_03.places, budget: OVERVIEW_READY_03.budget }]] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  await screen.findByRole("table");
  fireEvent.change(screen.getByLabelText("С места"), { target: { value: "03" } });
  fireEvent.change(screen.getByLabelText("На место"), { target: { value: "05" } });
  fireEvent.click(screen.getByRole("button", { name: "Остановить место" }));
  expect(await screen.findByText(/уже не выше оплаченного/)).toBeTruthy();
  expect(a.stopPlace).toHaveBeenCalledWith("03", "05");                      // числа пола в вызове нет
  await waitFor(() => expect(a.overview).toHaveBeenCalledTimes(2));         // на 400 — обзор заново
  fireEvent.click(screen.getByRole("button", { name: "Остановить место" }));
  expect(await screen.findByText("Доли обновлены")).toBeTruthy();
  expect(a.shares).not.toHaveBeenCalled();
});

test("signed_out_on_401", async () => {
  const onSignedOut = vi.fn();
  render(<TeacherPage api={teacherApi({ overview: [401, { name: "Unauthorized" }] })} onSignedOut={onSignedOut} />);
  await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
});

// --- сверх брифа ---

test("overview_table_marks_budget_and_refresh", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  expect(await screen.findByText("Кабинет преподавателя")).toBeTruthy();
  const table = await screen.findByRole("table");
  expect(table.classList.contains("places")).toBe(true);
  expect([...table.querySelectorAll("tbody tr")].map((r) => r.getAttribute("data-place")))
    .toEqual(["01", "02", "03", "04", "05", "06", "07", "08", "09", "10"]);
  const cells = (nn: string) => [...container.querySelectorAll(`tr[data-place="${nn}"] td`)].map((c) => c.textContent!.replace(/\s/g, " "));
  expect(cells("03").slice(0, 6)).toEqual(["03", "stu-03", "работает", "100,00 $", "12,35 $", "2"]);
  expect(cells("06")[1]).toBe("stu-06 не подтвердил предупреждение");
  expect(cells("07")[1]).toBe("stu-07 отключена");
  expect(cells("08")[1]).toBe("stu-08 замок");
  expect(cells("10").slice(1, 3)).toEqual(["свободно", "спит"]);
  expect(container.querySelectorAll("button.open-place")).toHaveLength(9);    // у свободного места открывать нечего
  expect(container.querySelector('tr[data-place="10"] button.open-place')).toBeNull();
  expect(screen.getByRole("button", { name: "Открыть место 03" }).textContent).toBe("Открыть");
  expect(screen.getByText(/^Общий бюджет:/).textContent!.replace(/\s/g, " ")).toBe(
    "Общий бюджет: потрачено 12,35 $ из 1 000,00 $; распределено по местам 1 000,00 $, свободно 0,00 $; отбор и проверки 0,50 $");
  fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
  await waitFor(() => expect(a.overview).toHaveBeenCalledTimes(2));        // сам обзор не опрашивается — только кнопкой
  await new Promise((r) => setTimeout(r, 50));
  expect(a.overview).toHaveBeenCalledTimes(2);
  expect(a.sessions).not.toHaveBeenCalled();                                  // обзор места не трогает
});

test("overview_errors_and_no_budget", async () => {
  render(<TeacherPage api={teacherApi({ overview: [200, { places: [] }] })} onSignedOut={vi.fn()} />);
  expect(await screen.findByText("Неожиданный ответ сервера — сообщите преподавателю.")).toBeTruthy();
  const none = { ...OVERVIEW_READY_03, budget: null, shares_ok: false,
                 places: OVERVIEW_READY_03.places.map((p) => ({ ...p, share_cents: null, floor_cents: null, spent_usd: null })) };
  const { container } = render(<TeacherPage api={teacherApi({ overview: [200, none] })} onSignedOut={vi.fn()} />);
  expect(await screen.findByText("Доли бюджета недоступны — перевод закрыт")).toBeTruthy();
  expect([...container.querySelectorAll('tr[data-place="03"] td')].slice(3, 5).map((c) => c.textContent)).toEqual(["—", "—"]);
  expect((container.querySelector("button.share-move") as HTMLButtonElement).disabled).toBe(true);
  expect((container.querySelector("button.share-floor") as HTMLButtonElement).disabled).toBe(true);
});

test("transfer_checks_before_sending_then_ok_reloads", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03],
                         shares: [200, { places: OVERVIEW_READY_03.places, budget: OVERVIEW_READY_03.budget }] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  await screen.findByRole("table");
  expect(container.querySelector("#share-from")).toBe(screen.getByLabelText("С места"));
  expect(container.querySelector("#share-to")).toBe(screen.getByLabelText("На место"));
  expect(container.querySelector("#share-amount")).toBe(screen.getByLabelText("Сумма, $"));
  fireEvent.change(screen.getByLabelText("С места"), { target: { value: "03" } });
  fireEvent.change(screen.getByLabelText("На место"), { target: { value: "05" } });
  fireEvent.change(screen.getByLabelText("Сумма, $"), { target: { value: "1,234" } });
  fireEvent.click(screen.getByRole("button", { name: "Перевести" }));
  expect(await screen.findByText("Сумма — в долларах, не больше двух знаков после запятой")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Сумма, $"), { target: { value: "90" } });
  fireEvent.click(screen.getByRole("button", { name: "Перевести" }));
  expect(await screen.findByText("У места 03 можно взять не больше 87,66 $")).toBeTruthy();
  expect(a.shares).not.toHaveBeenCalled();                                   // подсказка — без запроса
  fireEvent.change(screen.getByLabelText("Сумма, $"), { target: { value: "20.5" } });
  fireEvent.click(screen.getByRole("button", { name: "Перевести" }));
  expect(await screen.findByText("Доли обновлены")).toBeTruthy();
  expect(a.shares).toHaveBeenCalledWith([{ place: "03", from_cents: 10000, to_cents: 7950 },
                                         { place: "05", from_cents: 10000, to_cents: 12050 }]);
  await waitFor(() => expect(a.overview).toHaveBeenCalledTimes(2));         // 200 — новый обзор
  await waitFor(() => expect(a.journal).toHaveBeenCalledTimes(2));          // и журнал
  expect(a.stopPlace).not.toHaveBeenCalled();
});

test("share_refusal_400_and_503_text_as_is", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03],
                         shares: [[400, { name: "BadRequest", data: { message: "Сумма долей мест больше общего бюджета: 1 000,01 $ из 1 000,00 $" } }],
                                  [503, { name: "Unavailable", data: { message: "Журнал действий преподавателя не пишется — просмотр и изменения закрыты" } }]] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  await screen.findByRole("table");
  fireEvent.change(screen.getByLabelText("С места"), { target: { value: "03" } });
  fireEvent.change(screen.getByLabelText("На место"), { target: { value: "05" } });
  fireEvent.change(screen.getByLabelText("Сумма, $"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Перевести" }));
  expect(await screen.findByText("Сумма долей мест больше общего бюджета: 1 000,01 $ из 1 000,00 $")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Перевести" }));
  expect(await screen.findByText("Журнал действий преподавателя не пишется — просмотр и изменения закрыты")).toBeTruthy();
  expect(a.overview).toHaveBeenCalledTimes(1);                               // 400 и 503 перевода — только текст
});

test("agents_tab_cards_and_instruction", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03], sessions: [200, { sessions: [SESSION, SESSION_ARCHIVED] }],
                         agents: [200, AGENTS_03], agent: [200, { name: "pech-1", text: "# Печь 1\n<script>x</script>" }] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть место 03" }));
  const archived = await screen.findByRole("button", { name: /Без названия/ });
  expect(archived.textContent).toContain("в архиве");
  expect(container.querySelectorAll(".sessions button[data-session-id]")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Агенты" }));
  const card = await screen.findByText("Печь 1");
  const article = card.closest("article")!;
  expect(article.textContent).toContain("Узел печей");
  expect(article.textContent).toContain("Следит за 20FAKE_001_PV");
  expect(article.textContent).toContain("текущее значение, сводка за период");   // инструменты учёта — словами
  expect(article.textContent).toContain("работает");
  fireEvent.click(within(article).getByRole("button", { name: "Показать инструкцию" }));
  await waitFor(() => expect(article.querySelector("pre")).not.toBeNull());
  const pre = article.querySelector("pre")!;
  expect(pre.textContent).toBe("# Печь 1\n<script>x</script>");
  expect(article.querySelector("script")).toBeNull();
  expect(a.agent).toHaveBeenCalledWith("03", "pech-1");
  fireEvent.click(screen.getByRole("button", { name: "Разговоры" }));      // вкладка не теряет список
  expect(container.querySelectorAll(".sessions button[data-session-id]")).toHaveLength(2);
  expect(a.sessions).toHaveBeenCalledTimes(1);
  expect(a.agents).toHaveBeenCalledTimes(1);
});

test("reader_asleep_mid_read_offers_wake_and_401_signs_out", async () => {
  const onSignedOut = vi.fn();
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03], sessions: [200, { sessions: [SESSION] }],
                         session: [[503, { name: "WorkplaceNotReady", data: { message: "Рабочее место студента спит — разбудите его для просмотра" } }],
                                   [401, { name: "Unauthorized" }]],
                         wake: [202, { place: "03", state: "ready", detail: "Рабочее место готово" }] });
  render(<TeacherPage api={a} onSignedOut={onSignedOut} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть место 03" }));
  fireEvent.click(await screen.findByRole("button", { name: /Значение 20FAKE_001_PV/ }));
  expect(await screen.findByText("Рабочее место студента спит — разбудите его для просмотра")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Разбудить для просмотра" }));
  fireEvent.click(await screen.findByRole("button", { name: /Значение 20FAKE_001_PV/ }));
  await waitFor(() => expect(onSignedOut).toHaveBeenCalledTimes(1));
});

test("journal_lists_actions_in_words", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03], journal: [200, JOURNAL_ROWS] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  await waitFor(() => expect(container.querySelectorAll(".journal li")).toHaveLength(3));
  const items = [...container.querySelectorAll(".journal li")].map((li) => li.textContent!.replace(/\s/g, " "));
  expect(items[0]).toContain("перевёл долю");
  expect(items[0]).toContain("03: 100,00 $ → 80,00 $");
  expect(items[0]).toContain("выполнено");
  expect(items[1]).toContain("прочитал разговор");
  expect(items[1]).toContain("место 03");
  expect(items[1]).toContain("запрошено");
  expect(items[2]).toContain("разбудил место");
  expect(items[2]).toContain("место 05");
  fireEvent.click(screen.getByRole("button", { name: "Обновить журнал" }));
  await waitFor(() => expect(a.journal).toHaveBeenCalledTimes(2));
});
