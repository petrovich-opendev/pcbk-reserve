// Сбой интерфейса чата (Д6-R51, ui_crash): граница ошибок вокруг чата. Сбой отрисовки — отчёт (один на сбой), текст
// и «Перезапустить чат»: потомки монтируются заново (новый клиент, рантайм и поток). С onRestart перезапуск решает
// владелец границы (App: новое монтирование с последним открытым разговором — доводка 1, I1), без него граница
// перемонтирует потомков сама. Шапка с «Выйти» — вне границы и остаётся. После старта загрузчик boot.js молчит, так
// что сбои React ловит только эта граница.
// Граница ошибок в React — только классом.
import { Component, Fragment, type ReactNode } from "react";
import { T_UI_CRASH, T_UI_RESTART } from "../texts";

export interface ErrorBoundaryProps {
  onCrash(error: unknown): void;
  onRestart?(): void;             // «Перезапустить чат»: новое монтирование у владельца границы
  children: ReactNode;
}

interface State {
  crashed: boolean;
  restarts: number;               // ключ потомков: перезапуск — новое монтирование
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, State> {
  state: State = { crashed: false, restarts: 0 };
  private reported = false;       // один отчёт на сбой: React может передать границе несколько ошибок одной отрисовки

  static getDerivedStateFromError(): Partial<State> {
    return { crashed: true };
  }

  componentDidCatch(error: unknown): void {
    if (this.reported) return;
    this.reported = true;
    try {
      this.props.onCrash(error);
    } catch {
      /* отчёт не ушёл — интерфейс сбоя всё равно показан */
    }
  }

  private restart = (): void => {
    if (this.props.onRestart !== undefined) {
      this.props.onRestart();
      return;
    }
    this.reported = false;
    this.setState((s) => ({ crashed: false, restarts: s.restarts + 1 }));
  };

  render(): ReactNode {
    if (this.state.crashed) {
      return (
        <section className="phase crash">
          <p data-kind="fail" role="alert">{T_UI_CRASH}</p>
          <button type="button" onClick={this.restart}>{T_UI_RESTART}</button>
        </section>
      );
    }
    return <Fragment key={this.state.restarts}>{this.props.children}</Fragment>;
  }
}
