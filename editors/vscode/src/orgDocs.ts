// Read-only views of an organisation's YAML, for the Organisations panel's "View YAML". Built-in
// templates open through this provider, so they cannot be edited by accident; your own
// organisations open as the real file under CADRE_HOME/orgs instead (panels.ts).
import * as vscode from "vscode";
import type { CadreClient } from "./api";

export const ORG_SCHEME = "cadre-org";
const ORG_NAME = /^[a-z][a-z0-9_-]{0,31}$/;

export function orgUri(name: string): vscode.Uri {
  return vscode.Uri.from({ scheme: ORG_SCHEME, path: `/${name}.yaml` });
}

export class OrgContent implements vscode.TextDocumentContentProvider {
  constructor(private readonly client: CadreClient) {}

  async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    const name = uri.path.replace(/^\//, "").replace(/\.yaml$/, "");
    if (!ORG_NAME.test(name)) return "";
    try {
      return (await this.client.org(name)).yaml;
    } catch (e) {
      return `# Could not load the organisation "${name}": ${(e as Error).message}\n`;
    }
  }
}
