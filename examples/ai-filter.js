// Node-RED function: "!ai filter" (2 outputs: 1 -> Ollama request, 2 -> direct reply)
const OLLAMA_URL = "http://10.10.0.36:11434/api/chat";
const MODEL = "gemma3:4b";
const TRIGGER = /^!ai\b\s*(.*)$/is;
const USER_COOLDOWN_S = 30;   // per sender
const BUSY_TIMEOUT_S = 120;   // one question at a time; stale lock expires
const SYSTEM_PROMPT =
    "You are TaptonBot, a friendly assistant on the Tapton channel of a MeshCore LoRa mesh radio network. " +
    "Answer in ONE short sentence, under 120 characters. Plain text only: no markdown, no emoji, no lists. " +
    "If you are not sure, say so briefly.";

const p = (typeof msg.payload === "string") ? JSON.parse(msg.payload) : (msg.payload || {});
const m = (p.text || "").trim().match(TRIGGER);
if (!m) return null;

const sender = p.sender || "someone";
const question = m[1].trim();
if (!question) {
    return [null, { payload: "Ask me something: !ai <question>" }];
}

const now = Date.now() / 1000;
if (now - (flow.get("ai_busy") || 0) < BUSY_TIMEOUT_S) {
    node.status({ fill: "yellow", shape: "ring", text: `busy, ignored ${sender}` });
    return null;
}
const last = flow.get("ai_last") || {};
if (now - (last[sender] || 0) < USER_COOLDOWN_S) {
    node.status({ fill: "yellow", shape: "ring", text: `cooldown: ${sender}` });
    return null;
}
last[sender] = now;
flow.set("ai_last", last);
flow.set("ai_busy", now);

const history = flow.get("ai_history") || [];
msg.sender = sender;
msg.question = question;
msg.url = OLLAMA_URL;
msg.method = "POST";
msg.headers = { "content-type": "application/json" };
msg.payload = {
    model: MODEL,
    stream: false,
    keep_alive: "10m",
    options: { num_predict: 60, temperature: 0.6 },
    messages: [
        { role: "system", content: SYSTEM_PROMPT },
        ...history,
        { role: "user", content: `${sender}: ${question}` },
    ],
};
node.status({ fill: "blue", shape: "dot", text: `thinking for ${sender}` });
return [msg, null];
