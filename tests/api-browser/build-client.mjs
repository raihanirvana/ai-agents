// Transpile the actual client for the disposable browser fixture. Production uses Vite.
import ts from "typescript";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";

const source = readFileSync("apps/web/src/api/client.ts", "utf8");
const result = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020,
} });
mkdirSync("data/dev008", { recursive: true });
writeFileSync("data/dev008/client.js", result.outputText);
