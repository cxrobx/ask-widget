// The changes that turn one version of a note into another, line by line (Myers' diff), for taking in a version written
// on disk while the editor is open. Each changed run of lines is its own change, so a cursor between two of them stays
// where it was; a single change from the first difference to the last would carry it off (an agent that edits two
// places at once did, in the browser suite).
import type { ChangeSpec } from "@codemirror/state";

// Past this much work the note is taken in as one change, as it was before this existed.
const MAX_LINES = 20_000, MAX_EDITS = 400;

// Lines with their newlines on, so offsets are plain sums and the last line needs no special case.
const split = (text: string) => text.match(/[^\n]*\n|[^\n]+$/g) ?? [];

export function lineChanges(now: string, next: string): ChangeSpec[] {
  if (now === next) return [];
  const a = split(now), b = split(next);
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length, endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
  const offA = offsets(a), offB = offsets(b);
  const whole = (): ChangeSpec[] => [{ from: offA[start], to: offA[endA], insert: next.slice(offB[start], offB[endB]) }];
  const n = endA - start, m = endB - start;
  if (!n || !m || n + m > MAX_LINES) return whole();

  // Forward pass, keeping each round's furthest-reaching x per diagonal for the walk back.
  const max = Math.min(n + m, MAX_EDITS), off = max + 1, v = new Int32Array(2 * max + 3), trace: Int32Array[] = [];
  let d = 0, done = false;
  for (; d <= max && !done; d++) {
    trace.push(v.slice());
    for (let k = -d; k <= d; k += 2) {
      let x = k === -d || (k !== d && v[off + k - 1] < v[off + k + 1]) ? v[off + k + 1] : v[off + k - 1] + 1;
      let y = x - k;
      while (x < n && y < m && a[start + x] === b[start + y]) { x++; y++; }
      v[off + k] = x;
      if (x >= n && y >= m) { done = true; break; }
    }
  }
  if (!done) return whole();

  // Walk back from the end, gathering the edits as runs: [from line, to line) of `a` becomes [from, to) of `b`.
  const runs: [number, number, number, number][] = [];
  let x = n, y = m;
  for (let step = d - 1; step > 0; step--) {
    const prev = trace[step], k = x - y;
    const down = k === -step || (k !== step && prev[off + k - 1] < prev[off + k + 1]);
    const pk = down ? k + 1 : k - 1, px = prev[off + pk], py = px - pk;
    while (x > px && y > py) { x--; y--; }
    const run: [number, number, number, number] = down ? [x, x, y - 1, y] : [x - 1, x, y, y];
    const last = runs[runs.length - 1];
    if (last && last[0] === run[1] && last[2] === run[3]) { last[0] = run[0]; last[2] = run[2]; }
    else runs.push(run);
    x = px; y = py;
  }
  return runs.reverse().map(([a0, a1, b0, b1]) => ({
    from: offA[start + a0], to: offA[start + a1], insert: next.slice(offB[start + b0], offB[start + b1]),
  }));
}

function offsets(lines: string[]): number[] {
  const out = [0];
  for (const line of lines) out.push(out[out.length - 1] + line.length);
  return out;
}
