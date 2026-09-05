import json

import httpx
import pytest

from kindroid_relay.kindroid_api import KindroidAPIError, KindroidClient


def test_group_api_request_shapes() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("get-chat-messages"):
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {
                            "id": "m1",
                            "sender": "ai",
                            "display_name": "Hannah",
                            "timestamp": 10,
                            "message": "Hello",
                        }
                    ],
                    "pagination": {"hasMore": False, "lastTimestamp": 10},
                },
            )
        if request.url.path.endswith("groupchats-get-turn"):
            return httpx.Response(200, text="ai1")
        return httpx.Response(200, text="")

    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://example.test/v1"
    )
    client = KindroidClient("kn_test", client=http)

    page = client.get_messages_page("group", start_after_timestamp=9)
    client.post_user_message("group", "Hi")
    client.activate_user_persona(
        "persona-1", name="Codex Test", gender="Nonbinary", backstory="External"
    )
    assert client.get_turn("group", allow_user=False) == "ai1"
    client.generate_ai_response("group", "ai1")

    assert page.messages[0].display_name == "Hannah"
    assert page.messages[0].sender_type == "ai"
    assert requests[0].url.params["group_id"] == "group"
    assert json.loads(requests[1].content) == {"group_id": "group", "message": "Hi"}
    assert json.loads(requests[2].content) == {
        "active_persona_id": "persona-1",
        "user_name": "Codex Test",
        "user_gender": "Nonbinary",
        "user_backstory": "External",
    }
    assert json.loads(requests[3].content)["allow_user"] is False
    assert json.loads(requests[4].content)["ai_id"] == "ai1"


def test_history_rate_limit_is_not_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            429,
            text="Too Many Requests (API limit)",
            headers={"Retry-After": "7200"},
        )

    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://example.test/v1"
    )
    client = KindroidClient("kn_test", client=http)

    with pytest.raises(KindroidAPIError, match="Too Many Requests") as caught:
        client.get_messages_page("group")

    assert attempts == 1
    assert caught.value.retry_after_seconds == 7200
