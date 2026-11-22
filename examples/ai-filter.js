// Node-RED function: "!ai filter" (2 outputs: 1 -> Ollama request, 2 -> direct reply)
// Handles channel messages (need "!ai") and DMs (no trigger needed).
const PREFIX = "meshcore";
const OLLAMA_URL = "http://10.10.0.36:11434/api/chat";
const MODEL = "gemma3:4b";
const TRIGGER = /^!ai\b\s*(.*)$/is;
const COOLDOWN_S = { channel: 30, dm: 10 };  // per sender
const BUSY_TIMEOUT_S = 120;                   // one question at a time; stale lock expires
const HISTORY_IDLE_S = 30 * 60;               // forget a conversation after 30 min quiet
const SYSTEM_PROMPT = {
    channel:
        "You are TaptonBot, a friendly assistant on the Tapton channel of a MeshCore LoRa mesh radio network. " +
        "Answer in ONE short sentence, under 120 characters. Plain text only: no markdown, no emoji, no lists. " +
        "If you are not sure, say so briefly.",
    dm:
        "You are TaptonBot, a friendly assistant reached by direct message over a MeshCore LoRa mesh radio network. " +
        "Answer in at most two short sentences, under 240 characters. Plain text only: no markdown, no emoji, no lists. " +
        "If you are not sure, say so briefly.",
};

const p = (typeof msg.payload === "string") ? JSON.parse(msg.payload) : (msg.payload || {});
const source = String(msg.topic || "").includes("/rx/dm/") ? "dm" : "channel";
const text = (p.text || "").trim();

let question;
const m = text.match(TRIGGER);
if (source === "channel") {
    if (!m) return null;
    question = m[1].trim();
} else {
    question = (m ? m[1] : text).trim();
}

const senderKey = source === "dm" ? p.pubkey_prefix : (p.sender || "someone");
const senderName = p.sender || (source === "dm" ? p.pubkey_prefix : "someone");
const replyTopic = source === "dm"
    ? `${PREFIX}/tx/dm/${p.pubkey_prefix}`
    : `${PREFIX}/tx/channel/${p.channel_slug || p.channel_idx}`;
const historyKey = source === "dm" ? `dm:${p.pubkey_prefix}` : `ch:${p.channel_slug || p.channel_idx}`;
const reply = (t) => [null, { topic: replyTopic, payload: t }];

if (!senderKey) return null;
if (!question) {
    return source === "dm" ? null : reply("Ask me something: !ai <question>  (!ai reset clears my memory)");
}

const histories = flow.get("ai_history") || {};
if (/^reset$/i.test(question)) {
    delete histories[historyKey];
    flow.set("ai_history", histories);
    node.status({ fill: "grey", shape: "ring", text: `history reset by ${senderName}` });
    return reply("OK, I've forgotten our conversation.");
}

const now = Date.now() / 1000;
if (now - (flow.get("ai_busy") || 0) < BUSY_TIMEOUT_S) {
    node.status({ fill: "yellow", shape: "ring", text: `busy, ignored ${senderName}` });
    return source === "dm" ? reply("Busy answering someone else, try again in a minute.") : null;
}
const last = flow.get("ai_last") || {};
const cooldownKey = `${source}:${senderKey}`;
if (now - (last[cooldownKey] || 0) < COOLDOWN_S[source]) {
    node.status({ fill: "yellow", shape: "ring", text: `cooldown: ${senderName}` });
    return null;
}
last[cooldownKey] = now;
flow.set("ai_last", last);
flow.set("ai_busy", now);

let h = histories[historyKey];
if (!h || now - h.updated > HISTORY_IDLE_S) h = { updated: now, messages: [] };

msg.source = source;
msg.sender = senderName;
msg.question = question;
msg.historyKey = historyKey;
msg.reply_topic = replyTopic;
msg.url = OLLAMA_URL;
msg.method = "POST";
msg.headers = { "content-type": "application/json" };
msg.payload = {
    model: MODEL,
    stream: false,
    keep_alive: "10m",
    options: { num_predict: source === "dm" ? 110 : 60, temperature: 0.6 },
    messages: [
        { role: "system", content: SYSTEM_PROMPT[source] },
        ...h.messages,
        { role: "user", content: source === "dm" ? question : `${senderName}: ${question}` },
    ],
};
node.status({ fill: "blue", shape: "dot", text: `thinking for ${senderName} (${source})` });
return [msg, null];
