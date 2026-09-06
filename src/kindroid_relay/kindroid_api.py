from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import ceil
from typing import Any

import httpx

from .models import ChatMessage


class KindroidAPIError(RuntimeError):
    def __init__(
        self,
        operation: str,
        status_code: int | None,
        detail: str,
        *,
        retry_after_seconds: int | None = None,
    ):
        self.operation = operation
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"{operation} failed ({status_code or 'network'}): {detail}")


def _retry_after_seconds(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max(0, ceil((retry_at - datetime.now(timezone.utc)).total_seconds()))


@dataclass(frozen=True, slots=True)
class MessagePage:
    messages: list[ChatMessage]
    has_more: bool
    last_timestamp: int | None


class KindroidClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.kindroid.ai/v1",
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(180.0, connect=15.0),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json, text/plain",
                "User-Agent": "kindroid-codex-relay/0.1",
            },
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "KindroidClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        retry_safe: bool,
        **kwargs: Any,
    ) -> httpx.Response:
        attempts = 4 if retry_safe else 1
        for attempt in range(attempts):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                if attempt + 1 < attempts:
                    time.sleep(2**attempt)
                    continue
                raise KindroidAPIError(operation, None, exc.__class__.__name__) from exc

            if response.status_code < 400:
                return response
            if retry_safe and response.status_code in {429, 500} and attempt + 1 < attempts:
                retry_after = _retry_after_seconds(
                    response.headers.get("Retry-After", "")
                )
                delay = retry_after if retry_after is not None else 2**attempt
                time.sleep(min(delay, 30))
                continue
            # Response bodies are untrusted and may echo credentials or private
            # content. Keep persisted/logged failures to the protocol reason.
            detail = response.reason_phrase or "HTTP error"
            raise KindroidAPIError(
                operation,
                response.status_code,
                detail,
                retry_after_seconds=_retry_after_seconds(
                    response.headers.get("Retry-After", "")
                ),
            )
        raise AssertionError("unreachable")

    def get_messages_page(
        self,
        group_id: str,
        *,
        limit: int = 100,
        start_after_timestamp: int | None = None,
    ) -> MessagePage:
        params: dict[str, str | int] = {"group_id": group_id, "limit": limit}
        if start_after_timestamp is not None:
            params["start_after_timestamp"] = start_after_timestamp
        response = self._request(
            "GET",
            "/get-chat-messages",
            operation="get chat messages",
            # This endpoint has a strict request quota. Let the relay schedule
            # another read instead of multiplying one read into four HTTP
            # attempts when Kindroid returns 429.
            retry_safe=False,
            params=params,
        )
        try:
            payload = response.json()
            pagination = payload.get("pagination", {})
            messages = [
                ChatMessage.from_api(item) for item in payload.get("messages", [])
            ]
        except (TypeError, ValueError) as exc:
            raise KindroidAPIError(
                "get chat messages", response.status_code, "invalid JSON response"
            ) from exc
        last_timestamp = pagination.get("lastTimestamp")
        return MessagePage(
            messages=messages,
            has_more=bool(pagination.get("hasMore", False)),
            last_timestamp=int(last_timestamp) if last_timestamp is not None else None,
        )

    def post_user_message(self, group_id: str, message: str) -> None:
        self._request(
            "POST",
            "/groupchats-user-message",
            operation="post group user message",
            retry_safe=False,
            json={"group_id": group_id, "message": message},
        )

    def activate_user_persona(
        self,
        persona_id: str,
        *,
        name: str,
        gender: str,
        backstory: str,
    ) -> None:
        # active_persona_id is used by the Kindroid web app but is not part of
        # the documented external API schema.
        self._request(
            "POST",
            "/update-info",
            operation=f"activate user persona {name!r}",
            retry_safe=True,
            json={
                "active_persona_id": persona_id,
                "user_name": name,
                "user_gender": gender,
                "user_backstory": backstory,
            },
        )

    def get_turn(self, group_id: str, *, allow_user: bool) -> str | None:
        response = self._request(
            "POST",
            "/groupchats-get-turn",
            operation="get group turn",
            retry_safe=True,
            json={"group_id": group_id, "allow_user": allow_user},
        )
        value = response.text.strip().strip('"')
        return value or None

    def generate_ai_response(self, group_id: str, ai_id: str) -> str:
        response = self._request(
            "POST",
            "/groupchats-ai-response",
            operation="generate group AI response",
            retry_safe=False,
            json={"group_id": group_id, "ai_id": ai_id, "stream": False},
        )
        return response.text.strip()
