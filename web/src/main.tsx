// Точка входа чата. zodJitless — первым импортом (Д6-R40): ESM исполняет модули в порядке импортов, и
// настройка zod встаёт раньше рантайма assistant-ui. Стили — файлом (CSP style-src 'self'). Сбой вне границы
// ошибок чата ловит корень (mount.tsx).
import "./zodJitless";
import "./chat.css";
import { createApi } from "./api";
import { mountChat } from "./mount";

const root = document.getElementById("root");
if (root) mountChat(root, { api: createApi(), navigate: (u) => location.assign(u) });
