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

## Airtime protection

Outgoing messages go through a single queue:

- Sends are at least `TX_MIN_INTERVAL` seconds apart.
- Text longer than `TX_MAX_BYTES` (UTF-8) is split on word boundaries into at most `TX_MAX_PARTS` messages; anything beyond that is truncated with `…`.
- A queued message older than `TX_MAX_AGE` is dropped, so a backlog doesn't flood the mesh after a reconnect.

## Notes

- The bridge splits channel text `Sender: message` into `sender` and `text`.
- Your own sent messages are not echoed back to `rx/`, so a bot replying through `tx/` can't loop on its own output.
- The bridge reconnects automatically to both the node and the broker.
