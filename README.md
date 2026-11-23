# meshcore2mqtt

Bridges a MeshCore companion node running the WiFi firmware (such as a Heltec V4) to MQTT, so Node-RED and other clients can read and send mesh messages.

```
LoRa mesh ⇄ Heltec V4 (TCP :5000) ⇄ meshcore2mqtt ⇄ MQTT broker ⇄ Node-RED
```

> The companion accepts **one TCP client at a time**. While the bridge is connected, the phone app can't connect to this node over WiFi.

## Run with Docker

```sh
cp .env.example .env      # set MESHCORE_HOST, MQTT_HOST, etc.
docker compose up -d --build
docker compose logs -f
```

## Run without Docker

```sh
pip install -r requirements.txt
cp .env.example .env
python meshcore2mqtt.py
```

## Topics (`MQTT_PREFIX` defaults to `meshcore`)

| Topic | Dir | Payload |
|---|---|---|
| `meshcore/status` | out (retained) | `online` / `offline` (offline is also the MQTT last will) |
| `meshcore/node` | out (retained) | node name, public key, radio settings, `connected` |
| `meshcore/channels` | out (retained) | `[{"idx":2,"name":"Tapton","slug":"tapton"}, ...]` |
| `meshcore/rx/channel/<slug>` | out | `{channel_idx, channel_name, channel_slug, sender, text, raw_text, snr, path_len, sender_timestamp, received_at}` |
| `meshcore/rx/dm/<pubkey_prefix>` | out | `{pubkey_prefix, sender, text, snr, path_len, sender_timestamp, received_at}` |
| `meshcore/tx/channel/<slug or idx>` | in | plain text, or `{"text":"...","id":"optional"}` |
| `meshcore/tx/dm/<name or pubkey prefix>` | in | plain text, or `{"text":"...","id":"optional"}` |
| `meshcore/tx/result` | out | `{id, kind, target, ok, parts?, error?}` |
| `meshcore/cmd/advert` | in | empty for a zero-hop advert, `flood` for a flood advert |
| `meshcore/cmd/refresh` | in | anything; re-reads contacts and channels |
| `meshcore/cmd/contacts` | in | anything; publishes the contact list to `meshcore/contacts` |
| `meshcore/contacts` | out | `{contacts: [{name, pubkey_prefix, type, last_advert, path}], pending: [...]}` |
| `meshcore/cmd/add_contact` | in | name or key prefix of a pending advert |
| `meshcore/cmd/result` | out | `{cmd, ok, error?, ...}` |

Test with mosquitto clients:

```sh
mosquitto_sub -h <broker> -t 'meshcore/#' -v
mosquitto_pub -h <broker> -t meshcore/tx/channel/tapton -m 'hello from mqtt'
```

## Channel topics use names, not indexes

A channel's index is just its slot on the device, which changes if you re-add channels or move to a new device. Topics use a **slug** of the channel name instead: lowercased, spaces become `-`, and anything other than `a-z 0-9 - _` is removed (MQTT reserves `#` and `+`).

| Channel name | Topic |
|---|---|
| `Public` | `meshcore/rx/channel/public` |
| `#yorkshire` | `meshcore/rx/channel/yorkshire` |
| `Tapton` | `meshcore/rx/channel/tapton` |

- If two names give the same slug (for example `#tapton` and `Tapton`), the higher index gets `-<idx>` appended and a warning is logged.
- Sending accepts the slug, the original name, or a numeric index.
- The channel list is re-read on reconnect, when a message arrives on an unknown index, and when a send names an unknown channel.
- When you move to a new device, add the channels with the same names (and, for private channels, the same secret). Topics stay the same.

## Direct messages

- DMs are sent with retries until the recipient ACKs: direct route first, then flood (`DM_MAX_ATTEMPTS`, default 3). If no ACK arrives, `tx/result` reports `ok: false`. The message may still have arrived; only the ACK was lost.
- To DM the node, people need it in their contacts, so send an advert with `meshcore/cmd/advert`. The node must also know *them* to decrypt and reply, so turn on auto-add contacts on the device. If auto-add is off (`manual_add_contacts: true` on `meshcore/node`), adverts the bridge hears go to `pending` in `cmd/contacts`; add them with `cmd/add_contact`.
- Contacts are kept in sync as adverts arrive, and re-read if a DM arrives from an unknown key.

## Airtime protection

Outgoing messages go through a single queue:

- Sends are at least `TX_MIN_INTERVAL` seconds apart.
- Text longer than `TX_MAX_BYTES` (UTF-8) is split on word boundaries into at most `TX_MAX_PARTS` messages; anything beyond that is truncated with `…`.
- A queued message older than `TX_MAX_AGE` is dropped, so a backlog doesn't flood the mesh after a reconnect.

## Notes

- The bridge splits channel text `Sender: message` into `sender` and `text`.
- Your own sent messages are not echoed back to `rx/`, so a bot replying through `tx/` can't loop on its own output.
- The bridge reconnects automatically to both the node and the broker.

## Example: local AI chatbot (Node-RED + Ollama)

[`examples/node-red-ai-chatbot.json`](examples/node-red-ai-chatbot.json) is an importable Node-RED flow:

- **Channel** (`rx/channel/tapton`): answers messages that start with `!ai`, in one mesh message. History is shared by the whole channel.
- **DMs** (`rx/dm/+`): every DM is a question, no `!ai` needed. Each person has their own history, and replies can be up to 2 messages.
- **Memory:** the last 4 exchanges per conversation, forgotten after 30 minutes of quiet. `!ai reset` on the channel, or `reset` by DM, clears it.
- **Limits:** one question at a time, a per-sender cooldown (30 s channel, 10 s DM), and markdown and emoji are stripped.

Edit `examples/ai-filter.js` and `examples/ai-format.js`, then run `python examples/build-flow.py` to rebuild the JSON. Node IDs and the broker config ID match a specific Node-RED install; change them in `build-flow.py` for yours.
