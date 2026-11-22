#!/usr/bin/env python3
"""
meshcore2mqtt - bridge a MeshCore companion node (WiFi/TCP) to MQTT.

Topics (PREFIX defaults to "meshcore"):

  Published by the bridge
    {PREFIX}/status                 "online" / "offline"   (retained, MQTT LWT)
    {PREFIX}/node                   JSON node info + connected flag (retained)
    {PREFIX}/channels               JSON list of configured channels (retained)
    {PREFIX}/rx/channel/{slug}      JSON for each received channel message
    {PREFIX}/rx/dm/{pubkey_prefix}  JSON for each received direct message
    {PREFIX}/tx/result              JSON result of every send attempt

  Subscribed by the bridge
    {PREFIX}/tx/channel/{slug|idx}  send to a channel by name slug (or index)

  Channel slug = lowercased name with anything but a-z 0-9 - _ removed,
  e.g. "#yorkshire" -> "yorkshire", "Tapton" -> "tapton". Names are stable
  across devices; slot indexes are not.
    {PREFIX}/tx/dm/{name|pubkey}    send a DM to a contact (name or key prefix)

  TX payload is either plain text, or JSON: {"text": "...", "id": "optional"}
"""

import asyncio
import json
import logging
import os
import re
import signal
import time
from dataclasses import dataclass, field

import aiomqtt
from meshcore import EventType, MeshCore

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def env(name, default=None, cast=str):
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return cast(v)


MESHCORE_HOST = env("MESHCORE_HOST", "192.168.1.50")
MESHCORE_PORT = env("MESHCORE_PORT", 5000, int)
MESHCORE_MAX_CHANNELS = env("MESHCORE_MAX_CHANNELS", 8, int)

MQTT_HOST = env("MQTT_HOST", "localhost")
MQTT_PORT = env("MQTT_PORT", 1883, int)
MQTT_USER = env("MQTT_USER")
MQTT_PASS = env("MQTT_PASS")
MQTT_CLIENT_ID = env("MQTT_CLIENT_ID", "meshcore2mqtt")
PREFIX = env("MQTT_PREFIX", "meshcore").rstrip("/")

# Airtime protection
TX_MIN_INTERVAL = env("TX_MIN_INTERVAL", 3.0, float)  # seconds between sends
TX_MAX_BYTES = env("TX_MAX_BYTES", 140, int)  # per-message UTF-8 byte limit
TX_MAX_PARTS = env("TX_MAX_PARTS", 3, int)  # max chunks a long message is split into
TX_MAX_AGE = env("TX_MAX_AGE", 120, int)  # drop queued sends older than this (s)
TX_QUEUE_SIZE = env("TX_QUEUE_SIZE", 20, int)

RECONNECT_DELAY = env("RECONNECT_DELAY", 10, int)
LOG_LEVEL = env("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=LOG_LEVEL.upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("meshcore2mqtt")
logging.getLogger("meshcore").setLevel(logging.WARNING)


@dataclass
class TxJob:
    kind: str  # "channel" | "dm"
    target: str  # channel idx or contact name / key prefix
    text: str
    id: str | None = None
    queued_at: float = field(default_factory=time.time)


def slugify(name: str) -> str:
    """MQTT-safe channel slug: '#Yorkshire' -> 'yorkshire', 'Hope Valley' -> 'hope-valley'."""
    s = re.sub(r"\s+", "-", (name or "").strip().lower())
    return re.sub(r"[^a-z0-9_-]", "", s).strip("-")


def build_slugs(channels: dict[int, str]) -> dict[int, str]:
    """Map channel idx -> unique slug. On a collision the later index gets '-<idx>'."""
    slugs, used = {}, set()
    for idx, name in sorted(channels.items()):
        slug = slugify(name) or f"ch{idx}"
        if slug in used:
            log.warning("Channel slug '%s' clashes (idx %d '%s'); using '%s-%d'",
                        slug, idx, name, slug, idx)
            slug = f"{slug}-{idx}"
        used.add(slug)
        slugs[idx] = slug
    return slugs


def split_text(text: str, max_bytes: int, max_parts: int) -> list[str]:
    """Split text into chunks of <= max_bytes UTF-8, preferring word boundaries."""
    text = " ".join(text.split())  # collapse whitespace/newlines
    if len(text.encode()) <= max_bytes:
        return [text] if text else []

    parts, current = [], ""
    for word in text.split(" "):
        # hard-split words that are themselves too long
        while len(word.encode()) > max_bytes:
            cut = max_bytes
            while len(word[:cut].encode()) > max_bytes:
                cut -= 1
            if current:
                parts.append(current)
                current = ""
            parts.append(word[:cut])
            word = word[cut:]
        candidate = f"{current} {word}" if current else word
        if len(candidate.encode()) <= max_bytes:
            current = candidate
        else:
            parts.append(current)
            current = word
    if current:
        parts.append(current)

    if len(parts) > max_parts:
        parts = parts[:max_parts]
        last = parts[-1]
        while len((last + "…").encode()) > max_bytes:
            last = last[:-1]
        parts[-1] = last + "…"
    return parts


class Bridge:
    def __init__(self):
        self.mc: MeshCore | None = None
        self.mqtt: aiomqtt.Client | None = None
        self.mc_ready = asyncio.Event()
        self.channels: dict[int, str] = {}  # idx -> name
        self.slugs: dict[int, str] = {}  # idx -> slug
        self.channel_lock = asyncio.Lock()
        self.node_info: dict = {}
        self.tx_queue: asyncio.Queue[TxJob] = asyncio.Queue(maxsize=TX_QUEUE_SIZE)
        self.stopping = asyncio.Event()

    # ---------------------------------------------------------------- MQTT out
    async def publish(self, topic: str, payload, retain=False, qos=0):
        if self.mqtt is None:
            log.debug("MQTT not connected, dropping publish to %s", topic)
            return
        if not isinstance(payload, (str, bytes)):
            payload = json.dumps(payload, default=str)
        try:
            await self.mqtt.publish(f"{PREFIX}/{topic}", payload, qos=qos, retain=retain)
        except aiomqtt.MqttError as e:
            log.warning("MQTT publish failed (%s): %s", topic, e)

    async def publish_node_state(self):
        await self.publish(
            "node",
            {**self.node_info, "connected": self.mc_ready.is_set(),
             "host": MESHCORE_HOST, "port": MESHCORE_PORT},
            retain=True,
        )
        await self.publish(
            "channels",
            [{"idx": i, "name": n, "slug": self.slugs.get(i)}
             for i, n in sorted(self.channels.items())],
            retain=True,
        )

    # ------------------------------------------------------------ MeshCore in
    async def on_channel_msg(self, event):
        p = event.payload
        idx = p.get("channel_idx")
        if idx not in self.slugs:
            await self.load_channels()  # channel added on the device since connect
        slug = self.slugs.get(idx, f"ch{idx}")
        raw = p.get("text", "")
        # Channel messages arrive as "SenderName: message"
        sender, sep, text = raw.partition(": ")
        if not sep:
            sender, text = None, raw
        msg = {
            "channel_idx": idx,
            "channel_name": self.channels.get(idx),
            "channel_slug": slug,
            "sender": sender,
            "text": text,
            "raw_text": raw,
            "snr": p.get("SNR"),
            "path_len": p.get("path_len"),
            "sender_timestamp": p.get("sender_timestamp"),
            "received_at": int(time.time()),
        }
        log.info("RX ch%s [%s] %s: %s", idx, msg["channel_name"], sender, text)
        await self.publish(f"rx/channel/{slug}", msg)

    async def on_contact_msg(self, event):
        p = event.payload
        prefix = p.get("pubkey_prefix", "")
        contact = self.mc.get_contact_by_key_prefix(prefix) if self.mc else None
        msg = {
            "pubkey_prefix": prefix,
            "sender": contact.get("adv_name") if contact else None,
            "text": p.get("text", ""),
            "snr": p.get("SNR"),
            "path_len": p.get("path_len"),
            "sender_timestamp": p.get("sender_timestamp"),
            "received_at": int(time.time()),
        }
        log.info("RX DM %s (%s): %s", msg["sender"], prefix, msg["text"])
        await self.publish(f"rx/dm/{prefix}", msg)

    # ---------------------------------------------------------- MeshCore loop
    async def load_channels(self):
        """(Re)read the channel list from the device and rebuild the slug map."""
        if self.mc is None:
            return
        async with self.channel_lock:
            channels = {}
            for idx in range(MESHCORE_MAX_CHANNELS):
                try:
                    res = await self.mc.commands.get_channel(idx)
                except Exception as e:
                    log.debug("get_channel(%d) failed: %s", idx, e)
                    break
                if res is None or res.type == EventType.ERROR:
                    break
                name = res.payload.get("channel_name", "")
                if name:
                    channels[idx] = name
            changed = channels != self.channels
            self.channels = channels
            self.slugs = build_slugs(channels)
            log.info("Channels: %s",
                     {self.slugs[i]: i for i in sorted(channels)} or "none")
        if changed and self.mc_ready.is_set():
            await self.publish_node_state()

    async def resolve_channel(self, target: str) -> int:
        """Channel slug, name or numeric index -> idx. Refreshes the list once on a miss."""
        if target.isdigit():
            return int(target)
        want = slugify(target)
        for attempt in range(2):
            for idx, slug in self.slugs.items():
                if slug == want or slugify(self.channels.get(idx, "")) == want:
                    return idx
            if attempt == 0:
                await self.load_channels()
        raise LookupError(f"unknown channel '{target}'")

    async def meshcore_loop(self):
        while not self.stopping.is_set():
            mc = None
            try:
                log.info("Connecting to MeshCore node %s:%s", MESHCORE_HOST, MESHCORE_PORT)
                mc = await MeshCore.create_tcp(MESHCORE_HOST, MESHCORE_PORT)
                if mc is None:
                    raise ConnectionError("no response from node")
                self.mc = mc

                lost = asyncio.Event()
                mc.subscribe(EventType.DISCONNECTED, lambda e: lost.set())
                mc.subscribe(EventType.CHANNEL_MSG_RECV, self.on_channel_msg)
                mc.subscribe(EventType.CONTACT_MSG_RECV, self.on_contact_msg)

                info = mc.self_info or {}
                self.node_info = {
                    "name": info.get("name"),
                    "public_key": info.get("public_key"),
                    "radio_freq": info.get("radio_freq"),
                    "radio_bw": info.get("radio_bw"),
                    "radio_sf": info.get("radio_sf"),
                    "tx_power": info.get("tx_power"),
                }
                await mc.ensure_contacts()
                await self.load_channels()
                await mc.start_auto_message_fetching()

                self.mc_ready.set()
                log.info("MeshCore connected as '%s'", self.node_info.get("name"))
                await self.publish_node_state()

                stop_task = asyncio.create_task(self.stopping.wait())
                lost_task = asyncio.create_task(lost.wait())
                await asyncio.wait({stop_task, lost_task}, return_when=asyncio.FIRST_COMPLETED)
                stop_task.cancel()
                lost_task.cancel()
                if lost.is_set():
                    log.warning("MeshCore connection lost")
            except Exception as e:
                log.error("MeshCore error: %s", e)
            finally:
                self.mc_ready.clear()
                self.mc = None
                if mc is not None:
                    try:
                        await mc.disconnect()
                    except Exception:
                        pass
                await self.publish_node_state()

            if not self.stopping.is_set():
                log.info("Reconnecting to MeshCore in %ss", RECONNECT_DELAY)
                try:
                    await asyncio.wait_for(self.stopping.wait(), RECONNECT_DELAY)
                except asyncio.TimeoutError:
                    pass

    # ------------------------------------------------------------- MQTT loop
    async def handle_tx_message(self, message: aiomqtt.Message):
        topic = str(message.topic)
        rest = topic[len(f"{PREFIX}/tx/"):]
        kind, _, target = rest.partition("/")
        if kind not in ("channel", "dm") or not target:
            return

        raw = message.payload.decode("utf-8", "ignore") if message.payload else ""
        text, job_id = raw, None
        if raw.lstrip().startswith("{"):
            try:
                data = json.loads(raw)
                text = str(data.get("text", ""))
                job_id = data.get("id")
            except json.JSONDecodeError:
                pass
        text = text.strip()
        if not text:
            return

        job = TxJob(kind=kind, target=target, text=text, id=job_id)
        try:
            self.tx_queue.put_nowait(job)
        except asyncio.QueueFull:
            log.warning("TX queue full, dropping message to %s/%s", kind, target)
            await self.publish("tx/result", {"id": job_id, "kind": kind, "target": target,
                                             "ok": False, "error": "queue_full"})

    async def mqtt_loop(self):
        will = aiomqtt.Will(f"{PREFIX}/status", "offline", qos=1, retain=True)
        while not self.stopping.is_set():
            try:
                async with aiomqtt.Client(
                    hostname=MQTT_HOST, port=MQTT_PORT,
                    username=MQTT_USER, password=MQTT_PASS,
                    identifier=MQTT_CLIENT_ID, will=will,
                ) as client:
                    self.mqtt = client
                    log.info("MQTT connected to %s:%s (prefix '%s')", MQTT_HOST, MQTT_PORT, PREFIX)
                    await client.publish(f"{PREFIX}/status", "online", qos=1, retain=True)
                    await self.publish_node_state()
                    await client.subscribe(f"{PREFIX}/tx/#", qos=1)
                    async for message in client.messages:
                        await self.handle_tx_message(message)
            except aiomqtt.MqttError as e:
                log.error("MQTT error: %s", e)
            finally:
                self.mqtt = None

            if not self.stopping.is_set():
                log.info("Reconnecting to MQTT in %ss", RECONNECT_DELAY)
                try:
                    await asyncio.wait_for(self.stopping.wait(), RECONNECT_DELAY)
                except asyncio.TimeoutError:
                    pass

    # -------------------------------------------------------------- TX worker
    async def resolve_contact(self, target: str):
        mc = self.mc
        contact = mc.get_contact_by_name(target) or mc.get_contact_by_key_prefix(target)
        if contact is None:
            await mc.commands.get_contacts()  # refresh and retry once
            contact = mc.get_contact_by_name(target) or mc.get_contact_by_key_prefix(target)
        return contact

    async def send_one(self, job: TxJob, part: str):
        mc = self.mc
        if job.kind == "channel":
            res = await mc.commands.send_chan_msg(await self.resolve_channel(job.target), part)
        else:
            contact = await self.resolve_contact(job.target)
            if contact is None:
                raise LookupError(f"unknown contact '{job.target}'")
            res = await mc.commands.send_msg(contact, part)
        if res is None or res.type == EventType.ERROR:
            reason = res.payload.get("reason") if res else "timeout"
            raise RuntimeError(f"node rejected send: {reason}")

    async def tx_worker(self):
        last_send = 0.0
        while not self.stopping.is_set():
            job = await self.tx_queue.get()
            result = {"id": job.id, "kind": job.kind, "target": job.target, "ok": False}
            try:
                await self.mc_ready.wait()
                if time.time() - job.queued_at > TX_MAX_AGE:
                    raise TimeoutError("message expired in queue")

                parts = split_text(job.text, TX_MAX_BYTES, TX_MAX_PARTS)
                for part in parts:
                    wait = TX_MIN_INTERVAL - (time.monotonic() - last_send)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    await self.send_one(job, part)
                    last_send = time.monotonic()
                    log.info("TX %s/%s: %s", job.kind, job.target, part)
                result.update(ok=True, parts=len(parts))
            except Exception as e:
                log.warning("TX %s/%s failed: %s", job.kind, job.target, e)
                result["error"] = str(e)
            finally:
                self.tx_queue.task_done()
            await self.publish("tx/result", result)

    # ------------------------------------------------------------------- run
    async def run(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stopping.set)
            except (NotImplementedError, RuntimeError):
                pass  # Windows: Ctrl+C raises KeyboardInterrupt instead

        tasks = [
            asyncio.create_task(self.mqtt_loop(), name="mqtt"),
            asyncio.create_task(self.meshcore_loop(), name="meshcore"),
            asyncio.create_task(self.tx_worker(), name="tx"),
        ]
        await self.stopping.wait()
        log.info("Shutting down")
        if self.mqtt is not None:
            try:
                await self.mqtt.publish(f"{PREFIX}/status", "offline", qos=1, retain=True)
            except aiomqtt.MqttError:
                pass
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main():
    import sys

    # aiomqtt (paho) needs a selector loop on Windows
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        asyncio.run(Bridge().run(), loop_factory=loop_factory)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
