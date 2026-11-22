"""Builds node-red-ai-chatbot.json from the function .js files next to it.

Reuses the node IDs from the hand-made "Meshcore Local AI" group so that
importing offers Replace. The "TC MQTT" broker config node (29ce661a.a836aa)
is referenced but deliberately NOT included, so its credentials stay intact.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
Z = "e8690753eb651967"
G = "421b3bc7ca6833a7"
BROKER = "29ce661a.a836aa"
CHANNEL = "tapton"  # channel slug (see README)
PREFIX = "meshcore"

IDS = {
    "rx_chan": "71f6c0a5f71f2485", "filter": "a31efbb9a0b66f87", "ollama": "d882334f0492f174",
    "tx": "0ca0d65d5714ecf1", "format": "b7c1e2d3f4a50601", "catch": "b7c1e2d3f4a50602",
    "unlock": "b7c1e2d3f4a50603", "debug": "b7c1e2d3f4a50604", "rx_dm": "b7c1e2d3f4a50605",
}


def js(name):
    return (HERE / name).read_text(encoding="utf-8").split("\n", 1)[1]  # drop header comment


func_common = {"timeout": 0, "noerr": 0, "initialize": "", "finalize": "", "libs": []}
mqtt_in_common = {"qos": "1", "datatype": "json", "broker": BROKER, "nl": False, "rap": True,
                  "rh": 0, "inputs": 0}

nodes = [
    {"id": G, "type": "group", "z": Z, "name": "Meshcore Local AI", "style": {"label": True},
     "nodes": list(IDS.values()), "x": 94, "y": 339, "w": 952, "h": 222},
    {"id": IDS["rx_chan"], "type": "mqtt in", "z": Z, "g": G, "name": "Tapton rx",
     "topic": f"{PREFIX}/rx/channel/{CHANNEL}", **mqtt_in_common, "x": 210, "y": 380,
     "wires": [[IDS["filter"]]]},
    {"id": IDS["rx_dm"], "type": "mqtt in", "z": Z, "g": G, "name": "DMs rx",
     "topic": f"{PREFIX}/rx/dm/+", **mqtt_in_common, "x": 210, "y": 440,
     "wires": [[IDS["filter"]]]},
    {"id": IDS["filter"], "type": "function", "z": Z, "g": G, "name": "!ai filter",
     "func": js("ai-filter.js"), "outputs": 2, **func_common, "x": 400, "y": 400,
     "wires": [[IDS["ollama"]], [IDS["tx"]]]},
    {"id": IDS["ollama"], "type": "http request", "z": Z, "g": G, "name": "Ollama",
     "method": "use", "ret": "obj", "paytoqs": "ignore", "url": "", "tls": "", "persist": False,
     "proxy": "", "insecureHTTPParser": False, "authType": "", "senderr": False, "headers": [],
     "x": 570, "y": 400, "wires": [[IDS["format"]]]},
    {"id": IDS["format"], "type": "function", "z": Z, "g": G, "name": "format reply",
     "func": js("ai-format.js"), "outputs": 1, **func_common, "x": 740, "y": 400,
     "wires": [[IDS["tx"], IDS["debug"]]]},
    # blank topic: uses msg.topic (tx/channel/<slug> or tx/dm/<pubkey>) set by the functions
    {"id": IDS["tx"], "type": "mqtt out", "z": Z, "g": G, "name": "reply tx (msg.topic)",
     "topic": "", "qos": "1", "retain": "false", "respTopic": "",
     "contentType": "", "userProps": "", "correl": "", "expiry": "", "broker": BROKER,
     "x": 950, "y": 400, "wires": []},
    {"id": IDS["catch"], "type": "catch", "z": Z, "g": G, "name": "Ollama errors",
     "scope": [IDS["ollama"]], "uncaught": False, "x": 570, "y": 520,
     "wires": [[IDS["unlock"]]]},
    {"id": IDS["unlock"], "type": "function", "z": Z, "g": G, "name": "unlock",
     "func": 'flow.set("ai_busy", 0);\n'
             'node.status({ fill: "red", shape: "ring", text: msg.error && msg.error.message });\n'
             'return null;\n',
     "outputs": 1, **func_common, "x": 740, "y": 520, "wires": [[]]},
    {"id": IDS["debug"], "type": "debug", "z": Z, "g": G, "name": "reply", "active": True,
     "tosidebar": True, "console": False, "tostatus": False, "complete": "true",
     "targetType": "full", "statusVal": "", "statusType": "auto", "x": 930, "y": 460, "wires": []},
]

out = HERE / "node-red-ai-chatbot.json"
out.write_text(json.dumps(nodes, indent=2), encoding="utf-8")
print(f"wrote {out} ({len(nodes)} nodes)")
