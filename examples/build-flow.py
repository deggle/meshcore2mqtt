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


def js(name):
    return (HERE / name).read_text(encoding="utf-8").split("\n", 1)[1]  # drop header comment


func_common = {"timeout": 0, "noerr": 0, "initialize": "", "finalize": "", "libs": []}

nodes = [
    {"id": G, "type": "group", "z": Z, "name": "Meshcore Local AI", "style": {"label": True},
     "nodes": ["71f6c0a5f71f2485", "a31efbb9a0b66f87", "d882334f0492f174", "0ca0d65d5714ecf1",
               "b7c1e2d3f4a50601", "b7c1e2d3f4a50602", "b7c1e2d3f4a50603", "b7c1e2d3f4a50604"],
     "x": 94, "y": 339, "w": 952, "h": 182},
    {"id": "71f6c0a5f71f2485", "type": "mqtt in", "z": Z, "g": G, "name": "Tapton rx",
     "topic": f"{PREFIX}/rx/channel/{CHANNEL}", "qos": "1", "datatype": "json", "broker": BROKER,
     "nl": False, "rap": True, "rh": 0, "inputs": 0, "x": 210, "y": 400,
     "wires": [["a31efbb9a0b66f87"]]},
    {"id": "a31efbb9a0b66f87", "type": "function", "z": Z, "g": G, "name": "!ai filter",
     "func": js("ai-filter.js"), "outputs": 2, **func_common, "x": 400, "y": 400,
     "wires": [["d882334f0492f174"], ["0ca0d65d5714ecf1"]]},
    {"id": "d882334f0492f174", "type": "http request", "z": Z, "g": G, "name": "Ollama",
     "method": "use", "ret": "obj", "paytoqs": "ignore", "url": "", "tls": "", "persist": False,
     "proxy": "", "insecureHTTPParser": False, "authType": "", "senderr": False, "headers": [],
     "x": 570, "y": 400, "wires": [["b7c1e2d3f4a50601"]]},
    {"id": "b7c1e2d3f4a50601", "type": "function", "z": Z, "g": G, "name": "format reply",
     "func": js("ai-format.js"), "outputs": 1, **func_common, "x": 740, "y": 400,
     "wires": [["0ca0d65d5714ecf1", "b7c1e2d3f4a50604"]]},
    {"id": "0ca0d65d5714ecf1", "type": "mqtt out", "z": Z, "g": G, "name": "Tapton tx",
     "topic": f"{PREFIX}/tx/channel/{CHANNEL}", "qos": "1", "retain": "false", "respTopic": "",
     "contentType": "", "userProps": "", "correl": "", "expiry": "", "broker": BROKER,
     "x": 950, "y": 400, "wires": []},
    {"id": "b7c1e2d3f4a50602", "type": "catch", "z": Z, "g": G, "name": "Ollama errors",
     "scope": ["d882334f0492f174"], "uncaught": False, "x": 570, "y": 480,
     "wires": [["b7c1e2d3f4a50603"]]},
    {"id": "b7c1e2d3f4a50603", "type": "function", "z": Z, "g": G, "name": "unlock",
     "func": 'flow.set("ai_busy", 0);\n'
             'node.status({ fill: "red", shape: "ring", text: msg.error && msg.error.message });\n'
             'return null;\n',
     "outputs": 1, **func_common, "x": 740, "y": 480, "wires": [[]]},
    {"id": "b7c1e2d3f4a50604", "type": "debug", "z": Z, "g": G, "name": "reply", "active": True,
     "tosidebar": True, "console": False, "tostatus": False, "complete": "payload",
     "targetType": "msg", "statusVal": "", "statusType": "auto", "x": 930, "y": 460, "wires": []},
]

out = HERE / "node-red-ai-chatbot.json"
out.write_text(json.dumps(nodes, indent=2), encoding="utf-8")
print(f"wrote {out} ({len(nodes)} nodes)")
