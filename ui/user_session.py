# ui/user_session.py

class UserSession:
    """Синглтон для хранения данных текущего пользователя."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.login = "Гость"
        self.tariff = "Trial"
        self.days_left = 0
        self.days_total = 30
        self.is_authenticated = False

    def set_user(self, login: str, tariff: str, days_left: int = None, days_total: int = 30):
        self.login = login
        self.tariff = tariff
        if days_left is not None:
            self.days_left = days_left
        self.days_total = days_total
        self.is_authenticated = True

    def clear(self):
        self.login = "Гость"
        self.tariff = "Trial"
        self.days_left = 0
        self.days_total = 30
        self.is_authenticated = False


# Глобальный экземпляр
user_session = UserSession()