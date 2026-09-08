/**
 * Server-sent-event splitting and parsing, with no Obsidian dependency so the
 * unit tests can load this file directly under `node --test`.
 *
 * The Ask Widget service emits `event: <name>` + `data: <json>` pairs separated
 * by a blank line, plus periodic comment heartbeats. Frames arrive split across
 * arbitrary chunk boundaries, so the splitter buffers until it sees a blank line.
 */

export interface SseFrame {
  event: string;
  data: Record<string, unknown>;
  raw: string;
}

/** Parse one already-delimited frame. Malformed JSON yields an empty payload. */
export function parseFrame(raw: string): SseFrame | null {
  let event = "";
  const dataLines: string[] = [];
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith(":")) continue; // heartbeat / comment
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
  }
  if (!event && dataLines.length === 0) return null;
  let data: Record<string, unknown> = {};
  if (dataLines.length > 0) {
    try {
      const parsed: unknown = JSON.parse(dataLines.join("\n"));
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        data = parsed as Record<string, unknown>;
      }
    } catch {
      data = {};
    }
  }
  return { event: event || "message", data, raw };
}

export class SseSplitter {
  private buffer = "";

  /** Feed a decoded chunk; returns every complete frame it now contains. */
  push(chunk: string): SseFrame[] {
    this.buffer += chunk;
    const frames: SseFrame[] = [];
    let match = /\r?\n\r?\n/.exec(this.buffer);
    while (match) {
      const raw = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const frame = parseFrame(raw);
      if (frame) frames.push(frame);
      match = /\r?\n\r?\n/.exec(this.buffer);
    }
    return frames;
  }

  /** Flush a trailing frame that the stream ended without a blank line after. */
  flush(): SseFrame[] {
    const rest = this.buffer.trim();
    this.buffer = "";
    if (!rest) return [];
    const frame = parseFrame(rest);
    return frame ? [frame] : [];
  }
}

/** First-line verdict for a Prove-it answer; tolerant of markdown decoration. */
export function parseVerdict(answer: string): string | null {
  const first = answer
    .split("\n")
    .map((line) => line.trim())
    .find((line) => line.length > 0);
  if (!first) return null;
  const cleaned = first
    .replace(/^#+\s*/, "")
    .replace(/\*\*/g, "")
    .replace(/^_+|_+$/g, "")
    .replace(/^verdict\s*[:\-—]\s*/i, "")
    .trim();
  const match = /^(supported|partially supported|not supported|no evidence found)\b/i.exec(cleaned);
  if (!match) return null;
  const verdict = match[1].toLowerCase();
  return verdict.charAt(0).toUpperCase() + verdict.slice(1);
}
