# ui/pages/settings.py
import json
import os

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QCheckBox, QGroupBox, QFormLayout, QPushButton,
    QScrollArea, QFrame, QTextEdit, QDialog, QDialogButtonBox,
    QMessageBox, QApplication
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from ui.paths import get_app_data_dir


def _settings_path() -> str:
    return os.path.join(get_app_data_dir(), "settings.json")


def load_settings() -> dict:
    path = _settings_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_settings(data: dict) -> None:
    path = _settings_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения settings.json: {e}")


TUTORIAL_HTML = """
<style>
h2 { color: #21c1de; margin-top: 24px; }
h3 { color: #f5fbff; margin-top: 18px; }
code { background: rgba(8,167,200,0.12); color: #7dd3e8;
       padding: 1px 6px; border-radius: 4px;
       font-family: Consolas, monospace; font-size: 12px; }
ul, ol { margin-left: 22px; }
li { margin-bottom: 4px; }
table { border-collapse: collapse; margin: 10px 0; }
td, th { border: 1px solid rgba(255,255,255,0.1);
         padding: 6px 12px; font-size: 13px; }
th { background: rgba(8,167,200,0.1); color: #f5fbff; }
.hint { color: rgba(199,214,223,0.7); font-style: italic; }
.warn { color: #f2c94c; font-weight: 600; }
</style>

<h1 style="color:#f5fbff;">Value Detector Pro — полное руководство</h1>
<p class="hint">Этот туториал объясняет всё, что есть в приложении —
каждый параметр, каждую цифру, каждую кнопку. Читай по порядку,
если запускаешь впервые. Если что-то уже знаешь — прыгай
по разделам.</p>

<hr/>

<h2>1. Что делает приложение (простыми словами)</h2>

<p>Представь две букмекерские конторы. Одна — <b>быстрая</b> (например,
Fonbet): она мгновенно видит, что игрок выиграл очко, и сразу меняет
коэффициенты. Другая — <b>медленная</b> (например, LigaStavok):
она получает данные с задержкой 3–5 секунд и всё это время показывает
<b>старые</b> коэффициенты.</p>

<p>Вот в этот промежуток (пока медленная не пересчитала линию) мы
и ставим. Быстрая уже знает, что партия почти выиграна, а медленная
всё ещё даёт коэффициент как будто счёт равный. Через пару секунд
медленная «догонит» — а наша ставка уже принята по старому
выгодному коэффициенту.</p>

<p class="warn">⚠️ Это НЕ вилки и НЕ валуи в классическом смысле.
Мы ловим не «недооценённый коэффициент», а «устаревший коэффициент,
который вот-вот изменится».</p>

<hr/>

<h2>2. Как приложение устроено</h2>

<p>Три главные сущности:</p>

<table>
  <tr>
    <th>Что</th>
    <th>Где настраивается</th>
    <th>Зачем</th>
  </tr>
  <tr>
    <td><b>Аккаунт</b></td>
    <td>Страница «Аккаунты»</td>
    <td>Логин/пароль от БК + привязка к профилю AdsPower</td>
  </tr>
  <tr>
    <td><b>Стратегия</b></td>
    <td>Страница «Стратегии»</td>
    <td>Правила: какую БК слушать, на какие рынки ставить,
        с какими порогами и размером ставки</td>
  </tr>
  <tr>
    <td><b>Профиль AdsPower</b></td>
    <td>Приложение AdsPower</td>
    <td>«Виртуальный компьютер» для каждой БК. Хранит
        авторизацию, cookies, отпечаток браузера</td>
  </tr>
</table>

<p>Один <b>аккаунт</b> = один <b>профиль AdsPower</b> = одна <b>БК</b>.
Стратегия выбирает, какой аккаунт использовать. При включении
стратегии приложение само запустит AdsPower, откроет нужный профиль,
зайдёт на страницу БК и начнёт ждать сигналы.</p>

<hr/>

<h2>3. Первый запуск — пошагово</h2>

<h3>Шаг 1. Настроить AdsPower</h3>
<ol>
  <li>Установить и запустить <b>AdsPower</b>.</li>
  <li>Создать отдельный профиль для каждой БК (например,
      «Fonbet-1», «LigaStavok-1»).</li>
  <li>Запустить профиль вручную, залогиниться в БК,
      <b>сохранить cookies</b> (AdsPower сделает это сам).</li>
  <li>Скопировать <b>Profile ID</b> — он выглядит как <code>k1gy7l7t</code>.</li>
  <li>В AdsPower найти <b>API-ключ</b>: Настройки → API →
      скопировать ключ.</li>
</ol>

<h3>Шаг 2. Добавить аккаунты в приложении</h3>
<ol>
  <li>Открыть страницу <b>«Аккаунты»</b>.</li>
  <li>В правой форме заполнить:
    <ul>
      <li><b>Контора</b> — какая БК.</li>
      <li><b>Логин</b> — email или номер телефона от БК.</li>
      <li><b>Пароль</b> — пароль от БК.</li>
      <li><b>Профиль AdsPower</b> — Profile ID из шага 1.</li>
      <li><b>API Key</b> — ключ AdsPower (один и тот же
          для всех профилей в аккаунте AdsPower).</li>
    </ul>
  </li>
  <li>Нажать <b>«💾 Сохранить аккаунт»</b>.</li>
  <li>Повторить для каждой БК.</li>
</ol>

<h3>Шаг 3. Создать стратегию</h3>
<ol>
  <li>Открыть страницу <b>«Стратегии»</b>.</li>
  <li>Нажать <b>«➕ Создать»</b>.</li>
  <li>Заполнить форму (см. раздел 5).</li>
  <li>Нажать <b>«💾 Сохранить»</b>.</li>
</ol>

<h3>Шаг 4. Включить стратегию</h3>
<ol>
  <li>Тумблер в правом верхнем углу карточки стратегии
      → <b>ON</b>.</li>
  <li>Приложение запустит AdsPower, откроет браузер,
      зайдёт на страницу БК.</li>
  <li>В логах увидишь <code>✅ Браузер для профиля X запущен</code>.</li>
  <li>Теперь можно ждать сигналы. Они появятся в
      <b>логах</b> и <b>дашборде</b>.</li>
</ol>

<hr/>

<h2>4. Дашборд — что означают цифры</h2>

<table>
  <tr>
    <th>Карточка</th>
    <th>Что показывает</th>
  </tr>
  <tr>
    <td><b>БК в работе</b></td>
    <td>Сколько разных букмекеров у тебя в аккаунтах. Если добавил
        5 аккаунтов от 5 БК — тут будет 5.</td>
  </tr>
  <tr>
    <td><b>Сигналов сегодня</b></td>
    <td>Сколько раз сегодня бекенд заметил задержку между
        fast и slow БК. Обновляется автоматически.</td>
  </tr>
  <tr>
    <td><b>Оборот сегодня</b></td>
    <td>Сумма <b>принятых</b> ставок за день. Если поставили 3 ставки
        по 100 ₽ — здесь будет 300 ₽.</td>
  </tr>
  <tr>
    <td><b>Ставок сегодня</b></td>
    <td>Количество принятых ставок за день.</td>
  </tr>
  <tr>
    <td><b>БК отслеживается</b></td>
    <td>Сколько БК бекенд парсит прямо сейчас (обычно 9).</td>
  </tr>
</table>

<p>Кнопка <b>↻ Сбросить за сегодня</b> обнуляет <b>оборот</b> и
<b>счётчик ставок</b>. Сигналы не трогает.</p>

<p>Таблица «Букмекерские конторы в работе» показывает реальные
<b>балансы</b> тех БК, чьи профили сейчас запущены. Если стратегия
включена — БК запущена → через пару секунд появится баланс.
Если выключена — прочерк.</p>

<hr/>

<h2>5. Стратегия — что означает каждый параметр</h2>

<h3>5.1. Основные параметры</h3>

<table>
  <tr>
    <th>Поле</th>
    <th>Что значит</th>
    <th>Рекомендация</th>
  </tr>
  <tr>
    <td><b>Название</b></td>
    <td>Только для тебя — как ты будешь называть стратегию</td>
    <td>Например «НТ-LigaStavok-Fonbet»</td>
  </tr>
  <tr>
    <td><b>Тип стратегии</b></td>
    <td>Сейчас работает только <b>Послегол</b> (After-goal) —
        ловля задержек счёта</td>
    <td>Оставить как есть</td>
  </tr>
  <tr>
    <td><b>Вид спорта</b></td>
    <td>Какой спорт слушать: НТ, волейбол, баскет, кибер,
        или «все виды»</td>
    <td>Начни с одного — например НТ</td>
  </tr>
  <tr>
    <td><b>Аккаунт для ставок</b></td>
    <td>Какой из твоих аккаунтов использовать. Это определяет
        <b>slow БК</b> — ту, на которой будем ставить</td>
    <td>Выбрать из добавленных</td>
  </tr>
  <tr>
    <td><b>Мин. задержка</b></td>
    <td>Минимальное отставание fast БК от slow (в секундах),
        при котором сигнал считается валидным</td>
    <td>1.5–2.5 сек. Меньше — много шума.
        Больше — мало сигналов</td>
  </tr>
</table>

<p class="hint">💡 Fast БК не задаётся вручную — приложение
автоматически выбирает самую быструю из 9 контор для каждого матча.</p>

<h3>5.2. Что ставить (рынки и пороги)</h3>

<p>Рынок — это на что ставим:</p>
<ul>
  <li><b>Победитель</b> — кто выиграет партию/сет.</li>
  <li><b>Тотал</b> — сколько всего очков наберут обе команды
      (например, больше 18.5).</li>
  <li><b>Фора</b> — разница в счёте с форой (например, П1 -4.5).</li>
</ul>

<p>Для каждого рынка можно задать <b>свой порог</b> —
минимальное отставание fast от slow. Это самая важная настройка
в стратегии.</p>

<table>
  <tr>
    <th>Рынок</th>
    <th>Что означает порог</th>
    <th>Рекомендуемый порог</th>
  </tr>
  <tr>
    <td><b>Победитель</b></td>
    <td>Fast БК уже видит, что партия выиграна, а slow — нет.
        Чем больше порог, тем увереннее, но реже</td>
    <td>2–3 очка</td>
  </tr>
  <tr>
    <td><b>Тотал</b></td>
    <td>Fast уже набрал очков больше линии, slow ещё нет.
        Безопаснее всего — тотал либо сбудется, либо нет</td>
    <td>1–2 очка</td>
  </tr>
  <tr>
    <td><b>Фора</b></td>
    <td>Fast уже покрыл фору, slow ещё нет</td>
    <td>2 очка</td>
  </tr>
</table>

<p class="hint">💡 Если ставишь только на тотал — поставь порог 1.
Тотал редко ошибается. Если на победителя — поставь 3,
чтобы ловить только явные случаи.</p>

<h3>5.3. Стороны ставок</h3>

<table>
  <tr>
    <th>Поле</th>
    <th>Что значит</th>
  </tr>
  <tr>
    <td><b>Направление (Победитель)</b></td>
    <td>
      <b>Лучший коэффициент</b> — из всех подтверждённых исходов
      берётся с самым высоким коэфом.<br/>
      <b>Лидер фазы</b> — ставим только на того, кто ведёт
      в текущей партии.<br/>
      <b>Отстающий в фазе</b> — наоборот, на отстающего.<br/>
      <b>Как на быстрой БК</b> — на того, кто уже выиграл
      по данным fast БК.
    </td>
  </tr>
  <tr>
    <td><b>Стороны Победителя</b></td>
    <td>Кого брать: П1, П2 или обоих. Сужение помогает
        если по одной стороне нет сигналов</td>
  </tr>
  <tr>
    <td><b>Стороны Тотала</b></td>
    <td>Только Больше (агрессивно), только Меньше
        (безопасно, только после конца партии), либо оба</td>
  </tr>
  <tr>
    <td><b>Стороны Фор</b></td>
    <td>Аналогично Победителю — П1, П2 или оба</td>
  </tr>
</table>

<h3>5.4. Ставка</h3>

<table>
  <tr>
    <th>Поле</th>
    <th>Что значит</th>
    <th>Рекомендация</th>
  </tr>
  <tr>
    <td><b>Размер ставки</b></td>
    <td>Сколько ставить в одной ставке. Фиксированно,
        не меняется</td>
    <td>Начни с 50–100 ₽</td>
  </tr>
  <tr>
    <td><b>Мин. коэффициент</b></td>
    <td>Не ставить, если коэф меньше этого. Низкие коэфы
        (1.05–1.2) дают мало профита при риске потерять всё</td>
    <td>1.30–1.50</td>
  </tr>
  <tr>
    <td><b>Макс. коэффициент</b></td>
    <td>Не ставить, если коэф больше. Высокие (3.0+) — часто
        означают, что БК в курсе события, и это не задержка,
        а «подстава»</td>
    <td>3.0–5.0</td>
  </tr>
</table>

<h3>5.5. Автоматизация</h3>

<table>
  <tr>
    <th>Поле</th>
    <th>Что значит</th>
    <th>Рекомендация</th>
  </tr>
  <tr>
    <td><b>Пауза после сигнала</b></td>
    <td>Сколько ждать после открытия вкладки матча, прежде чем
        начать проверять задержку. Нужно, чтобы данные slow БК
        успели подгрузиться</td>
    <td>2–3 сек</td>
  </tr>
  <tr>
    <td><b>Макс. ставок на матч</b></td>
    <td>Сколько раз можно поставить по одному матчу</td>
    <td>1–3</td>
  </tr>
  <tr>
    <td><b>Макс. ставок на фазу</b></td>
    <td>Сколько раз можно поставить в одном сете/партии.
        Не даёт долбить в один и тот же сет</td>
    <td>1</td>
  </tr>
  <tr>
    <td><b>Лимит ставок за сессию</b></td>
    <td>Остановить работу после N принятых ставок за сессию.
        <b>0 = без лимита</b>. Сбрасывается при перезапуске
        приложения</td>
    <td>Для теста: 3–5. Для прода: 0 или 50–100</td>
  </tr>
  <tr>
    <td><b>Игнорировать повторы</b></td>
    <td>Реагировать только на <b>новые</b> сигналы, не
        переоткрывать матч на обновлениях</td>
    <td>Вкл., если хочешь меньше шума</td>
  </tr>
  <tr>
    <td><b>Скрытый режим</b></td>
    <td>AdsPower запустит браузер без окна (headless). Не видно,
        что происходит. Полезно на слабом ПК</td>
    <td>Выкл. для отладки, Вкл. для продакшена</td>
  </tr>
</table>

<hr/>

<h2>6. Логи — как читать</h2>

<p>Каждая запись имеет вид:</p>
<pre style="color:#cfdae2;">
[12:34:56] ● ИНФО [Fonbet→LigaStavok] Иванов vs Петров
         Fast БК (Fonbet, впереди) счёт 8:5 · П1 1.55 / П2 2.40
         Slow БК (LigaStavok, отстаёт) счёт 6:5 · П1 1.70 / П2 2.10
         Разница: +2</pre>

<p>Уровни:</p>
<ul>
  <li><b>ИНФО</b> (синий) — служебные события.</li>
  <li><b>OK</b> (зелёный) — успех: ставка принята, сигнал подтверждён.</li>
  <li><b>ВНИМАНИЕ</b> (жёлтый) — что-то пошло не так, но не критично.</li>
  <li><b>ОШИБКА</b> (красный) — ставка не прошла, ошибка API.</li>
  <li><b>СИГНАЛ</b> (оранжевый) — сработала задержка между БК.</li>
</ul>

<p>Фильтры сверху помогают найти нужное:</p>
<ul>
  <li><b>Тип</b> — только сигналы / только ошибки / только успехи.</li>
  <li><b>Вид спорта</b> — оставить логи одного вида.</li>
  <li><b>БК</b> — логи конкретной букмекерской конторы.</li>
  <li><b>Матч</b> — по имени игрока.</li>
  <li><b>Поиск</b> — по тексту.</li>
</ul>

<p><b>Показывать детали</b> — включает debug-сообщения
(внутренние проверки, <code>best_bet</code>, <code>on_update</code>).
Без галки их не видно, чтобы не забивать экран.</p>

<hr/>

<h2>7. Словарь терминов</h2>

<table>
  <tr>
    <th>Термин</th>
    <th>Что значит</th>
  </tr>
  <tr>
    <td><b>Fast БК</b></td>
    <td>Быстрая букмекерская контора. Её счёт обновляется первым.
        Служит источником «правды»</td>
  </tr>
  <tr>
    <td><b>Slow БК</b></td>
    <td>Медленная БК. На ней ставим, потому что она ещё не
        пересчитала коэффициенты под реальный счёт</td>
  </tr>
  <tr>
    <td><b>Задержка</b></td>
    <td>Разница между счётом fast и slow. Если fast видит 8:5,
        а slow 6:5 — задержка = 2 очка</td>
  </tr>
  <tr>
    <td><b>Порог</b></td>
    <td>Минимальная задержка для конкретного рынка. Если порог
        Победителя = 3, то на победителя поставим только когда
        fast обогнал slow на 3+ очка</td>
  </tr>
  <tr>
    <td><b>PREOPEN</b></td>
    <td>Этап: приложение открывает вкладку с матчем на slow БК.
        Ещё не ставит, только готовится</td>
  </tr>
  <tr>
    <td><b>MONITOR</b></td>
    <td>Этап: приложение слушает данные с slow БК и сравнивает
        с fast. Длится до 20 секунд</td>
  </tr>
  <tr>
    <td><b>Фаза</b></td>
    <td>Партия в НТ/волейболе или четверть в баскетболе</td>
  </tr>
  <tr>
    <td><b>Рынок</b></td>
    <td>На что ставим: победитель / тотал / фора</td>
  </tr>
  <tr>
    <td><b>Сигнал</b></td>
    <td>Событие от бекенда: «нашёл задержку в матче X»</td>
  </tr>
</table>

<hr/>

<h2>8. Частые вопросы</h2>

<h3>Почему ставки не идут?</h3>
<ol>
  <li>Стратегия выключена (тумблер OFF).</li>
  <li>Нет активного профиля AdsPower — открой «Аккаунты» → проверь,
      что профиль привязан и AdsPower запущен.</li>
  <li>Задержка слишком маленькая — попробуй снизить
      <b>«Мин. задержка»</b> до 1.0–1.5 сек.</li>
  <li>Коэффициенты не попадают в диапазон <b>мин/макс</b> —
      расширь их (например, 1.2–5.0).</li>
  <li>Все рынки отсеиваются порогами — снизь пороги.</li>
</ol>

<h3>Ставка падает с «400 исход недоступен»</h3>
<p>Fast и slow БК уже синхронизировались к моменту отправки ставки.
Это нормальная ситуация на быстрых рынках (кибербаскет).
Система попробует ещё раз. Если ошибка массовая — снизь порог
или исключи кибер из стратегии.</p>

<h3>Баланс БК не отображается</h3>
<ul>
  <li>Стратегия выключена — баланс не подтягивается.</li>
  <li>Slow БК — Zenit (не реализован парсер баланса).</li>
  <li>Профиль AdsPower не запустился — открой «Аккаунты»
      и запусти вручную.</li>
</ul>

<h3>Лимит активных мониторингов = 2. Почему?</h3>
<p>AdsPower может держать на одном профиле не более 2 вкладок
одновременно. Если третий сигнал приходит, он игнорируется,
пока один из двух не закроется. Это техническое ограничение
AdsPower, а не приложения.</p>

<h3>Что такое «Оборот сегодня»?</h3>
<p>Это сумма <b>принятых</b> ставок за день. Не путать с прибылью —
прибыль будет считаться позже, когда появится расчёт выигрышей.</p>

<hr/>

<h2>9. Telegram-уведомления (скоро)</h2>
<p>Сейчас галка есть, но уведомления не отправляются.
Функционал появится в следующем обновлении: сигналы, принятые
ставки, ошибки будут приходить в Telegram-бот.</p>

<hr/>

<p class="hint" style="text-align:center; margin-top: 30px;">
Value Detector Pro · v1.0 · Если что-то непонятно — пиши в поддержку
</p>
"""


class TutorialDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Руководство пользователя")
        self.setMinimumSize(700, 600)

        layout = QVBoxLayout(self)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setHtml(TUTORIAL_HTML)
        text.setStyleSheet("""
            QTextEdit {
                background-color: #14181c;
                color: #eff7fb;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
                padding: 12px;
                font-size: 13px;
            }
        """)
        layout.addWidget(text)

        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)


class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(16)

        # ---- Telegram ----
        tg_group = QGroupBox("Telegram-уведомления")
        tg_form = QFormLayout(tg_group)
        self.telegram = QCheckBox("Получать уведомления в Telegram")
        tg_form.addRow(self.telegram)
        tg_hint = QLabel(
            "💡 Функционал появится в следующем обновлении. "
            "Галка сохраняется, но уведомления пока не отправляются."
        )
        tg_hint.setProperty("class", "hintLabel")
        tg_hint.setWordWrap(True)
        tg_form.addRow("", tg_hint)
        layout.addWidget(tg_group)

        # ---- Помощь ----
        help_group = QGroupBox("Помощь")
        help_layout = QVBoxLayout(help_group)

        help_hint = QLabel(
            "Подробное руководство по работе с приложением: "
            "как настроить аккаунты, стратегии, пороги по рынкам и "
            "понять логи системы."
        )
        help_hint.setWordWrap(True)
        help_layout.addWidget(help_hint)

        tutorial_btn = QPushButton("📖 Открыть туториал")
        tutorial_btn.setProperty("class", "primaryBtn")
        tutorial_btn.clicked.connect(self._open_tutorial)
        help_layout.addWidget(tutorial_btn, 0, Qt.AlignLeft)

        layout.addWidget(help_group)

        # ---- Учётная запись ----
        acc_group = QGroupBox("Учётная запись")
        acc_layout = QVBoxLayout(acc_group)

        logout_btn = QPushButton("🚪 Выйти из аккаунта")
        logout_btn.setProperty("class", "dangerBtn")
        logout_btn.setToolTip(
            "Удалить сохранённую сессию и перезапустить приложение"
        )
        logout_btn.clicked.connect(self._on_logout)
        acc_layout.addWidget(logout_btn, 0, Qt.AlignLeft)

        logout_hint = QLabel(
            "Выйти — значит забыть текущую сессию. При следующем "
            "запуске нужно будет снова ввести логин и пароль."
        )
        logout_hint.setProperty("class", "hintLabel")
        logout_hint.setWordWrap(True)
        acc_layout.addWidget(logout_hint)

        layout.addWidget(acc_group)

        # ---- Сохранить ----
        save_row = QHBoxLayout()
        save_btn = QPushButton("💾 Сохранить")
        save_btn.setProperty("class", "primaryBtn")
        save_btn.clicked.connect(self._on_save)
        save_row.addWidget(save_btn)
        save_row.addStretch()
        layout.addLayout(save_row)

        layout.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        # Загружаем сохранённые настройки
        self._load()

    def _load(self):
        s = load_settings()
        self.telegram.setChecked(bool(s.get("telegram_enabled", False)))

    def _on_save(self):
        save_settings({
            "telegram_enabled": self.telegram.isChecked(),
        })
        # Простой отклик — можно без всплывающих окон
        print("✅ Настройки сохранены")

    def _open_tutorial(self):
        dlg = TutorialDialog(self)
        dlg.exec_()

    def _on_logout(self):
        from ui.session_store import clear_session
        from ui.user_session import user_session

        reply = QMessageBox.question(
            self,
            "Выйти из аккаунта?",
            "Сессия будет удалена. При следующем запуске "
            "потребуется ввести логин и пароль.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        clear_session()
        user_session.clear()
        print("🚪 Сессия удалена, выходим")

        # Форсированный выход — приложение закроется,
        # при следующем старте покажется LoginWindow
        QApplication.quit()