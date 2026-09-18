// Incremental parser for the run event stream (`GET /api/v1/runs/{id}/stream`, text/event-stream).
// The stream is read with fetch + an Authorization header, never EventSource (ADR-010), so the
// framing is parsed here. Chunks may split anywhere, including inside a line or a CRLF.

export interface SseMessage {
  event: string;
  data: string;
  id?: string;
}

export class SseParser {
  private buf = "";
  private pendingCR = false;

  push(chunk: string): SseMessage[] {
    let text = this.pendingCR ? "\r" + chunk : chunk;
    this.pendingCR = text.endsWith("\r");
    if (this.pendingCR) text = text.slice(0, -1);
    this.buf += text.replace(/\r\n?/g, "\n");
    const out: SseMessage[] = [];
    let i: number;
    while ((i = this.buf.indexOf("\n\n")) >= 0) {
      const block = this.buf.slice(0, i);
      this.buf = this.buf.slice(i + 2);
      const msg = parseBlock(block);
      if (msg) out.push(msg);
    }
    return out;
  }
}

function parseBlock(block: string): SseMessage | undefined {
  let event = "message";
  let id: string | undefined;
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue; // comment / keep-alive
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
    else if (field === "id") id = value;
  }
  return data.length ? { event, data: data.join("\n"), id } : undefined;
}
