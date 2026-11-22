// Node-RED function: "format reply" - clean up and fit into one mesh message
const MAX_BYTES = 135;
const HISTORY_MESSAGES = 8; // 4 question/answer pairs kept as context

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

if (Buffer.byteLength(reply) > MAX_BYTES) {
    let out = "";
    for (const word of reply.split(" ")) {
        const candidate = out ? `${out} ${word}` : word;
        if (Buffer.byteLength(candidate + "…") > MAX_BYTES) break;
        out = candidate;
    }
    if (!out) out = Buffer.from(reply).subarray(0, MAX_BYTES - 3).toString().replace(/�$/, "");
    reply = out.replace(/[\s,;:]+$/, "") + "…";
}

const history = flow.get("ai_history") || [];
history.push({ role: "user", content: `${msg.sender}: ${msg.question}` });
history.push({ role: "assistant", content: reply });
flow.set("ai_history", history.slice(-HISTORY_MESSAGES));

node.status({ fill: "green", shape: "dot", text: `replied to ${msg.sender}` });
return { payload: reply };
