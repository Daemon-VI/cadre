// "Cadre: Review branch" (AC-19.3). A project-mode run commits its work to `cadre/<run-id>` in a
// worktree of the project (ADR-016). This lists the files changed between the run's base commit
// and that branch (`git diff base...branch`) and opens each as a diff. Both sides are served
// read-only by a `cadre-git:` content provider backed by `git cat-file`, so nothing is checked
// out and the owner's working tree is untouched.
import * as fs from "node:fs";
import * as vscode from "vscode";
import type { CadreClient, RunDetail } from "./api";
import { changedFiles, fileAt, isSafeRef } from "./git";

export const SCHEME = "cadre-git";

interface Side {
  root: string;
  ref: string;
}

function sideUri(root: string, ref: string, relPath: string): vscode.Uri {
  return vscode.Uri.from({ scheme: SCHEME, path: "/" + relPath, query: JSON.stringify({ root, ref } satisfies Side) });
}

export class GitContent implements vscode.TextDocumentContentProvider {
  async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    let side: Side;
    try {
      side = JSON.parse(uri.query) as Side;
    } catch {
      return "";
    }
    if (typeof side?.root !== "string" || typeof side?.ref !== "string" || !isSafeRef(side.ref)) return "";
    return (await fileAt(side.root, side.ref, uri.path.replace(/^\//, ""))) ?? "";
  }
}

const STATUS_WORD: Record<string, string> = { A: "added", M: "modified", D: "deleted", T: "type changed" };

export async function reviewBranch(client: CadreClient, runId: string | undefined): Promise<void> {
  if (!vscode.workspace.isTrusted) {
    void vscode.window.showWarningMessage("Cadre: Review branch runs git, so it needs a trusted workspace.");
    return;
  }
  const id = runId ?? (await pickProjectRun(client));
  if (!id) return;
  let run: RunDetail;
  try {
    run = await client.run(id);
  } catch (e) {
    void vscode.window.showErrorMessage(`Cadre: ${(e as Error).message}`);
    return;
  }
  const root = run.project_path;
  const base = run.base;
  const branch = run.branch;
  if (!root || !base || !branch) {
    void vscode.window.showInformationMessage(
      `Cadre: run ${id} did not run in project mode, so it has no branch to review. Its files are in the run view.`);
    return;
  }
  if (!fs.existsSync(root)) {
    void vscode.window.showErrorMessage(`Cadre: the run's project folder ${root} is not on this machine.`);
    return;
  }
  let files;
  try {
    files = await changedFiles(root, base, branch);
  } catch (e) {
    void vscode.window.showErrorMessage(`Cadre: git could not diff ${base.slice(0, 10)}...${branch} — ${(e as Error).message}`);
    return;
  }
  if (!files.length) {
    void vscode.window.showInformationMessage(`Cadre: ${branch} has no changes against its base yet.`);
    return;
  }
  const shortBase = base.slice(0, 10);
  const picked = await vscode.window.showQuickPick(
    files.map((f) => ({
      label: f.path,
      description: STATUS_WORD[f.status] ?? f.status,
      file: f,
    })),
    { title: `${branch} vs ${shortBase} — ${files.length} file${files.length === 1 ? "" : "s"} changed`,
      placeHolder: "Pick files to open as diffs", canPickMany: true, matchOnDescription: true });
  if (!picked?.length) return;
  for (const p of picked) {
    const left = sideUri(root, base, p.file.path);
    const right = sideUri(root, branch, p.file.path);
    await vscode.commands.executeCommand("vscode.diff", left, right,
      `${p.file.path} (${shortBase} ↔ ${branch})`, { preview: picked.length === 1 });
  }
}

async function pickProjectRun(client: CadreClient): Promise<string | undefined> {
  // `GET /runs` rows do not say which runs used project mode, so read the recent ones' details.
  let details: RunDetail[];
  try {
    const rows = await client.runs(30);
    details = (await Promise.all(rows.map((r) => client.run(r.id).catch(() => undefined))))
      .filter((d): d is RunDetail => !!d && !!d.project_path && !!d.branch);
  } catch (e) {
    void vscode.window.showErrorMessage(`Cadre: ${(e as Error).message}`);
    return undefined;
  }
  if (!details.length) {
    void vscode.window.showInformationMessage("Cadre: none of the recent runs used project mode. Start one with “Cadre: Start run on this folder”.");
    return undefined;
  }
  const folders = (vscode.workspace.workspaceFolders ?? []).map((f) => f.uri.fsPath.toLowerCase());
  details.sort((a, b) => Number(folders.includes((b.project_path ?? "").toLowerCase()))
    - Number(folders.includes((a.project_path ?? "").toLowerCase())));
  const pick = await vscode.window.showQuickPick(
    details.map((d) => ({ label: d.goal, description: `${d.branch} · ${d.status}`, detail: d.project_path ?? "", id: d.id })),
    { placeHolder: "Which run's branch?", matchOnDescription: true, matchOnDetail: true });
  return pick?.id;
}
