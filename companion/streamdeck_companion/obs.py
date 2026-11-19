"""OBS Studio over obs-websocket 5 (built into OBS 28 and newer): one request per call.

OBS: Tools > WebSocket Server Settings > Enable WebSocket server (default port 4455; a password is set there, too).
A request is any obs-websocket 5 request type, for example {"request": "SetCurrentProgramScene", "data": {"sceneName": "Game"}};
see the obs-websocket protocol documentation for the list. Each call opens a connection, identifies, sends the request,
reads the answer and closes: a few milliseconds locally, and nothing to keep alive between key presses.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid

from .wsclient import WebSocket, WebSocketError

SUBPROTOCOL = "obswebsocket.json"
RPC_VERSION = 1
OBS_DEFAULTS = {"host": "127.0.0.1", "port": 4455, "password": ""}


class ObsError(RuntimeError):
    pass


def auth_string(password: str, salt: str, challenge: str) -> str:
    """The answer to the server's authentication challenge: base64(sha256(base64(sha256(password + salt)) + challenge))."""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode("utf-8")).digest()).decode("ascii")
    return base64.b64encode(hashlib.sha256((secret + challenge).encode("utf-8")).digest()).decode("ascii")


def obs_request(config: dict, request: str, data: dict | None = None, timeout: float = 5.0, connect=WebSocket) -> dict:
    """Sends one request to OBS and returns its `responseData` ({} if there is none). Raises ObsError with a readable message."""
    cfg = {**OBS_DEFAULTS, **(config or {})}
    try:
        with connect(cfg["host"], int(cfg["port"]), "/", SUBPROTOCOL, timeout) as ws:
            hello = _recv(ws, 0)
            identify = {"rpcVersion": RPC_VERSION, "eventSubscriptions": 0}  # no events: only the answer to our request
            auth = hello.get("authentication")
            if auth:
                if not cfg["password"]:
                    raise ObsError("OBS asks for a password: set obs.password in deck.json (General settings)")
                identify["authentication"] = auth_string(cfg["password"], auth["salt"], auth["challenge"])
            ws.send_text(json.dumps({"op": 1, "d": identify}))
            try:
                _recv(ws, 2)
            except WebSocketError:  # OBS closes the connection when the password is wrong
                raise ObsError("OBS closed the connection while identifying: is the password right?") from None
            request_id = uuid.uuid4().hex
            body = {"requestType": request, "requestId": request_id}
            if data:
                body["requestData"] = data
            ws.send_text(json.dumps({"op": 6, "d": body}))
            while True:  # (events are off, but be tolerant of anything that is not our answer)
                message = json.loads(ws.recv_text())
                if message.get("op") == 7 and message["d"].get("requestId") == request_id:
                    break
            reply = message["d"]
            status = reply.get("requestStatus", {})
            if not status.get("result"):
                raise ObsError(f"OBS refused {request}: {status.get('comment') or 'error ' + str(status.get('code'))}")
            return reply.get("responseData") or {}
    except WebSocketError as e:
        text = str(e)
        if "could not connect" in text or "refused" in text.lower():
            text += " (is OBS running with the WebSocket server enabled?)"
        raise ObsError(text) from None
    except (KeyError, TypeError, ValueError) as e:  # a reply that is not what the protocol says
        raise ObsError(f"unexpected answer from OBS: {e!r}") from None


def _recv(ws, expected_op: int) -> dict:
    message = json.loads(ws.recv_text())
    if message.get("op") != expected_op:
        raise ObsError(f"unexpected message from OBS (op {message.get('op')}, expected {expected_op})")
    return message.get("d") or {}
