# ui/bet_executor_pari.py
import requests
import json
from typing import Optional, Dict, Any


class PariBetExecutor:
    """
    Отправка ставок на Pari.ru напрямую через API.
    Требует действующей сессии (cookies и идентификаторы).
    """

    def __init__(
        self,
        client_id: int,
        fsid: str,
        sys_id: int = 21,
        device_id: str = "",
        cookies: Optional[Dict[str, str]] = None,
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 YaBrowser/26.6.0.0 Safari/537.36"
    ):
        self.client_id = client_id
        self.fsid = fsid
        self.sys_id = sys_id
        self.device_id = device_id
        self.user_agent = user_agent

        self.session = requests.Session()
        if cookies:
            self.session.cookies.update(cookies)

        # Основные хосты (могут меняться, но в ваших запросах были такие)
        self.host_betrequest = "https://clientsapi-lb01-w.pb06e2-resources.com"
        self.host_bet = "https://clientsapi61.pb06e2-resources.ru"
        self.host_info = "https://clientsapi61.pb06e2-resources.ru"

        # Общие заголовки
        self.headers = {
            "accept": "*/*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "ru,en;q=0.9",
            "cache-control": "no-cache",
            "content-type": "text/plain;charset=UTF-8",  # как в ваших запросах
            "origin": "https://pari.ru",
            "referer": "https://pari.ru/",
            "sec-ch-ua": '"Chromium";v="148", "YaBrowser";v="26.6", "Not/A)Brand";v="99", "Yowser";v="2.5"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "user-agent": self.user_agent,
        }

    def _request(
        self, url: str, payload: Dict[str, Any], extra_headers: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Универсальный метод для POST с JSON-телом."""
        headers = self.headers.copy()
        if extra_headers:
            headers.update(extra_headers)
        resp = self.session.post(
            url,
            json=payload,          # requests сам выставит Content-Type: application/json
            headers=headers,
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()

    def get_request_id(self, lang: str = "ru") -> int:
        """
        Шаг 1: получить requestId для будущей ставки.
        """
        url = f"{self.host_betrequest}/coupon/betRequestId"
        payload = {
            "lang": lang,
            "fsid": self.fsid,
            "sysId": self.sys_id,
            "clientId": self.client_id,
            "CDI": 942,                # ? это число из вашего запроса, возможно, константа
            "deviceId": self.device_id,
        }
        data = self._request(url, payload)
        if data.get("result") != "requestId":
            raise RuntimeError(f"Не удалось получить requestId: {data}")
        return int(data["requestId"])

    def place_bet(
        self,
        event_id: int,
        factor: int,          # например, 923 (это код исхода?)
        value: float,         # коэффициент (2.5)
        amount: float,        # сумма ставки в валюте
        score: str = "0:0",   # текущий счёт (для live)
        zone: str = "sp",     # зона (у вас "sp")
        lang: str = "ru",
        mirror: str = "https://pari.ru",
    ) -> Dict[str, Any]:
        """
        Шаг 2: отправить ставку.
        Возвращает ответ сервера (например, {"result":"betDelay","betDelay":3000}).
        """
        # 1) Получаем requestId
        request_id = self.get_request_id(lang)

        # 2) Формируем тело ставки
        url = f"{self.host_bet}/coupon/bet"
        payload = {
            "requestId": request_id,
            "lang": lang,
            "clientId": self.client_id,
            "fsid": self.fsid,
            "sysId": self.sys_id,
            "coupon": {
                "amount": amount,
                "flexBet": "any",           # как в вашем запросе
                "flexParam": False,
                "mirror": mirror,
                "bets": [
                    {
                        "num": 1,
                        "event": event_id,
                        "factor": factor,
                        "value": value,
                        "score": score,
                        "zone": zone,
                    }
                ],
            },
        }
        data = self._request(url, payload)
        return data

    def get_balance(self) -> float:
        """
        (Опционально) получить текущий баланс.
        """
        url = f"{self.host_info}/session/info"
        payload = {
            "clientId": self.client_id,
            "fsid": self.fsid,
            "sysId": self.sys_id,
            "deviceId": self.device_id,
        }
        data = self._request(url, payload)
        return float(data.get("saldo", 0.0))