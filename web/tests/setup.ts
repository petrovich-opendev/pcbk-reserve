// Общая подготовка тестов веба (setupFiles в vite.config.ts).
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// В jsdom нет ResizeObserver, а ThreadPrimitive.Viewport assistant-ui его зовёт.
class ResizeObserverStub implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
globalThis.ResizeObserver ??= ResizeObserverStub;

// В jsdom 30 у Element нет scrollTo, а автопрокрутка Viewport зовёт div.scrollTo через
// requestAnimationFrame — без заглушки необработанное исключение Vitest (preflight M7).
// В среде node (`// @vitest-environment node`) Element нет — пропуск.
if (typeof Element !== "undefined") Element.prototype.scrollTo ??= function scrollTo() {};

// Vitest без globals: автоочистка @testing-library/react не включается сама, и разметка
// прошлого теста осталась бы в document.body следующего.
afterEach(() => cleanup());
