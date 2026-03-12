"""Komeco cloud API client."""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse

from aiohttp import ClientSession
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from .const import (
    AWS_IOT_DATA_SERVICE,
    AWS_REGION,
    AWS_SERVICE,
    COGNITO_CLIENT_ID,
    COGNITO_IDENTITY_POOL_ID,
    COGNITO_UHASH_PASSPHRASE,
    COGNITO_USER_POOL_ID,
    ENDPOINTS,
    IOT_DATA_ENDPOINT,
    SUPPORTED_DEVICE_TYPES,
)

_LOGGER = logging.getLogger(__name__)


class KomecoApiError(Exception):
    """Generic API error."""


class KomecoAuthError(KomecoApiError):
    """Authentication error."""


def _json_loads_safe(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _decode_jwt_payload_unverified(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = "=" * ((4 - len(payload) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode((payload + padding).encode("ascii")).decode("utf-8")
        data = json.loads(decoded)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _jwt_expired(token: str | None, skew_seconds: int = 120) -> bool:
    payload = _decode_jwt_payload_unverified(token)
    exp = payload.get("exp")
    if not isinstance(exp, int):
        return True
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    return exp <= now + skew_seconds


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "on", "yes"}:
            return True
        if normalized in {"0", "false", "off", "no"}:
            return False
    return None


def _mask_email(email: str) -> str:
    value = email.strip()
    if "@" not in value:
        return "***"
    local, domain = value.split("@", 1)
    if len(local) <= 2:
        local = "*" * len(local)
    else:
        local = f"{local[:2]}***"
    return f"{local}@{domain}"


class KomecoApiClient:
    """Komeco cloud API client with Cognito + SigV4."""

    def __init__(
        self,
        session: ClientSession,
        *,
        email: str,
        password: str | None = None,
        refresh_token: str,
        device_id: str | None = None,
        place_id: str | None = None,
        id_token: str | None = None,
        access_token: str | None = None,
        sub: str | None = None,
    ) -> None:
        self._session = session
        self.email = email.strip()
        self.password = password
        self.refresh_token = refresh_token.strip()
        self.device_id = device_id.strip() if device_id else None
        self.place_id = place_id.strip() if place_id else None
        self.id_token = id_token
        self.access_token = access_token
        self.sub = sub

        self._identity_id: str | None = None
        self._aws_access_key: str | None = None
        self._aws_secret_key: str | None = None
        self._aws_session_token: str | None = None
        self._aws_expiration_epoch: float = 0.0
        _LOGGER.debug(
            "KomecoApiClient initialized email=%s device_id=%s place_id=%s",
            _mask_email(self.email),
            self.device_id,
            self.place_id,
        )

    @property
    def token_data(self) -> dict[str, str]:
        """Return current token fields suitable for storage."""
        data: dict[str, str] = {}
        if self.id_token:
            data["id_token"] = self.id_token
        if self.access_token:
            data["access_token"] = self.access_token
        if self.refresh_token:
            data["refresh_token"] = self.refresh_token
        if self.sub:
            data["sub"] = self.sub
        return data

    async def async_authenticate(self, *, force: bool = False) -> None:
        """Ensure we have a valid IdToken."""
        if not force and self.id_token and not _jwt_expired(self.id_token):
            _LOGGER.debug("Using cached IdToken for email=%s", _mask_email(self.email))
            if not self.sub:
                self.sub = _decode_jwt_payload_unverified(self.id_token).get("sub")
            return

        if not self.refresh_token:
            raise KomecoAuthError("Missing refresh token")

        _LOGGER.debug("Refreshing Cognito tokens via REFRESH_TOKEN_AUTH for email=%s", _mask_email(self.email))
        auth_params = {"REFRESH_TOKEN": self.refresh_token}
        payload = {
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": auth_params,
        }
        try:
            data = await self._async_aws_json_request(
                service="cognito-idp",
                action="InitiateAuth",
                payload=payload,
            )
        except KomecoAuthError as err:
            # Some users get invalid refresh token responses even with a valid session.
            # If password is available, recover by performing SRP login and replacing tokens.
            if self.password and "Invalid Refresh Token" in str(err):
                _LOGGER.debug("Refresh token rejected; falling back to password login for email=%s", _mask_email(self.email))
                await self.async_login_with_password(self.password)
                return
            raise
        auth_result = data.get("AuthenticationResult")
        if not isinstance(auth_result, dict):
            raise KomecoAuthError("AuthenticationResult missing from Cognito response")

        id_token = auth_result.get("IdToken")
        if not isinstance(id_token, str) or not id_token:
            raise KomecoAuthError("IdToken missing from Cognito response")

        access_token = auth_result.get("AccessToken")
        refresh_token = auth_result.get("RefreshToken")

        self.id_token = id_token
        self.access_token = access_token if isinstance(access_token, str) else None
        if isinstance(refresh_token, str) and refresh_token:
            self.refresh_token = refresh_token

        payload_unverified = _decode_jwt_payload_unverified(self.id_token)
        sub = payload_unverified.get("sub")
        self.sub = sub if isinstance(sub, str) else self.sub

        # Force fresh IAM credentials after token refresh.
        self._aws_expiration_epoch = 0.0
        _LOGGER.debug(
            "Token refresh succeeded email=%s sub=%s refresh_rotated=%s",
            _mask_email(self.email),
            self.sub,
            isinstance(refresh_token, str) and bool(refresh_token),
        )

    async def async_login_with_password(self, password: str) -> None:
        """Login using Cognito USER_SRP_AUTH and persist tokens in memory."""
        if not self.email:
            raise KomecoAuthError("Email is required for password login")
        if not password:
            raise KomecoAuthError("Password is required for login")

        _LOGGER.debug("Starting SRP password login for email=%s", _mask_email(self.email))

        def _login() -> dict[str, Any]:
            try:
                from pycognito.aws_srp import AWSSRP
            except Exception as exc:
                raise KomecoAuthError(
                    "Password login requires pycognito and boto3"
                ) from exc

            aws = AWSSRP(
                username=self.email,
                password=password,
                pool_id=COGNITO_USER_POOL_ID,
                client_id=COGNITO_CLIENT_ID,
                pool_region=AWS_REGION,
            )
            try:
                result = aws.authenticate_user()
            except Exception as exc:
                raise KomecoAuthError(f"Cognito password login failed: {exc}") from exc
            if not isinstance(result, dict):
                raise KomecoAuthError("Invalid SRP login response")
            return result

        login_data = await asyncio.to_thread(_login)
        auth_result = login_data.get("AuthenticationResult")
        if not isinstance(auth_result, dict):
            raise KomecoAuthError("AuthenticationResult missing from SRP response")

        id_token = auth_result.get("IdToken")
        access_token = auth_result.get("AccessToken")
        refresh_token = auth_result.get("RefreshToken")
        if not isinstance(id_token, str) or not id_token:
            raise KomecoAuthError("IdToken missing from SRP response")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise KomecoAuthError("RefreshToken missing from SRP response")

        self.id_token = id_token
        self.access_token = access_token if isinstance(access_token, str) else None
        self.refresh_token = refresh_token

        payload_unverified = _decode_jwt_payload_unverified(self.id_token)
        sub = payload_unverified.get("sub")
        self.sub = sub if isinstance(sub, str) else self.sub

        # Force fresh IAM credentials after login.
        self._aws_expiration_epoch = 0.0
        _LOGGER.debug("SRP password login succeeded email=%s sub=%s", _mask_email(self.email), self.sub)

    async def async_discover_heaters(self) -> list[dict[str, str]]:
        """Discover heater devices from places endpoint."""
        _LOGGER.debug("Discovering heaters for email=%s", _mask_email(self.email))
        places_resp = await self._async_signed_request(
            method="GET",
            endpoint_name="prod-places",
            path="/get-all-places",
        )
        places_body = self._extract_body(places_resp)
        places_list: list[dict[str, Any]] = []
        if isinstance(places_body, dict):
            value = places_body.get("value")
            if isinstance(value, list):
                places_list = [item for item in value if isinstance(item, dict)]

        found: list[dict[str, str]] = []
        for place in places_list:
            place_id = place.get("placeId")
            place_name = place.get("name")
            rooms = place.get("rooms")
            if not (isinstance(place_id, str) and isinstance(rooms, list)):
                continue
            for room in rooms:
                if not isinstance(room, dict):
                    continue
                devices = room.get("devices")
                if not isinstance(devices, list):
                    continue
                for device in devices:
                    if not isinstance(device, dict):
                        continue
                    device_type = device.get("deviceType")
                    normalized_type = _as_int(device_type)
                    if normalized_type not in SUPPORTED_DEVICE_TYPES:
                        continue
                    device_id = device.get("deviceId")
                    device_name = device.get("deviceName") or device.get("name") or "Komeco Device"
                    if isinstance(device_id, str):
                        found.append(
                            {
                                "device_id": device_id,
                                "place_id": place_id,
                                "device_name": str(device_name),
                                "place_name": str(place_name) if isinstance(place_name, str) else "",
                            }
                        )
        _LOGGER.debug("Discovery finished: %s heaters found", len(found))
        return found

    async def async_fetch_state(self) -> dict[str, Any]:
        """Fetch all data used by entities."""
        if not self.device_id:
            raise KomecoApiError("device_id is not configured")
        if not self.place_id:
            raise KomecoApiError("place_id is not configured")

        _LOGGER.debug("Fetching state device_id=%s place_id=%s", self.device_id, self.place_id)
        await self.async_authenticate()
        if not self.sub:
            raise KomecoApiError("JWT sub is missing")

        device_resp = await self._async_signed_request(
            method="GET",
            endpoint_name="prod-device",
            path="/get-device",
            query={
                "deviceId": self.device_id,
                "userType": "admin",
                "sub": self.sub,
            },
        )
        dashboard_resp = await self._async_signed_request(
            method="GET",
            endpoint_name="prod-dataset",
            path="/getGasHeaterParamsDash",
            query={"deviceId": self.device_id},
        )
        history_resp = await self._async_signed_request(
            method="GET",
            endpoint_name="prod-commandHistory",
            path="/commandHistory-get",
            query={"placeId": self.place_id},
        )
        shadow_reported, shadow_raw, shadow_thing_name, shadow_error = await self._async_fetch_shadow_reported()

        device_body = self._extract_body(device_resp)
        dashboard_body = self._extract_body(dashboard_resp)
        history_items = self._extract_history_items(history_resp)

        command_values = self._extract_command_values(
            shadow_reported=shadow_reported,
            dashboard_body=dashboard_body,
            history_items=history_items,
        )
        supported_command_keys = self._extract_supported_command_keys(
            shadow_reported=shadow_reported,
            dashboard_body=dashboard_body,
            history_items=history_items,
        )
        current_temp = self._extract_current_temperature(
            shadow_reported=shadow_reported,
            dashboard_body=dashboard_body,
            command_values=command_values,
        )

        last_command_at = None
        if history_items:
            last_command_at = history_items[0].get("date")

        result = {
            "device": device_body if isinstance(device_body, dict) else {},
            "dashboard": dashboard_body if isinstance(dashboard_body, dict) else {},
            "command_values": command_values,
            "supported_command_keys": supported_command_keys,
            "current_temperature": current_temp,
            "last_command_at": last_command_at,
            "shadow_reported": shadow_reported,
            "shadow_raw": shadow_raw,
            "shadow_timestamp": _as_int(shadow_raw.get("timestamp")),
            "shadow_version": _as_int(shadow_raw.get("version")),
            "shadow_thing_name": shadow_thing_name,
            "shadow_error": shadow_error,
        }
        _LOGGER.debug(
            "State fetch complete device_id=%s thing=%s temp=%s command_keys=%s shadow_error=%s",
            self.device_id,
            shadow_thing_name,
            result.get("current_temperature"),
            sorted(result.get("command_values", {}).keys()) if isinstance(result.get("command_values"), dict) else [],
            shadow_error,
        )
        return result

    async def async_send_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send command to heater."""
        if not self.device_id:
            raise KomecoApiError("device_id is not configured")

        _LOGGER.debug("Sending command device_id=%s payload=%s", self.device_id, payload)
        response = await self._async_signed_request(
            method="POST",
            endpoint_name="prod-command",
            path=f"/send-commmand/{self.device_id}",
            json_body=payload,
        )
        _LOGGER.debug("Command accepted device_id=%s response_keys=%s", self.device_id, sorted(response.keys()))
        return response

    def _extract_current_temperature(
        self,
        *,
        shadow_reported: Any,
        dashboard_body: Any,
        command_values: dict[str, Any],
    ) -> int | None:
        if isinstance(shadow_reported, dict):
            for key in (
                "current_temp",
                "temp_current_output",
                "temp_current_input",
                "temp",
                "temp_current",
                "temperature",
                "water_temp",
            ):
                value = _as_int(shadow_reported.get(key))
                if value is not None:
                    return value
        if isinstance(dashboard_body, dict):
            for key in ("temp", "temp_current", "temperature", "water_temp"):
                value = _as_int(dashboard_body.get(key))
                if value is not None:
                    return value
        return command_values.get("temp_set")

    def _extract_command_values(
        self,
        *,
        shadow_reported: Any,
        dashboard_body: Any,
        history_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        values: dict[str, Any] = {
            "switch": None,
            "temp_set": None,
            "zero_cold_water_mode": None,
            "zero_cold_water_mode_status": None,
        }

        if isinstance(shadow_reported, dict):
            if "switch" in shadow_reported:
                values["switch"] = _as_bool(shadow_reported.get("switch"))
            if "temp_set" in shadow_reported:
                values["temp_set"] = _as_int(shadow_reported.get("temp_set"))
            if "zero_cold_water_mode" in shadow_reported:
                values["zero_cold_water_mode"] = _as_int(shadow_reported.get("zero_cold_water_mode"))
            if "zero_cold_water_mode_status" in shadow_reported:
                values["zero_cold_water_mode_status"] = _as_bool(
                    shadow_reported.get("zero_cold_water_mode_status")
                )

        if isinstance(dashboard_body, dict):
            for key in values:
                if values[key] is not None:
                    continue
                if key not in dashboard_body:
                    continue
                if key in {"switch", "zero_cold_water_mode_status"}:
                    values[key] = _as_bool(dashboard_body.get(key))
                else:
                    values[key] = _as_int(dashboard_body.get(key))

        for item in history_items:
            data = item.get("data")
            if not isinstance(data, dict):
                continue

            if values["switch"] is None and "switch" in data:
                values["switch"] = _as_bool(data.get("switch"))
            if values["temp_set"] is None and "temp_set" in data:
                values["temp_set"] = _as_int(data.get("temp_set"))
            if values["zero_cold_water_mode"] is None and "zero_cold_water_mode" in data:
                values["zero_cold_water_mode"] = _as_int(data.get("zero_cold_water_mode"))
            if values["zero_cold_water_mode_status"] is None and "zero_cold_water_mode_status" in data:
                values["zero_cold_water_mode_status"] = _as_bool(data.get("zero_cold_water_mode_status"))

            if all(values[key] is not None for key in values):
                break

        return values

    def _extract_supported_command_keys(
        self,
        *,
        shadow_reported: Any,
        dashboard_body: Any,
        history_items: list[dict[str, Any]],
    ) -> list[str]:
        tracked = {"switch", "temp_set", "zero_cold_water_mode", "zero_cold_water_mode_status"}
        supported: set[str] = set()

        if isinstance(shadow_reported, dict):
            supported.update(key for key in tracked if key in shadow_reported)

        if isinstance(dashboard_body, dict):
            supported.update(key for key in tracked if key in dashboard_body)

        for item in history_items:
            data = item.get("data")
            if not isinstance(data, dict):
                continue
            for key in tracked:
                if key in data:
                    supported.add(key)

        # Core controls should always be presented if backend telemetry is temporarily missing.
        supported.add("switch")
        supported.add("temp_set")
        return sorted(supported)

    async def _async_fetch_shadow_reported(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], str | None, str | None]:
        thing_candidates = self._build_shadow_thing_candidates()
        _LOGGER.debug("Trying shadow candidates: %s", thing_candidates)
        errors: list[str] = []

        for thing_name in thing_candidates:
            try:
                shadow_resp = await self._async_signed_request(
                    method="GET",
                    base_url=IOT_DATA_ENDPOINT,
                    path=f"/things/{thing_name}/shadow",
                    service=AWS_IOT_DATA_SERVICE,
                    include_uhash=False,
                )
            except KomecoApiError as err:
                _LOGGER.debug("Shadow read failed for thing=%s err=%s", thing_name, err)
                errors.append(f"{thing_name}: {err}")
                continue

            reported = self._extract_shadow_reported(shadow_resp)
            _LOGGER.debug(
                "Shadow read succeeded thing=%s reported_keys=%s",
                thing_name,
                sorted(reported.keys()),
            )
            return reported, shadow_resp, thing_name, None

        if errors:
            _LOGGER.debug("All shadow candidates failed: %s", errors)
            return {}, {}, None, "; ".join(errors)
        return {}, {}, None, "No shadow thing candidates available"

    def _build_shadow_thing_candidates(self) -> list[str]:
        if not self.device_id:
            return []
        raw_device_id = self.device_id.strip()
        if not raw_device_id:
            return []

        candidates = [raw_device_id]
        if not raw_device_id.startswith("KO_"):
            candidates.append(f"KO_{raw_device_id}")

        deduped: list[str] = []
        for candidate in candidates:
            if candidate and candidate not in deduped:
                deduped.append(candidate)
        return deduped

    def get_shadow_thing_candidates(self) -> list[str]:
        """Return candidate AWS IoT thing names for this device."""
        return self._build_shadow_thing_candidates()

    def _extract_shadow_reported(self, response: dict[str, Any]) -> dict[str, Any]:
        state = response.get("state")
        if isinstance(state, dict):
            reported = state.get("reported")
            if isinstance(reported, dict):
                return reported
        return {}

    def _extract_history_items(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        body = self._extract_body(response)
        items: list[dict[str, Any]] = []
        if isinstance(body, list):
            items = [item for item in body if isinstance(item, dict)]
        elif isinstance(body, dict):
            value = body.get("value")
            if isinstance(value, list):
                items = [item for item in value if isinstance(item, dict)]

        filtered: list[dict[str, Any]] = []
        for item in items:
            device_id = item.get("deviceId")
            if isinstance(device_id, str) and self.device_id and device_id != self.device_id:
                continue
            filtered.append(item)

        filtered.sort(key=lambda item: str(item.get("date", "")), reverse=True)
        return filtered

    def _extract_body(self, response: dict[str, Any]) -> Any:
        body = response.get("body")
        if isinstance(body, str):
            parsed = _json_loads_safe(body)
            if parsed is not None:
                return parsed
        return body

    async def _ensure_aws_credentials(self) -> None:
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        if (
            self._aws_access_key
            and self._aws_secret_key
            and self._aws_session_token
            and self._identity_id
            and now < self._aws_expiration_epoch - 60
        ):
            _LOGGER.debug(
                "Using cached IAM credentials identity_id=%s expires_in=%ss",
                self._identity_id,
                int(self._aws_expiration_epoch - now),
            )
            return

        _LOGGER.debug("Refreshing IAM credentials from Cognito Identity")
        await self.async_authenticate()
        if not self.id_token:
            raise KomecoAuthError("id_token missing after authenticate")

        login_key = f"cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
        get_id_payload = {
            "IdentityPoolId": COGNITO_IDENTITY_POOL_ID,
            "Logins": {
                login_key: self.id_token,
            },
        }
        get_id = await self._async_aws_json_request(
            service="cognito-identity",
            action="GetId",
            payload=get_id_payload,
        )
        identity_id = get_id.get("IdentityId")
        if not isinstance(identity_id, str):
            raise KomecoAuthError("IdentityId missing from GetId")

        get_creds_payload = {
            "IdentityId": identity_id,
            "Logins": {
                login_key: self.id_token,
            },
        }
        creds = await self._async_aws_json_request(
            service="cognito-identity",
            action="GetCredentialsForIdentity",
            payload=get_creds_payload,
        )
        credentials = creds.get("Credentials")
        if not isinstance(credentials, dict):
            raise KomecoAuthError("Credentials missing from GetCredentialsForIdentity")

        access_key = credentials.get("AccessKeyId")
        secret_key = credentials.get("SecretKey")
        session_token = credentials.get("SessionToken")
        expiration = credentials.get("Expiration")
        if not (
            isinstance(access_key, str)
            and isinstance(secret_key, str)
            and isinstance(session_token, str)
            and isinstance(expiration, (int, float))
        ):
            raise KomecoAuthError("Invalid IAM credentials response")

        self._identity_id = identity_id
        self._aws_access_key = access_key
        self._aws_secret_key = secret_key
        self._aws_session_token = session_token
        self._aws_expiration_epoch = float(expiration)
        _LOGGER.debug(
            "IAM credentials refreshed identity_id=%s expires_epoch=%s",
            self._identity_id,
            int(self._aws_expiration_epoch),
        )

    async def _async_aws_json_request(
        self,
        *,
        service: str,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"https://{service}.{AWS_REGION}.amazonaws.com/"
        _LOGGER.debug("AWS JSON request service=%s action=%s payload_keys=%s", service, action, sorted(payload.keys()))
        if service == "cognito-idp":
            target = f"AWSCognitoIdentityProviderService.{action}"
        elif service == "cognito-identity":
            target = f"AWSCognitoIdentityService.{action}"
        else:
            target = f"AWS{service}Service.{action}"

        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": target,
        }
        async with self._session.post(url, headers=headers, json=payload) as resp:
            text = await resp.text()
            data = _json_loads_safe(text)
            if not isinstance(data, dict):
                data = {"raw": text}
            if resp.status >= 400:
                _LOGGER.debug("AWS JSON request failed service=%s action=%s status=%s", service, action, resp.status)
                raise KomecoAuthError(f"{action} failed: HTTP {resp.status} {data}")
            _LOGGER.debug("AWS JSON request succeeded service=%s action=%s status=%s", service, action, resp.status)
            return data

    async def _async_signed_request(
        self,
        *,
        method: str,
        endpoint_name: str | None = None,
        base_url: str | None = None,
        path: str,
        query: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        service: str = AWS_SERVICE,
        include_uhash: bool = True,
    ) -> dict[str, Any]:
        await self._ensure_aws_credentials()
        if endpoint_name:
            if endpoint_name not in ENDPOINTS:
                raise KomecoApiError(f"Unknown endpoint: {endpoint_name}")
            base = ENDPOINTS[endpoint_name].rstrip("/")
        elif base_url:
            base = base_url.rstrip("/")
        else:
            raise KomecoApiError("Either endpoint_name or base_url must be provided")

        full_path = path if path.startswith("/") else f"/{path}"
        url = f"{base}{full_path}"
        if query:
            url = f"{url}?{urlencode(query, doseq=True)}"
        _LOGGER.debug(
            "Signed request method=%s service=%s endpoint=%s path=%s query_keys=%s body_keys=%s include_uhash=%s",
            method.upper(),
            service,
            endpoint_name or base_url,
            full_path,
            sorted(query.keys()) if isinstance(query, dict) else [],
            sorted(json_body.keys()) if isinstance(json_body, dict) else [],
            include_uhash,
        )

        body_bytes = b""
        headers: dict[str, str] = {}
        if include_uhash:
            headers["uhash"] = self._make_uhash(self.email)
        if json_body is not None:
            body_bytes = json.dumps(json_body, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            headers["Content-Type"] = "application/json"

        signed_headers = self._sigv4_headers(
            method=method,
            url=url,
            headers=headers,
            body=body_bytes,
            service=service,
        )
        request_data = body_bytes if body_bytes else None
        async with self._session.request(
            method=method.upper(),
            url=url,
            headers=signed_headers,
            data=request_data,
        ) as resp:
            text = await resp.text()
            parsed = _json_loads_safe(text)
            if parsed is None:
                parsed = {"raw": text}
            if resp.status >= 400:
                _LOGGER.debug("Signed request failed method=%s path=%s status=%s", method.upper(), full_path, resp.status)
                raise KomecoApiError(f"{method} {path} failed: HTTP {resp.status} {parsed}")
            if not isinstance(parsed, dict):
                raise KomecoApiError(f"Unexpected API response type: {type(parsed)!r}")
            _LOGGER.debug("Signed request succeeded method=%s path=%s status=%s", method.upper(), full_path, resp.status)
            return parsed

    def _make_uhash(self, email: str) -> str:
        normalized = email.strip().lower()
        salt = os.urandom(8)
        passphrase = COGNITO_UHASH_PASSPHRASE.encode("utf-8")

        target_len = 32 + 16
        data = b""
        last = b""
        while len(data) < target_len:
            last = hashlib.md5(last + passphrase + salt).digest()
            data += last
        key = data[:32]
        iv = data[32:48]

        cipher = AES.new(key, AES.MODE_CBC, iv=iv)
        ciphertext = cipher.encrypt(pad(normalized.encode("utf-8"), AES.block_size))
        openssl_blob = b"Salted__" + salt + ciphertext
        return base64.b64encode(openssl_blob).decode("ascii")

    def _sigv4_headers(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        service: str,
    ) -> dict[str, str]:
        if not (self._aws_access_key and self._aws_secret_key and self._aws_session_token):
            raise KomecoApiError("AWS credentials are missing")

        now = dt.datetime.now(dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        parsed = urlparse(url)

        signing_headers = {k.lower(): v.strip() for k, v in headers.items()}
        signing_headers["host"] = parsed.netloc
        signing_headers["x-amz-date"] = amz_date
        signing_headers["x-amz-security-token"] = self._aws_session_token

        sorted_names = sorted(signing_headers)
        canonical_headers = "".join(f"{name}:{signing_headers[name]}\n" for name in sorted_names)
        signed_headers = ";".join(sorted_names)
        payload_hash = hashlib.sha256(body).hexdigest()

        canonical_request = "\n".join(
            [
                method.upper(),
                self._canonical_uri(parsed.path or "/"),
                self._normalize_query(parsed.query),
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        credential_scope = f"{datestamp}/{AWS_REGION}/{service}/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )

        signing_key = self._get_signature_key(
            secret_key=self._aws_secret_key,
            datestamp=datestamp,
            region=AWS_REGION,
            service=service,
        )
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self._aws_access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )

        result = dict(headers)
        result["Host"] = parsed.netloc
        result["X-Amz-Date"] = amz_date
        result["X-Amz-Security-Token"] = self._aws_session_token
        result["X-Amz-Content-Sha256"] = payload_hash
        result["Authorization"] = authorization
        return result

    def _get_signature_key(self, *, secret_key: str, datestamp: str, region: str, service: str) -> bytes:
        k_date = self._sign(("AWS4" + secret_key).encode("utf-8"), datestamp)
        k_region = self._sign(k_date, region)
        k_service = self._sign(k_region, service)
        return self._sign(k_service, "aws4_request")

    def _sign(self, key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    def _canonical_uri(self, path: str) -> str:
        if not path:
            return "/"
        segments = path.split("/")
        return "/".join(quote(seg, safe="-_.~") for seg in segments)

    def _normalize_query(self, query: str) -> str:
        pairs = parse_qsl(query, keep_blank_values=True)
        encoded = sorted((quote(k, safe="-_.~"), quote(v, safe="-_.~")) for k, v in pairs)
        return "&".join(f"{k}={v}" for k, v in encoded)
