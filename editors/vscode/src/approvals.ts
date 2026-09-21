// Approvals (AC-19.4). Pending approvals are polled; each is offered to the human once:
//   gate     → a notification with Approve / Reject
//   question → a notification with Answer… (input box) / Reject
//   exec     → a notification with Review…. A poll never opens the modal: it takes keyboard focus
//              and its default button allows execution, so an Enter typed into another window
//              approved two runs on 2026-09-19. Review…, the run view's Decide… and "Review pending
//              approvals" open a MODAL warning whose detail is the engine's prompt, which lists every check as
//              `name: exact command` from the org file. Only an explicit "Allow execution" click
//              approves; closing the dialog decides nothing (the run keeps waiting); "Reject"
//              rejects.
// A dismissed approval is not re-shown on the next poll (that would nag every 5 s); it stays
// pending and is reachable from "Cadre: Review pending approvals", the Runs tree and the run view.
import * as vscode from "vscode";
import { ApiError, type Approval, type CadreClient } from "./api";
import { offerStyle } from "./format";
import { inert, notificationText } from "./text";

export class Approvals implements vscode.Disposable {
  private offered = new Set<string>();
  private pending: Approval[] = [];
  private modalChain: Promise<unknown> = Promise.resolve();
  private readonly changed = new vscode.EventEmitter<number>();
  /** Fires with the pending count whenever it changes. */
  readonly onDidChange = this.changed.event;

  constructor(
    private readonly client: CadreClient,
    private readonly openRun: (runId: string) => void,
    private readonly out: vscode.OutputChannel,
  ) {}

  get count(): number {
    return this.pending.length;
  }

  dispose(): void {
    this.changed.dispose();
  }

  /**
   * Called by the poller with the latest `GET /api/v1/approvals?pending=true`. New approvals are
   * offered unless `offerNew` is false (the caller is about to offer one itself).
   */
  update(items: Approval[], offerNew = true): void {
    const before = this.pending.map((a) => a.id).join(",");
    this.pending = items;
    const ids = new Set(items.map((a) => a.id));
    for (const id of [...this.offered]) if (!ids.has(id)) this.offered.delete(id);
    for (const a of items) {
      if (this.offered.has(a.id)) continue;
      this.offered.add(a.id);
      if (offerNew) void this.offer(a);
    }
    if (items.map((a) => a.id).join(",") !== before) this.changed.fire(items.length);
  }

  clear(): void {
    if (this.pending.length) {
      this.pending = [];
      this.changed.fire(0);
    }
  }

  /** "Cadre: Review pending approvals": pick one (or the only one) and offer it again. */
  async review(runId?: string): Promise<void> {
    let items: Approval[];
    try {
      items = await this.client.approvals();
    } catch (e) {
      void vscode.window.showErrorMessage(`Cadre: ${(e as Error).message}`);
      return;
    }
    this.update(items, false);
    if (runId) items = items.filter((a) => a.run_id === runId);
    if (!items.length) {
      void vscode.window.showInformationMessage("Cadre: nothing is waiting for you.");
      return;
    }
    const chosen = items.length === 1 ? items[0] : (await vscode.window.showQuickPick(
      items.map((a) => ({
        label: `${a.kind === "exec" ? "$(terminal) run code" : a.kind === "question" ? "$(question) question"
          : a.kind === "memory" ? "$(book) memory" : "$(pass) gate"}`,
        description: `run ${a.run_id}${a.agent ? ` · ${a.agent}` : ""}`,
        detail: notificationText(a.prompt, 200),
        approval: a,
      })),
      { placeHolder: "Pending approvals" }))?.approval;
    if (chosen) await this.offer(chosen, true);
  }

  /** Offer one approval by id (the run view's button). */
  async offerById(id: string): Promise<void> {
    let items: Approval[];
    try {
      items = await this.client.approvals();
    } catch (e) {
      void vscode.window.showErrorMessage(`Cadre: ${(e as Error).message}`);
      return;
    }
    const a = items.find((x) => x.id === id);
    if (!a) {
      void vscode.window.showInformationMessage("Cadre: that approval has already been decided.");
      return;
    }
    await this.offer(a, true);
  }

  /** `userAsked`: the person picked this approval (Review…, Decide…), rather than a poll finding it. */
  private offer(a: Approval, userAsked = false): Promise<void> {
    if (a.kind === "exec" && offerStyle(a.kind, userAsked) === "notice") return this.noticeExec(a);
    if (a.kind === "exec") {
      // One modal at a time, in arrival order.
      const next = this.modalChain.then(() => this.offerExec(a));
      this.modalChain = next.catch(() => undefined);
      return next;
    }
    return a.kind === "question" ? this.offerQuestion(a) : this.offerGate(a);
  }

  private async offerExec(a: Approval): Promise<void> {
    const pick = await vscode.window.showWarningMessage(
      `Cadre run ${a.run_id} asks to execute code on this machine.`,
      {
        modal: true,
        detail: `${inert(a.prompt)}\n\nThe commands run as you, with your permissions. This is not a sandbox. `
          + "Closing this dialog leaves the run waiting.",
      },
      "Allow execution", "Reject");
    if (pick === "Allow execution") await this.decide(a, true, "");
    else if (pick === "Reject") await this.decide(a, false, "");
  }

  private async noticeExec(a: Approval): Promise<void> {
    const pick = await vscode.window.showWarningMessage(
      `Cadre run ${a.run_id} asks to execute code on this machine.`, "Review…", "Open run");
    if (pick === "Review…") await this.offer(a, true);
    else if (pick === "Open run") this.openRun(a.run_id);
  }

  private async offerGate(a: Approval): Promise<void> {
    const who = a.agent ? ` · ${a.agent}` : "";
    const what = a.kind === "memory"
      ? "remember this fact for later runs? (it is replayed to builders and managers as data)"
      : "approval needed";
    const pick = await vscode.window.showInformationMessage(
      `Cadre run ${a.run_id}${who} — ${what}: ${notificationText(a.prompt)}`,
      "Approve", "Reject", "Open run");
    if (pick === "Approve") await this.decide(a, true, "");
    else if (pick === "Reject") await this.decide(a, false, "");
    else if (pick === "Open run") this.openRun(a.run_id);
  }

  private async offerQuestion(a: Approval): Promise<void> {
    const who = a.agent ?? "an agent";
    const pick = await vscode.window.showInformationMessage(
      `Cadre run ${a.run_id} — ${who} asks: ${notificationText(a.prompt)}`,
      "Answer…", "Reject", "Open run");
    if (pick === "Answer…") {
      const answer = await vscode.window.showInputBox({
        title: `Answer ${who} (run ${a.run_id})`,
        prompt: notificationText(a.prompt, 300),
        ignoreFocusOut: true,
        validateInput: (v) => (v.trim() ? undefined : "Type an answer, or press Escape to leave it waiting."),
      });
      if (answer !== undefined) await this.decide(a, true, answer.trim());
    } else if (pick === "Reject") {
      await this.decide(a, false, "");
    } else if (pick === "Open run") {
      this.openRun(a.run_id);
    }
  }

  private async decide(a: Approval, approve: boolean, answer: string): Promise<void> {
    try {
      await this.client.decide(a.id, approve, answer);
      this.out.appendLine(`[approval] ${a.kind} ${a.id} on run ${a.run_id}: ${approve ? "approved" : "rejected"}`);
      this.pending = this.pending.filter((x) => x.id !== a.id);
      this.changed.fire(this.pending.length);
    } catch (e) {
      const msg = e instanceof ApiError && e.status === 409 ? "it was already decided elsewhere" : (e as Error).message;
      void vscode.window.showWarningMessage(`Cadre: could not record the decision — ${msg}.`);
    }
  }
}
