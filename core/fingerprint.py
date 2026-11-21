"""
Генератор и применение отпечатков профиля.
Всё детерминировано из seed: один seed = один человек навсегда.
"""
import json
import logging
import random
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

UA_TEMPLATES = {
    "windows": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/{ver} Safari/537.36"),
    "macos": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/{ver} Safari/537.36"),
}

WEBGL_PROFILES = [
    {"vendor": "Google Inc. (NVIDIA)",
     "renderer": "ANGLE (NVIDIA, NVIDIA GeForce GTX 1050 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (NVIDIA)",
     "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (Intel)",
     "renderer": "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (AMD)",
     "renderer": "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
]

SCREENS = [(1920, 1080), (2560, 1440), (1366, 768), (1536, 864), (1440, 900)]
CORES = [4, 6, 8, 12, 16]
RAM = [8, 16, 32]
TASKBARS = {"windows": 48, "macos": 25}


def resolve_geo_via_proxy(proxy_server: str, timeout: float = 4.0) -> Optional[dict]:
    """'auto'-поля: таймзона/локаль/координаты по IP прокси."""
    if not proxy_server:
        return None
    try:
        handler = urllib.request.ProxyHandler(
            {"http": proxy_server, "https": proxy_server}
        )
        opener = urllib.request.build_opener(handler)
        with opener.open("http://ip-api.com/json/?fields=timezone,lat,lon",
                         timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        tz = data.get("timezone") or ""
        locale = "ru-RU" if tz.startswith("Europe/Moscow") else "en-US"
        return {"timezone": tz, "locale": locale,
                "lat": data.get("lat"), "lon": data.get("lon")}
    except Exception as e:
        logger.debug(f"[fingerprint] geoip: {e}")
        return None


def derive_fingerprint(fp_cfg: dict, proxy_server: str = "",
                       browser_version: str = "") -> dict:
    """Разворачивает конфиг профиля ('auto'-поля) в конкретные значения."""
    seed = int(fp_cfg.get("seed") or 1)
    rnd = random.Random(seed)
    platform = fp_cfg.get("platform", "windows")

    geo = None
    if "auto" in (fp_cfg.get("timezone"), fp_cfg.get("locale"),
                  fp_cfg.get("geolocation")):
        geo = resolve_geo_via_proxy(proxy_server)

    # ── Экран ──
    scr = fp_cfg.get("screen")
    if isinstance(scr, dict) and scr.get("width"):
        screen = {"width": int(scr["width"]), "height": int(scr["height"])}
    else:
        w, h = rnd.choice(SCREENS)
        screen = {"width": w, "height": h}

    # ── WebGL ──
    wgl = fp_cfg.get("webgl")
    if isinstance(wgl, dict) and wgl.get("renderer"):
        webgl = {"vendor": wgl.get("vendor", "Google Inc."),
                 "renderer": wgl["renderer"]}
    else:
        webgl = rnd.choice(WEBGL_PROFILES)

    ver = browser_version or "156.0.6968.0"
    ua = fp_cfg.get("user_agent") or ""

    fp = {
        "seed": seed,
        "platform": platform,
        "user_agent": ua or UA_TEMPLATES.get(platform, UA_TEMPLATES["windows"]).format(ver=ver),
        "ua_override": bool(ua),
        "timezone": (geo or {}).get("timezone")
                      if fp_cfg.get("timezone") == "auto"
                      else fp_cfg.get("timezone", "Europe/Moscow"),
        "locale": (geo or {}).get("locale")
                  if fp_cfg.get("locale") == "auto"
                  else fp_cfg.get("locale", "ru-RU"),
        "geo_mode": fp_cfg.get("geolocation", "deny"),
        "geo_lat": (geo or {}).get("lat") if fp_cfg.get("geolocation") == "auto" else None,
        "geo_lon": (geo or {}).get("lon") if fp_cfg.get("geolocation") == "auto" else None,
        "screen": screen,
        "taskbar": TASKBARS.get(platform, 48),
        "webgl": webgl,
        "canvas": fp_cfg.get("canvas", "noise"),
        "audio": fp_cfg.get("audio", "noise"),
        "client_rects": fp_cfg.get("client_rects", "noise"),
        "speech_voices": fp_cfg.get("speech_voices", "noise"),
        "cpu_cores": fp_cfg.get("cpu_cores") if isinstance(fp_cfg.get("cpu_cores"), int)
                     else rnd.choice(CORES),
        "ram_gb": fp_cfg.get("ram_gb") if isinstance(fp_cfg.get("ram_gb"), int)
                  else rnd.choice(RAM),
        "webrtc": fp_cfg.get("webrtc", "proxy_only"),
        "dnt": fp_cfg.get("dnt", "default"),
        "hardware_accel": fp_cfg.get("hardware_accel", True),
        "rects_eps": round(rnd.uniform(1e-7, 9e-7), 9),
    }
    if isinstance(fp_cfg.get("geolocation"), dict):
        fp["geo_mode"] = "coords"
        fp["geo_lat"] = fp_cfg["geolocation"].get("lat")
        fp["geo_lon"] = fp_cfg["geolocation"].get("lon")
    return fp


def build_chromium_args(fp: dict) -> list:
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        f"--window-size={fp['screen']['width']},{fp['screen']['height']}",
    ]
    if fp["webrtc"] == "proxy_only":
        args += [
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--enforce-webrtc-ip-permission-check",
        ]
    if not fp["hardware_accel"]:
        args.append("--disable-gpu")
    return args


_INIT_JS = r"""
(() => {
  const FP = __FP_JSON__;
  const mulberry32 = (a) => () => {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
  const def = (obj, prop, val) => {
    try { Object.defineProperty(obj, prop, { get: () => val, configurable: true }); } catch (e) {}
  };
  def(Navigator.prototype, 'hardwareConcurrency', FP.cpu_cores);
  def(Navigator.prototype, 'deviceMemory', FP.ram_gb);
  if (FP.dnt !== 'default') def(Navigator.prototype, 'doNotTrack', FP.dnt);
  def(Screen.prototype, 'width', FP.screen.width);
  def(Screen.prototype, 'height', FP.screen.height);
  def(Screen.prototype, 'availWidth', FP.screen.width);
  def(Screen.prototype, 'availHeight', FP.screen.height - FP.taskbar);
  def(Screen.prototype, 'colorDepth', 24);
  def(Screen.prototype, 'pixelDepth', 24);
  def(window, 'devicePixelRatio', 1);
  def(window, 'outerWidth', FP.screen.width);
  def(window, 'outerHeight', FP.screen.height);

  if (FP.canvas === 'noise') {
    const gID = CanvasRenderingContext2D.prototype.getImageData;
    const toDataURL = HTMLCanvasElement.prototype.toDataURL;
    const toBlob = HTMLCanvasElement.prototype.toBlob;
    const noisify = (canvas) => {
      const ctx = canvas.getContext && canvas.getContext('2d');
      if (!ctx || !canvas.width || !canvas.height) return;
      const img = gID.call(ctx, 0, 0, canvas.width, canvas.height);
      const r = mulberry32(FP.seed ^ 0xC0FFEE);
      for (let i = 0; i < img.data.length; i += 97) {
        img.data[i] = (img.data[i] + (r() < 0.5 ? 0 : 1)) & 0xFF;
      }
      ctx.putImageData(img, 0, 0);
    };
    HTMLCanvasElement.prototype.toDataURL = function (...a) {
      try { noisify(this); } catch (e) {}
      return toDataURL.apply(this, a);
    };
    HTMLCanvasElement.prototype.toBlob = function (...a) {
      try { noisify(this); } catch (e) {}
      return toBlob.apply(this, a);
    };
  }

  if (FP.webgl) {
    const patch = (proto) => {
      if (!proto) return;
      const orig = proto.getParameter;
      proto.getParameter = function (p) {
        if (p === 0x9245) return FP.webgl.vendor;
        if (p === 0x9246) return FP.webgl.renderer;
        return orig.call(this, p);
      };
    };
    patch(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
    patch(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
  }

  if (FP.audio === 'noise') {
    const gcd = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function (ch) {
      const d = gcd.call(this, ch);
      const r = mulberry32(FP.seed ^ 0xA0D10);
      for (let i = 0; i < 6; i++) {
        d[Math.floor(r() * d.length)] += (r() - 0.5) * 1e-6;
      }
      return d;
    };
  }

  if (FP.client_rects === 'noise') {
    const e = FP.rects_eps;
    const orig = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function () {
      const r = orig.call(this);
      return new DOMRect(r.x + e, r.y + e, r.width, r.height);
    };
  }

  if (FP.speech_voices === 'noise') {
    const list = [
      { name: 'Microsoft Irina - Russian (Russia)', lang: 'ru-RU', default: true, localService: true },
      { name: 'Microsoft Pavel - Russian (Russia)', lang: 'ru-RU', default: false, localService: true },
      { name: 'Google русский', lang: 'ru-RU', default: false, localService: false },
    ];
    if (window.speechSynthesis) {
      speechSynthesis.getVoices = () => list.map(v =>
        Object.assign(Object.create(SpeechSynthesisVoice.prototype), v));
    }
  }

  if (FP.geo_mode === 'deny' && navigator.geolocation) {
    const err = (cb) => cb && cb({ code: 1, message: 'User denied Geolocation', PERMISSION_DENIED: 1 });
    navigator.geolocation.getCurrentPosition = (ok, e) => err(e);
    navigator.geolocation.watchPosition = (ok, e) => { err(e); return 0; };
  }
})();
"""


def build_init_script(fp: dict) -> str:
    return _INIT_JS.replace("__FP_JSON__", json.dumps(fp))