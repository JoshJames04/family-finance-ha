/**
 * Regenerate src/api/schema.d.ts from the backend's OpenAPI spec.
 * Dumps openapi.json via the app factory (no server needed), then
 * runs openapi-typescript. The spec file itself is gitignored —
 * this script is the single entry point so the two never drift.
 */
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

execFileSync(
  "python",
  [
    "-c",
    "import json; from app.main import create_app; "
      + "open('openapi.json', 'w').write(json.dumps(create_app().openapi()))",
  ],
  { cwd: join(root, "..", "backend"), stdio: "inherit" },
);

execFileSync(
  "npx",
  ["openapi-typescript", "../backend/openapi.json", "-o", "src/api/schema.d.ts"],
  { cwd: root, stdio: "inherit", shell: process.platform === "win32" },
);
