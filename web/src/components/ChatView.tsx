// Разговор — только отображение (Д6-R41, R42): runtime приходит в пропсах, сети здесь нет.
// Примитивы — без Radix-меню и всплывающих окон (они вставляют <style>, а CSP его запрещает): ThreadPrimitive,
// ComposerPrimitive (Root, Input, Send, Cancel), MessagePrimitive (Parts, Error), ErrorPrimitive.Message.
// Кнопки — «Отправить» и «Стоп» (и «Новый разговор» в toolbar от Chat); ни вложений, ни правки, ни повтора,
// ни копирования, ни ветвления — такие вызовы шлюз отверг бы (Review Focus 4).
import {
  AssistantRuntimeProvider, ComposerPrimitive, ErrorPrimitive, MessagePrimitive, ThreadPrimitive,
  type AssistantRuntime,
} from "@assistant-ui/react";
import type { ReactNode } from "react";
import { MAX_TEXT, T_SEND, T_STOP } from "../texts";
import { ImagePartText, MarkdownText, PlainText, ToolLine } from "./MessageParts";

export interface ChatViewProps {
  runtime: AssistantRuntime;
  streamLine?: string;          // «Связь с рабочим местом прервалась — переподключаюсь через N с»
  error?: string;               // ошибка последнего действия (explainError)
  pending?: ReactNode;          // висящие разрешения и вопросы агента
  toolbar?: ReactNode;          // «Новый разговор»
}

const Nothing = () => null;

// постоянные объекты: MessagePrimitive.Parts сравнивает их части по ссылке (memo)
const ASSISTANT_PARTS = {
  Text: MarkdownText,
  Reasoning: Nothing,                                   // рассуждение модели не показывается
  Image: ImagePartText,
  tools: { Fallback: ToolLine },
};
const USER_PARTS = { Text: PlainText, Image: ImagePartText };

function UserMessage() {
  return (
    <div className="msg" data-role="user">
      <MessagePrimitive.Parts components={USER_PARTS} />
    </div>
  );
}

function AssistantMessage() {
  return (
    <div className="msg" data-role="assistant">
      <MessagePrimitive.Parts components={ASSISTANT_PARTS} />
      <MessagePrimitive.Error>
        <p className="msg-error" role="alert"><ErrorPrimitive.Message /></p>
      </MessagePrimitive.Error>
    </div>
  );
}

export function ChatView(p: ChatViewProps) {
  return (
    <AssistantRuntimeProvider runtime={p.runtime}>
      <ThreadPrimitive.Root className="thread">
        {p.toolbar !== undefined && <div className="toolbar">{p.toolbar}</div>}
        <ThreadPrimitive.Viewport className="viewport">
          <ThreadPrimitive.Messages>
            {({ message }) => message.role === "user" ? <UserMessage />
              : message.role === "assistant" ? <AssistantMessage /> : null}
          </ThreadPrimitive.Messages>
        </ThreadPrimitive.Viewport>
        {p.pending}
        {p.streamLine && <p className="stream-line" role="status">{p.streamLine}</p>}
        {p.error && <p className="error-line" role="alert">{p.error}</p>}
        <ComposerPrimitive.Root className="composer">
          <ComposerPrimitive.Input className="composer-input" maxLength={MAX_TEXT} minRows={2} maxRows={10} autoFocus
                                   addAttachmentOnPaste={false} />
          <ThreadPrimitive.If running={false}>
            <ComposerPrimitive.Send className="send">{T_SEND}</ComposerPrimitive.Send>
          </ThreadPrimitive.If>
          <ThreadPrimitive.If running>
            <ComposerPrimitive.Cancel className="stop">{T_STOP}</ComposerPrimitive.Cancel>
          </ThreadPrimitive.If>
        </ComposerPrimitive.Root>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
