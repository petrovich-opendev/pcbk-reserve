// m1 доводки р1, отказ переключения: прежний разговор (initialSessionId) не открылся — switchToThread отвергнут.
// Блокировка «ждём прежний разговор» снимается и при отказе, ошибка видна строкой, студент пишет в новый разговор.
// Отдельный файл: vi.mock над useOpenCodeRuntime подменяет threads.switchToThread у настоящего рантайма.
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { createApi } from "../src/api";
import { Chat } from "../src/components/Chat";
import { T_UNKNOWN } from "../src/texts";
import { CHAT_TIMEOUT_MS, SES_K, agentLine, box, chatGateway, send } from "./chatGateway";

const failSwitch = vi.hoisted(() => ({ on: false, calls: 0 }));
vi.mock("@assistant-ui/react-opencode", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@assistant-ui/react-opencode")>();
  return {
    ...mod,
    useOpenCodeRuntime: (o: Parameters<typeof mod.useOpenCodeRuntime>[0]) => {
      const runtime = mod.useOpenCodeRuntime(o);
      const threads = runtime.threads as unknown as { switchToThread(...a: unknown[]): Promise<void>; __wrapped?: true };
      if (!threads.__wrapped) {
        const original = threads.switchToThread.bind(threads);
        threads.switchToThread = (...a: unknown[]) => {
          failSwitch.calls += 1;
          return failSwitch.on ? Promise.reject(new Error("переключение не удалось")) : original(...a);
        };
        threads.__wrapped = true;
      }
      return runtime;
    },
  };
});

afterEach(() => {
  failSwitch.on = false;
  failSwitch.calls = 0;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("open_previous_switch_rejects_unlocks", async () => {
  failSwitch.on = true;
  const gw = chatGateway({});
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES_K} loadAgents={createApi().agents} />);
  await waitFor(() => expect(failSwitch.calls).toBe(1));                         // список пришёл — открыть прежний
  expect((await screen.findByRole("alert")).textContent).toBe(T_UNKNOWN);       // отказ — строкой ошибки
  await waitFor(() => expect(box().disabled).toBe(false));                       // блокировка снята и при отказе
  expect(agentLine(container)).toBe("Агент: Помощник технолога КДМ-1");
  const first = await send(gw, "что сейчас");
  expect(first.session).not.toBe(SES_K);                                         // новый разговор, помощник
  expect(first.body).not.toHaveProperty("agent");
  expect(gw.loads(SES_K)).toBe(0);
}, CHAT_TIMEOUT_MS);

test("open_previous_switch_ok_wrapper_passes_through", async () => {
  // положительный контроль обёртки: без отказа прежний разговор открывается как обычно
  const gw = chatGateway({});
  const { container } = render(<Chat onHalt={vi.fn()} initialSessionId={SES_K} loadAgents={createApi().agents} />);
  await screen.findByText("Какой узел и какие теги?");
  expect(failSwitch.calls).toBe(1);
  await waitFor(() => expect(agentLine(container)).toBe("Агент: Конструктор агентов"));
  const next = await send(gw, "дальше");
  expect(next).toMatchObject({ session: SES_K, body: { agent: "konstruktor" } });
}, CHAT_TIMEOUT_MS);
