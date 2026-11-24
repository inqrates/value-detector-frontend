# -*- coding: utf-8 -*-
"""
Тесты продления JWT (ui/session_store.py + ui/components/ws_client.py).

Запуск:
    venv\\Scripts\\python.exe -m unittest tests.test_session_refresh -v

Зачем: access-токен живёт 2 часа, а WS-клиент ходил с ним пока
сервер не начнёт отвечать 403 (Starlette отдаёт 403, когда endpoint
закрывает соединение до accept). Фолбэк «подключиться без токена»
никогда не срабатывал, потому что _use_token нигде не учитывался
в _get_ws_url_with_token. now: просроченный access продлевается по
refresh-токену, а если продлить нечем — URL строится без токена.
"""
import base64
import json
import time
import unittest
from unittest.mock import patch

import ui.session_store as ss


def _make_token(exp: int) -> str:
    """JWT-подобная строка с нужным exp (подпись не проверяется)."""
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": "u", "exp": exp}).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.sig"


class TestJwtExpiry(unittest.TestCase):
    def test_real_token_from_log(self):
        """Реальный токен пользователя: exp=1795510797 (13:59:57)."""
        tok = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
               "eyJzdWIiOiJydXNpc2giLCJpZCI6MSwidGFyaWZmIjoicHJvIiwi"
               "aWF0IjoxNzk1NTAzNTk3LCJleHAiOjE3OTU1MTA3OTcsInR5cGUi"
               "OiJhY2Nlc3MifQ."
               "Ih_VBxCdIx7UKso_S_gPi6_pAjmZ3PYhnOCmM2XvTRI")
        self.assertEqual(ss.jwt_expires_at(tok), 1795510797)
        self.assertFalse(ss.access_token_valid(tok))

    def test_fresh_and_garbage(self):
        self.assertTrue(ss.access_token_valid(
            _make_token(int(time.time()) + 3600)))
        self.assertFalse(ss.access_token_valid(
            _make_token(int(time.time()) - 10)))
        # margin: истекает через минуту → считаем негодным
        self.assertFalse(ss.access_token_valid(
            _make_token(int(time.time()) + 60)))
        self.assertTrue(ss.access_token_valid(
            _make_token(int(time.time()) + 60), margin=30))

    def test_garbage_is_silent(self):
        for bad in ("", "abc", "a.b.c", "..", "x.y.z"):
            self.assertEqual(ss.jwt_expires_at(bad), 0)
            self.assertFalse(ss.access_token_valid(bad))


class TestRefresh(unittest.TestCase):
    def setUp(self):
        ss._last_refresh_at = 0.0
        self.expired = _make_token(int(time.time()) - 100)
        self.fresh = _make_token(int(time.time()) + 3600)
        # backend_url берём из конфига пользователя — в тесте фиксируем
        self._cfg = patch("ui.config_loader.load_config",
                          lambda: {"backend_url": "https://srv.test"})
        self._cfg.start()
        self.addCleanup(self._cfg.stop)

    def test_valid_access_no_http(self):
        """Годный access → на сервер не ходим вовсе."""
        with patch.object(ss, "get_saved_tokens",
                          return_value=(self.fresh, "r")), \
             patch("requests.post", side_effect=AssertionError(
                 "HTTP при валидном токене")):
            self.assertEqual(ss.refresh_access_token(), self.fresh)

    def test_refresh_success(self):
        saved = {}

        def fake_save(login, access_token, refresh_token):
            saved.update(login=login, access=access_token,
                         refresh=refresh_token)

        class Resp:
            def json(self):
                return {"success": True, "access_token": self.fresh}

        with patch.object(ss, "get_saved_tokens",
                          return_value=(self.expired, "refresh-abc")), \
             patch("requests.post", return_value=Resp()) as post, \
             patch.object(ss, "save_session_tokens", fake_save), \
             patch.object(ss, "user_session",
                          type("U", (), {"login": "rusish"})()):
            self.assertEqual(ss.refresh_access_token(), self.fresh)
        self.assertEqual(post.call_args[0][0],
                         "https://srv.test/auth/refresh")
        self.assertEqual(post.call_args[1]["json"],
                         {"refresh_token": "refresh-abc"})
        self.assertEqual(saved["access"], self.fresh)
        self.assertEqual(saved["refresh"], "refresh-abc")
        self.assertEqual(saved["login"], "rusish")

    def test_refresh_rejected_by_server(self):
        class Resp:
            def json(self):
                return {"success": False, "message": "Refresh-токен истёк"}

        with patch.object(ss, "get_saved_tokens",
                          return_value=(self.expired, "r")), \
             patch("requests.post", return_value=Resp()), \
             patch.object(ss, "save_session_tokens") as save:
            self.assertIsNone(ss.refresh_access_token())
        save.assert_not_called()

    def test_network_error_returns_none(self):
        with patch.object(ss, "get_saved_tokens",
                          return_value=(self.expired, "r")), \
             patch("requests.post", side_effect=OSError("timeout")):
            self.assertIsNone(ss.refresh_access_token())

    def test_no_tokens_at_all(self):
        with patch.object(ss, "get_saved_tokens", return_value=None):
            self.assertIsNone(ss.refresh_access_token())

    def test_empty_refresh_token(self):
        with patch.object(ss, "get_saved_tokens",
                          return_value=(self.expired, "")), \
             patch("requests.post", side_effect=AssertionError(
                 "HTTP без refresh-токена")):
            self.assertIsNone(ss.refresh_access_token())

    def test_throttle(self):
        """Не дёргаем /auth/refresh чаще REFRESH_MIN_INTERVAL_SEC."""
        class Resp:
            def json(self):
                return {"success": True, "access_token": self.fresh}

        with patch.object(ss, "get_saved_tokens",
                          return_value=(self.expired, "r")), \
             patch("requests.post", return_value=Resp()) as post, \
             patch.object(ss, "save_session_tokens"):
            self.assertEqual(ss.refresh_access_token(), self.fresh)
            # вторая попытка сразу — отброшена троттлом
            self.assertIsNone(ss.refresh_access_token())
        self.assertEqual(post.call_count, 1)


class TestWsUrl(unittest.TestCase):
    def _client(self):
        from ui.components.ws_client import SignalClient
        return SignalClient("wss://example.test:8000/ws")

    def test_valid_token_used(self):
        fresh = _make_token(int(time.time()) + 3600)
        with patch.object(ss, "get_saved_tokens",
                          return_value=(fresh, "r")):
            client = self._client()
            url = client._get_ws_url_with_token()
        self.assertEqual(url, f"wss://example.test:8000/ws?token={fresh}")

    def test_expired_token_is_refreshed(self):
        expired = _make_token(int(time.time()) - 10)
        fresh = _make_token(int(time.time()) + 3600)
        with patch.object(ss, "get_saved_tokens",
                          return_value=(expired, "r")), \
             patch.object(ss, "refresh_access_token", return_value=fresh):
            url = self._client()._get_ws_url_with_token()
        self.assertIn(f"token={fresh}", url)
        self.assertNotIn(expired, url)

    def test_unrefreshable_token_dropped(self):
        """Продлить нечем → подключаемся БЕЗ токена, а не ловим 403."""
        expired = _make_token(int(time.time()) - 10)
        with patch.object(ss, "get_saved_tokens",
                          return_value=(expired, "r")), \
             patch.object(ss, "refresh_access_token", return_value=None):
            url = self._client()._get_ws_url_with_token()
        self.assertEqual(url, "wss://example.test:8000/ws")

    def test_no_saved_tokens(self):
        with patch.object(ss, "get_saved_tokens", return_value=None):
            url = self._client()._get_ws_url_with_token()
        self.assertEqual(url, "wss://example.test:8000/ws")

    def test_existing_query_uses_ampersand(self):
        fresh = _make_token(int(time.time()) + 3600)
        with patch.object(ss, "get_saved_tokens",
                          return_value=(fresh, "r")):
            client = self._client()
            client.url = "wss://example.test:8000/ws?x=1"
            url = client._get_ws_url_with_token()
        self.assertIn("?x=1&token=", url)


class TestAccountsSaveWithoutLogin(unittest.TestCase):
    """
    Аккаунт без логина раньше молча не сохранялся (return без
    сообщения). Для Onewin логин не нужен — авторизация в браузере
    профиля, поэтому пустой логин теперь допустим, если задан ID.
    """

    def setUp(self):
        import os
        import tempfile
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        self.tmp = tempfile.mkdtemp()
        # Путь стора резолвится внутри ui.pages.accounts — мок именно
        # туда, иначе тест запишет в реальный %APPDATA%.
        self._patcher = patch(
            "ui.pages.accounts.get_app_data_dir", lambda: self.tmp)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _page(self):
        from PyQt5.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from ui.pages.accounts import AccountsPage
        return AccountsPage()

    def test_saved_with_profile_only(self):
        page = self._page()
        page.edit_index = -1
        page.bk_combo.setCurrentText("Onewin")
        page.login_edit.setText("")
        page.profile_edit.setText("onewin_1")
        page._on_save()
        self.assertEqual(len(page.store.accounts), 1)
        acc = page.store.accounts[0]
        self.assertEqual(acc["bk"], "Onewin")
        self.assertEqual(acc["login"], "")
        self.assertEqual(acc["ads_power_id"], "onewin_1")
        self.assertEqual(page.acc_table.rowCount(), 1)

    def test_persisted_to_file(self):
        page = self._page()
        page.edit_index = -1
        page.bk_combo.setCurrentText("Onewin")
        page.login_edit.setText("")
        page.profile_edit.setText("onewin_1")
        page._on_save()
        # второй экземпляр обязан прочитать то же самое
        page2 = self._page()
        self.assertEqual(len(page2.store.accounts), 1)
        self.assertEqual(page2.store.accounts[0]["bk"], "Onewin")

    def test_rejected_when_both_empty(self):
        page = self._page()
        page.edit_index = -1
        page.login_edit.setText("")
        page.profile_edit.setText("")
        with patch("ui.pages.accounts.QMessageBox.warning") as warn:
            page._on_save()
        self.assertEqual(page.store.accounts, [])
        self.assertTrue(warn.called)


if __name__ == "__main__":
    unittest.main()
