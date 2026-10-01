// Монтирование чата в #root (итоговая доводка Д6б, m3). Сбой вне ErrorBoundary — в App, шапке или фазах до чата —
// React 19 не показывает: корень размонтирует всё дерево и зовёт onUncaughtError, а загрузчик boot.js после старта
// молчит. Без обработчика страница осталась бы пустой и без отчёта. Обработчик — один раз на страницу: отметка старта
// загрузчику (иначе сбой первой отрисовки через 20 с дал бы второй отчёт boot_failed и сменил бы текст), отчёт
// ui_crash и текст T_UI_CRASH в #root вместо снятого дерева; сам сбой — в консоль, как без обработчика. Кнопки
// «Перезапустить» здесь нет: дерево снято целиком, помогает только перезагрузка страницы.
import type { ComponentType } from "react";
import { createRoot, type Root } from "react-dom/client";
import { App, type AppProps } from "./App";
import { T_UI_CRASH } from "./texts";

// AppImpl — для тестов (как ChatImpl у App): сбой первой отрисовки, до эффектов App
export function mountChat(container: HTMLElement, props: AppProps, AppImpl: ComponentType<AppProps> = App): Root {
  let crashed = false;
  const root = createRoot(container, {
    onUncaughtError(error) {
      console.error("[pcbk] сбой оболочки чата:", error);
      if (crashed) return;
      crashed = true;
      window.__pcbkStarted?.();
      void props.api.report("ui_crash").catch(() => undefined);
      const text = document.createElement("p");
      text.className = "phase";
      text.setAttribute("data-kind", "fail");
      text.setAttribute("role", "alert");
      text.textContent = T_UI_CRASH;
      container.replaceChildren(text);
    },
  });
  root.render(<AppImpl {...props} />);
  return root;
}
