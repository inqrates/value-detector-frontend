# ui/pages/settings.py
import json
import os

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QCheckBox, QGroupBox, QFormLayout, QPushButton,
    QScrollArea, QFrame, QTextEdit, QDialog, QDialogButtonBox,
    QMessageBox, QApplication, QLineEdit, QSpinBox
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from ui.paths import get_app_data_dir
from ui.log_bus import log_bus
from ui.fast_cookie_runner import FastCookieRunner

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
.tip { background: rgba(8,167,200,0.08); border-left: 3px solid #21c1de;
       padding: 8px 12px; margin: 10px 0; border-radius: 4px; }
</style>

<h1 style="color:#f5fbff;">Value Detector Pro — полное руководство</h1>
<p class="hint">Этот туториал объясняет всё, что есть в приложении —
каждый параметр, каждую кнопку, обе стратегии (Послегол и Лайв-валуй).
Читай по порядку, если запускаешь впервые.</p>

<hr/>

<h2>1. Что делает приложение</h2>

<p>Приложение ловит <b>задержки между букмекерскими конторами</b>.
Все БК получают данные матча (счёт, коэффициенты) от поставщиков,
но <b>с разной задержкой</b>: одна видит событие мгновенно, другая —
на 2–5 секунд позже. В этот промежуток мы и ставим: на медленной
БК, по ещё не пересчитанному коэффициенту.</p>

<p>Есть <b>два типа стратегий</b> — у них разная логика:</p>

<table>
  <tr>
    <th>Тип</th>
    <th>Когда ставим</th>
    <th>Риск</th>
  </tr>
  <tr>
    <td><b>Послегол</b></td>
    <td>Когда событие <b>уже случилось</b> на быстрой БК, а медленная
        ещё показывает старый счёт. Например: fast видит 11:5 в партии,
        slow — 10:5. Ставим на победу партии, которая <b>уже решена</b>.
    </td>
    <td>Почти нулевой (событие 100%)</td>
  </tr>
  <tr>
    <td><b>Лайв-валуй</b></td>
    <td>Когда кэф на медленной БК <b>сильно выше</b>, чем на быстрой,
        при одинаковом счёте. Событие ещё не завершено, но кэф
        устарел на 5%+.
    </td>
    <td>Есть (событие вероятностное)</td>
  </tr>
</table>

<p class="warn">⚠️ Ни одна из стратегий не является «вилкой» или
«арбитражем» в классическом смысле. Послегол — это точная ставка
по устаревшему кэфу. Лайв-валуй — это вероятностная ставка
на устаревший кэф.</p>

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
    <td><b>Fast-профиль</b></td>
    <td>Страница «Настройки»</td>
    <td>Отдельный AdsPower-профиль, из которого система берёт
        <b>быстрые</b> коэффициенты для сравнения.
        Логиниться в БК <b>не нужно</b>.</td>
  </tr>
</table>

<h3>Что такое «fast БК» и «slow БК»</h3>

<ul>
  <li><b>Fast БК</b> — быстрая контора. Она видит событие первой.
      Её данные — «эталон».</li>
  <li><b>Slow БК</b> — медленная контора. Она отстаёт. На ней ставим.</li>
</ul>

<p class="tip">💡 <b>Fast БК определяется автоматически</b> на бэкенде.
Приложение получает её в сигнале, вместе со slow БК. Выбирать
вручную ничего не нужно — система сама знает, кто быстрее.</p>

<hr/>

<h2>3. Первый запуск — пошагово</h2>

<h3>Шаг 1. Настроить AdsPower</h3>
<ol>
  <li>Установить и запустить <b>AdsPower</b>.</li>
  <li>Создать отдельный профиль для каждой БК, на которой
      собираетесь ставить (slow БК). Например: «Betcity-1»,
      «LigaStavok-1».</li>
  <li>Запустить каждый профиль вручную, залогиниться в БК,
      закрыть (AdsPower сам сохранит cookies).</li>
  <li>Скопировать <b>Profile ID</b> каждого профиля —
      он выглядит как <code>k1gy7l7t</code>.</li>
  <li>В AdsPower найти <b>API-ключ</b>: Настройки → API →
      скопировать ключ.</li>
</ol>

<h3>Шаг 2. Добавить аккаунты в приложении</h3>
<ol>
  <li>Открыть страницу <b>«Аккаунты»</b>.</li>
  <li>Заполнить: контора, логин, пароль, Profile ID, API Key.</li>
  <li>Нажать <b>«💾 Сохранить аккаунт»</b>. Повторить для каждой БК,
      на которой будете ставить.</li>
</ol>

<h3>Шаг 3. Настроить Fast-профиль (для лайв-валуя)</h3>

<p>Если вы собираетесь использовать стратегию <b>Послегол</b> —
этот шаг можно пропустить. Fast-профиль нужен <b>только</b>
для лайв-валуя.</p>

<ol>
  <li>Создать в AdsPower <b>отдельный</b> профиль для fast-БК.
      Логиниться никуда не надо — просто профиль.</li>
  <li>Открыть в приложении <b>«Настройки»</b>.</li>
  <li>В блоке <b>«Fast БК для валуёв»</b> указать:
    <ul>
      <li>AdsPower Profile ID этого профиля</li>
      <li>AdsPower API Key</li>
    </ul>
  </li>
  <li>Нажать <b>«💾 Сохранить»</b> внизу страницы.</li>
  <li>Приложение автоматически начнёт <b>сбор cookies</b>
      с fast БК. Это займёт 30–60 секунд. Прогресс видно
      в логе.</li>
</ol>

<p class="tip">💡 <b>Что такое сбор cookies.</b> Некоторые БК
(например, Winline, Betcity, LigaStavok) не отдают данные
без cookies — небольших файлов, которые браузер сохраняет
после входа на сайт. Приложение открывает эти сайты в
fast-профиле, забирает cookies и сохраняет их. Дальше все
запросы к БК идут с этими cookies, а не через браузер —
так быстрее и не жрёт ресурсы.</p>

<p class="warn">⚠️ Cookies живут 1–4 часа. Приложение
пересобирает их автоматически перед запуском стратегии,
если они устарели. Но если что-то не работает — нажмите
кнопку <b>«🔄 Пересобрать cookies»</b> в настройках.</p>

<h3>Шаг 4. Создать стратегию</h3>
<ol>
  <li>Открыть страницу <b>«Стратегии»</b>.</li>
  <li>Нажать <b>«➕ Создать»</b>.</li>
  <li>Заполнить форму (см. раздел 5).</li>
  <li>Нажать <b>«💾 Сохранить»</b>.</li>
</ol>

<h3>Шаг 5. Включить стратегию</h3>
<ol>
  <li>Тумблер в правом верхнем углу карточки стратегии → <b>ON</b>.</li>
  <li>Приложение запустит AdsPower, откроет профиль и зайдёт
      на live-раздел выбранной БК.</li>
  <li>В логах увидите <code>✅ Браузер для профиля X запущен</code>.</li>
  <li>Теперь ждём сигналы. Они появятся в логах и на дашборде.</li>
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
    <td>Сколько разных букмекеров в ваших аккаунтах</td>
  </tr>
  <tr>
    <td><b>Сигналов сегодня</b></td>
    <td>Сколько раз сегодня бэкенд нашёл задержку между
        fast и slow БК</td>
  </tr>
  <tr>
    <td><b>Оборот сегодня</b></td>
    <td>Сумма <b>принятых</b> ставок за день</td>
  </tr>
  <tr>
    <td><b>Ставок сегодня</b></td>
    <td>Количество принятых ставок за день</td>
  </tr>
  <tr>
    <td><b>БК отслеживается</b></td>
    <td>Сколько БК бэкенд парсит прямо сейчас</td>
  </tr>
</table>

<p>Кнопка <b>↻ Сбросить за сегодня</b> обнуляет оборот и счётчик
ставок. Сигналы не трогает.</p>

<hr/>

<h2>5. Стратегия — каждый параметр</h2>

<h3>5.1. Основные</h3>

<table>
  <tr><th>Поле</th><th>Что значит</th></tr>
  <tr>
    <td><b>Название</b></td>
    <td>Своё название для стратегии</td>
  </tr>
  <tr>
    <td><b>Тип стратегии</b></td>
    <td><b>Послегол</b> или <b>Валуй-Live</b>. От этого зависит вся
        логика работы (см. раздел 1).</td>
  </tr>
  <tr>
    <td><b>Вид спорта</b></td>
    <td>Какой спорт слушать. Можно выбрать один или «Все виды».
        <span class="hint">Рекомендуется отдельная стратегия
        под каждый спорт — пороги разные.</span></td>
  </tr>
  <tr>
    <td><b>Аккаунт для ставок</b></td>
    <td>Какой из ваших аккаунтов использовать. Это определяет
        <b>slow БК</b>.</td>
  </tr>
  <tr>
    <td><b>Мин. задержка</b></td>
    <td>Минимальный возраст сигнала (в секундах), при котором
        он считается валидным. Меньше — много шума, больше —
        мало сигналов.</td>
  </tr>
</table>

<h3>5.2. Что ставить (для Послегола)</h3>

<p>Два режима на выбор:</p>

<ul>
  <li><b>Автоматически</b> — бот сам выберет лучший исход из
      подтверждённых. Есть три критерия:
    <ul>
      <li><b>Самый надёжный</b> — приоритет победителю партии,
          потом тоталу, потом форе.</li>
      <li><b>Самый высокий коэффициент</b> — берётся максимальный
          кэф из доступных.</li>
      <li><b>Всё подтверждённое</b> — пока работает как «надёжный»
          (одна ставка за сигнал).</li>
    </ul>
  </li>
  <li><b>Вручную</b> — отмечаете галочками, на что ставить.
      Галочки:
    <ul>
      <li>Победитель партии: П1, П2</li>
      <li>Тотал партии: Больше, Меньше</li>
      <li>Фора в партии: Фора 1, Фора 2</li>
      <li>Индивидуальный тотал: ИТ1 Б/М, ИТ2 Б/М</li>
      <li>Дополнительно: Чёт/Нечёт, Гонка, Очко</li>
    </ul>
  </li>
</ul>

<p class="hint">💡 Если выбрали рынок, которого у БК нет — приложение
покажет предупреждение, но не запретит сохранить. Просто сигналы
по этому рынку не будут срабатывать.</p>

<h3>5.3. Параметры Лайв-Валуя (только для live-value)</h3>

<table>
  <tr><th>Поле</th><th>Что значит</th></tr>
  <tr>
    <td><b>Минимальный edge</b></td>
    <td>Насколько кэф на slow должен быть выше кэфа на fast,
        чтобы мы посчитали это валуем.<br/>
        Формула: <code>edge = slow / fast − 1</code><br/>
        Пример: slow = 2.05, fast = 1.75 → edge = 17%<br/>
        <span class="hint">По умолчанию 5%. Меньше — много сигналов,
        но edge съедается комиссией. Больше — редко, но метко.</span></td>
  </tr>
  <tr>
    <td><b>Рынки для сравнения</b></td>
    <td>Галочки, какие рынки сравнивать: Победитель, Тотал, Фора,
        ИТ, Чёт/Нечёт, Очко.
        <span class="hint">Рекомендую начать с Победитель + Тотал —
        они ликвидные и edge там стабильнее.</span></td>
  </tr>
</table>

<h3>5.4. Пороги отставания</h3>

<p>Минимальная разница по счёту (в очках) между fast и slow,
при которой исход считается «подтверждённым». Настраивается
для каждого рынка отдельно.</p>

<table>
  <tr><th>Рынок</th><th>Рекомендуемый порог</th></tr>
  <tr><td>Победитель (НТ)</td><td>2–3 очка</td></tr>
  <tr><td>Тотал (НТ)</td><td>1–2 очка</td></tr>
  <tr><td>Фора (НТ)</td><td>2–3 очка</td></tr>
  <tr><td>ИТ (НТ)</td><td>2 очка</td></tr>
  <tr><td>Победитель (волейбол)</td><td>3–4 очка</td></tr>
  <tr><td>Победитель (баскетбол)</td><td>6–10 очков</td></tr>
</table>

<p class="tip">💡 Под спиннерами всегда показывается подсказка
с рекомендованными порогами под выбранный вид спорта.</p>

<h3>5.5. RACE (гонка внутри сета)</h3>

<p>Гонка — это отрезок внутри партии, например «до 5 очков».
Некоторые БК дают отдельные рынки на гонку. <b>Работает только
у LigaStavok</b>. У остальных БК таких рынков нет.</p>

<h3>5.6. Ставка</h3>

<table>
  <tr><th>Поле</th><th>Что значит</th></tr>
  <tr>
    <td><b>Размер ставки</b></td>
    <td>Фиксированная сумма на каждую ставку</td>
  </tr>
  <tr>
    <td><b>Мин. коэффициент</b></td>
    <td>Не ставить, если кэф ниже. Защита от невыгодных
        кэфов 1.05–1.2</td>
  </tr>
  <tr>
    <td><b>Макс. коэффициент</b></td>
    <td>Не ставить, если кэф выше. Кэфы 3.0+ часто означают,
        что БК в курсе события</td>
  </tr>
</table>

<h3>5.7. Автоматизация</h3>

<table>
  <tr><th>Поле</th><th>Что значит</th></tr>
  <tr>
    <td><b>Пауза после сигнала</b></td>
    <td>Сколько ждать после открытия вкладки, чтобы данные
        успели загрузиться. 2–3 секунды.</td>
  </tr>
  <tr>
    <td><b>Макс. ставок на матч</b></td>
    <td>Лимит ставок по одному матчу. Обычно 1–3.</td>
  </tr>
  <tr>
    <td><b>Макс. ставок на фазу</b></td>
    <td>Лимит ставок в одной партии/сете. Обычно 1.</td>
  </tr>
  <tr>
    <td><b>Лимит ставок за сессию</b></td>
    <td>Остановить работу после N ставок за запуск приложения.
        0 = без лимита.</td>
  </tr>
  <tr>
    <td><b>Игнорировать повторы</b></td>
    <td>Реагировать только на новые сигналы, не переоткрывать
        матч на обновлениях.</td>
  </tr>
  <tr>
    <td><b>Скрытый режим</b></td>
    <td>AdsPower запускает браузер без окна. Экономит ресурсы,
        но не видно, что происходит. Включите для работы
        в фоне.</td>
  </tr>
</table>

<hr/>

<h2>6. Логи — как читать</h2>

<p>Каждая запись:</p>
<pre style="color:#cfdae2;">
[12:34:56] ● ИНФО [Betcity→Fonbet] Иванов vs Петров
         Fast БК (Fonbet, впереди) счёт 8:5 · П1 1.55 / П2 2.40
         Slow БК (Betcity, отстаёт) счёт 6:5 · П1 1.70 / П2 2.10
         Разница: +2
</pre>

<p>Уровни:</p>
<ul>
  <li><b>ИНФО</b> (синий) — служебные события</li>
  <li><b>OK</b> (зелёный) — успех: ставка принята, сигнал подтверждён</li>
  <li><b>ВНИМАНИЕ</b> (жёлтый) — не критично, но стоит обратить внимание</li>
  <li><b>ОШИБКА</b> (красный) — ставка не прошла, ошибка API</li>
  <li><b>СИГНАЛ</b> (оранжевый) — найдена задержка между БК</li>
</ul>

<p>Фильтры сверху:</p>
<ul>
  <li><b>Тип</b> — сигналы / ошибки / успехи</li>
  <li><b>Вид спорта</b> — оставить логи одного вида</li>
  <li><b>БК</b> — логи конкретной конторы</li>
  <li><b>Матч</b> — по имени игрока</li>
  <li><b>Поиск</b> — по тексту</li>
  <li><b>Показывать детали</b> — включает debug-логи (best_bet,
      on_update и т.п.)</li>
</ul>

<p class="tip">💡 Если ставите стратегию Лайв-Валуй, в логах ищите
строки <code>💎 LV: ...</code> — там указан edge и сравнение кэфов.</p>

<hr/>

<h2>7. Словарь терминов</h2>

<table>
  <tr><th>Термин</th><th>Значение</th></tr>
  <tr>
    <td><b>Fast БК</b></td>
    <td>Быстрая контора. Её данные — эталон. Определяется
        бэкендом автоматически.</td>
  </tr>
  <tr>
    <td><b>Slow БК</b></td>
    <td>Медленная БК, на которой ставим.</td>
  </tr>
  <tr>
    <td><b>Задержка</b></td>
    <td>Разница в счёте или кэфах между fast и slow.</td>
  </tr>
  <tr>
    <td><b>Порог</b></td>
    <td>Минимальная задержка по счёту, при которой исход
        считается подтверждённым.</td>
  </tr>
  <tr>
    <td><b>Edge</b></td>
    <td>Разница в кэфах: <code>slow / fast − 1</code>.
        Используется в Лайв-Валуе.</td>
  </tr>
  <tr>
    <td><b>PREOPEN</b></td>
    <td>Этап: открытие вкладки матча на slow БК.</td>
  </tr>
  <tr>
    <td><b>MONITOR</b></td>
    <td>Этап: сравнение данных fast и slow. Длится
        до 20 секунд.</td>
  </tr>
  <tr>
    <td><b>Фаза</b></td>
    <td>Партия в НТ/волейболе, четверть в баскетболе.</td>
  </tr>
  <tr>
    <td><b>Рынок</b></td>
    <td>На что ставим: победитель / тотал / фора / ИТ /
        чёт-нечёт / очко.</td>
  </tr>
</table>

<hr/>

<h2>8. Частые вопросы</h2>

<h3>Почему ставки не идут?</h3>
<ol>
  <li>Стратегия выключена.</li>
  <li>Нет активного профиля AdsPower — проверьте на странице
      «Аккаунты», что профиль привязан и AdsPower запущен.</li>
  <li>Мин. задержка слишком высокая — попробуйте снизить
      до 1.0–1.5 сек.</li>
  <li>Коэффициенты не попадают в диапазон мин/макс — расширьте.</li>
  <li>Все рынки отсеиваются порогами — снизьте пороги.</li>
  <li>Для Лайв-Валуя: edge ниже порога — снизьте минимальный edge.</li>
</ol>

<h3>Лайв-Валуй не ставит, хотя сигналы идут</h3>
<p>Проверьте в логах:</p>
<ul>
  <li><code>live_value: нет свежих fast_markets</code> — fast-кэфы
      не успевают обновляться. Возможно, выбранная fast БК не
      покрывает этот матч.</li>
  <li><code>live_value: фазы разные</code> — fast и slow в разных
      партиях. Ждём синхронизации.</li>
  <li><code>live_value: нет кандидатов</code> — edge ниже порога.
      Снизьте минимальный edge.</li>
</ul>

<h3>Fast-профиль не запускается</h3>
<ul>
  <li>Проверьте, что в настройках указаны <b>Profile ID</b>
      и <b>API Key</b>.</li>
  <li>Проверьте, что этот профиль не запущен сейчас вручную
      в AdsPower — одновременно нельзя.</li>
  <li>Если профиль запущен — закройте его в AdsPower и попробуйте
      снова.</li>
</ul>

<h3>Cookies протухли — что делать</h3>
<p>Настройки → кнопка <b>«🔄 Пересобрать cookies»</b>. Занимает
около минуты. Приложение само найдёт те БК, которым нужны
cookies, и пересоберёт их.</p>

<h3>Как понять, работает ли Лайв-Валуй</h3>
<p>Смотрите логи на строки <code>💎 LV: ...</code>. Если за час
приходит 5+ таких сигналов с edge &gt; 5% — стратегия рабочая.
Если сигналов нет — либо рынки неликвидные, либо edge слишком
маленький (снизьте порог до 3%).</p>

<h3>Максимум активных вкладок = 2. Почему?</h3>
<p>AdsPower не даёт открыть больше 2 вкладок на один профиль
одновременно. Если сигналов больше — они становятся в очередь.
Это техническое ограничение.</p>

<hr/>

<p class="hint" style="text-align:center; margin-top: 30px;">
Value Detector Pro · v1.1 · Если что-то непонятно — пиши в поддержку
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

        # ---- Fast БК для валуёв ----
        fast_group = QGroupBox("Fast БК для валуёв (live-value)")
        fast_layout = QVBoxLayout(fast_group)
        fast_layout.setSpacing(10)

        fast_hint = QLabel(
            "Укажите AdsPower-профиль, который будет использоваться\n"
            "для автоматического сбора cookies с fast БК.\n"
            "Логиниться в БК не нужно — система всё делает сама.\n\n"
            "Если поле пустое — валуйные стратегии работать не будут."
        )
        fast_hint.setWordWrap(True)
        fast_hint.setProperty("class", "hintLabel")
        fast_layout.addWidget(fast_hint)

        fast_form = QFormLayout()
        fast_form.setSpacing(8)

        self.fast_profile_edit = QLineEdit()
        self.fast_profile_edit.setPlaceholderText("Например: k1h5id2p")
        fast_form.addRow("AdsPower Profile ID:", self.fast_profile_edit)

        self.fast_api_key_edit = QLineEdit()
        self.fast_api_key_edit.setPlaceholderText("API-ключ AdsPower")
        self.fast_api_key_edit.setEchoMode(QLineEdit.Password)
        fast_form.addRow("AdsPower API Key:", self.fast_api_key_edit)

        fast_layout.addLayout(fast_form)

        # ---- Список БК, что будут использоваться (только для справки) ----
        from core.fast_config import FAST_BKS_ALL
        bks_text = " · ".join(
            meta["label"] + ("*" if meta["needs_cookies"] else "")
            for meta in FAST_BKS_ALL.values()
        )
        bks_label = QLabel(
            f"<b>Fast БК:</b> {bks_text}<br>"
            f"<span style='color: rgba(199,214,223,0.5); font-size: 10px;'>"
            f"* — требуется сбор cookies (делается автоматически)</span>"
        )
        bks_label.setWordWrap(True)
        bks_label.setStyleSheet(
            "color: rgba(245,249,252,0.85); font-size: 11px; padding: 4px 0;")
        fast_layout.addWidget(bks_label)

        # ---- Кнопка (для диагностики) ----
        fast_btn_row = QHBoxLayout()
        self.fast_collect_btn = QPushButton("🔄 Пересобрать cookies")
        self.fast_collect_btn.setProperty("class", "ghostBtn")
        self.fast_collect_btn.setToolTip(
            "Обычно не требуется — система собирает cookies автоматически.\n"
            "Нажмите, если что-то перестало работать."
        )
        self.fast_collect_btn.clicked.connect(self._collect_fast_cookies)

        self.fast_test_btn = QPushButton("🧪 Проверить")
        self.fast_test_btn.setProperty("class", "ghostBtn")
        self.fast_test_btn.clicked.connect(self._test_fast_bks)

        fast_btn_row.addWidget(self.fast_collect_btn)
        fast_btn_row.addWidget(self.fast_test_btn)
        fast_btn_row.addStretch()
        fast_layout.addLayout(fast_btn_row)

        # ---- Статус ----
        self.fast_status_label = QLabel("Статус: не настроено")
        self.fast_status_label.setStyleSheet(
            "color: rgba(199,214,223,0.62); font-size: 11px; padding: 4px 0;")
        self.fast_status_label.setWordWrap(True)
        fast_layout.addWidget(self.fast_status_label)

        layout.addWidget(fast_group)

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

        # ── Fast БК ──
        try:
            from core.fast_config import (
                load_fast_config, cookies_age_hours, get_enabled_fast_bks,
            )
            fc = load_fast_config()
            self.fast_profile_edit.setText(fc.get("profile_id", ""))
            self.fast_api_key_edit.setText(fc.get("api_key", ""))

            last_at = fc.get("last_collect_at", "")
            status = fc.get("last_collect_status", {}) or {}

            if not fc.get("profile_id"):
                self.fast_status_label.setText("Статус: укажите Profile ID и API Key")
            elif last_at:
                lines = [f"Последний сбор: {last_at}"]
                for bk in get_enabled_fast_bks():
                    age = cookies_age_hours(bk)
                    if age is None:
                        lines.append(f"  • {bk}: cookies не собраны")
                    elif age > 24:
                        lines.append(f"  • {bk}: ⚠️ устарели ({age:.1f} ч)")
                    else:
                        lines.append(f"  • {bk}: ✅ {age:.1f} ч")
                self.fast_status_label.setText("\n".join(lines))
            else:
                self.fast_status_label.setText("Статус: ещё не собирались")
        except Exception as e:
            self.fast_status_label.setText(f"Ошибка: {e}")

    def _on_save(self):
        # ── Общие настройки ──
        save_settings({
            "telegram_enabled": self.telegram.isChecked(),
        })

        # ── Fast БК ──
        try:
            from core.fast_config import load_fast_config, save_fast_config
            fc = load_fast_config()

            old_profile = fc.get("profile_id", "")
            new_profile = self.fast_profile_edit.text().strip()
            new_api_key = self.fast_api_key_edit.text().strip()

            fc["profile_id"] = new_profile
            fc["api_key"] = new_api_key
            # fast_bks всегда фиксированный список, не пользовательский
            save_fast_config(fc)

            # Если профиль только что указали или изменили — запускаем
            # автосбор cookies в фоне (один раз, не блокируя UI).
            if new_profile and new_api_key and (
                new_profile != old_profile
                or not fc.get("last_collect_at")
            ):
                log_bus.info("Настройки", "Собираю cookies fast БК...")
                self._auto_collect_cookies()
            else:
                log_bus.success("Настройки", "Настройки fast БК сохранены")
        except Exception as e:
            log_bus.error("Настройки", f"Fast БК: {e}")
            print(f"❌ Fast БК: {e}")

        print("✅ Настройки сохранены")

    def _open_tutorial(self):
        dlg = TutorialDialog(self)
        dlg.exec_()

    # ============================================================
    # Fast БК: сбор cookies и тест
    # ============================================================
    def _collect_fast_cookies(self):
        profile_id = self.fast_profile_edit.text().strip()
        api_key = self.fast_api_key_edit.text().strip()

        if not profile_id or not api_key:
            QMessageBox.warning(
                self, "Fast БК",
                "Укажи AdsPower Profile ID и API Key."
            )
            return

        # Собираем cookies только с тех БК, которым они нужны.
        # Список фиксированный — берём из fast_config.
        from core.fast_config import bks_needing_cookies
        selected = bks_needing_cookies()
        if not selected:
            QMessageBox.warning(
                self, "Fast БК",
                "Нет БК, требующих cookies."
            )
            return

        # Сохраняем конфиг перед сбором
        self._on_save()

        self.fast_collect_btn.setEnabled(False)
        self.fast_collect_btn.setText("⏳ Сбор cookies...")

        from ui.fast_cookie_runner import FastCookieRunner
        self._fast_runner = FastCookieRunner(
            profile_id=profile_id,
            api_key=api_key,
            headless=True,
            bks=selected,
        )
        self._fast_runner.finished.connect(self._on_collect_finished)
        self._fast_runner.progress.connect(self._on_collect_progress)
        self._fast_runner.start()

    def _auto_collect_cookies(self):
        """Запускает сбор cookies со ВСЕХ БК, требующих их. Без диалогов."""
        from core.fast_config import (
            load_fast_config, get_enabled_fast_bks, ALL_FAST_BKS,
        )
        fc = load_fast_config()
        profile_id = fc.get("profile_id")
        api_key = fc.get("api_key")

        if not profile_id or not api_key:
            return

        # Собираем только те, кому нужны cookies
        bks_to_collect = [
            bk for bk in get_enabled_fast_bks()
            if ALL_FAST_BKS.get(bk, {}).get("needs_cookies")
        ]
        if not bks_to_collect:
            return

        self.fast_collect_btn.setEnabled(False)
        self.fast_collect_btn.setText("⏳ Сбор cookies...")

        from ui.fast_cookie_runner import FastCookieRunner
        self._fast_runner = FastCookieRunner(
            profile_id=profile_id,
            api_key=api_key,
            headless=True,
            bks=bks_to_collect,
        )
        self._fast_runner.finished.connect(self._on_collect_finished)
        self._fast_runner.progress.connect(self._on_collect_progress)
        self._fast_runner.start()

    def _on_collect_progress(self, message: str):
        self.fast_status_label.setText(message)

    def _on_collect_finished(self, status: dict):
        self.fast_collect_btn.setEnabled(True)
        self.fast_collect_btn.setText("🔐 Собрать cookies сейчас")

        from datetime import datetime
        from core.fast_config import load_fast_config, save_fast_config
        fc = load_fast_config()
        fc["last_collect_at"] = datetime.now().isoformat(timespec="seconds")
        fc["last_collect_status"] = status
        save_fast_config(fc)

        ok = [bk for bk, st in status.items() if st == "ok"]
        err = [bk for bk, st in status.items() if st != "ok"]

        msg = f"Готово: {len(ok)} успешно"
        if err:
            msg += f", {len(err)} с ошибкой"
        msg += f"\n{status}"

        self.fast_status_label.setText(msg)

        if err:
            log_bus.warning("Fast БК", f"Сбор cookies: ошибки в {err}")
        else:
            log_bus.success("Fast БК", "Все cookies собраны")

    def _test_fast_bks(self):
        """Проверяет, что fast-клиенты работают и cookies свежие."""
        from core.fast_config import (
            load_fast_config, load_cookies, cookies_age_hours,
            ALL_FAST_BKS,
        )
        fc = load_fast_config()
        lines = []

        for bk in fc.get("fast_bks", []):
            meta = ALL_FAST_BKS.get(bk, {})
            if meta.get("needs_cookies"):
                age = cookies_age_hours(bk)
                if age is None:
                    lines.append(f"❌ {bk}: cookies не собраны")
                elif age > 24:
                    lines.append(f"⚠️ {bk}: cookies устарели ({age:.1f} ч)")
                else:
                    n = len(load_cookies(bk))
                    lines.append(f"✅ {bk}: {n} cookies, {age:.1f} ч")
            else:
                lines.append(f"✅ {bk}: без cookies (HTTP)")

        QMessageBox.information(
            self, "Fast БК",
            "\n".join(lines) or "Нет выбранных БК"
        )

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