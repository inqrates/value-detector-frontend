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
.good { color: #42d78d; font-weight: 600; }
.tip { background: rgba(8,167,200,0.08); border-left: 3px solid #21c1de;
       padding: 8px 12px; margin: 10px 0; border-radius: 4px; }
.danger { background: rgba(235,87,87,0.08); border-left: 3px solid #eb5757;
          padding: 8px 12px; margin: 10px 0; border-radius: 4px; }
</style>

<h1 style="color:#f5fbff;">Value Detector Pro — полное руководство</h1>
<p class="hint">Версия на встроенном браузере Patchright. AdsPower больше
не нужен — браузер встроен в приложение, профили хранятся локально.
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
  <tr><th>Тип</th><th>Когда ставим</th><th>Риск</th></tr>
  <tr>
    <td><b>Послегол</b></td>
    <td>Событие <b>уже случилось</b> на быстрой БК, а медленная
        ещё показывает старый счёт. Например: fast видит 11:5 в партии,
        slow — 10:5. Ставим на исход, который <b>уже решён</b>.</td>
    <td>Почти нулевой (событие 100%)</td>
  </tr>
  <tr>
    <td><b>Лайв-валуй</b></td>
    <td>Кэф на медленной БК <b>сильно выше</b>, чем на быстрой,
        при одинаковом счёте. Событие ещё не завершено, но кэф
        устарел на 5%+.</td>
    <td>Есть (событие вероятностное)</td>
  </tr>
</table>

<p class="warn">⚠️ Ни одна из стратегий не является «вилкой» или
«арбитражем» в классическом смысле. Послегол — точная ставка
по устаревшему кэфу. Лайв-валуй — вероятностная ставка.</p>

<h3>Два сценария послегола</h3>
<ul>
  <li><b>Обе БК в одной партии</b> — fast впереди по очкам.
      Ставим тотал больше / ИТ больше, а если партия уже решена —
      победителя, тотал меньше, фору.</li>
  <li><b>Fast ушёл на партию вперёд</b> — партия завершилась,
      fast уже начал следующую, а slow ещё торгует завершившейся.
      Играем её рынки по финальному счёту. Это самый «жирный» кейс.</li>
</ul>

<div class="tip">🛡 Второй сценарий защищён двумя предохранителями:
<b>Ghost Frame</b> (если счёт по партиям не изменился — данные битые,
пропускаем) и <b>Math Guard</b> (финал должен быть математически
завершённым: 11+ и разница 2 для НТ). Лучше пропустить ставку,
чем поставить на «фантомную» партию.</div>

<hr/>

<h2>2. Как приложение устроено</h2>

<table>
  <tr><th>Что</th><th>Где настраивается</th><th>Зачем</th></tr>
  <tr>
    <td><b>Аккаунт</b></td>
    <td>Страница «Аккаунты»</td>
    <td>Логин/пароль от БК + ID профиля браузера, в котором
        вы залогинены</td>
  </tr>
  <tr>
    <td><b>Профиль браузера</b></td>
    <td>«Аккаунты» → ⚙️ Настройки профиля</td>
    <td>Прокси, отпечаток, экран, часовой пояс — как в антидетекте</td>
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
    <td>Отдельный профиль, из которого система берёт
        <b>быстрые</b> коэффициенты и собирает cookies.
        Логиниться в БК <b>не нужно</b>.</td>
  </tr>
</table>

<h3>Что такое профиль браузера теперь</h3>
<p>Профиль — это <b>папка на вашем диске</b>:</p>
<pre style="color:#cfdae2;">%APPDATA%\\ValueDetectorPro\\profiles\\&lt;ID профиля&gt;\\</pre>
<p>Внутри — cookies, сессии, история браузера и файл
<code>profile.json</code> с настройками прокси и отпечатка.
Никакого внешнего приложения не требуется.</p>

<div class="danger">⚠️ <b>Если вы перешли со старой версии (AdsPower):</b>
старые ID профилей стали новыми пустыми папками. Куки не переносятся —
по каждому аккаунту нужно один раз нажать «🚀 Открыть профиль»
и залогиниться в БК заново.</div>

<h3>Что такое «fast БК» и «slow БК»</h3>
<ul>
  <li><b>Fast БК</b> — быстрая контора. Её данные — «эталон».</li>
  <li><b>Slow БК</b> — медленная контора. Она отстаёт. На ней ставим.</li>
</ul>
<p class="tip">💡 <b>Fast БК определяется автоматически</b> на бэкенде.
Приложение получает её в сигнале вместе со slow БК. Выбирать
вручную ничего не нужно.</p>

<hr/>

<h2>3. Первый запуск — пошагово</h2>

<h3>Шаг 1. Браузер уже встроен</h3>
<p>Ничего устанавливать не нужно — патченный Chromium идёт
вместе с приложением. Если браузер не стартует и в логах ошибка
про отсутствие Chromium, выполните один раз в терминале:</p>
<pre style="color:#cfdae2;">python -m patchright install chromium</pre>

<h3>Шаг 2. Добавить аккаунты</h3>
<ol>
  <li>Откройте страницу <b>«Аккаунты»</b>.</li>
  <li>Заполните: контора, логин, пароль, <b>ID профиля</b>.</li>
  <li>ID профиля — <b>любое имя латиницей</b>: <code>betcity_1</code>,
      <code>fonbet_main</code>, <code>liga_2</code>. Придумываете сами.</li>
  <li>Нажмите <b>«💾 Сохранить аккаунт»</b>.</li>
  <li>Выделите строку в таблице → <b>«🚀 Открыть профиль»</b>.
      Откроется браузер на сайте вашей БК.</li>
  <li><b>Залогиньтесь в БК вручную</b> в этом окне. Сессия сохранится
      в папке профиля навсегда — повторный вход не потребуется.</li>
  <li>Можете закрыть браузер кнопкой <b>«⏹ Закрыть профиль»</b>
      или оставить открытым — стратегия переиспользует его.</li>
</ol>

<div class="tip">💡 Один профиль = один аккаунт БК. Не используйте
один ID для двух разных аккаунтов — сессии перезапишут друг друга.</div>

<h3>Шаг 3. Настроить профиль (по желанию)</h3>
<p>Выделите аккаунт → <b>«⚙️ Настройки профиля»</b>. Откроется панель
как в антидетект-браузере: прокси, отпечаток, экран, часовой пояс.
Подробнее — раздел 4.</p>
<p class="hint">Если прокси не нужны — можно пропустить этот шаг,
профиль работает с вашим реальным IP и случайным отпечатком.</p>

<h3>Шаг 4. Fast-профиль (только для лайв-валуя)</h3>
<p>Если используете стратегию <b>Послегол</b> — шаг можно пропустить.
Fast-профиль нужен <b>только</b> для лайв-валуя.</p>
<ol>
  <li>Откройте <b>«Настройки»</b> → блок «Fast БК для валуёв».</li>
  <li>Впишите <b>отдельный</b> ID профиля, например <code>fast_1</code>.</li>
  <li>Нажмите <b>«💾 Сохранить»</b> — сбор cookies запустится сам.
      Займёт 1–3 минуты, прогресс виден в статусе и логах.</li>
  <li>Логиниться никуда не надо — профиль просто заходит на сайты
      БК за антибот-cookies.</li>
</ol>

<div class="danger">⚠️ <b>ID fast-профиля обязан отличаться от ID
ставочных аккаунтов.</b> Перед каждой БК сборщик стирает все cookies
в профиле. Если укажете ID ставочного аккаунта — он будет разлогинен.</div>

<div class="danger">⚠️ <b>Не запускайте сбор cookies, пока работают
live-value стратегии.</b> Они используют тот же fast-профиль,
а два браузера на одной папке одновременно открыть нельзя.
Сначала выключите live-value стратегии, потом собирайте cookies.</div>

<h3>Какие БК требуют cookies</h3>
<table>
  <tr><th>Без cookies (работают сразу)</th><th>Нужны cookies</th></tr>
  <tr>
    <td>Fonbet, Pari, Olimp, Sportbet</td>
    <td>Betcity, Leon, Marathon, LigaStavok, Winline, Zenit</td>
  </tr>
</table>

<p class="tip">💡 <b>Что такое сбор cookies.</b> Некоторые БК не отдают
данные без cookies — небольших файлов, которые браузер сохраняет
после входа на сайт. Приложение открывает эти сайты в fast-профиле,
забирает cookies и сохраняет. Дальше все запросы к БК идут с этими
cookies, а не через браузер — так быстрее и не жрёт ресурсы.</p>

<p class="warn">⚠️ Cookies живут 1–4 часа. Если что-то перестало
работать — нажмите <b>«🔄 Пересобрать cookies»</b> в настройках.
<b>После пересбора перезапустите приложение</b> — клиенты читают
cookies один раз при старте.</p>

<h3>Шаг 5. Создать стратегию</h3>
<ol>
  <li>Страница <b>«Стратегии»</b> → <b>«➕ Создать»</b>.</li>
  <li>Заполните форму (см. раздел 6).</li>
  <li>Нажмите <b>«💾 Сохранить»</b>.</li>
</ol>

<h3>Шаг 6. Включить стратегию</h3>
<ol>
  <li>Тумблер в правом верхнем углу карточки → <b>ON</b>.</li>
  <li>Приложение откроет профиль и зайдёт на live-раздел БК.</li>
  <li>В логах увидите <code>✅ Браузер для профиля X готов</code>.</li>
  <li>Теперь ждём сигналы — они появятся в логах и на дашборде.</li>
</ol>

<hr/>

<h2>4. Панель настроек профиля</h2>

<p>Открывается кнопкой <b>«⚙️ Настройки профиля»</b> на странице
«Аккаунты». Всё сохраняется в <code>profile.json</code> внутри папки
профиля и применяется при <b>следующем запуске</b> профиля.</p>

<h3>Прокси</h3>
<table>
  <tr><th>Поле</th><th>Что значит</th></tr>
  <tr><td><b>Сервер</b></td>
      <td><code>http://host:port</code> или <code>socks5://host:port</code>.
          Пусто = без прокси, ваш реальный IP.</td></tr>
  <tr><td><b>Логин / Пароль</b></td>
      <td>Если прокси с авторизацией.</td></tr>
</table>
<p class="hint">Для БК лучше резидентные прокси. Дата-центровые
IP антиботы блокируют чаще.</p>

<h3>Личность профиля (отпечаток)</h3>
<p>Каждому профилю выдаётся <b>записанный отпечаток реального устройства</b>
(пресет Camoufox): экран, число ядер, видеокарта, список голосов и User-Agent
берутся из настоящей машины и не меняются между запусками.</p>
<table>
  <tr><th>Поле</th><th>Что значит</th></tr>
  <tr><td><b>Карточка личности</b></td>
      <td>Показывает, каким устройством «представляется» профиль.
          Только для чтения: поля должны совпадать друг с другом,
          ручная правка одного из них выдаёт подмену.</td></tr>
  <tr><td><b>🎲 Новая личность</b></td>
      <td>Выдаёт другой реальный отпечаток. Меняйте только при смене
          прокси — частая ротация на одном IP выглядит подозрительно.</td></tr>
  <tr><td><b>Часовой пояс / Язык</b></td>
      <td><b>По IP прокси</b> — рекомендуется: локаль и таймзона
          совпадут со страной IP. «Вручную» — только если уверены,
          что значения совпадают с прокси.</td></tr>
  <tr><td><b>WebRTC</b></td>
      <td><b>Блокировать</b> — рекомендуется: иначе сайт может узнать
          ваш локальный IP в обход прокси. На работу сайтов не влияет.</td></tr>
</table>
<p class="hint">Canvas, Audio, ClientRects и список голосов подменяются
движком автоматически, поэтому в панели их нет.</p>

<h3>Железо и сеть</h3>
<table>
  <tr><th>Поле</th><th>Что значит</th></tr>
  <tr><td><b>CPU / RAM</b></td>
      <td>Берутся из личности профиля и жёстко закрепляются за ней.</td></tr>
  <tr><td><b>Do Not Track</b></td>
      <td>Не настраивается: включённый DNT выделяет браузер из общей массы,
          поэтому движок оставляет его по умолчанию.</td></tr>
</table>

<div class="tip">💡 <b>MAC-адрес и имя устройства</b> сайты прочитать
не могут — их подмена не нужна, поэтому в панели их нет.
<b>TLS-отпечаток</b> у встроенного браузера как у настоящего Chrome —
это плюс, его тоже не трогаем.</div>

<p class="warn">⚠️ Настройки применяются при следующем запуске профиля.
Если профиль открыт — закройте его и откройте заново.</p>

<hr/>

<h2>5. Дашборд — что означают цифры</h2>

<table>
  <tr><th>Карточка</th><th>Что показывает</th></tr>
  <tr><td><b>БК в работе</b></td><td>Сколько разных БК в ваших аккаунтах</td></tr>
  <tr><td><b>Сигналов сегодня</b></td>
      <td>Сколько раз бэкенд нашёл задержку между fast и slow</td></tr>
  <tr><td><b>Оборот сегодня</b></td><td>Сумма <b>принятых</b> ставок за день</td></tr>
  <tr><td><b>Ставок сегодня</b></td><td>Количество принятых ставок</td></tr>
  <tr><td><b>БК отслеживается</b></td><td>Сколько БК бэкенд парсит сейчас</td></tr>
</table>
<p>Кнопка <b>↻ Сбросить за сегодня</b> обнуляет оборот и счётчик
ставок. Сигналы не трогает.</p>

<hr/>

<h2>6. Стратегия — каждый параметр</h2>

<h3>6.1. Основные</h3>
<table>
  <tr><th>Поле</th><th>Что значит</th></tr>
  <tr><td><b>Название</b></td><td>Своё имя для стратегии</td></tr>
  <tr><td><b>Тип стратегии</b></td>
      <td><b>Послегол</b> или <b>Валуй-Live</b> (см. раздел 1)</td></tr>
  <tr><td><b>Вид спорта</b></td>
      <td>Какой спорт слушать. Рекомендуется отдельная стратегия
          под каждый спорт — пороги разные.</td></tr>
  <tr><td><b>Аккаунт для ставок</b></td>
      <td>Какой аккаунт использовать. Определяет <b>slow БК</b>
          и профиль браузера.</td></tr>
  <tr><td><b>Мин. задержка</b></td>
      <td>Сколько секунд задержка уже должна длиться, чтобы сигнал
          считался валидным. Меньше — больше шума, больше — меньше
          сигналов. Рекомендую 1.0–2.0 сек.</td></tr>
</table>

<h3>6.2. Что ставить (для Послегола)</h3>
<ul>
  <li><b>Автоматически</b> — бот сам выберет лучший исход:
    <ul>
      <li><b>Самый надёжный</b> — победитель → тотал → фора.</li>
      <li><b>Самый высокий коэффициент</b> — максимальный кэф.</li>
      <li><b>Всё подтверждённое</b> — пока как «надёжный».</li>
    </ul>
  </li>
  <li><b>Вручную</b> — отмечаете галочками: П1/П2, тотал Б/М,
      фора 1/2, ИТ1/ИТ2 Б/М, чёт/нечёт, гонка, очко.</li>
</ul>
<p class="hint">💡 Если выбрали рынок, которого у БК нет — приложение
предупредит, но сохранит. Сигналы по нему просто не сработают.</p>

<h3>6.3. Параметры Лайв-Валуя</h3>
<table>
  <tr><th>Поле</th><th>Что значит</th></tr>
  <tr><td><b>Минимальный edge</b></td>
      <td>Насколько кэф slow должен быть выше fast.<br/>
          Формула: <code>edge = slow / fast − 1</code><br/>
          Пример: slow 2.05, fast 1.75 → edge 17%.<br/>
          По умолчанию 5%.</td></tr>
  <tr><td><b>Максимальный edge</b></td>
      <td>Всё выше — мусор: кэф устарел, рынок закрывается,
          рассинхрон. Реальные валуи — 5–20%.</td></tr>
  <tr><td><b>Способ детекции</b></td>
      <td><b>Кэфы + задержка по счёту</b> — консервативно, меньше
          ставок, надёжнее.<br/>
          <b>Только кэфы</b> — больше ставок, винрейт может быть ниже.</td></tr>
  <tr><td><b>Рынки для сравнения</b></td>
      <td>Начните с Победитель + Тотал — они ликвидные.</td></tr>
</table>

<h3>6.4. Пороги отставания</h3>
<p>Минимальная разница по счёту между fast и slow, при которой
исход считается подтверждённым. Настраивается по рынкам.</p>
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

<h3>6.5. RACE (гонка внутри сета)</h3>
<p>Гонка — отрезок внутри партии, например «до 5 очков».
<b>Работает только у LigaStavok.</b> У остальных БК таких рынков нет.</p>

<h3>6.6. Ставка</h3>
<table>
  <tr><th>Поле</th><th>Что значит</th></tr>
  <tr><td><b>Размер ставки</b></td><td>Фиксированная сумма на каждую ставку</td></tr>
  <tr><td><b>Мин. коэффициент</b></td>
      <td>Не ставить ниже. Защита от невыгодных 1.05–1.2</td></tr>
  <tr><td><b>Макс. коэффициент</b></td>
      <td>Не ставить выше. Кэфы 3.0+ часто значат, что БК в курсе события</td></tr>
</table>

<h3>6.7. Автоматизация</h3>
<table>
  <tr><th>Поле</th><th>Что значит</th></tr>
  <tr><td><b>Пауза после сигнала</b></td>
      <td>Сколько ждать после открытия вкладки. 1–3 секунды.
          Меньше — быстрее ставка, но данные могут не успеть.</td></tr>
  <tr><td><b>Макс. ставок на матч</b></td><td>Обычно 1–3</td></tr>
  <tr><td><b>Макс. ставок на фазу</b></td><td>Обычно 1</td></tr>
  <tr><td><b>Лимит ставок за сессию</b></td>
      <td>Остановить работу после N ставок. 0 = без лимита.</td></tr>
  <tr><td><b>Игнорировать повторы</b></td>
      <td>Реагировать только на новые сигналы</td></tr>
  <tr><td><b>Скрытый режим</b></td>
      <td>Браузер без окна — экономит ресурсы.
          <span class="warn">Но Qrator (LigaStavok) и другие антиботы
          могут не пропустить headless.</span> Для проблемных БК
          оставляйте выключенным.</td></tr>
</table>

<hr/>

<h2>7. Логи — как читать</h2>

<p>Уровни:</p>
<ul>
  <li><b>ИНФО</b> (синий) — служебные события</li>
  <li><b>OK</b> (зелёный) — успех: ставка принята, задержка подтверждена</li>
  <li><b>ВНИМАНИЕ</b> (жёлтый) — не критично, но обратить внимание</li>
  <li><b>ОШИБКА</b> (красный) — ставка не прошла, ошибка API</li>
  <li><b>СИГНАЛ</b> (оранжевый) — найдена задержка между БК</li>
</ul>

<p>Фильтры сверху:</p>
<ul>
  <li><b>Тип</b> — сигналы / ошибки / успехи / <b>Браузер</b> /
      стратегии / ставки</li>
  <li><b>Вид спорта</b>, <b>БК</b>, <b>Матч</b>, <b>Поиск</b></li>
  <li><b>Показывать детали</b> — служебные логи для отладки</li>
</ul>

<h3>Ключевые строки, которые стоит знать</h3>
<table>
  <tr><th>Строка в логе</th><th>Что значит</th></tr>
  <tr><td><code>✅ ПОСЛЕГОЛ: Fast в N, Slow в M</code></td>
      <td>Сработал сценарий завершившейся партии — ставка пойдёт</td></tr>
  <tr><td><code>👻 GHOST FRAME BLOCKED</code></td>
      <td>БК прислала битые данные, ставка <b>отменена</b> —
          это защита ваших денег</td></tr>
  <tr><td><code>🛑 MATH GUARD</code></td>
      <td>Счёт не похож на завершённую партию — пропуск</td></tr>
  <tr><td><code>💎 LV: ...</code></td>
      <td>Найден лайв-валуй, указан edge и сравнение кэфов</td></tr>
  <tr><td><code>✅ Ставка принята! ID ...</code></td>
      <td>БК приняла ставку</td></tr>
</table>

<hr/>

<h2>8. Словарь терминов</h2>
<table>
  <tr><th>Термин</th><th>Значение</th></tr>
  <tr><td><b>Профиль</b></td>
      <td>Папка с браузером: cookies, сессии, отпечаток, прокси</td></tr>
  <tr><td><b>Отпечаток</b></td>
      <td>Набор параметров, по которым сайт опознаёт браузер</td></tr>
  <tr><td><b>Seed</b></td>
      <td>Число, из которого генерируется весь отпечаток профиля</td></tr>
  <tr><td><b>Fast БК</b></td><td>Быстрая контора, эталон. Определяется автоматически</td></tr>
  <tr><td><b>Slow БК</b></td><td>Медленная БК, на ней ставим</td></tr>
  <tr><td><b>Задержка</b></td><td>Разница в счёте или кэфах между fast и slow</td></tr>
  <tr><td><b>Порог</b></td><td>Минимальная задержка, при которой исход подтверждён</td></tr>
  <tr><td><b>Edge</b></td><td><code>slow / fast − 1</code>, для лайв-валуя</td></tr>
  <tr><td><b>PREOPEN</b></td><td>Этап: открытие вкладки матча на slow БК</td></tr>
  <tr><td><b>MONITOR</b></td><td>Этап: сравнение данных fast и slow</td></tr>
  <tr><td><b>Фаза</b></td><td>Партия в НТ/волейболе, четверть в баскетболе</td></tr>
  <tr><td><b>Ghost Frame</b></td>
      <td>Битый кадр от БК: счёт партии обновился, а счёт по партиям нет</td></tr>
</table>

<hr/>

<h2>9. Частые вопросы</h2>

<h3>Почему ставки не идут?</h3>
<ol>
  <li>Стратегия выключена.</li>
  <li>Профиль не открыт — включите стратегию заново (тумблер OFF → ON).</li>
  <li>Вы не залогинились в БК в этом профиле — нажмите
      «🚀 Открыть профиль» и войдите.</li>
  <li>Мин. задержка слишком высокая — снизьте до 1.0–1.5 сек.</li>
  <li>Кэфы не попадают в диапазон мин/макс — расширьте.</li>
  <li>Все рынки отсеиваются порогами — снизьте пороги.</li>
  <li>Для лайв-валуя: edge ниже порога — снизьте минимальный edge.</li>
</ol>

<h3>Браузер не открывается</h3>
<ul>
  <li>Проверьте, что профиль не открыт вручную прямо сейчас —
      два браузера на одной папке невозможны.</li>
  <li>Закройте профиль кнопкой «⏹ Закрыть профиль» и попробуйте снова.</li>
  <li>Если в логах ошибка про Chromium — выполните
      <code>python -m patchright install chromium</code>.</li>
  <li>После неудачного запуска профиль уходит в паузу 30 секунд —
      подождите и повторите.</li>
</ul>

<h3>Лайв-Валуй не ставит, хотя сигналы идут</h3>
<ul>
  <li><code>нет свежих fast_markets</code> — fast-кэфы не успевают
      обновляться, либо fast БК не покрывает этот матч.</li>
  <li><code>фазы разные</code> — fast и slow в разных партиях, ждём.</li>
  <li><code>нет кандидатов</code> — edge ниже порога, снизьте его.</li>
  <li>Cookies fast-профиля протухли — пересоберите и перезапустите
      приложение.</li>
</ul>

<h3>Cookies протухли — что делать</h3>
<p>«Настройки» → <b>«🔄 Пересобрать cookies»</b>. Займёт 1–3 минуты.
<b>Выключите live-value стратегии перед сбором</b> и
<b>перезапустите приложение после</b>.</p>

<h3>Сколько матчей может работать одновременно?</h3>
<p>До <b>5 матчей на профиль</b> для большинства БК.
<b>Winline — только 1 матч</b>: он разлогинивает при открытии
второй вкладки, поэтому используется главная вкладка профиля.
Остальные сигналы становятся в очередь.</p>

<h3>Много «GHOST FRAME BLOCKED» в логах — это плохо?</h3>
<p class="good">Наоборот, хорошо. Это значит, что защита ловит битые
данные от БК и не даёт поставить на «фантомную» партию. Именно такие
ставки раньше приносили убыток.</p>

<h3>Нужно ли менять MAC-адрес или имя устройства?</h3>
<p>Нет. Сайты физически не могут их прочитать — этих полей нет
ни в одном браузерном API. Всё, что сайт реально читает, уже
настраивается в панели профиля.</p>

<hr/>

<p class="hint" style="text-align:center; margin-top: 30px;">
Value Detector Pro · v1.2 · встроенный браузер Patchright<br/>
Если что-то непонятно — пиши в поддержку
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
        fast_form.addRow("ID профиля:", self.fast_profile_edit)
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
            last_at = fc.get("last_collect_at", "")
            status = fc.get("last_collect_status", {}) or {}
            if not fc.get("profile_id"):
                self.fast_status_label.setText("Статус: укажите ID профиля")
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
            fc["profile_id"] = new_profile
            # fast_bks всегда фиксированный список, не пользовательский
            save_fast_config(fc)
            # Если профиль только что указали или изменили — запускаем
            # автосбор cookies в фоне (один раз, не блокируя UI).
            if new_profile and (
                new_profile != old_profile
                or not fc.get("last_collect_at")
            ):
                log_bus.info("Настройки", "Собираю cookies fast БК...")
                self._auto_collect_cookies()
            else:
                log_bus.success("Настройки", "Настройки fast БК сохранены")
        except Exception as e:
            log_bus.error("Настройки", f"Fast БК: {e}")
            print(f"[X] Fast БК: {e}")

        print("[OK] Настройки сохранены")

    def _open_tutorial(self):
        dlg = TutorialDialog(self)
        dlg.exec_()

    # ============================================================
    # Fast БК: сбор cookies и тест
    # ============================================================
    def _is_collecting(self) -> bool:
        """True, если поток сбора cookies ещё жив."""
        r = getattr(self, "_fast_runner", None)
        try:
            return r is not None and r.isRunning()
        except Exception:
            return False

    def _collect_fast_cookies(self):
        if self._is_collecting():
            QMessageBox.information(
                self, "Fast БК",
                "Сбор cookies уже идёт. Дождись завершения."
            )
            return
        profile_id = self.fast_profile_edit.text().strip()
        if not profile_id:
            QMessageBox.warning(
                self, "Fast БК",
                "Укажи ID профиля."
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

        # Сохраняем ID профиля НАПРЯМУЮ, без _on_save().
        # _on_save() может запустить _auto_collect_cookies() → второй
        # QThread перезапишет self._fast_runner →
        # "QThread: Destroyed while thread is still running" → краш.
        from core.fast_config import load_fast_config, save_fast_config
        fc = load_fast_config()
        fc["profile_id"] = profile_id
        save_fast_config(fc)

        self.fast_collect_btn.setEnabled(False)
        self.fast_collect_btn.setText("⏳ Сбор cookies...")

        from ui.fast_cookie_runner import FastCookieRunner
        self._fast_runner = FastCookieRunner(
            profile_id=profile_id,
            headless=False,
            bks=selected,
        )
        self._fast_runner.finished.connect(self._on_collect_finished)
        self._fast_runner.progress.connect(self._on_collect_progress)
        self._fast_runner.start()

    def _auto_collect_cookies(self):
        """Запускает сбор cookies со ВСЕХ БК, требующих их. Без диалогов."""
        if self._is_collecting():
            return
        from core.fast_config import (
            load_fast_config, get_enabled_fast_bks, ALL_FAST_BKS,
        )
        fc = load_fast_config()
        profile_id = fc.get("profile_id")
        if not profile_id:
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
        print("[OK] Сессия удалена, выходим")

        # Форсированный выход — приложение закроется,
        # при следующем старте покажется LoginWindow
        QApplication.quit()