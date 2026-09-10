// Shared client state and the element handles every module reaches for.
//
// The DOM lookups run at module load. Module scripts are deferred, so
// the document is fully parsed before this executes, which is the same
// timing the inline script had at the end of <body>.

export const STORE_KEY = "pearl-convs-v3";
export const MAX_CONVS = 60;
export const POLL_MS = 9000;

export const S = {
  mode: "agent",
  streaming: false,
  id: null,
  convs: [],
  ws: { path: "", name: "" },
  ctrl: null,
};

export const g = (id) => document.getElementById(id);
export const mk = (t, c) => {
  const e = document.createElement(t);
  if (c) e.className = c;
  return e;
};

export const msgInner = g("msg-inner");
export const emptySt = g("empty-state");
export const sidebar = g("sidebar");
export const sbList = g("sb-list");
export const sbSearch = g("sb-search");
export const inputTxt = g("input-txt");
export const sendBtn = g("send-btn");
export const stopBtn = g("stop-btn");
export const connDot = g("conn-dot");
export const connLbl = g("conn-label");
export const wsName = g("ws-name");
export const chatTtl = g("chat-title");
