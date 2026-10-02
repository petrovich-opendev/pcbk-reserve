// Висящие разрешения и вопросы агента (Д6-R42): ответить на них этот чат пока не умеет — строка и только
// «Отклонить» (reply(id, "reject") / rejectQuestion(id); отклонение шлюз пропускает всегда, Д5-R34).
// Кнопок «Разрешить» и ответа на вопрос нет.
import { T_PENDING, T_REJECT } from "../texts";

export interface PendingInteractionsProps {
  permissions: { id: string; permission: string }[];
  questions: { id: string }[];
  onRejectPermission(id: string): void;
  onRejectQuestion(id: string): void;
}

export function PendingInteractions(p: PendingInteractionsProps) {
  if (p.permissions.length === 0 && p.questions.length === 0) return null;
  return (
    <ul className="pending">
      {p.permissions.map((r) => (
        <li key={`per:${r.id}`} data-permission={r.permission}>
          <span>{T_PENDING}</span>
          <button type="button" onClick={() => p.onRejectPermission(r.id)}>{T_REJECT}</button>
        </li>
      ))}
      {p.questions.map((q) => (
        <li key={`que:${q.id}`}>
          <span>{T_PENDING}</span>
          <button type="button" onClick={() => p.onRejectQuestion(q.id)}>{T_REJECT}</button>
        </li>
      ))}
    </ul>
  );
}
