# -*- coding: utf-8 -*-
"""
Тесты OnewinFastClient (core/fast_client.py) — WS Socket.IO без браузера.

Запуск:
    venv\\Scripts\\python.exe -m unittest tests.test_onewin_fast_client -v

Реальная сеть не используется: websockets.connect подменён фейковым
сокетом, который отдаёт пакеты из списка. Проверяем:
  - handshake Engine.IO v4 ("0" → "40" → "40{sid}") и подписку;
  - ответ "3" на серверный ping "2";
  - реконнект сбрасывает кэши и переподписывается;
  - get_markets_by_native_id отдаёт снапшот в формате after_goal;
  - протухшие / нечисловые / пустые match_id → None.
Разбор самих фреймов покрыт в tests/test_onewin.py — здесь клиент
кормит хендлер теми же дампами, поэтому формат данных не дублируется.
"""
import asyncio
import json
import time
import unittest

import core.fast_client as fc
from core.fast_client import OnewinFastClient
from ui.after_goal.onewin import OnewinHandler

from tests.test_onewin import (
    TT_INFO,
    TT_ODDS_SNAPSHOT,
    _wrap_socket_frame,
)

MATCH_ID = "43721681"


class FakeWS:
    """Фейковый websockets connection: отдаёт inbound, пишет в sent."""

    def __init__(self, inbound=None):
        self._inbound = list(inbound or [])
        self.sent = []
        self.closed = False

    async def send(self, msg):
        self.sent.append(msg)

    async def recv(self):
        if not self._inbound:
            raise RuntimeError("EOF")
        return self._inbound.pop(0)

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._inbound:
            raise StopAsyncIteration
        return self._inbound.pop(0)


class _FakeConnect:
    """Подмена websockets.connect, возвращающая один и тот же FakeWS."""

    def __init__(self, ws):
        self.ws = ws
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        ws = self.ws

        class _CM:
            async def __aenter__(_self):
                return ws

            async def __aexit__(_self, *exc):
                return False

        return _CM()


def _open_pkt():
    return '0{"sid":"abc","pingInterval":25000,"pingTimeout":60000}'


def _connect_pkt():
    return '40{"sid":"def"}'


def _data_frames():
    return [
        _wrap_socket_frame(TT_INFO, "match-info-snapshot"),
        _wrap_socket_frame(TT_ODDS_SNAPSHOT, "match-odds-snapshot"),
    ]


def _feed_dump(handler):
    """Скормить хендлеру реальные дампы НТ (фаза 2, sub 9:10)."""
    for frame in _data_frames():
        handler.feed_frame(frame)


class TestHandshake(unittest.TestCase):
    def test_handshake_subscribes_and_pongs(self):
        ws = FakeWS([_open_pkt(), _connect_pkt(), "2"] + _data_frames())
        client = OnewinFastClient()
        client._ws = ws
        fake_connect = _FakeConnect(ws)
        orig = fc.websockets
        try:
            fc.websockets = type("_WS", (), {"connect": fake_connect})
            # Матч уже известен движку → подписываемся при коннекте
            client._get_handler(MATCH_ID)
            asyncio.run(client._connect_and_listen())
        finally:
            fc.websockets = orig

        self.assertIn("40", ws.sent)
        self.assertIn("3", ws.sent)          # pong на серверный ping
        self.assertTrue(any("subscribe-match-info" in m for m in ws.sent))
        self.assertTrue(any("subscribe-match-odds" in m for m in ws.sent))
        self.assertIn(MATCH_ID, client._subscribed)
        self.assertTrue(client._connected)

        handler = client._handlers[MATCH_ID]
        parsed = handler.snapshot()
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["phase_num"], 2)
        self.assertEqual(parsed["set_markets"]["set_2"]["winner"]["1"], 4.33)

    def test_connect_error_raises(self):
        ws = FakeWS([_open_pkt(), '44{"message":"Unauthorized"}'])
        client = OnewinFastClient()
        fake_connect = _FakeConnect(ws)
        orig = fc.websockets
        try:
            fc.websockets = type("_WS", (), {"connect": fake_connect})
            with self.assertRaises(RuntimeError):
                asyncio.run(client._connect_and_listen())
        finally:
            fc.websockets = orig

    def test_reconnect_resets_caches_and_resubscribes(self):
        ws = FakeWS([_open_pkt(), _connect_pkt()])
        client = OnewinFastClient()
        handler = client._get_handler(MATCH_ID)
        _feed_dump(handler)
        self.assertTrue(handler._match_info)
        client._subscribed.add(MATCH_ID)

        fake_connect = _FakeConnect(ws)
        orig = fc.websockets
        try:
            fc.websockets = type("_WS", (), {"connect": fake_connect})
            asyncio.run(client._connect_and_listen())
        finally:
            fc.websockets = orig

        # Сессия новая → старый кэш выброшен, подписка отправлена заново
        self.assertEqual(handler._match_info, {})
        self.assertEqual(handler._odds, {})
        self.assertIn(MATCH_ID, client._subscribed)
        self.assertTrue(
            any("subscribe-match-info" in m for m in ws.sent)
        )


class TestSubscribeFrames(unittest.TestCase):
    def test_frame_format(self):
        ws = FakeWS()
        client = OnewinFastClient()
        client._ws = ws
        client._connected = True

        ok = asyncio.run(client._subscribe([MATCH_ID, "123"]))
        self.assertTrue(ok)
        self.assertEqual(len(ws.sent), 2)

        for msg, expected_type in (
            (ws.sent[0], "subscribe-match-info"),
            (ws.sent[1], "subscribe-match-odds"),
        ):
            self.assertTrue(msg.startswith("42"))
            event, body = json.loads(msg[2:])
            self.assertEqual(event, "subscribe")
            self.assertEqual(body["messageType"], expected_type)
            self.assertEqual(
                body["data"]["matchIds"], [int(MATCH_ID), 123]
            )
        self.assertTrue(
            json.loads(ws.sent[1][2:])[1]["data"]["isBaseOddsGroups"]
        )

    def test_subscribe_without_connection(self):
        client = OnewinFastClient()
        self.assertFalse(asyncio.run(client._subscribe([MATCH_ID])))
        self.assertEqual(client._subscribed, set())

    def test_non_numeric_ids_skipped(self):
        ws = FakeWS()
        client = OnewinFastClient()
        client._ws = ws
        client._connected = True
        self.assertFalse(asyncio.run(client._subscribe(["abc", ""])))
        self.assertEqual(ws.sent, [])


class TestFramesRouting(unittest.TestCase):
    def test_ping_gets_pong(self):
        ws = FakeWS()
        client = OnewinFastClient()
        client._ws = ws
        asyncio.run(client._on_text("2"))
        self.assertEqual(ws.sent, ["3"])

    def test_service_frames_ignored(self):
        ws = FakeWS()
        client = OnewinFastClient()
        client._ws = ws
        handler = client._get_handler(MATCH_ID)
        for frame in ('0{"sid":"a"}', '40{"sid":"b"}', "3", '44{"x":1}'):
            asyncio.run(client._on_text(frame))
        self.assertEqual(client._handlers[MATCH_ID], handler)
        self.assertEqual(handler._odds, {})

    def test_other_match_frames_not_leaked(self):
        """Хендлер чужого матча не должен получить данные другого матча."""
        ws = FakeWS()
        client = OnewinFastClient()
        client._ws = ws
        other = client._get_handler("99999999")
        asyncio.run(client._on_text(
            _wrap_socket_frame(TT_INFO, "match-info-snapshot")
        ))
        self.assertEqual(other._match_info, {})


class TestGetMarkets(unittest.TestCase):
    def _client_with_dump(self):
        """Клиент без сети: подписка сразу кормит хендлер дампом."""
        client = OnewinFastClient()
        client._ws = FakeWS()
        client._connected = True
        client.start = lambda: asyncio.sleep(0)   # без реального коннекта

        async def fake_subscribe(ids):
            handler = client._get_handler(ids[0])
            _feed_dump(handler)
            client._subscribed.update(ids)
            return True

        client._subscribe = fake_subscribe
        return client

    def test_snapshot_shape(self):
        client = self._client_with_dump()
        res = asyncio.run(client.get_markets_by_native_id(MATCH_ID))
        self.assertIsNotNone(res)
        self.assertEqual(str(res["match_id"]), MATCH_ID)
        self.assertEqual(res["phase_num"], 2)
        self.assertEqual(res["score1"], 0)
        self.assertEqual(res["score2"], 1)
        self.assertEqual(res["sub_score1"], 9)
        self.assertEqual(res["sub_score2"], 10)
        self.assertEqual(res["prev_sub_score1"], 9)
        self.assertEqual(res["prev_sub_score2"], 11)
        self.assertEqual(res["prev_phase_num"], 1)
        self.assertEqual(res["sport"], "table_tennis")
        self.assertEqual(
            res["set_markets"]["set_2"]["winner"], {"1": 4.33, "2": 1.2}
        )

    def test_outcome_ids_stripped(self):
        client = self._client_with_dump()
        res = asyncio.run(client.get_markets_by_native_id(MATCH_ID))
        self.assertNotIn("outcome_ids", res)

    def test_stale_data_returns_none(self):
        client = self._client_with_dump()
        res = asyncio.run(client.get_markets_by_native_id(MATCH_ID))
        self.assertIsNotNone(res)
        # Матч завершился → сервер молчит → данные протухли
        client._handlers[MATCH_ID]._last_frame_at = (
            time.time() - OnewinFastClient.MATCH_STALE - 5
        )
        self.assertIsNone(
            asyncio.run(client.get_markets_by_native_id(MATCH_ID))
        )

    def test_no_data_returns_none(self):
        client = OnewinFastClient()
        client._ws = FakeWS()
        client._connected = True
        client.start = lambda: asyncio.sleep(0)
        client.SNAPSHOT_WAIT = 0.2
        # Подписка ничего не приносит (куки протухли / матч не найден)
        client._subscribe = lambda ids: asyncio.sleep(0.01, result=True)
        self.assertIsNone(asyncio.run(client.get_markets_by_native_id(MATCH_ID)))

    def test_non_numeric_native_id(self):
        client = self._client_with_dump()
        for bad in ("", None, "abc", "43721681x"):
            self.assertIsNone(
                asyncio.run(client.get_markets_by_native_id(bad))
            )

    def test_get_markets_by_names_not_supported(self):
        client = self._client_with_dump()
        self.assertIsNone(
            asyncio.run(client.get_markets("table_tennis", "Максимкив", "Мельник"))
        )

    def test_emit_disabled_for_fast_handlers(self):
        """
        Fast-клиент не должен пересобирать снапшот на каждый кадр —
        иначе 24 матча × десятки инкрементов в секунду = пустой CPU.
        """
        client = OnewinFastClient()
        handler = client._get_handler(MATCH_ID)
        self.assertFalse(handler._emit_enabled)


class TestMaintenance(unittest.TestCase):
    def test_check_silence_closes_stale_socket(self):
        ws = FakeWS()
        client = OnewinFastClient()
        client._ws = ws
        client._connected = True
        client._last_frame_at = time.time()
        self.assertFalse(asyncio.run(client._check_silence()))
        self.assertFalse(ws.closed)

        client._last_frame_at = time.time() - OnewinFastClient.SILENCE_RECONNECT - 1
        self.assertTrue(asyncio.run(client._check_silence()))
        self.assertTrue(ws.closed)

    def test_check_silence_when_disconnected(self):
        client = OnewinFastClient()
        client._connected = False
        client._ws = None
        self.assertFalse(asyncio.run(client._check_silence()))

    def test_evict_handlers_overflow(self):
        client = OnewinFastClient()
        client.MAX_HANDLERS = 2
        now = time.time()
        for mid in ("1", "2", "3"):
            client._handlers[mid] = OnewinHandler(target_match_id=mid)
        client._touched_at.update({"1": now - 20, "2": now - 10, "3": now})
        client._subscribed.update({"1", "2", "3"})

        client._evict_handlers()
        self.assertEqual(set(client._handlers), {"2", "3"})
        self.assertNotIn("1", client._subscribed)
        self.assertNotIn("1", client._touched_at)

    def test_evict_handlers_ttl(self):
        client = OnewinFastClient()
        now = time.time()
        client._get_handler("1")
        client._get_handler("2")
        client._touched_at["1"] = now - OnewinFastClient.HANDLER_TTL - 1
        client._subscribed.add("1")

        client._evict_handlers()
        self.assertNotIn("1", client._handlers)
        self.assertIn("2", client._handlers)

    def test_fresh_handler_not_evicted(self):
        """
        Регресс: у нового хендлера _last_frame_at = 0. Если eviction
        смотреть на него — свежий матч вылетал бы сразу и подписка
        дёргалась на каждом опросе движка.
        """
        client = OnewinFastClient()
        client.MAX_HANDLERS = 1
        client._get_handler("1")
        client._get_handler("2")
        self.assertIn("2", client._handlers)
        self.assertNotIn("1", client._handlers)


class TestRegistry(unittest.TestCase):
    def test_onewin_in_classes(self):
        self.assertTrue(fc.has_fast_client("onewin"))
        client = fc.get_fast_client("onewin")
        self.assertIsInstance(client, OnewinFastClient)
        self.assertEqual(client.bk, "onewin")

    def test_cookie_header(self):
        import core.fast_config as fast_config
        client = OnewinFastClient()
        orig = fast_config.load_cookies
        try:
            fast_config.load_cookies = lambda bk: [
                {"name": "a", "value": "1"},
                {"name": "b", "value": "2"},
                {"name": "", "value": "3"},
                {"name": "c", "value": ""},
            ]
            self.assertEqual(client._build_cookie_header(), "a=1; b=2")

            fast_config.load_cookies = lambda bk: []
            self.assertEqual(client._build_cookie_header(), "")
        finally:
            fast_config.load_cookies = orig

    def test_fast_config_meta(self):
        from core.fast_config import FAST_BKS_ALL
        meta = FAST_BKS_ALL["onewin"]
        self.assertTrue(meta["needs_cookies"])
        self.assertEqual(meta["label"], "Onewin")

    def test_ws_url_matches_backend(self):
        # Тот же адрес, что у бекендового parsers/onewin_api.py
        self.assertIn("api-gateway.top-parser.com/push-server-v2",
                      OnewinFastClient.WS_URL)
        self.assertIn("44ba10e5-7df2-47ab-a44d-dc93803c7a6e",
                      OnewinFastClient.WS_URL)
        self.assertEqual(OnewinFastClient.ORIGIN, "https://one-vv7078.com")


if __name__ == "__main__":
    unittest.main()
