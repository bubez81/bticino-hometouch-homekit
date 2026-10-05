"""Client for the listener's network API (REST + Server-Sent Events)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

API_PREFIX = "/api/v1"


class ApiError(Exception):
    """The listener answered with an error or could not be reached."""


class AuthError(ApiError):
    """The token was rejected or this client is not allowed."""


def parse_sse(lines: list[str]) -> dict[str, Any] | None:
    """Return the JSON payload of one SSE block, or None for comments/keepalives."""
    data = [line[5:].lstrip() for line in lines if line.startswith("data:")]
    if not data:
        return None
    try:
        event = json.loads("\n".join(data))
    except ValueError:
        return None
    return event if isinstance(event, dict) and "type" in event else None


class HometouchApi:
    """Thin async client; one instance per config entry."""

    def __init__(self, session: aiohttp.ClientSession, host: str, port: int, token: str) -> None:
        self._session = session
        self._base = f"http://{host}:{port}{API_PREFIX}"
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> aiohttp.ClientResponse:
        try:
            response = await self._session.request(
                method, self._base + path, headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=10), **kwargs)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ApiError(f"listener unreachable: {err}") from err
        if response.status in (401, 403):
            response.release()
            raise AuthError(f"access denied ({response.status})")
        return response

    async def _json(self, method: str, path: str) -> dict[str, Any]:
        response = await self._request(method, path)
        try:
            payload = await response.json(content_type=None)
        except (aiohttp.ClientError, ValueError) as err:
            raise ApiError(f"invalid response: {err}") from err
        finally:
            response.release()
        if not isinstance(payload, dict):
            raise ApiError("invalid response")
        if response.status >= 400 and not payload.get("error"):
            raise ApiError(f"HTTP {response.status}")
        return payload

    async def info(self) -> dict[str, Any]:
        return await self._json("GET", "/info")

    async def state(self) -> dict[str, Any]:
        return await self._json("GET", "/state")

    async def open_entrance(self, entrance: str) -> dict[str, Any]:
        return await self._json("POST", f"/entrances/{entrance}/open")

    async def snapshot(self, width: int | None = None, height: int | None = None) -> bytes | None:
        query = f"?width={width}&height={height}" if width and height else ""
        response = await self._request("GET", f"/snapshot.jpg{query}")
        try:
            if response.status != 200:
                return None
            return await response.read()
        finally:
            response.release()

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield events until the stream ends; raises ApiError on failure."""
        try:
            response = await self._session.get(
                self._base + "/events", headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=60))
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ApiError(f"event stream unreachable: {err}") from err
        if response.status in (401, 403):
            response.release()
            raise AuthError(f"access denied ({response.status})")
        if response.status != 200:
            response.release()
            raise ApiError(f"event stream HTTP {response.status}")
        block: list[str] = []
        try:
            async for raw in response.content:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    block.append(line)
                    continue
                event = parse_sse(block)
                block = []
                if event is not None:
                    yield event
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ApiError(f"event stream interrupted: {err}") from err
        finally:
            response.release()
