#!/usr/bin/env python3
"""
koseyes.py — Kingdom OS Eyes: Computer Use Integration

Gives Kingdom agents the ability to see and interact with the screen.
This is the SIGHT organ — the Kingdom's eyes.

Architecture:
    koseyes is a tool executor, not a decision maker. The agent (Claude)
    decides what to look at and what to click. koseyes provides:

    1. SCREENSHOT — capture the current screen (or a region)
    2. CLICK — click at coordinates
    3. TYPE — type text
    4. KEY — press keys/combos
    5. MOVE — move mouse
    6. SCROLL — scroll
    7. INFO — display resolution, running apps, window positions
    8. OSASCRIPT — run AppleScript for macOS automation

    For the Anthropic API computer_use tool, this maps to:
        tool_type: "computer_20250124"
        display_width_px / display_height_px from system_profiler

macOS Implementation:
    Screenshots: /usr/sbin/screencapture (native, no deps)
    Mouse/keyboard: cliclick (brew install cliclick) OR osascript
    Window info: osascript + System Events
    Display info: system_profiler SPDisplaysDataType

Dependencies:
    Required: macOS (screencapture, osascript)
    Optional: cliclick (brew install cliclick) — more reliable mouse/keyboard
    Optional: Pillow (pip install Pillow) — image processing/resize

Usage:
    koseyes screenshot [--path /tmp/screen.png] [--region x,y,w,h] [--base64]
    koseyes click <x> <y> [--button left|right] [--double]
    koseyes type "text to type"
    koseyes key "cmd+shift+4"
    koseyes move <x> <y>
    koseyes scroll <direction> [--amount N]
    koseyes info
    koseyes apps
    koseyes windows [--app "Safari"]
    koseyes osascript "tell application ..."

Integration with kingdom-agent.py:
    The agent adapter can expose koseyes as the computer_use tool,
    translating Anthropic's computer use actions into koseyes commands.
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

# ── Colors ────────────────────────────────────────────────────────────────

_B = "\033[1m"
_G = "\033[0;32m"
_C = "\033[0;36m"
_Y = "\033[1;33m"
_R = "\033[0;31m"
_D = "\033[2m"
_N = "\033[0m"


# ══════════════════════════════════════════════════════════════════════
# DISPLAY INFO
# ══════════════════════════════════════════════════════════════════════

def get_display_info() -> dict:
    """Get display resolution and scale factor."""
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        displays = []
        for gpu in data.get("SPDisplaysDataType", []):
            for disp in gpu.get("spdisplays_ndrvs", []):
                res = disp.get("_spdisplays_resolution", "")
                # Parse "2560 x 1440" or similar
                match = re.search(r'(\d+)\s*x\s*(\d+)', res)
                if match:
                    w, h = int(match.group(1)), int(match.group(2))
                else:
                    w, h = 0, 0

                displays.append({
                    "name": disp.get("_name", "Unknown"),
                    "resolution": res,
                    "width": w,
                    "height": h,
                    "main": disp.get("spdisplays_main", "") == "spdisplays_yes",
                    "ui_resolution": disp.get("_spdisplays_pixels", ""),
                })

        main = next((d for d in displays if d["main"]), displays[0] if displays else None)
        return {
            "displays": displays,
            "main": main,
            "width": main["width"] if main else 1920,
            "height": main["height"] if main else 1080,
        }
    except Exception as e:
        return {"displays": [], "main": None, "width": 1920, "height": 1080, "error": str(e)}


def get_running_apps() -> list:
    """Get list of visible running applications."""
    try:
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of every process whose visible is true'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return [app.strip() for app in result.stdout.strip().split(",")]
    except Exception:
        pass
    return []


def get_window_list(app_name: str = None) -> list:
    """Get window positions and sizes. Requires accessibility permissions."""
    script = """
    tell application "System Events"
        set windowList to {}
        repeat with proc in (every process whose visible is true)
            try
                set procName to name of proc
                repeat with win in (every window of proc)
                    try
                        set winName to name of win
                        set winPos to position of win
                        set winSize to size of win
                        set end of windowList to procName & "|" & winName & "|" & (item 1 of winPos as string) & "," & (item 2 of winPos as string) & "|" & (item 1 of winSize as string) & "," & (item 2 of winSize as string)
                    end try
                end repeat
            end try
        end repeat
        return windowList
    end tell
    """
    if app_name:
        script = f"""
        tell application "System Events"
            set windowList to {{}}
            try
                set proc to process "{app_name}"
                repeat with win in (every window of proc)
                    try
                        set winName to name of win
                        set winPos to position of win
                        set winSize to size of win
                        set end of windowList to "{app_name}" & "|" & winName & "|" & (item 1 of winPos as string) & "," & (item 2 of winPos as string) & "|" & (item 1 of winSize as string) & "," & (item 2 of winSize as string)
                    end try
                end repeat
            end try
            return windowList
        end tell
        """

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            windows = []
            for entry in result.stdout.strip().split(", "):
                parts = entry.split("|")
                if len(parts) >= 4:
                    pos = parts[2].split(",")
                    size = parts[3].split(",")
                    windows.append({
                        "app": parts[0],
                        "title": parts[1],
                        "x": int(pos[0]) if pos[0].isdigit() else 0,
                        "y": int(pos[1]) if pos[1].isdigit() else 0,
                        "width": int(size[0]) if size[0].isdigit() else 0,
                        "height": int(size[1]) if size[1].isdigit() else 0,
                    })
            return windows
    except Exception:
        pass
    return []


# ══════════════════════════════════════════════════════════════════════
# SCREENSHOT
# ══════════════════════════════════════════════════════════════════════

def screenshot(path: str = None, region: Tuple[int, int, int, int] = None,
               as_base64: bool = False, resize: Tuple[int, int] = None) -> dict:
    """Capture screenshot.

    Args:
        path: Output file path (default: temp file)
        region: (x, y, width, height) to capture a region
        as_base64: Return base64-encoded PNG
        resize: (width, height) to resize for API efficiency

    Returns:
        {"path": str, "width": int, "height": int, "size_bytes": int, "base64": str|None}
    """
    if path is None:
        path = tempfile.mktemp(suffix=".png")

    cmd = ["/usr/sbin/screencapture", "-x", "-C"]  # -x = no sound, -C = capture cursor

    if region:
        x, y, w, h = region
        cmd.extend(["-R", f"{x},{y},{w},{h}"])

    cmd.append(path)

    result = subprocess.run(cmd, capture_output=True, timeout=10)
    if result.returncode != 0 or not os.path.exists(path):
        return {"error": f"Screenshot failed: {result.stderr.decode()}", "path": None}

    size_bytes = os.path.getsize(path)

    # Get dimensions
    width, height = 0, 0
    try:
        # Use sips (macOS native) to get dimensions
        sips_result = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
            capture_output=True, text=True, timeout=5
        )
        for line in sips_result.stdout.splitlines():
            if "pixelWidth" in line:
                width = int(line.split(":")[-1].strip())
            if "pixelHeight" in line:
                height = int(line.split(":")[-1].strip())
    except Exception:
        pass

    # Resize if requested (reduces tokens when sending to API)
    if resize and width > 0:
        try:
            rw, rh = resize
            subprocess.run(
                ["sips", "--resampleWidth", str(rw), path],
                capture_output=True, timeout=10
            )
            size_bytes = os.path.getsize(path)
            width, height = rw, int(height * rw / width) if width > 0 else rh
        except Exception:
            pass

    b64 = None
    if as_base64:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

    return {
        "path": path,
        "width": width,
        "height": height,
        "size_bytes": size_bytes,
        "base64": b64,
    }


# ══════════════════════════════════════════════════════════════════════
# MOUSE & KEYBOARD (via cliclick or osascript)
# ══════════════════════════════════════════════════════════════════════

_CLICLICK = shutil.which("cliclick")


def click(x: int, y: int, button: str = "left", double: bool = False) -> dict:
    """Click at coordinates."""
    if _CLICLICK:
        cmd = "dc" if double else ("rc" if button == "right" else "c")
        result = subprocess.run(
            [_CLICLICK, f"{cmd}:{x},{y}"],
            capture_output=True, text=True, timeout=5
        )
        return {"ok": result.returncode == 0, "method": "cliclick"}

    # Fallback: osascript + cliclick-like behavior
    script = f"""
    tell application "System Events"
        click at {{{x}, {y}}}
    end tell
    """
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=5
    )
    return {"ok": result.returncode == 0, "method": "osascript",
            "note": "osascript click may require accessibility permissions"}


def type_text(text: str, delay_ms: int = 0) -> dict:
    """Type text."""
    if _CLICLICK:
        # cliclick t:"text"
        result = subprocess.run(
            [_CLICLICK, f"t:{text}"],
            capture_output=True, text=True, timeout=10
        )
        return {"ok": result.returncode == 0, "method": "cliclick"}

    # Fallback: osascript
    # Escape for AppleScript
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "System Events" to keystroke "{escaped}"'
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=10
    )
    return {"ok": result.returncode == 0, "method": "osascript"}


def key_press(keys: str) -> dict:
    """Press key combination. E.g., 'cmd+shift+4', 'return', 'escape'.

    Supports: cmd, shift, alt/option, ctrl, fn + any key.
    """
    if _CLICLICK:
        # Map to cliclick key codes
        result = subprocess.run(
            [_CLICLICK, f"kp:{keys}"],
            capture_output=True, text=True, timeout=5
        )
        return {"ok": result.returncode == 0, "method": "cliclick"}

    # Parse key combo for osascript
    parts = keys.lower().split("+")
    key = parts[-1].strip()
    modifiers = [p.strip() for p in parts[:-1]]

    modifier_map = {
        "cmd": "command down",
        "command": "command down",
        "shift": "shift down",
        "alt": "option down",
        "option": "option down",
        "ctrl": "control down",
        "control": "control down",
    }

    using = ", ".join(modifier_map.get(m, "") for m in modifiers if m in modifier_map)

    # Special keys
    special = {
        "return": "return", "enter": "return", "escape": "escape", "esc": "escape",
        "tab": "tab", "space": "space", "delete": "delete", "backspace": "delete",
        "up": "up arrow", "down": "down arrow", "left": "left arrow", "right": "right arrow",
    }

    if key in special:
        if using:
            script = f'tell application "System Events" to key code {special[key]} using {{{using}}}'
        else:
            script = f'tell application "System Events" to key code {special[key]}'
    else:
        if using:
            script = f'tell application "System Events" to keystroke "{key}" using {{{using}}}'
        else:
            script = f'tell application "System Events" to keystroke "{key}"'

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=5
    )
    return {"ok": result.returncode == 0, "method": "osascript", "stderr": result.stderr.strip()}


def mouse_move(x: int, y: int) -> dict:
    """Move mouse to coordinates."""
    if _CLICLICK:
        result = subprocess.run(
            [_CLICLICK, f"m:{x},{y}"],
            capture_output=True, text=True, timeout=5
        )
        return {"ok": result.returncode == 0, "method": "cliclick"}
    return {"ok": False, "error": "cliclick not installed. brew install cliclick"}


def scroll(direction: str = "down", amount: int = 3) -> dict:
    """Scroll. Direction: up, down, left, right."""
    if _CLICLICK:
        dx, dy = 0, 0
        if direction == "up": dy = amount
        elif direction == "down": dy = -amount
        elif direction == "left": dx = amount
        elif direction == "right": dx = -amount
        result = subprocess.run(
            [_CLICLICK, f"s:{dx},{dy}"],  # Not standard cliclick, may need adjustment
            capture_output=True, text=True, timeout=5
        )
        return {"ok": result.returncode == 0, "method": "cliclick"}

    # osascript scroll
    clicks = amount if direction == "up" else -amount
    script = f"""
    tell application "System Events"
        scroll area 1 of process 1 by {clicks}
    end tell
    """
    return {"ok": False, "note": "Scroll via osascript is unreliable. Install cliclick."}


def run_osascript(script: str) -> dict:
    """Run arbitrary AppleScript."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=30
    )
    return {
        "ok": result.returncode == 0,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


# ══════════════════════════════════════════════════════════════════════
# ANTHROPIC COMPUTER USE ADAPTER
# ══════════════════════════════════════════════════════════════════════

def handle_computer_use_action(action: str, **kwargs) -> dict:
    """Handle an Anthropic computer_use tool action.

    Maps Anthropic's computer_use actions to koseyes functions.
    This is the bridge between the API tool schema and macOS.

    Actions:
        screenshot → screenshot()
        click → click(x, y)
        type → type_text(text)
        key → key_press(keys)
        mouse_move → mouse_move(x, y)
        scroll → scroll(direction, amount)
    """
    if action == "screenshot":
        return screenshot(
            as_base64=True,
            resize=kwargs.get("resize"),
        )
    elif action == "click":
        coords = kwargs.get("coordinate", [0, 0])
        return click(
            coords[0], coords[1],
            button=kwargs.get("button", "left"),
            double=kwargs.get("double", False),
        )
    elif action == "type":
        return type_text(kwargs.get("text", ""))
    elif action == "key":
        return key_press(kwargs.get("key", ""))
    elif action == "mouse_move":
        coords = kwargs.get("coordinate", [0, 0])
        return mouse_move(coords[0], coords[1])
    elif action == "scroll":
        return scroll(
            direction=kwargs.get("direction", "down"),
            amount=kwargs.get("amount", 3),
        )
    else:
        return {"error": f"Unknown action: {action}"}


def get_computer_use_tool_spec() -> dict:
    """Return the Anthropic computer_use tool specification.

    This is what gets sent to the API so Claude knows it can use the screen.
    """
    info = get_display_info()
    return {
        "type": "computer_20250124",
        "name": "computer",
        "display_width_px": info["width"],
        "display_height_px": info["height"],
    }


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Kingdom OS Eyes — Computer Use Integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  koseyes screenshot                          # Capture full screen
  koseyes screenshot --base64                 # As base64 for API
  koseyes screenshot --region 0,0,800,600     # Region capture
  koseyes click 500 300                       # Left click
  koseyes click 500 300 --right               # Right click
  koseyes type "hello world"                  # Type text
  koseyes key "cmd+c"                         # Key combo
  koseyes info                                # Display info
  koseyes apps                                # Running apps
  koseyes windows                             # Window positions
  koseyes osascript 'tell application "Finder" to activate'
        """
    )
    sub = parser.add_subparsers(dest="command")

    # screenshot
    p = sub.add_parser("screenshot", help="Capture screenshot")
    p.add_argument("--path", "-o", help="Output path")
    p.add_argument("--region", "-r", help="x,y,w,h region")
    p.add_argument("--base64", "-b", action="store_true")
    p.add_argument("--resize", help="width to resize to (preserves aspect)")

    # click
    p = sub.add_parser("click", help="Click at coordinates")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--right", action="store_true")
    p.add_argument("--double", action="store_true")

    # type
    p = sub.add_parser("type", help="Type text")
    p.add_argument("text")

    # key
    p = sub.add_parser("key", help="Press key combo")
    p.add_argument("keys", help="e.g. cmd+shift+4, return, escape")

    # move
    p = sub.add_parser("move", help="Move mouse")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)

    # scroll
    p = sub.add_parser("scroll", help="Scroll")
    p.add_argument("direction", choices=["up", "down", "left", "right"])
    p.add_argument("--amount", "-n", type=int, default=3)

    # info
    sub.add_parser("info", help="Display info")

    # apps
    sub.add_parser("apps", help="Running applications")

    # windows
    p = sub.add_parser("windows", help="Window positions")
    p.add_argument("--app", "-a", help="Filter by app name")

    # osascript
    p = sub.add_parser("osascript", help="Run AppleScript")
    p.add_argument("script")

    # anthropic adapter
    p = sub.add_parser("tool-spec", help="Print Anthropic computer_use tool spec")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "screenshot":
        region = None
        if args.region:
            parts = [int(x) for x in args.region.split(",")]
            region = tuple(parts[:4])
        resize = None
        if args.resize:
            resize = (int(args.resize), 0)

        result = screenshot(path=args.path, region=region,
                           as_base64=args.base64, resize=resize)
        if result.get("error"):
            print(f"  {_R}✗{_N} {result['error']}")
        else:
            print(f"  {_G}✓{_N} Screenshot: {result['path']}")
            print(f"    {_D}{result['width']}x{result['height']}, {result['size_bytes']} bytes{_N}")
            if result.get("base64"):
                print(f"    {_D}Base64: {len(result['base64'])} chars{_N}")

    elif args.command == "click":
        button = "right" if args.right else "left"
        result = click(args.x, args.y, button=button, double=args.double)
        print(f"  {_G if result['ok'] else _R}{'✓' if result['ok'] else '✗'}{_N} Click ({button}) at {args.x},{args.y} [{result.get('method')}]")

    elif args.command == "type":
        result = type_text(args.text)
        print(f"  {_G if result['ok'] else _R}{'✓' if result['ok'] else '✗'}{_N} Typed: {args.text[:50]} [{result.get('method')}]")

    elif args.command == "key":
        result = key_press(args.keys)
        print(f"  {_G if result['ok'] else _R}{'✓' if result['ok'] else '✗'}{_N} Key: {args.keys} [{result.get('method')}]")

    elif args.command == "move":
        result = mouse_move(args.x, args.y)
        print(f"  {_G if result['ok'] else _R}{'✓' if result['ok'] else '✗'}{_N} Move to {args.x},{args.y}")

    elif args.command == "scroll":
        result = scroll(args.direction, args.amount)
        print(f"  Scroll {args.direction} x{args.amount}")

    elif args.command == "info":
        info = get_display_info()
        print(f"\n  {_B}Display Info{_N}")
        print(f"  {'─' * 40}")
        for d in info.get("displays", []):
            main = " (main)" if d.get("main") else ""
            print(f"  {d['name']}{main}: {d['width']}x{d['height']}")
            if d.get("ui_resolution"):
                print(f"    {_D}UI: {d['ui_resolution']}{_N}")
        print(f"\n  Computer use spec: {info['width']}x{info['height']}")

        # Check capabilities
        print(f"\n  {_B}Capabilities{_N}")
        print(f"  {'─' * 40}")
        print(f"  screencapture: {_G}available{_N}")
        print(f"  osascript:     {_G}available{_N}")
        cliclick = shutil.which("cliclick")
        print(f"  cliclick:      {_G + 'available' + _N if cliclick else _Y + 'not installed (brew install cliclick)' + _N}")
        try:
            import PIL
            print(f"  Pillow:        {_G}available{_N}")
        except ImportError:
            print(f"  Pillow:        {_D}not installed (optional){_N}")
        print()

    elif args.command == "apps":
        apps = get_running_apps()
        print(f"\n  {_B}Running Applications ({len(apps)}){_N}")
        for app in apps:
            print(f"  {_C}●{_N} {app}")
        print()

    elif args.command == "windows":
        windows = get_window_list(app_name=args.app)
        if windows:
            print(f"\n  {_B}Windows{_N}")
            for w in windows:
                print(f"  {_C}●{_N} [{w['app']}] {w['title']}")
                print(f"    {_D}pos: {w['x']},{w['y']} size: {w['width']}x{w['height']}{_N}")
        else:
            print(f"  {_Y}No windows found (may need accessibility permissions){_N}")
        print()

    elif args.command == "osascript":
        result = run_osascript(args.script)
        if result["ok"]:
            print(f"  {_G}✓{_N} {result['stdout']}")
        else:
            print(f"  {_R}✗{_N} {result['stderr']}")

    elif args.command == "tool-spec":
        spec = get_computer_use_tool_spec()
        print(json.dumps(spec, indent=2))


if __name__ == "__main__":
    main()
