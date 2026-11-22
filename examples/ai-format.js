// Node-RED function: "format reply" - clean up, fit to size, save history
const MAX_BYTES = { channel: 135, dm: 270 };  // dm = 2 mesh messages (bridge splits)
const HISTORY_MESSAGES = 8;                   // 4 question/answer pairs per conversation

flow.set("ai_busy", 0);

let reply = (msg.payload && msg.payload.message && msg.payload.message.content) || "";
reply = reply
    .replace(/<think>[\s\S]*?<\/think>/g, "")
    .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/gu, "")
    .replace(/[*_`#>]/g, "")
    .replace(/\s+/g, " ")
    .trim();

if ((msg.statusCode && msg.statusCode !== 200) || !reply) {
    node.status({ fill: "red", shape: "ring", text: `no reply (HTTP ${msg.statusCode})` });
    return null;
}

const max = MAX_BYTES[msg.source] || MAX_BYTES.channel;
if (Buffer.byteLength(reply) > max) {
    let out = "";
    for (const word of reply.split(" ")) {
        const candidate = out ? `${out} ${word}` : word;
        if (Buffer.byteLength(candidate + "…") > max) break;
        out = candidate;
    }
    if (!out) out = Buffer.from(reply).subarray(0, max - 3).toString().replace(/�$/, "");
    reply = out.replace(/[\s,;:]+$/, "") + "…";
}

const histories = flow.get("ai_history") || {};
const h = histories[msg.historyKey] || { messages: [] };
h.messages.push({ role: "user", content: msg.source === "dm" ? msg.question : `${msg.sender}: ${msg.question}` });
h.messages.push({ role: "assistant", content: reply });
h.messages = h.messages.slice(-HISTORY_MESSAGES);
h.updated = Date.now() / 1000;
histories[msg.historyKey] = h;
flow.set("ai_history", histories);

node.status({ fill: "green", shape: "dot", text: `replied to ${msg.sender} (${msg.source})` });
return { topic: msg.reply_topic, payload: reply };
