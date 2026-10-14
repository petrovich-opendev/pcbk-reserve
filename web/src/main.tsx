// Точка входа чата. zodJitless — первым импортом (Д6-R40): ESM исполняет модули в порядке импортов, и
// настройка zod встаёт раньше рантайма assistant-ui. Стили — файлом (CSP style-src 'self'). Сбой вне границы
// ошибок чата ловит корень (mount.tsx).
import "./zodJitless";
import "./chat.css";
// аналитика КДМ-1 (план КДМ1, Task 6): стили — отдельным файлом, но импорт здесь, не в лениво загружаемом модуле
// AnalyticsPage — иначе вкладка «Аналитика» в шапке осталась бы без своих классов (.an-inner-nav и т. п.) до
// первого открытия вкладки; сама страница грузится отдельным кодовым блоком (App.tsx — lazy), стили — нет (в
// проекте CSS никогда не дробится по вкладкам, приём chat.css для «Уроки»/«События»).
import "./analytics.css";
import { createApi } from "./api";
import { mountChat } from "./mount";

const root = document.getElementById("root");
if (root) mountChat(root, { api: createApi(), navigate: (u) => location.assign(u) });
