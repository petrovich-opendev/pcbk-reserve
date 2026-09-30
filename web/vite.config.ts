// Д6-R40: сборка чата под строгий CSP — base /chat/, без data: и встроенных скриптов,
// без карт исходников; опись build.json и метка сборки — плагин pcbkBuildInfo.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { pcbkBuildInfo } from "./build/buildInfo.ts";

export default defineConfig({
  base: "/chat/",
  plugins: [react(), pcbkBuildInfo(process.env.PCBK_BUILD ?? "dev")],
  build: {
    outDir: process.env.PCBK_WEB_OUT ?? "../edge/static/chat",
    emptyOutDir: true,
    target: ["chrome109", "edge109", "firefox115"],                    // браузеры класса — по ответу владельца
    assetsInlineLimit: 0,                                               // без data: (CSP img-src/font-src 'self')
    modulePreload: { polyfill: false },                                 // без встроенного скрипта полифила
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts?(x)"],
    setupFiles: ["tests/setup.ts"],
  },
});
