// HTTP and server-sent-event access. The only module that calls fetch.

export async function get(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
  return r.json();
}

export async function post(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
  return r.json();
}

// Iterate a text/event-stream response as {event, data} objects.
//
// Frames are split on a blank line, and a partial frame is held in the
// buffer until the rest of it arrives: a chunk boundary can land in the
// middle of one, and parsing eagerly would drop it. A frame whose data
// is not valid JSON is skipped rather than thrown, so one malformed
// event cannot end a run that is otherwise streaming fine.
export async function* sse(resp) {
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const part of parts) {
      if (!part.trim()) continue;
      let event = "message";
      let data = "";
      for (const line of part.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data = line.slice(5).trim();
      }
      if (!data) continue;
      try {
        yield { event, data: JSON.parse(data) };
      } catch {}
    }
  }
}
