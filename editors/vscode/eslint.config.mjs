// ESLint flat config. Besides the recommended sets, two project rules are enforced here rather
// than trusted to review: nothing may parse text as HTML (ADR-029: model-written text reaches the
// webview and must go in with textContent), and extension code logs through an OutputChannel,
// never the console (so nothing sensitive ends up in the shared extension-host log).
import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import tseslint from "typescript-eslint";

const noHtmlParsing = [
  { selector: "MemberExpression[property.name='innerHTML']", message: "No innerHTML: build DOM with createElement + textContent." },
  { selector: "MemberExpression[property.name='outerHTML']", message: "No outerHTML: build DOM with createElement + textContent." },
  { selector: "CallExpression[callee.property.name='insertAdjacentHTML']", message: "No insertAdjacentHTML." },
  { selector: "CallExpression[callee.property.name='createContextualFragment']", message: "No createContextualFragment." },
  { selector: "CallExpression[callee.object.name='document'][callee.property.name=/^write(ln)?$/]", message: "No document.write." },
  { selector: "NewExpression[callee.name='DOMParser']", message: "No DOMParser: model text is never parsed as markup." },
  { selector: "CallExpression[callee.name='eval']", message: "No eval." },
  { selector: "NewExpression[callee.name='Function']", message: "No new Function." },
];

export default defineConfig(
  { ignores: ["node_modules/**", "out/**", "dist/**", ".vscode-test/**"] },
  js.configs.recommended,
  tseslint.configs.recommended,
  {
    files: ["**/*.ts"],
    rules: {
      "no-restricted-syntax": ["error", ...noHtmlParsing],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      eqeqeq: ["error", "always"],
    },
  },
  {
    files: ["src/**/*.ts"],
    rules: { "no-console": "error" },
  },
  {
    files: ["esbuild.mjs", "eslint.config.mjs"],
    languageOptions: { globals: { process: "readonly", console: "readonly" } },
  },
);
