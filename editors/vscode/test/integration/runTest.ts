// Launches a real VS Code (Electron) with this extension under development and runs the suite.
// CI only (`xvfb-run -a npm run test:integration` on ubuntu): it downloads VS Code and needs more
// memory than the development laptop has to spare.
import * as path from "node:path";
import { runTests } from "@vscode/test-electron";

async function main(): Promise<void> {
  const extensionDevelopmentPath = path.resolve(__dirname, "..", "..", "..");
  const extensionTestsPath = path.resolve(__dirname, "suite", "index");
  await runTests({
    extensionDevelopmentPath,
    extensionTestsPath,
    launchArgs: ["--disable-extensions", "--disable-workspace-trust", "--skip-welcome", "--skip-release-notes"],
  });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
