// «Учётные записи» кабинета преподавателя (Д10-R110): завести студента на свободное место, пароль один раз, новый пароль,
// отключить с подтверждением, включить, снять замок. api — заглушка TeacherApi (teacherFixtures.ts); ручек учёток на
// сервере тест не ждёт — договор R108. Пароли — выдуманные строки тестов.
import { readFileSync } from "node:fs";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { TeacherPage } from "../src/components/TeacherPage";
import { createTeacherApi, isLogin } from "../src/teacherApi";
import { T_TCH_ACC_LOGIN_RULE } from "../src/texts";
import { OVERVIEW_FREE_04, OVERVIEW_READY_03, teacherApi } from "./teacherFixtures";

afterEach(() => {
  vi.restoreAllMocks();
});

test("add_student_shows_password_once_then_forgets", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_FREE_04],
                         addStudent: [201, { login: "d10-probe-n", place: "04", password: "Abc-123-xyz_Q" }] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Логин"), { target: { value: "d10-probe-n" } });
  fireEvent.change(screen.getByLabelText("Место"), { target: { value: "04" } });
  fireEvent.click(screen.getByRole("button", { name: "Завести" }));
  expect((await screen.findByText("Abc-123-xyz_Q")).closest(".password-once")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Я записал пароль" }));
  expect(container.textContent).not.toContain("Abc-123-xyz_Q");
  expect(a.addStudent).toHaveBeenCalledWith("d10-probe-n", "04");
});

test("place_select_offers_only_free_places", async () => {          // m-11: free из обзора, не login === null
  // OVERVIEW_FREE_04: 04 — free; 07 — без учётки, но закреплено за удалённым логином (free: false); прочие — студенты
  render(<TeacherPage api={teacherApi({ overview: [200, OVERVIEW_FREE_04] })} onSignedOut={vi.fn()} />);
  const select = await screen.findByLabelText("Место") as HTMLSelectElement;
  expect([...select.options].map((o) => o.value).filter((v) => v !== "")).toEqual(["04"]);
});

test("disable_asks_first_then_reloads_overview", async () => {      // m-8
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03], disable: [204, null] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Отключить stu-03" }));
  expect(screen.getByText("Отключить учётку stu-03? Студент выйдет из чата")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(a.disable).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Отключить stu-03" }));
  fireEvent.click(screen.getByRole("button", { name: "Да, отключить" }));
  await waitFor(() => expect(a.disable).toHaveBeenCalledWith("stu-03"));
  await waitFor(() => expect(a.overview).toHaveBeenCalledTimes(2));     // обзор заново после действия
});

// --- сверх брифа ---

test("password_stays_out_of_console_storage_and_url", async () => {
  // пароль живёт только в состоянии окна: ни журнала браузера, ни localStorage/sessionStorage, ни адреса, ни заголовка
  const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m));
  const href = window.location.href;
  const a = teacherApi({ overview: [200, OVERVIEW_FREE_04],
                         addStudent: [201, { login: "d10-probe-n", place: "04", password: "Pw-Once-0123" }] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Логин"), { target: { value: "d10-probe-n" } });
  fireEvent.change(screen.getByLabelText("Место"), { target: { value: "04" } });
  fireEvent.click(screen.getByRole("button", { name: "Завести" }));
  const code = await screen.findByText("Pw-Once-0123");
  expect(code.tagName).toBe("CODE");                                          // договор с Б3: .password-once code
  expect(container.querySelector(".password-once code")).toBe(code);
  expect(code.getAttribute("translate")).toBe("no");                         // переводчик браузера текст не уносит
  expect(screen.getByText("Пароль показан один раз — запишите его и передайте студенту")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Скопировать" }));       // в jsdom буфера нет — пароль выделен
  expect(await screen.findByText("Скопировать не удалось — пароль выделен, нажмите Ctrl+C")).toBeTruthy();
  const leaks = (): string[] => [
    JSON.stringify(spies.flatMap((s) => s.mock.calls)),
    JSON.stringify({ ...window.localStorage }), JSON.stringify({ ...window.sessionStorage }),
    window.location.href, document.title,
  ].filter((v) => v.includes("Pw-Once-0123"));
  expect(leaks()).toEqual([]);
  expect(window.location.href).toBe(href);
  fireEvent.click(container.querySelector("button.acc-done")!);
  expect(container.querySelector(".password-once")).toBeNull();
  expect(document.body.innerHTML).not.toContain("Pw-Once-0123");
  expect(leaks()).toEqual([]);
  window.getSelection()?.removeAllRanges();
});

test("row_buttons_follow_account_state_enable_unlock_without_asking", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03], enable: [204, null], unlock: [204, null] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  await screen.findByRole("button", { name: "Отключить stu-03" });
  const buttons = (nn: string): string[] =>
    [...container.querySelectorAll(`tr[data-place="${nn}"] button[class^="acc-"]`)].map((b) => b.className);
  expect(buttons("03")).toEqual(["acc-passwd", "acc-disable"]);
  expect(buttons("07")).toEqual(["acc-passwd", "acc-enable"]);               // отключена — «Включить»
  expect(buttons("08")).toEqual(["acc-passwd", "acc-disable", "acc-unlock"]); // замок — «Снять замок»
  expect(buttons("10")).toEqual([]);                                         // свободное место — учётки нет
  expect(screen.getByRole("button", { name: "Новый пароль stu-03" }).textContent).toBe("Новый пароль");
  expect(screen.getByRole("button", { name: "Отключить stu-03" }).textContent).toBe("Отключить");
  fireEvent.click(screen.getByRole("button", { name: "Включить stu-07" }));
  expect(await screen.findByText("Учётка stu-07 включена")).toBeTruthy();
  expect(a.enable).toHaveBeenCalledWith("stu-07");
  await waitFor(() => expect(a.overview).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole("button", { name: "Снять замок stu-08" }));
  expect(await screen.findByText("Замок входа stu-08 снят")).toBeTruthy();
  expect(a.unlock).toHaveBeenCalledWith("stu-08");
  await waitFor(() => expect(a.overview).toHaveBeenCalledTimes(3));
  await waitFor(() => expect(a.journal).toHaveBeenCalledTimes(3));            // и журнал — строки действий
  expect(a.disable).not.toHaveBeenCalled();
  expect(a.newPassword).not.toHaveBeenCalled();
});

test("new_password_asks_then_same_window", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_READY_03], newPassword: [200, { password: "New-Pw-4567" }] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Новый пароль stu-03" }));
  expect(screen.getByText("Выдать учётке stu-03 новый пароль? Старый перестанет работать, студент выйдет из чата"))
    .toBeTruthy();
  expect(a.newPassword).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Да, выдать новый пароль" }));
  const window_ = (await screen.findByText("New-Pw-4567")).closest(".password-once") as HTMLElement;
  expect(window_.textContent).toContain("stu-03");
  expect(a.newPassword).toHaveBeenCalledWith("stu-03");
  await waitFor(() => expect(a.overview).toHaveBeenCalledTimes(2));
  // пока окно открыто, кнопки учёток выключены: новый пароль не перебьёт незаписанный
  expect((screen.getByRole("button", { name: "Отключить stu-05" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Завести" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(within(window_).getByRole("button", { name: "Я записал пароль" }));
  expect(container.textContent).not.toContain("New-Pw-4567");
  expect((screen.getByRole("button", { name: "Отключить stu-05" }) as HTMLButtonElement).disabled).toBe(false);
});

test("refusals_text_as_is_then_401_signs_out", async () => {
  const onSignedOut = vi.fn();
  const a = teacherApi({ overview: [200, OVERVIEW_FREE_04],
                         addStudent: [[400, { name: "BadRequest", data: { message: "место 04 занято" } }],
                                      [503, { name: "Unavailable", data: { message: "Журнал действий преподавателя не пишется — просмотр и изменения закрыты" } }],
                                      [401, { name: "Unauthorized" }]],
                         disable: [404, { name: "NotFound", data: { message: "Учётки студента с таким логином нет" } }] });
  render(<TeacherPage api={a} onSignedOut={onSignedOut} />);
  fireEvent.change(await screen.findByLabelText("Логин"), { target: { value: "d10-probe-n" } });
  fireEvent.change(screen.getByLabelText("Место"), { target: { value: "04" } });
  fireEvent.click(screen.getByRole("button", { name: "Завести" }));
  expect(await screen.findByText("место 04 занято")).toBeTruthy();
  await waitFor(() => expect(a.overview).toHaveBeenCalledTimes(2));          // и после отказа обзор заново
  expect(document.querySelector(".password-once")).toBeNull();
  expect((screen.getByLabelText("Логин") as HTMLInputElement).value).toBe("d10-probe-n");   // поле не стёрто
  fireEvent.click(screen.getByRole("button", { name: "Завести" }));
  expect(await screen.findByText("Журнал действий преподавателя не пишется — просмотр и изменения закрыты")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Отключить stu-03" }));
  fireEvent.click(screen.getByRole("button", { name: "Да, отключить" }));
  expect(await screen.findByText("Учётки студента с таким логином нет")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Завести" }));
  await waitFor(() => expect(onSignedOut).toHaveBeenCalledTimes(1));
  expect(a.addStudent).toHaveBeenCalledTimes(3);
});

test("checks_before_sending_without_request", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_FREE_04] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Логин"), { target: { value: "Иванов" } });
  fireEvent.change(screen.getByLabelText("Место"), { target: { value: "04" } });
  fireEvent.click(screen.getByRole("button", { name: "Завести" }));
  expect(await screen.findByText("Логин — 3…32 знака из a-z, 0-9, «.», «_», «-», первый — буква a-z")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Логин"), { target: { value: "d10-probe-n" } });
  fireEvent.change(screen.getByLabelText("Место"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Завести" }));
  expect(await screen.findByText("Выберите свободное место")).toBeTruthy();
  expect(a.addStudent).not.toHaveBeenCalled();
  expect(a.overview).toHaveBeenCalledTimes(1);
});

test("password_window_survives_failed_overview_reload", async () => {
  // обзор после заведения не пришёл (503) — окно пароля остаётся: второго показа не будет
  const a = teacherApi({ overview: [[200, OVERVIEW_FREE_04],
                                    [503, { name: "Unavailable", data: { message: "Учётные записи недоступны — повторите позже" } }]],
                         addStudent: [201, { login: "d10-probe-n", place: "04", password: "Keep-Me-0042" }] });
  render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Логин"), { target: { value: "d10-probe-n" } });
  fireEvent.change(screen.getByLabelText("Место"), { target: { value: "04" } });
  fireEvent.click(screen.getByRole("button", { name: "Завести" }));
  expect(await screen.findByText("Учётные записи недоступны — повторите позже")).toBeTruthy();
  expect(screen.getByText("Keep-Me-0042").closest(".password-once")).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();                                // таблицы нет, окно есть
  fireEvent.click(screen.getByRole("button", { name: "Я записал пароль" }));
  expect(screen.queryByText("Keep-Me-0042")).toBeNull();
});

test("odd_success_body_is_unknown_not_a_window", async () => {
  const a = teacherApi({ overview: [200, OVERVIEW_FREE_04], addStudent: [201, { login: "d10-probe-n", place: "04" }] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Логин"), { target: { value: "d10-probe-n" } });
  fireEvent.change(screen.getByLabelText("Место"), { target: { value: "04" } });
  fireEvent.click(screen.getByRole("button", { name: "Завести" }));
  expect(await screen.findByText("Неожиданный ответ сервера — сообщите преподавателю.")).toBeTruthy();
  expect(container.querySelector(".password-once")).toBeNull();
  await waitFor(() => expect(a.overview).toHaveBeenCalledTimes(2));         // учётка могла завестись — обзор покажет
});

test("api_paths_bodies_and_login_checked_before_request", async () => {
  // ручки R108: все POST; логин в пути — только по LOGIN_RE учёток, иначе 400 без запроса (второй рубеж после сервера)
  const calls: { path: string; method: string | undefined; body: unknown }[] = [];
  const fetchImpl = vi.fn(async (path: string, init: RequestInit) => {
    calls.push({ path, method: init.method, body: init.body === undefined ? undefined : JSON.parse(String(init.body)) });
    return new Response(null, { status: 204 });
  });
  const api = createTeacherApi(fetchImpl);
  await api.addStudent("d10-probe-n", "04");
  await api.newPassword("stu-03");
  await api.disable("stu-03");
  await api.enable("stu.07_x");
  await api.unlock("stu-08");
  expect(calls).toEqual([
    { path: "/pcbk/api/teacher/accounts", method: "POST", body: { login: "d10-probe-n", place: "04" } },
    { path: "/pcbk/api/teacher/accounts/stu-03/password", method: "POST", body: {} },
    { path: "/pcbk/api/teacher/accounts/stu-03/disable", method: "POST", body: {} },
    { path: "/pcbk/api/teacher/accounts/stu.07_x/enable", method: "POST", body: {} },
    { path: "/pcbk/api/teacher/accounts/stu-08/unlock", method: "POST", body: {} },
  ]);
  for (const bad of ["../x", "ab", "Stu-03", "stu-03/disable", "a".repeat(33), "stu 03", "1stu"]) {
    for (const call of [api.newPassword, api.disable, api.enable, api.unlock]) {
      const r = await call(bad);
      expect(r.status).toBe(400);
    }
  }
  expect((await api.addStudent("d10-probe-n", "4")).status).toBe(400);
  expect((await api.addStudent("Иванов", "04")).status).toBe(400);
  expect(calls).toHaveLength(5);                                               // отказы — без запроса
});

test("login_rule_same_as_gateway_accounts", () => {
  // подсказка и проверка до запроса — копия правила учёток шлюза (LOGIN_RE и _LOGIN_RULE accounts.py): разойдутся — тест
  const src = readFileSync("../core/pcbk_core/gateway/accounts.py", "utf8");
  expect(src).toMatch(/^LOGIN_RE = re\.compile\(r"\[a-z\]\[a-z0-9\._-\]\{2,31\}"\)$/m);
  const rule = /^_LOGIN_RULE = "логин: (.+)"$/m.exec(src)?.[1];
  expect(T_TCH_ACC_LOGIN_RULE).toBe(`Логин — ${rule}`);
  // fullmatch шлюза: 3…32 знака, первый — буква a-z
  expect(["abc", "d10-probe-n", "a.b_c-d", "a" + "b".repeat(31)].map(isLogin)).toEqual([true, true, true, true]);
  expect(["ab", "a" + "b".repeat(32), "-abc", "1abc", "Abc", "ab c", "abc\n", "абв"].map(isLogin))
    .toEqual([false, false, false, false, false, false, false, false]);
});

test("journal_shows_account_actions_in_words", async () => {
  // строки учёток журнала действий (R108: target — логин, у account_add — и место) — словами, а не кодом действия
  const rows = { rows: [
    { id: 2, at: "2026-10-11T09:05:00+00:00", login: "t", action: "account_disable", place: null, target: "stu-03",
      outcome: "ok" },
    { id: 1, at: "2026-10-11T09:04:00+00:00", login: "t", action: "account_add", place: "04", target: "d10-probe-n",
      outcome: "ok" },
  ] };
  const a = teacherApi({ overview: [200, OVERVIEW_FREE_04], journal: [200, rows] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  await waitFor(() => expect(container.querySelectorAll(".journal li")).toHaveLength(2));
  const items = [...container.querySelectorAll(".journal li")].map((li) => li.textContent!.replace(/\s/g, " "));
  expect(items[1]).toContain("завёл учётку студента");
  expect(items[1]).toContain("место 04");
  expect(items[1]).toContain("выполнено");
  expect(items[1]).not.toContain("account_add");
  expect(items[0]).toContain("отключил учётку");
  expect(items[0]).not.toContain("account_disable");
});

test("journal_account_rows_show_target_login_as_text", async () => {
  // у account_* — логин учётки (target) рядом с действием; без target — без него; разметка в логине — только текстом;
  // у прочих действий target (id разговора) не показывается
  const rows = { rows: [
    { id: 4, at: "2026-10-11T09:07:00+00:00", login: "t", action: "account_disable", place: null, target: "stu-03",
      outcome: "ok" },
    { id: 3, at: "2026-10-11T09:06:00+00:00", login: "t", action: "account_enable", place: null, target: null,
      outcome: "refused" },
    { id: 2, at: "2026-10-11T09:05:00+00:00", login: "t", action: "account_unlock", place: null, target: "<b>stu-08</b>",
      outcome: "ok" },
    { id: 1, at: "2026-10-11T09:04:00+00:00", login: "t", action: "read", place: "03", target: "ses_" + "A1".repeat(13),
      outcome: "requested" },
  ] };
  const a = teacherApi({ overview: [200, OVERVIEW_FREE_04], journal: [200, rows] });
  const { container } = render(<TeacherPage api={a} onSignedOut={vi.fn()} />);
  await waitFor(() => expect(container.querySelectorAll(".journal li")).toHaveLength(4));
  const what = [...container.querySelectorAll(".journal li .journal-what")].map((s) => s.textContent);
  expect(what).toEqual(["отключил учётку stu-03", "включил учётку", "снял замок входа <b>stu-08</b>",
                        "прочитал разговор; место 03"]);
  expect(container.querySelector(".journal b")).toBeNull();                       // текстом, не разметкой
  expect(container.querySelector(".journal")!.textContent).not.toMatch(/account_|null/);
});
