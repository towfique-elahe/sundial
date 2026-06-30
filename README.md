# Sundial

Automatically switches Windows between **Light** and **Dark** mode at your local
**sunrise** and **sunset** — the scheduling that *Settings → Personalization →
Colors* is missing.

Runs quietly in the system tray. No window unless you open Settings.

## What it does

- At **sunrise** → switches Windows to **Light** mode.
- At **sunset** → switches Windows to **Dark** mode.
- Checks every minute, and re-applies on launch, so it's always correct.
- **Location**: either **Auto** (detects your city and timezone from your IP)
  or **Fixed** (you type a latitude/longitude, using your PC's timezone).
- **Start on Windows sign-in**: one checkbox. Uses the per-user `Run` registry
  key — no admin rights needed.
- Switches the system/taskbar theme, and optionally the app theme too, and
  refreshes the shell immediately so the taskbar repaints right away.

## Run it (no build)

1. Install Python 3.9+ (tick "Add Python to PATH").
2. Double-click **`run.bat`**.
3. A sun/moon icon appears in the tray. Right-click → **Settings…**

## Build a standalone .exe (optional)

1. Double-click **`build.bat`**.
2. Find your app at **`dist\Sundial.exe`** — copy it anywhere and run it.
   No Python needed on the target machine.

## Settings

| Option | What it does |
|---|---|
| Location → Auto (IP) | Looks up your approximate coordinates and timezone online |
| Location → Fixed | Uses the latitude/longitude you enter, with your PC's timezone |
| Start automatically… | Launches Sundial when you sign in |
| Also switch app theme | Toggles `AppsUseLightTheme` as well as the system theme |
| Preview times | Shows today's sunrise/sunset for the chosen location |

Tray menu also has **Switch theme now** (manual flip) and **Re-apply for current
time**.

## How it works

- Sunrise/sunset come from the `astral` library, anchored to the location's
  own timezone so the calculation is correct regardless of how your PC's
  clock offset relates to the coordinates.
- The theme is set by writing `SystemUsesLightTheme` / `AppsUseLightTheme`
  (1 = light, 0 = dark) under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize` —
  the same keys the Settings app writes — followed by a `WM_SETTINGCHANGE`
  broadcast so the taskbar repaints immediately.
- Config is stored at `%APPDATA%\Sundial\config.json`.

## Notes

- Windows only.
- Auto location needs internet once to resolve your city; Fixed works offline.
- To stop it starting with Windows, untick the box in Settings (it removes the
  `Run` entry).
