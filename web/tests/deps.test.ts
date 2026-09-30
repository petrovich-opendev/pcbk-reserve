// Закреплённые версии и lock-файл (Д6-R40, Global Constraints Д6): точные версии,
// одна копия SDK, React и zod, пакеты только из реестра npm с суммой sha512.
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";

const pkg = JSON.parse(readFileSync("package.json", "utf8"));
const lock = JSON.parse(readFileSync("package-lock.json", "utf8"));
const versions = (name: string) => Object.entries<any>(lock.packages)
  .filter(([p]) => p.endsWith(`node_modules/${name}`)).map(([, v]) => v.version);

test("pins_are_exact_and_single", () => {
  for (const deps of [pkg.dependencies, pkg.devDependencies])
    for (const v of Object.values(deps)) expect(v).toMatch(/^\d+\.\d+\.\d+$/);
  expect(versions("@opencode-ai/sdk")).toEqual(["1.18.33"]);
  expect(versions("react")).toEqual(["19.3.0"]);
  expect(versions("zod")).toEqual(["4.6.5"]);                                           // один экземпляр: jitless действует и на assistant-ui
  expect(versions("@assistant-ui/react-opencode")).toEqual(["0.2.25"]);
  expect(pkg.devDependencies["@testing-library/react"]).toBe("16.3.3");
  expect(lock.packages["node_modules/@assistant-ui/react-opencode"].integrity).toBe(
    "sha512-MPP7w+MbTjFKSZX2aoNd8wK/6WXIEuU/w2qc5jy+/Vx249lQZ+mwzEZ6mPoYJbVr8VHA4x8yuXG30vZaadSx1w==");
  for (const [p, v] of Object.entries<any>(lock.packages)) if (p && !v.link) {
    expect(v.resolved, p).toMatch(/^https:\/\/registry\.npmjs\.org\//);
    expect(v.integrity, p).toMatch(/^sha512-/);
  }
});
