// Launches a real VS Code (Electron) with this extension under development and runs the suite.
// CI: `xvfb-run -a npm run test:integration` on ubuntu downloads VS Code. Locally, set
// CADRE_VSCODE_EXE to an installed Code executable to reuse it instead of downloading one; a
// throwaway profile and extensions folder keep the owner's own VS Code state out of the test.
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { runTests } from "@vscode/test-electron";

async function main(): Promise<void> {
  const extensionDevelopmentPath = path.resolve(__dirname, "..", "..", "..");
  const extensionTestsPath = path.resolve(__dirname, "suite", "index");
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "cadre-vscode-test-"));
  try {
    await runTests({
      extensionDevelopmentPath,
      extensionTestsPath,
      vscodeExecutablePath: process.env.CADRE_VSCODE_EXE || undefined,
      launchArgs: [
        "--disable-extensions", "--disable-workspace-trust", "--skip-welcome", "--skip-release-notes",
        `--user-data-dir=${path.join(scratch, "user")}`, `--extensions-dir=${path.join(scratch, "ext")}`,
      ],
    });
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
