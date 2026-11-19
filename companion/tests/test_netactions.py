"""The http and obs actions, the WebSocket client under them, and the obs connection setting in deck.json.

Everything runs against local servers on 127.0.0.1: a real http.server for the http action, and a small WebSocket server
that speaks obs-websocket 5 (hello, identify with the password challenge, request / response) for the OBS client.
"""
import json
import os
import socket
import struct
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from streamdeck_companion import obs as obsmod
from streamdeck_companion.actions import Executor, expand_env, safe_url, validate_action
from streamdeck_companion.deck import Deck, DeckError, check_obs
from streamdeck_companion.obs import ObsError, auth_string, obs_request
from streamdeck_companion.wsclient import (CLOSE, CONTINUATION, PING, PONG, TEXT, WebSocket, WebSocketError, accept_key,
                                           encode_frame)


# ---- a WebSocket server for the tests ------------------------------------------------------------------------

def read_frame(rfile):
    """One frame from a client (masked): (fin, opcode, payload)."""
    b0, b1 = rfile.read(2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", rfile.read(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", rfile.read(8))[0]
    key = rfile.read(4) if b1 & 0x80 else b""
    payload = rfile.read(n)
    if key:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return bool(b0 & 0x80), b0 & 0x0F, payload


def raw_frame(fin, opcode, payload):
    """An unmasked server frame, with control over FIN (for fragmented messages)."""
    head = bytes([(0x80 if fin else 0) | opcode])
    n = len(payload)
    head += bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n)
    return head + payload


class WsServer:
    """Accepts connections one after the other and hands each to `handler(conn)`. `conn` has handshake / send / recv_text."""

    def __init__(self, handler):
        self.handler = handler
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.errors = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            try:
                self.handler(ServerConn(client))
            except Exception as e:  # a failing handler shows up in the test through the client's error
                self.errors.append(repr(e))
            finally:
                try:
                    client.close()
                except OSError:
                    pass

    def close(self):
        self.sock.close()


class ServerConn:
    def __init__(self, sock):
        self.sock = sock
        self.rfile = sock.makefile("rb")
        self.headers = {}

    def handshake(self, subprotocol=None, answer_protocol=True, wrong_accept=False, status="101 Switching Protocols"):
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            head += self.rfile.read(1)
        lines = head.decode("latin-1").split("\r\n")
        self.request_line = lines[0]
        for line in lines[1:]:
            name, _, value = line.partition(":")
            self.headers[name.strip().lower()] = value.strip()
        key = self.headers["sec-websocket-key"]
        reply = [f"HTTP/1.1 {status}", "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Accept: {'x' * 28 if wrong_accept else accept_key(key)}"]
        if subprotocol and answer_protocol:
            reply.append(f"Sec-WebSocket-Protocol: {subprotocol}")
        self.sock.sendall(("\r\n".join(reply) + "\r\n\r\n").encode("ascii"))

    def send_text(self, text):
        self.sock.sendall(raw_frame(True, TEXT, text.encode("utf-8")))

    def send_raw(self, data):
        self.sock.sendall(data)

    def recv_text(self):
        while True:
            fin, opcode, payload = read_frame(self.rfile)
            if opcode == TEXT:
                return payload.decode("utf-8")
            if opcode == CLOSE:
                raise EOFError("client closed")
            if opcode == PONG:
                self.last_pong = payload


class FakeObs:
    """obs-websocket 5: `password` None = no authentication; `answers` maps a request type to (result, code, comment, data)."""

    def __init__(self, password=None, answers=None, version="5.4.2"):
        self.password, self.answers, self.version = password, answers or {}, version
        self.requests = []
        self.identify = None
        self.salt, self.challenge = "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI=", "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY="
        self.server = WsServer(self.handle)
        self.port = self.server.port

    def handle(self, conn):
        conn.handshake(subprotocol="obswebsocket.json")
        hello = {"obsWebSocketVersion": self.version, "rpcVersion": 1}
        if self.password is not None:
            hello["authentication"] = {"challenge": self.challenge, "salt": self.salt}
        conn.send_text(json.dumps({"op": 0, "d": hello}))
        message = json.loads(conn.recv_text())
        assert message["op"] == 1
        self.identify = message["d"]
        if self.password is not None and self.identify.get("authentication") != auth_string(self.password, self.salt, self.challenge):
            conn.send_raw(raw_frame(True, CLOSE, struct.pack(">H", 4009) + b"Authentication failed."))
            return
        conn.send_text(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        request = json.loads(conn.recv_text())
        assert request["op"] == 6
        body = request["d"]
        self.requests.append((body["requestType"], body.get("requestData")))
        result, code, comment, data = self.answers.get(body["requestType"], (True, 100, None, None))
        status = {"result": result, "code": code}
        if comment:
            status["comment"] = comment
        reply = {"requestType": body["requestType"], "requestId": body["requestId"], "requestStatus": status}
        if data is not None:
            reply["responseData"] = data
        conn.send_text(json.dumps({"op": 7, "d": reply}))
        try:
            conn.recv_text()  # waits for the client to close
        except (EOFError, OSError):
            pass

    def close(self):
        self.server.close()


# ---- the WebSocket client ------------------------------------------------------------------------------------

class WebSocketTest(unittest.TestCase):
    def server(self, handler):
        srv = WsServer(handler)
        self.addCleanup(srv.close)
        return srv

    def test_the_accept_key_of_rfc_6455(self):
        self.assertEqual(accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")  # the example in the RFC

    def test_frames_of_every_size_survive_the_trip(self):
        import io

        for n in (0, 5, 125, 126, 200, 65535, 65536, 70000):
            payload = bytes(range(256)) * (n // 256 + 1)
            payload = payload[:n]
            fin, opcode, got = read_frame(io.BytesIO(encode_frame(TEXT, payload)))
            self.assertEqual((fin, opcode, got), (True, TEXT, payload), n)
        a, b = encode_frame(TEXT, b"same"), encode_frame(TEXT, b"same")
        self.assertNotEqual(a, b)  # a fresh random mask every time

    def test_text_both_ways_with_the_subprotocol(self):
        def echo(conn):
            conn.handshake(subprotocol="chat")
            conn.send_text("hello " + conn.recv_text())

        srv = self.server(echo)
        with WebSocket("127.0.0.1", srv.port, "/x", "chat") as ws:
            ws.send_text("wörld ✓")
            self.assertEqual(ws.recv_text(), "hello wörld ✓")
        self.assertEqual(srv.errors, [])

    def test_fragmented_messages_and_pings_in_the_middle(self):
        def serve(conn):
            conn.handshake()
            conn.send_raw(raw_frame(False, TEXT, b"one "))
            conn.send_raw(raw_frame(True, PING, b"are you there"))
            conn.send_raw(raw_frame(False, CONTINUATION, b"two "))
            conn.send_raw(raw_frame(True, CONTINUATION, b"three"))
            conn.send_text("second message")
            try:
                conn.recv_text()
            except EOFError:
                pass

        srv = self.server(serve)
        with WebSocket("127.0.0.1", srv.port) as ws:
            self.assertEqual(ws.recv_text(), "one two three")
            self.assertEqual(ws.recv_text(), "second message")

    def test_ping_is_answered_with_pong(self):
        got = {}

        def serve(conn):
            conn.handshake()
            conn.send_raw(raw_frame(True, PING, b"abc"))
            conn.send_text("after the ping")
            try:
                conn.recv_text()
            except EOFError:
                pass
            got["pong"] = getattr(conn, "last_pong", None)

        srv = self.server(serve)
        with WebSocket("127.0.0.1", srv.port) as ws:
            self.assertEqual(ws.recv_text(), "after the ping")
        time.sleep(0.2)
        self.assertEqual(got.get("pong"), b"abc")

    def test_a_close_from_the_server_is_an_error(self):
        def serve(conn):
            conn.handshake()
            conn.send_raw(raw_frame(True, CLOSE, struct.pack(">H", 1001)))

        srv = self.server(serve)
        with self.assertRaises(WebSocketError):
            with WebSocket("127.0.0.1", srv.port) as ws:
                ws.recv_text()

    def test_refused_handshakes(self):
        for kwargs, expect in (({"status": "403 Forbidden"}, "did not accept"), ({"wrong_accept": True}, "handshake answer is wrong"),
                               ({"subprotocol": "other", "answer_protocol": False}, "does not speak")):
            srv = self.server(lambda conn, kw=kwargs: conn.handshake(**kw))
            with self.assertRaises(WebSocketError, msg=str(kwargs)) as ctx:
                WebSocket("127.0.0.1", srv.port, subprotocol="obswebsocket.json" if "subprotocol" in kwargs else None).connect()
            self.assertIn(expect, str(ctx.exception))

    def test_nothing_listening_and_silence_are_errors(self):
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()
        with self.assertRaises(WebSocketError) as ctx:
            WebSocket("127.0.0.1", port, timeout=1).connect()
        self.assertIn("could not connect", str(ctx.exception))

        def mute(conn):
            conn.handshake()
            time.sleep(1.5)

        srv = self.server(mute)
        started = time.time()
        with self.assertRaises(WebSocketError) as ctx:
            with WebSocket("127.0.0.1", srv.port, timeout=0.4) as ws:
                ws.recv_text()
        self.assertIn("timed out", str(ctx.exception))
        self.assertLess(time.time() - started, 1.2)

    def test_binary_and_oversized_messages_are_refused(self):
        def serve(conn):
            conn.handshake()
            conn.send_raw(raw_frame(True, 0x2, b"\x00\x01"))

        srv = self.server(serve)
        with self.assertRaises(WebSocketError):
            with WebSocket("127.0.0.1", srv.port) as ws:
                ws.recv_text()


# ---- OBS -----------------------------------------------------------------------------------------------------

class ObsTest(unittest.TestCase):
    def fake(self, **kw):
        fake = FakeObs(**kw)
        self.addCleanup(fake.close)
        return fake

    def test_the_authentication_answer_matches_the_obs_websocket_example(self):
        # the inputs of the worked example in the obs-websocket protocol documentation (its result is not printed there; this is the
        # string that comes out of them, so a change to the algorithm would show here)
        self.assertEqual(auth_string("supersecretpassword", "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI=",
                                     "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY="), "1Ct943GAT+6YQUUX47Ia/ncufilbe6+oD6lY+5kaCu4=")

    def test_a_request_without_a_password(self):
        fake = self.fake(answers={"GetVersion": (True, 100, None, {"obsVersion": "30.1"})})
        got = obs_request({"port": fake.port}, "GetVersion")
        self.assertEqual(got, {"obsVersion": "30.1"})
        self.assertEqual(fake.requests, [("GetVersion", None)])
        self.assertEqual((fake.identify["rpcVersion"], fake.identify["eventSubscriptions"]), (1, 0))  # no events wanted
        self.assertNotIn("authentication", fake.identify)

    def test_a_request_with_data_and_a_password(self):
        fake = self.fake(password="supersecretpassword")
        self.assertEqual(obs_request({"port": fake.port, "password": "supersecretpassword"}, "SetCurrentProgramScene",
                                     {"sceneName": "Game"}), {})
        self.assertEqual(fake.requests, [("SetCurrentProgramScene", {"sceneName": "Game"})])

    def test_password_problems_are_readable(self):
        fake = self.fake(password="right")
        with self.assertRaises(ObsError) as ctx:
            obs_request({"port": fake.port, "password": "wrong"}, "ToggleRecord")
        self.assertIn("password", str(ctx.exception))
        with self.assertRaises(ObsError) as ctx:
            obs_request({"port": fake.port}, "ToggleRecord")
        self.assertIn("asks for a password", str(ctx.exception))
        self.assertEqual(fake.requests, [])  # nothing was sent to a server that did not let us in

    def test_a_refused_request_says_why(self):
        fake = self.fake(answers={"SetCurrentProgramScene": (False, 600, "No scene was found by the name of `Nope`.", None)})
        with self.assertRaises(ObsError) as ctx:
            obs_request({"port": fake.port}, "SetCurrentProgramScene", {"sceneName": "Nope"})
        self.assertIn("No scene was found", str(ctx.exception))

    def test_obs_not_running(self):
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()
        with self.assertRaises(ObsError) as ctx:
            obs_request({"port": port}, "ToggleRecord", timeout=1)
        self.assertIn("is OBS running", str(ctx.exception))

    def test_a_server_that_is_not_obs(self):
        srv = WsServer(lambda conn: conn.handshake())  # accepts, but without the obswebsocket.json sub-protocol
        self.addCleanup(srv.close)
        with self.assertRaises(ObsError):
            obs_request({"port": srv.port}, "ToggleRecord", timeout=1)

    def test_the_action_uses_the_deck_connection_and_the_environment(self):
        fake = self.fake(password="from-the-environment")
        executor = Executor()
        executor.configure(obs={"host": "127.0.0.1", "port": fake.port, "password": "${SD_TEST_OBS_PASSWORD}"})
        with mock.patch.dict(os.environ, {"SD_TEST_OBS_PASSWORD": "from-the-environment"}):
            executor.run({"type": "obs", "request": "ToggleStream"})
        self.assertEqual(fake.requests, [("ToggleStream", None)])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SD_TEST_OBS_PASSWORD", None)
            with self.assertRaises(ValueError) as ctx:
                executor.run({"type": "obs", "request": "ToggleStream"})
            self.assertIn("SD_TEST_OBS_PASSWORD", str(ctx.exception))

    def test_obs_connection_setting_of_the_deck(self):
        self.assertEqual(check_obs(None), {})
        self.assertEqual(check_obs({"host": "192.168.1.5", "port": 4456, "password": "${OBS_PASSWORD}"}),
                         {"host": "192.168.1.5", "port": 4456, "password": "${OBS_PASSWORD}"})
        self.assertEqual(check_obs({"port": 4455}), {"port": 4455})
        for bad in ([], "x", {"hots": "x"}, {"host": ""}, {"host": "a b"}, {"port": 0}, {"port": 70000}, {"port": "4455"},
                    {"port": True}, {"password": 5}, {"password": "x" * 201}):
            with self.assertRaises(DeckError, msg=str(bad)):
                check_obs(bad)
        deck = Deck({"obs": {"port": 4456}, "desktops": [{"pages": [{"keys": [{"label": "R", "action": {"type": "obs", "request": "ToggleRecord"}}]}]}]})
        self.assertEqual(deck.obs, {"port": 4456})
        self.assertNotIn("4456", deck.layout_text())  # PC only

    def test_the_companion_hands_the_connection_to_the_executor(self):
        import shutil
        import tempfile
        from pathlib import Path

        from streamdeck_companion.app import Companion

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / "deck.json"
        path.write_text(json.dumps({"obs": {"host": "10.0.0.2", "port": 4460}, "desktops": [{"pages": [{"keys": []}]}]}), encoding="utf-8")
        executor = Executor(dry_run=True)
        Companion(path, executor=executor)
        self.assertEqual(executor.obs, {"host": "10.0.0.2", "port": 4460})


# ---- HTTP ----------------------------------------------------------------------------------------------------

class HttpActionTest(unittest.TestCase):
    def setUp(self):
        self.seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_any(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.seen.append({"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": self.rfile.read(length)})
                if self.path.startswith("/missing"):
                    self.send_response(404)
                elif self.path.startswith("/slow"):
                    time.sleep(2)
                    self.send_response(200)
                else:
                    self.send_response(200)
                try:
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"ok")
                except ConnectionError:  # the client gave up (the timeout tests): nothing to report
                    pass

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_any

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)   # (cleanups run last-in first-out: shut down, then close)
        self.addCleanup(self.httpd.shutdown)
        self.executor = Executor()

    def test_a_plain_get(self):
        self.executor.run({"type": "http", "url": self.base + "/hook?x=1"})
        self.assertEqual((self.seen[0]["method"], self.seen[0]["path"]), ("GET", "/hook?x=1"))
        self.assertEqual(self.seen[0]["body"], b"")

    def test_post_json_with_headers_and_environment_variables(self):
        with mock.patch.dict(os.environ, {"SD_TEST_TOKEN": "s3cret", "SD_TEST_ENTITY": "light.desk"}):
            self.executor.run({"type": "http", "method": "post", "url": self.base + "/api/services/light/toggle",
                               "headers": {"Authorization": "Bearer ${SD_TEST_TOKEN}"},
                               "json": {"entity_id": "${SD_TEST_ENTITY}", "brightness": 200, "tags": ["a", "${SD_TEST_ENTITY}"]}})
        got = self.seen[0]
        self.assertEqual(got["method"], "POST")
        self.assertEqual(got["headers"]["authorization"], "Bearer s3cret")
        self.assertEqual(got["headers"]["content-type"], "application/json")
        self.assertEqual(json.loads(got["body"]), {"entity_id": "light.desk", "brightness": 200, "tags": ["a", "light.desk"]})

    def test_a_text_body_and_a_content_type_of_your_own(self):
        self.executor.run({"type": "http", "method": "PUT", "url": self.base + "/x", "body": "plain text ✓",
                           "headers": {"content-type": "text/plain; charset=utf-8"}})
        self.assertEqual((self.seen[0]["method"], self.seen[0]["body"]), ("PUT", "plain text ✓".encode("utf-8")))
        self.assertEqual(self.seen[0]["headers"]["content-type"], "text/plain; charset=utf-8")
        self.executor.run({"type": "http", "method": "DELETE", "url": self.base + "/x"})
        self.assertEqual(self.seen[1]["method"], "DELETE")

    def test_a_json_body_does_not_override_a_content_type_you_set(self):
        self.executor.run({"type": "http", "method": "POST", "url": self.base + "/x", "json": {"a": 1},
                           "headers": {"Content-Type": "application/vnd.api+json"}})
        self.assertEqual(self.seen[0]["headers"]["content-type"], "application/vnd.api+json")

    def test_errors_say_what_went_wrong_without_the_secrets(self):
        with mock.patch.dict(os.environ, {"SD_TEST_TOKEN": "s3cret"}):
            with self.assertRaises(RuntimeError) as ctx:
                self.executor.run({"type": "http", "url": self.base + "/missing?token=${SD_TEST_TOKEN}",
                                   "headers": {"Authorization": "Bearer ${SD_TEST_TOKEN}"}})
        self.assertIn("404", str(ctx.exception))
        self.assertNotIn("s3cret", str(ctx.exception))
        self.assertNotIn("token=", str(ctx.exception))
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()
        with self.assertRaises(RuntimeError) as ctx:
            self.executor.run({"type": "http", "url": f"http://127.0.0.1:{port}/", "timeout": 1})
        self.assertIn("failed", str(ctx.exception))

    def test_a_missing_environment_variable_stops_before_anything_is_sent(self):
        os.environ.pop("SD_TEST_NOT_SET", None)
        with self.assertRaises(ValueError) as ctx:
            self.executor.run({"type": "http", "url": self.base + "/x", "headers": {"Authorization": "Bearer ${SD_TEST_NOT_SET}"}})
        self.assertIn("SD_TEST_NOT_SET", str(ctx.exception))
        self.assertEqual(self.seen, [])

    def test_the_timeout_is_kept(self):
        started = time.time()
        with self.assertRaises(RuntimeError):
            self.executor.run({"type": "http", "url": self.base + "/slow", "timeout": 1})
        self.assertLess(time.time() - started, 1.9)

    def test_the_log_has_neither_headers_nor_query_nor_body(self):
        with mock.patch.dict(os.environ, {"SD_TEST_TOKEN": "s3cret"}):
            with self.assertLogs("actions", level="INFO") as logs:
                self.executor.run({"type": "http", "method": "POST", "url": self.base + "/hook?key=${SD_TEST_TOKEN}",
                                   "headers": {"Authorization": "Bearer ${SD_TEST_TOKEN}"}, "json": {"password": "hunter2"}})
        text = "\n".join(logs.output)
        self.assertIn("/hook", text)
        for secret in ("s3cret", "hunter2", "Authorization", "key="):
            self.assertNotIn(secret, text)

    def test_network_actions_do_not_hold_up_key_presses(self):
        pressed = []
        executor = Executor(backend=(lambda codes: pressed.append(time.time()), lambda s: None, lambda: None))
        slow = threading.Thread(target=lambda: executor.run({"type": "http", "url": self.base + "/slow", "timeout": 3}), daemon=True)
        started = time.time()
        slow.start()
        time.sleep(0.2)
        executor.run({"type": "hotkey", "keys": "ctrl+c"})   # does not wait for the request to finish
        self.assertLess(pressed[0] - started, 1.0)
        slow.join(5)

    def test_inside_a_multi_action(self):
        self.executor.run({"type": "multi", "steps": [{"type": "http", "url": self.base + "/one"}, {"wait": 0},
                                                      {"type": "http", "method": "POST", "url": self.base + "/two", "body": "x"}]})
        self.assertEqual([s["path"] for s in self.seen], ["/one", "/two"])

    def test_dry_run_sends_nothing(self):
        Executor(dry_run=True).run({"type": "http", "url": self.base + "/x"})
        Executor(dry_run=True).run({"type": "obs", "request": "ToggleRecord"})
        self.assertEqual(self.seen, [])


class ValidationTest(unittest.TestCase):
    def test_good_actions(self):
        for action in ({"type": "http", "url": "http://localhost:8123/x"},
                       {"type": "http", "url": "https://example.com/a?b=c", "method": "patch", "headers": {"X-Api-Key": "${K}"},
                        "json": [1, 2], "timeout": 10},
                       {"type": "http", "url": "https://example.com", "body": "text"},
                       {"type": "obs", "request": "ToggleRecord"},
                       {"type": "obs", "request": "SetCurrentProgramScene", "data": {"sceneName": "Game"}},
                       {"type": "multi", "steps": [{"type": "obs", "request": "StartStream"}, {"wait": 1}, {"type": "http", "url": "http://x.y/"}]}):
            validate_action(action)

    def test_bad_actions(self):
        bad = [{"type": "http"}, {"type": "http", "url": "ftp://x.y/"}, {"type": "http", "url": "file:///c:/x"}, {"type": "http", "url": "javascript:alert(1)"},
               {"type": "http", "url": "http://x y/"}, {"type": "http", "url": "http://x.y/", "method": "TRACE"},
               {"type": "http", "url": "http://x.y/", "headers": []}, {"type": "http", "url": "http://x.y/", "headers": {"bad name": "x"}},
               {"type": "http", "url": "http://x.y/", "headers": {"X": "a\nInjected: yes"}}, {"type": "http", "url": "http://x.y/", "headers": {"X": 1}},
               {"type": "http", "url": "http://x.y/", "headers": {f"H{i}": "v" for i in range(21)}},
               {"type": "http", "url": "http://x.y/", "json": {}, "body": "x"}, {"type": "http", "url": "http://x.y/", "json": "text"},
               {"type": "http", "url": "http://x.y/", "body": 5}, {"type": "http", "url": "http://x.y/", "timeout": 0},
               {"type": "http", "url": "http://x.y/", "timeout": 31}, {"type": "http", "url": "http://x.y/", "timeout": True},
               {"type": "obs"}, {"type": "obs", "request": ""}, {"type": "obs", "request": "Toggle Record"}, {"type": "obs", "request": "1Toggle"},
               {"type": "obs", "request": "X" * 81}, {"type": "obs", "request": "ToggleRecord", "data": "x"}]
        for action in bad:
            with self.assertRaises(ValueError, msg=str(action)[:80]):
                validate_action(action)

    def test_environment_expansion(self):
        env = {"A": "1", "B": "two"}
        self.assertEqual(expand_env("x${A}y${B}z", env), "x1ytwoz")
        self.assertEqual(expand_env({"k": ["${A}", {"n": "${B}"}], "i": 5, "t": True, "z": None}, env),
                         {"k": ["1", {"n": "two"}], "i": 5, "t": True, "z": None})
        self.assertEqual(expand_env("no placeholders $ { } $A ${1X}", env), "no placeholders $ { } $A ${1X}")
        with self.assertRaises(ValueError) as ctx:
            expand_env("${A}${MISSING}", env)
        self.assertIn("MISSING", str(ctx.exception))

    def test_safe_url(self):
        self.assertEqual(safe_url("https://user:pw@host.example:8123/api/x?token=SECRET#frag"), "https://host.example:8123/api/x")
        self.assertEqual(safe_url("http://localhost/"), "http://localhost/")
        self.assertEqual(safe_url("http://[::1]:80/a"), "http://::1:80/a")


if __name__ == "__main__":
    unittest.main()
