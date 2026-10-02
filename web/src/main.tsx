// Точка входа чата. zodJitless — первым импортом (Д6-R40): ESM исполняет модули в порядке импортов, и
// настройка zod встаёт раньше рантайма assistant-ui. Стили — файлом (CSP style-src 'self').
import "./zodJitless";
import "./chat.css";
import { createRoot } from "react-dom/client";
import { createApi } from "./api";
import { App } from "./App";

const root = document.getElementById("root");
if (root) createRoot(root).render(<App api={createApi()} navigate={(u) => location.assign(u)} />);
