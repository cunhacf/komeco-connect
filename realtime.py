"""Realtime AWS IoT shadow updates over MQTT/WebSocket."""

from __future__ import annotations

import asyncio
import datetime as dt
from functools import partial
import hashlib
import hmac
import json
import logging
import ssl
from typing import Any
from urllib.parse import parse_qsl, quote, urlparse, urlunparse

from homeassistant.core import HomeAssistant
from paho.mqtt import client as mqtt

from .api import KomecoApiClient
from .const import AWS_REGION, IOT_DATA_ENDPOINT
from .coordinator import KomecoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
_IOT_WEBSOCKET_SERVICE = "iotdevicegateway"


class KomecoRealtimeListener:
    """Maintain an MQTT/WebSocket subscription for AWS IoT shadow topics."""

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        api: KomecoApiClient,
        coordinator: KomecoDataUpdateCoordinator,
    ) -> None:
        self._hass = hass
        self._api = api
        self._coordinator = coordinator
        self._client: mqtt.Client | None = None
        self._connected_event = asyncio.Event()
        self._reconnect_task: asyncio.Task | None = None
        self._running = False
        self._thing_names: list[str] = []

    async def async_start(self) -> None:
        """Start realtime listener."""
        _LOGGER.debug("Starting realtime listener")
        self._running = True
        if self._client and self._connected_event.is_set():
            _LOGGER.debug("Realtime listener already connected; skipping start")
            return
        await self._async_shutdown_client()
        await self._async_connect()

    async def async_stop(self) -> None:
        """Stop realtime listener."""
        _LOGGER.debug("Stopping realtime listener")
        self._running = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        await self._async_shutdown_client()

    async def _async_connect(self) -> None:
        self._connected_event.clear()
        _LOGGER.debug("Realtime connect attempt started")
        await self._api._ensure_aws_credentials()  # noqa: SLF001

        ws_url = self._build_presigned_ws_url()
        parsed = urlparse(ws_url)
        if not parsed.hostname:
            raise ValueError("Invalid AWS IoT WebSocket URL")
        _LOGGER.debug(
            "Realtime WebSocket target host=%s port=%s path=%s query_keys=%s",
            parsed.hostname,
            parsed.port or 443,
            parsed.path,
            sorted(dict(parse_qsl(parsed.query, keep_blank_values=True)).keys()),
        )

        self._thing_names = self._resolve_thing_names()
        if not self._thing_names:
            _LOGGER.warning("Realtime MQTT disabled: no shadow thing name candidates")
            return
        _LOGGER.debug("Realtime thing candidates=%s", self._thing_names)

        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"komeco-ha-{self._api.device_id or 'device'}",
                transport="websockets",
                protocol=mqtt.MQTTv311,
            )
        except Exception:
            client = mqtt.Client(
                client_id=f"komeco-ha-{self._api.device_id or 'device'}",
                transport="websockets",
                protocol=mqtt.MQTTv311,
            )

        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        client.ws_set_options(path=path)
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        client.enable_logger(_LOGGER)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        self._client = client

        def _connect() -> None:
            client.loop_start()
            rc = client.connect(parsed.hostname, parsed.port or 443, keepalive=60)
            if rc != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(f"MQTT connect failed rc={rc}")

        try:
            await asyncio.to_thread(_connect)
            await asyncio.wait_for(self._connected_event.wait(), timeout=20)
            _LOGGER.debug("Komeco realtime MQTT connected to thing(s): %s", self._thing_names)
        except Exception as err:
            _LOGGER.warning("Realtime MQTT connect failed: %s", err)
            await self._async_shutdown_client()

    async def _async_shutdown_client(self) -> None:
        client = self._client
        self._client = None
        self._connected_event.clear()
        if not client:
            return

        _LOGGER.debug("Shutting down realtime MQTT client")
        def _shutdown() -> None:
            try:
                client.disconnect()
            except Exception:
                pass
            try:
                client.loop_stop()
            except Exception:
                pass

        await asyncio.to_thread(_shutdown)

    def _schedule_reconnect(self) -> None:
        if not self._running:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        _LOGGER.debug("Scheduling realtime reconnect in 5 seconds")
        self._reconnect_task = self._hass.async_create_task(self._async_reconnect())

    async def _async_reconnect(self) -> None:
        await asyncio.sleep(5)
        if not self._running:
            return
        _LOGGER.debug("Executing realtime reconnect")
        await self._async_shutdown_client()
        await self._async_connect()

    def _resolve_thing_names(self) -> list[str]:
        thing_name = self._coordinator.data.get("shadow_thing_name") if isinstance(self._coordinator.data, dict) else None
        names: list[str] = []
        if isinstance(thing_name, str) and thing_name:
            names.append(thing_name)
        names.extend(self._api.get_shadow_thing_candidates())
        deduped: list[str] = []
        for name in names:
            if name and name not in deduped:
                deduped.append(name)
        return deduped

    def _shadow_topics_for_thing(self, thing_name: str) -> list[str]:
        root = f"$aws/things/{thing_name}/shadow"
        return [
            f"{root}/update",
            f"{root}/update/accepted",
            f"{root}/update/documents",
            f"{root}/get/accepted",
        ]

    def _on_connect(self, client, userdata, flags, reason_code=0, properties=None) -> None:
        rc = getattr(reason_code, "value", reason_code)
        if rc != 0:
            _LOGGER.warning("Realtime MQTT connect rejected: rc=%s", rc)
            return

        for thing_name in self._thing_names:
            for topic in self._shadow_topics_for_thing(thing_name):
                _LOGGER.debug("Realtime subscribe topic=%s", topic)
                client.subscribe(topic, qos=0)
            _LOGGER.debug("Realtime publish shadow get thing=%s", thing_name)
            client.publish(f"$aws/things/{thing_name}/shadow/get", payload="{}", qos=0, retain=False)

        self._hass.loop.call_soon_threadsafe(self._connected_event.set)

    def _on_disconnect(self, client, userdata, *args) -> None:
        _LOGGER.debug("Realtime MQTT disconnected args=%s", args)
        self._hass.loop.call_soon_threadsafe(self._connected_event.clear)
        if self._running:
            self._hass.loop.call_soon_threadsafe(self._schedule_reconnect)

    def _on_message(self, client, userdata, msg) -> None:
        payload = self._parse_json(msg.payload)
        if not isinstance(payload, dict):
            _LOGGER.debug("Realtime message ignored (not JSON dict) topic=%s", msg.topic)
            return

        reported = self._extract_reported(payload)
        if not reported:
            _LOGGER.debug("Realtime message has no reported section topic=%s", msg.topic)
            return
        _LOGGER.debug("Realtime message topic=%s reported_keys=%s", msg.topic, sorted(reported.keys()))

        thing_name = self._extract_thing_name_from_topic(msg.topic)
        update = partial(
            self._coordinator.handle_realtime_shadow_update,
            reported=reported,
            raw=payload,
            thing_name=thing_name,
        )
        self._hass.loop.call_soon_threadsafe(update)

    def _extract_thing_name_from_topic(self, topic: str) -> str | None:
        parts = topic.split("/")
        if len(parts) >= 4 and parts[0] == "$aws" and parts[1] == "things":
            return parts[2]
        return None

    def _extract_reported(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = payload.get("state")
        if isinstance(state, dict):
            reported = state.get("reported")
            if isinstance(reported, dict):
                return reported
            current = state.get("current")
            if isinstance(current, dict):
                current_state = current.get("state")
                if isinstance(current_state, dict):
                    reported = current_state.get("reported")
                    if isinstance(reported, dict):
                        return reported

        current = payload.get("current")
        if isinstance(current, dict):
            current_state = current.get("state")
            if isinstance(current_state, dict):
                reported = current_state.get("reported")
                if isinstance(reported, dict):
                    return reported

        reported = payload.get("reported")
        if isinstance(reported, dict):
            return reported
        return {}

    def _parse_json(self, raw: bytes) -> dict[str, Any] | None:
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _build_presigned_ws_url(self) -> str:
        access_key = self._api._aws_access_key  # noqa: SLF001
        secret_key = self._api._aws_secret_key  # noqa: SLF001
        session_token = self._api._aws_session_token  # noqa: SLF001
        if not (access_key and secret_key and session_token):
            raise ValueError("Missing AWS temporary credentials for realtime MQTT")

        parsed = urlparse(IOT_DATA_ENDPOINT)
        host = parsed.netloc
        if not host:
            raise ValueError("Invalid IoT endpoint host")

        # Mirror app behavior (Amplify Signer.signUrl for iotdevicegateway):
        # sign without X-Amz-Expires/X-Amz-Security-Token, then append session token.
        now = dt.datetime.now(dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        credential_scope = f"{datestamp}/{AWS_REGION}/{_IOT_WEBSOCKET_SERVICE}/aws4_request"

        signed_params: dict[str, str] = dict(parse_qsl(parsed.query, keep_blank_values=True))
        signed_params.update(
            {
                "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
                "X-Amz-Credential": f"{access_key}/{credential_scope}",
                "X-Amz-Date": amz_date,
                "X-Amz-SignedHeaders": "host",
            }
        )
        canonical_query = self._canonical_query(signed_params)
        canonical_request = "\n".join(
            [
                "GET",
                self._canonical_uri(parsed.path or "/mqtt"),
                canonical_query,
                f"host:{host}\n",
                "host",
                hashlib.sha256(b"").hexdigest(),
            ]
        )
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = self._get_signature_key(secret_key, datestamp, AWS_REGION, _IOT_WEBSOCKET_SERVICE)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        final_params = dict(signed_params)
        final_params["X-Amz-Signature"] = signature
        final_params["X-Amz-Security-Token"] = session_token
        final_query = self._canonical_query(final_params)
        _LOGGER.debug("Built realtime presigned URL for host=%s path=%s", host, parsed.path or "/mqtt")

        return urlunparse(
            (
                "wss",
                parsed.netloc,
                parsed.path or "/mqtt",
                "",
                final_query,
                "",
            )
        )

    def _canonical_query(self, params: dict[str, str]) -> str:
        encoded = sorted(
            (quote(str(k), safe="-_.~"), quote(str(v), safe="-_.~"))
            for k, v in params.items()
        )
        return "&".join(f"{k}={v}" for k, v in encoded)

    def _canonical_uri(self, path: str) -> str:
        if not path:
            return "/"
        segments = path.split("/")
        return "/".join(quote(seg, safe="-_.~") for seg in segments)

    def _get_signature_key(self, secret_key: str, datestamp: str, region: str, service: str) -> bytes:
        k_date = self._sign(("AWS4" + secret_key).encode("utf-8"), datestamp)
        k_region = self._sign(k_date, region)
        k_service = self._sign(k_region, service)
        return self._sign(k_service, "aws4_request")

    def _sign(self, key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()
