import "./zodJitless";
import { createRoot } from "react-dom/client";

// Заглушка каркаса (А2); приложение чата монтирует А4.
const root = document.getElementById("root");
if (root) createRoot(root).render(<p>Чат загружается…</p>);
