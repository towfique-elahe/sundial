"""
Sundial - Automatic Windows light/dark mode switching at sunrise & sunset.

A minimal system-tray app. Windows has a "Choose your color mode" setting under
Settings > Personalization > Colors but no scheduling. This fills that gap:
it flips Windows between Light and Dark at your local sunrise and sunset, using
either your geolocation (via IP) or a fixed latitude/longitude you set.

Runs on Windows only. Python 3.9+.
"""

import json
import os
import sys
import threading
import time
import datetime
import urllib.request

# ---- Third-party ----
import pystray
from PIL import Image, ImageDraw
from astral import LocationInfo
from astral.sun import sun

try:
    import winreg
except ImportError:
    winreg = None  # allows non-Windows import for inspection

APP_NAME = "Sundial"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

DEFAULT_CONFIG = {
    "location_mode": "auto",   # "auto" (IP geolocation) or "fixed"
    "latitude": 22.3569,
    "longitude": 91.7832,
    "label": "Auto (IP based)",
    "timezone": None,          # IANA name, e.g. "Asia/Dhaka"; None = PC local tz
    "autostart": False,
    "apps_theme": True,        # also switch app theme (not just taskbar/system)
}


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# --------------------------------------------------------------------------- #
# Windows theme
# --------------------------------------------------------------------------- #
def _broadcast_theme_change():
    """Tell the shell to repaint immediately.

    Writing the registry value alone changes apps that re-read it, but the
    taskbar / system tray won't repaint until it's told the theme settings
    changed. The Settings app sends these same broadcasts.
    """
    try:
        import ctypes
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        send = ctypes.windll.user32.SendMessageTimeoutW
        for param in ("ImmersiveColorSet", "WindowsThemeElement", "Policy"):
            send(HWND_BROADCAST, WM_SETTINGCHANGE, 0, param,
                 SMTO_ABORTIFHUNG, 200, ctypes.byref(ctypes.c_ulong()))
    except Exception:
        pass


def set_windows_theme(light: bool, apps_theme: bool = True):
    """Light mode = 1, Dark mode = 0."""
    if winreg is None:
        return
    value = 1 if light else 0
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY) as key:
        winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, value)
        if apps_theme:
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, value)
    _broadcast_theme_change()


def get_windows_theme_is_light() -> bool:
    if winreg is None:
        return True
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY) as key:
            v, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            return bool(v)
    except FileNotFoundError:
        return True


# --------------------------------------------------------------------------- #
# Autostart (registry Run key, current user — no admin needed)
# --------------------------------------------------------------------------- #
def _run_command():
    if getattr(sys, "frozen", False):           # built .exe
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def set_autostart(enabled: bool):
    if winreg is None:
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _run_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


# --------------------------------------------------------------------------- #
# Location & sun times
# --------------------------------------------------------------------------- #
def geolocate_ip():
    """Return (lat, lon, label, tzname) from IP, or None on failure."""
    try:
        with urllib.request.urlopen("http://ip-api.com/json/", timeout=6) as r:
            d = json.loads(r.read().decode())
        if d.get("status") == "success":
            label = f"{d.get('city','?')}, {d.get('country','?')}"
            return float(d["lat"]), float(d["lon"]), label, d.get("timezone")
    except Exception:
        pass
    return None


def resolve_location(cfg):
    """Return (lat, lon, label, tzname). tzname may be None (use PC local tz)."""
    if cfg["location_mode"] == "auto":
        geo = geolocate_ip()
        if geo:
            return geo
        # fall back to last-known fixed values
    return (cfg["latitude"], cfg["longitude"],
            cfg.get("label", "Fixed"), cfg.get("timezone"))


def _resolve_tz(tzname):
    """A tzinfo for the given IANA name, falling back to the PC's local tz."""
    if tzname:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(tzname)
        except Exception:
            pass
    return datetime.datetime.now().astimezone().tzinfo


def sun_times(lat, lon, on_date=None, tzname=None):
    """Sunrise/sunset for the location, expressed in the location's timezone.

    The calculation is anchored to the *local* calendar day at the location
    (via tzinfo), so it's correct regardless of how the PC clock's offset
    relates to the coordinates. `now` must be compared in this same tz.
    """
    tz = _resolve_tz(tzname)
    on_date = on_date or datetime.datetime.now(tz).date()
    loc = LocationInfo(latitude=lat, longitude=lon)
    s = sun(loc.observer, date=on_date, tzinfo=tz)
    return s["sunrise"], s["sunset"]


def desired_light_now(lat, lon, tzname=None):
    """True if it should currently be Light mode (between sunrise and sunset)."""
    tz = _resolve_tz(tzname)
    now = datetime.datetime.now(tz)
    sunrise, sunset = sun_times(lat, lon, now.date(), tzname)
    return sunrise <= now < sunset


# --------------------------------------------------------------------------- #
# Tray icon image
# --------------------------------------------------------------------------- #
def make_icon(light_phase: bool):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if light_phase:  # sun
        d.ellipse((20, 20, 44, 44), fill=(245, 200, 60, 255))
        for a in range(0, 360, 45):
            import math
            x = 32 + 22 * math.cos(math.radians(a))
            y = 32 + 22 * math.sin(math.radians(a))
            d.line((32, 32, x, y), fill=(245, 200, 60, 255), width=3)
    else:  # moon
        d.ellipse((16, 16, 48, 48), fill=(210, 215, 230, 255))
        d.ellipse((26, 12, 58, 44), fill=(0, 0, 0, 0))
        d.ellipse((26, 12, 58, 44), fill=(40, 44, 60, 255))
    return img


# --------------------------------------------------------------------------- #
# Core controller
# --------------------------------------------------------------------------- #
class Sundial:
    def __init__(self):
        self.cfg = load_config()
        self.lat, self.lon, self.label, self.tzname = resolve_location(self.cfg)
        self.icon = None
        self._stop = threading.Event()
        self._last_applied = None

    # ---- theme loop ----
    def apply_now(self, force=False):
        light = desired_light_now(self.lat, self.lon, self.tzname)
        if force or light != self._last_applied:
            set_windows_theme(light, self.cfg.get("apps_theme", True))
            self._last_applied = light
            if self.icon:
                self.icon.icon = make_icon(light)
                self.icon.title = self._status_text()

    def _loop(self):
        self.apply_now(force=True)
        while not self._stop.wait(60):  # check every minute
            try:
                self.apply_now()
            except Exception:
                pass

    def _status_text(self):
        try:
            sunrise, sunset = sun_times(self.lat, self.lon, tzname=self.tzname)
            phase = "Light" if self._last_applied else "Dark"
            return (f"{APP_NAME} — {phase} now\n{self.label}\n"
                    f"Sunrise {sunrise:%H:%M} · Sunset {sunset:%H:%M}")
        except Exception:
            return f"{APP_NAME} — {self.label}"

    # ---- menu actions ----
    def reload_location(self):
        self.lat, self.lon, self.label, self.tzname = resolve_location(self.cfg)
        # persist resolved tz/label so fixed-mode and offline restarts stay correct
        self.cfg["label"] = self.label
        self.cfg["timezone"] = self.tzname
        save_config(self.cfg)
        self.apply_now(force=True)

    def open_settings(self, *_):
        threading.Thread(target=self._settings_window, daemon=True).start()

    def _settings_window(self):
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title(f"{APP_NAME} settings")
        root.resizable(False, False)
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass

        pad = {"padx": 14, "pady": 6}
        frm = ttk.Frame(root, padding=16)
        frm.grid()

        ttk.Label(frm, text="Auto light / dark at sunrise & sunset",
                  font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        # Location mode
        mode = tk.StringVar(value=self.cfg["location_mode"])
        ttk.Label(frm, text="Location").grid(row=1, column=0, sticky="w", **pad)
        modebox = ttk.Frame(frm)
        modebox.grid(row=1, column=1, sticky="w")
        ttk.Radiobutton(modebox, text="Auto (IP)", variable=mode, value="auto").grid(row=0, column=0)
        ttk.Radiobutton(modebox, text="Fixed", variable=mode, value="fixed").grid(row=0, column=1)

        lat = tk.StringVar(value=str(self.cfg["latitude"]))
        lon = tk.StringVar(value=str(self.cfg["longitude"]))
        ttk.Label(frm, text="Latitude").grid(row=2, column=0, sticky="w", **pad)
        lat_e = ttk.Entry(frm, textvariable=lat, width=20); lat_e.grid(row=2, column=1, sticky="w")
        ttk.Label(frm, text="Longitude").grid(row=3, column=0, sticky="w", **pad)
        lon_e = ttk.Entry(frm, textvariable=lon, width=20); lon_e.grid(row=3, column=1, sticky="w")

        autostart = tk.BooleanVar(value=self.cfg["autostart"])
        ttk.Checkbutton(frm, text="Start automatically when I sign in to Windows",
                        variable=autostart).grid(row=4, column=0, columnspan=2, sticky="w", **pad)

        apps_theme = tk.BooleanVar(value=self.cfg.get("apps_theme", True))
        ttk.Checkbutton(frm, text="Also switch app theme (not just system / taskbar)",
                        variable=apps_theme).grid(row=5, column=0, columnspan=2, sticky="w", **pad)

        status = ttk.Label(frm, text="", foreground="#666")
        status.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def refresh_state(*_):
            fixed = mode.get() == "fixed"
            lat_e.configure(state="normal" if fixed else "disabled")
            lon_e.configure(state="normal" if fixed else "disabled")
        mode.trace_add("write", refresh_state)
        refresh_state()

        def show_times():
            try:
                if mode.get() == "fixed":
                    la, lo, lbl, tzn = float(lat.get()), float(lon.get()), "Fixed", None
                else:
                    la, lo, lbl, tzn = resolve_location({**self.cfg, "location_mode": "auto"})
                sr, ss = sun_times(la, lo, tzname=tzn)
                status.config(text=f"{lbl} — sunrise {sr:%H:%M}, sunset {ss:%H:%M}")
            except Exception as e:
                status.config(text=f"Couldn't compute times: {e}")

        def save_and_apply():
            self.cfg["location_mode"] = mode.get()
            try:
                self.cfg["latitude"] = float(lat.get())
                self.cfg["longitude"] = float(lon.get())
            except ValueError:
                status.config(text="Latitude and longitude must be numbers.")
                return
            self.cfg["autostart"] = bool(autostart.get())
            self.cfg["apps_theme"] = bool(apps_theme.get())
            if mode.get() == "fixed":
                # use the PC's own timezone for a manually entered location
                self.cfg["timezone"] = None
                self.cfg["label"] = "Fixed location"
            save_config(self.cfg)
            set_autostart(self.cfg["autostart"])
            self.reload_location()
            show_times()
            status.config(text=status.cget("text") + "  ·  Saved.")

        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Preview times", command=show_times).grid(row=0, column=0, padx=4)
        ttk.Button(btns, text="Save", command=save_and_apply).grid(row=0, column=1, padx=4)
        ttk.Button(btns, text="Close", command=root.destroy).grid(row=0, column=2, padx=4)

        show_times()
        root.mainloop()

    def toggle_now(self, *_):
        light = not get_windows_theme_is_light()
        set_windows_theme(light, self.cfg.get("apps_theme", True))
        self._last_applied = light
        if self.icon:
            self.icon.icon = make_icon(light)

    def quit(self, *_):
        self._stop.set()
        if self.icon:
            self.icon.stop()

    # ---- run ----
    def run(self):
        threading.Thread(target=self._loop, daemon=True).start()
        menu = pystray.Menu(
            pystray.MenuItem("Settings…", self.open_settings, default=True),
            pystray.MenuItem("Switch theme now", self.toggle_now),
            pystray.MenuItem("Re-apply for current time", lambda *_: self.apply_now(force=True)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.quit),
        )
        self.icon = pystray.Icon(APP_NAME, make_icon(True), self._status_text(), menu)
        self.icon.run()


if __name__ == "__main__":
    Sundial().run()
