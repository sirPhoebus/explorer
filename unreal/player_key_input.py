"""Send keys into the playing game.

PlayerController.InputKey is not callable from Python. The key is delivered
with a Windows input event after the editor window is in front and the game
viewport has keyboard focus. That keeps the key out of the status-bar console
and the editor viewport. move_player is separate and keeps working.
"""

import ctypes
import os
import time
from ctypes import wintypes

import unreal

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5C, 0x6F, 0xA3, 0xA5}

_KEYS = {
    "BackSpace": 0x08,
    "Tab": 0x09,
    "Enter": 0x0D,
    "Pause": 0x13,
    "CapsLock": 0x14,
    "Escape": 0x1B,
    "SpaceBar": 0x20,
    "PageUp": 0x21,
    "PageDown": 0x22,
    "End": 0x23,
    "Home": 0x24,
    "Left": 0x25,
    "Up": 0x26,
    "Right": 0x27,
    "Down": 0x28,
    "Insert": 0x2D,
    "Delete": 0x2E,
    "Zero": 0x30,
    "One": 0x31,
    "Two": 0x32,
    "Three": 0x33,
    "Four": 0x34,
    "Five": 0x35,
    "Six": 0x36,
    "Seven": 0x37,
    "Eight": 0x38,
    "Nine": 0x39,
    "NumPadZero": 0x60,
    "NumPadOne": 0x61,
    "NumPadTwo": 0x62,
    "NumPadThree": 0x63,
    "NumPadFour": 0x64,
    "NumPadFive": 0x65,
    "NumPadSix": 0x66,
    "NumPadSeven": 0x67,
    "NumPadEight": 0x68,
    "NumPadNine": 0x69,
    "Multiply": 0x6A,
    "Add": 0x6B,
    "Subtract": 0x6D,
    "Decimal": 0x6E,
    "Divide": 0x6F,
    "NumLock": 0x90,
    "ScrollLock": 0x91,
    "LeftShift": 0xA0,
    "RightShift": 0xA1,
    "LeftControl": 0xA2,
    "RightControl": 0xA3,
    "LeftAlt": 0xA4,
    "RightAlt": 0xA5,
    "LeftCommand": 0x5B,
    "RightCommand": 0x5C,
}
for _index in range(12):
    _KEYS[f"F{_index + 1}"] = 0x70 + _index
for _letter in range(26):
    _KEYS[chr(ord("A") + _letter)] = 0x41 + _letter

_MOUSE = {
    "LeftMouseButton": "left",
    "RightMouseButton": "right",
    "MiddleMouseButton": "middle",
    "MouseWheelUp": "wheelup",
    "MouseWheelDown": "wheeldown",
}

_ALIASES = {
    "space": "SpaceBar",
    "spacebar": "SpaceBar",
    "shift": "LeftShift",
    "leftshift": "LeftShift",
    "lshift": "LeftShift",
    "rightshift": "RightShift",
    "rshift": "RightShift",
    "ctrl": "LeftControl",
    "control": "LeftControl",
    "leftctrl": "LeftControl",
    "leftcontrol": "LeftControl",
    "rightctrl": "RightControl",
    "rightcontrol": "RightControl",
    "alt": "LeftAlt",
    "leftalt": "LeftAlt",
    "rightalt": "RightAlt",
    "enter": "Enter",
    "return": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "backspace": "BackSpace",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "capslock": "CapsLock",
    "win": "LeftCommand",
    "windows": "LeftCommand",
    "leftmouse": "LeftMouseButton",
    "leftmousebutton": "LeftMouseButton",
    "lmb": "LeftMouseButton",
    "rightmouse": "RightMouseButton",
    "rightmousebutton": "RightMouseButton",
    "rmb": "RightMouseButton",
    "middlemouse": "MiddleMouseButton",
    "middlemousebutton": "MiddleMouseButton",
    "mmb": "MiddleMouseButton",
    "wheelup": "MouseWheelUp",
    "wheeldown": "MouseWheelDown",
    "mousewheelup": "MouseWheelUp",
    "mousewheeldown": "MouseWheelDown",
}
_DIGITS = {str(i): name for i, name in enumerate(
    ("Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine")
)}
_NAME_BY_LOWER = {name.lower(): name for name in list(_KEYS) + list(_MOUSE)}

_MOUSE_FLAG = {
    ("left", True): 0x0002,
    ("left", False): 0x0004,
    ("right", True): 0x0008,
    ("right", False): 0x0010,
    ("middle", True): 0x0020,
    ("middle", False): 0x0040,
}


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", _POINT),
    ]


_READY = False


def _configure():
    global _READY
    if _READY:
        return
    if ctypes.sizeof(_INPUT) != 40:
        raise RuntimeError(f"Windows INPUT size is {ctypes.sizeof(_INPUT)}, expected 40.")
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    user32.MapVirtualKeyW.restype = wintypes.UINT
    user32.VkKeyScanExW.argtypes = [wintypes.WCHAR, wintypes.HKL]
    user32.VkKeyScanExW.restype = ctypes.c_short
    user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
    user32.GetKeyboardLayout.restype = wintypes.HKL
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(_MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT,
    ]
    user32.PeekMessageW.restype = wintypes.BOOL
    _READY = True


def _box():
    import player_bridge

    box = getattr(player_bridge, "_KEY_RUNTIME", None)
    if not isinstance(box, dict):
        box = {"held": {}, "handle": None, "queue": [], "busy": False, "hwnd": None, "factory": None}
        player_bridge._KEY_RUNTIME = box
    return box


def make_key(name):
    """Build an FKey. The working constructor is cached on the editor module."""
    box = _box()
    factory = box.get("factory")
    if factory is not None:
        return factory(name)

    def by_name(value):
        return unreal.Key(unreal.Name(value))

    def by_field(value):
        return unreal.Key(key_name=value)

    def by_property_name(value):
        key = unreal.Key()
        key.set_editor_property("key_name", unreal.Name(value))
        return key

    def by_property_text(value):
        key = unreal.Key()
        key.set_editor_property("key_name", value)
        return key

    errors = []
    for factory in (by_name, by_field, by_property_name, by_property_text):
        try:
            key = factory(name)
        except Exception as exc:
            errors.append(str(exc))
            continue
        box["factory"] = factory
        return key
    raise RuntimeError("Could not build an Unreal key. " + " | ".join(errors))


def _pawn():
    import player_bridge

    return player_bridge._pawn()


def _require_pawn():
    import player_bridge

    return player_bridge._require_pawn()


def _window_thread(hwnd):
    pid = wintypes.DWORD()
    thread = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return thread, pid.value


def _windows():
    _configure()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != os.getpid():
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value:
            found.append((hwnd, buffer.value))
        return True

    user32.EnumWindows(visit, 0)
    return found


def _choose_window():
    windows = _windows()
    previews = [hwnd for hwnd, title in windows if " Preview" in title]
    if previews:
        return previews[-1]
    editors = [hwnd for hwnd, title in windows if "Unreal Editor" in title]
    if editors:
        return editors[0]
    if windows:
        return windows[0][0]
    raise RuntimeError("The Unreal Editor window is not open.")


def _pump_activation(hwnd):
    """Let the focus change land before the game viewport takes the keyboard."""
    message = _MSG()
    for _ in range(8):
        # WM_ACTIVATE through WM_KILLFOCUS, plus WM_ACTIVATEAPP.
        got = False
        for low, high in ((0x0006, 0x0008), (0x001C, 0x001C)):
            if user32.PeekMessageW(ctypes.byref(message), hwnd, low, high, 1):
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
                got = True
        if not got:
            return


def _focus_editor():
    _configure()
    hwnd = _choose_window()
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    foreground = user32.GetForegroundWindow()
    current = kernel32.GetCurrentThreadId()
    foreground_thread = _window_thread(foreground)[0] if foreground else current
    target_thread = _window_thread(hwnd)[0]
    user32.AttachThreadInput(current, foreground_thread, True)
    user32.AttachThreadInput(current, target_thread, True)
    try:
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    finally:
        user32.AttachThreadInput(current, target_thread, False)
        user32.AttachThreadInput(current, foreground_thread, False)
    _pump_activation(hwnd)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    if pid.value != os.getpid():
        raise RuntimeError("Could not bring Unreal Editor to the front, so no key was sent.")
    _box()["hwnd"] = hwnd
    return hwnd


def _focus_game(controller):
    hwnd = _focus_editor()
    unreal.WidgetLibrary.set_input_mode_game_only(controller)
    unreal.WidgetLibrary.set_focus_to_game_viewport()
    return hwnd


def _send(inputs):
    _configure()
    array = (_INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, ctypes.sizeof(_INPUT))
    if sent != len(inputs):
        raise RuntimeError(f"Windows sent {sent} of {len(inputs)} input events.")


def _keyboard_input(vk, down, extended):
    flags = 0 if down else 0x0002
    if extended:
        flags |= 0x0001
    scan = user32.MapVirtualKeyW(vk, 0) & 0xFFFF
    event = _INPUT()
    event.type = 1
    event.ki = _KEYBDINPUT(vk, scan, flags, 0, 0)
    return event


def _mouse_input(flag, data=0):
    event = _INPUT()
    event.type = 0
    event.mi = _MOUSEINPUT(0, 0, data, flag, 0, 0)
    return event


def _cursor_at_window(hwnd):
    point = _POINT()
    user32.GetCursorPos(ctypes.byref(point))
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    center = ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    user32.SetCursorPos(center[0], center[1])
    return point


def _restore_cursor(point):
    user32.SetCursorPos(point.x, point.y)


def _emit(entry, down, hwnd):
    mouse = entry.get("mouse")
    if mouse is None:
        _send([_keyboard_input(entry["vk"], down, entry["extended"])])
        return
    if mouse == "wheelup" or mouse == "wheeldown":
        if not down:
            return
        point = _cursor_at_window(hwnd)
        try:
            delta = 120 if mouse == "wheelup" else 0xFFFFFFFF - 119
            _send([_mouse_input(0x0800, delta & 0xFFFFFFFF)])
        finally:
            _restore_cursor(point)
        return
    point = _cursor_at_window(hwnd)
    try:
        _send([_mouse_input(_MOUSE_FLAG[(mouse, down)])])
    finally:
        _restore_cursor(point)


def _canonical(raw):
    compact = "".join(str(raw).strip().split())
    if not compact:
        raise RuntimeError("Name a key, such as E, SpaceBar, W, or LeftShift+E.")
    lowered = compact.lower()
    if lowered in _ALIASES:
        return _ALIASES[lowered]
    if len(compact) == 1 and compact.isalpha():
        return compact.upper()
    if compact in _DIGITS:
        return _DIGITS[compact]
    if lowered in _NAME_BY_LOWER:
        return _NAME_BY_LOWER[lowered]
    if len(compact) == 1:
        return compact
    raise RuntimeError(
        f"Unknown key '{raw}'. Use names such as W, A, S, D, SpaceBar, LeftShift, E, One, Up, F1, or LeftMouseButton."
    )


def _scan_character(character):
    layout = user32.GetKeyboardLayout(0)
    scanned = user32.VkKeyScanExW(character, layout)
    if scanned < 0:
        raise RuntimeError(f"This keyboard layout cannot type {character!r}.")
    return scanned & 0xFF, (scanned >> 8) & 0x7


def _entries_for_character(character):
    """Type one written character, including the shift state of this layout."""
    if character == "\n":
        return _entries_for_token("Enter")
    if character == "\t":
        return _entries_for_token("Tab")
    if character == " ":
        return _entries_for_token("SpaceBar")
    vk, modifiers = _scan_character(character)
    entries = []
    if modifiers & 1:
        entries.extend(_entries_for_token("LeftShift"))
    if modifiers & 2:
        entries.extend(_entries_for_token("LeftControl"))
    if modifiers & 4:
        entries.extend(_entries_for_token("LeftAlt"))
    entries.append({"name": character, "vk": vk, "mouse": None, "extended": vk in _EXTENDED})
    return entries


def _entries_for_token(token):
    if token in _KEYS:
        vk = _KEYS[token]
        return [{"name": token, "vk": vk, "mouse": None, "extended": vk in _EXTENDED}]
    if token in _MOUSE:
        return [{"name": token, "vk": None, "mouse": _MOUSE[token], "extended": False}]
    vk, modifiers = _scan_character(token)
    entries = []
    if modifiers & 1:
        entries.extend(_entries_for_token("LeftShift"))
    if modifiers & 2:
        entries.extend(_entries_for_token("LeftControl"))
    if modifiers & 4:
        entries.extend(_entries_for_token("LeftAlt"))
    entries.append({"name": token, "vk": vk, "mouse": None, "extended": vk in _EXTENDED})
    return entries


def _parse_chord(text):
    parts = [part.strip() for part in str(text).replace(",", "+").split("+")]
    parts = [part for part in parts if part]
    if not parts:
        raise RuntimeError("Name a key, such as E, SpaceBar, W, or LeftShift+E.")
    entries = []
    for part in parts:
        entries.extend(_entries_for_token(_canonical(part)))
    wheels = [entry for entry in entries if entry.get("mouse") in ("wheelup", "wheeldown")]
    if wheels and len(entries) != 1:
        raise RuntimeError("Send MouseWheelUp or MouseWheelDown on its own.")
    return entries


def _duration(seconds):
    value = float(seconds)
    if value < 0:
        value = 0.0
    if value > 30:
        value = 30.0
    if 0 < value < 0.05:
        value = 0.05
    return value


def _ensure_tick():
    box = _box()
    if box["handle"] is None:
        box["handle"] = unreal.register_slate_post_tick_callback(_tick)


def _stop_tick():
    box = _box()
    handle = box.get("handle")
    box["handle"] = None
    if handle is not None:
        unreal.unregister_slate_post_tick_callback(handle)


def _release_entries(entries, hwnd):
    for entry in reversed(entries):
        if entry.get("mouse") in ("wheelup", "wheeldown"):
            continue
        _emit(entry, False, hwnd)


def _press_entries(entries, hwnd):
    for entry in entries:
        _emit(entry, True, hwnd)


def _tick(_delta_time):
    box = _box()
    if box.get("busy"):
        return
    box["busy"] = True
    try:
        _tick_body()
    except Exception as exc:
        unreal.log_error("PlayerKeyTools tick: " + str(exc))
    finally:
        box["busy"] = False


def _tick_body():
    box = _box()
    controller, pawn = _pawn()
    now = time.monotonic()
    expired = [
        name
        for name, info in list(box["held"].items())
        if info["until"] is not None and now >= info["until"]
    ]
    play_ended = pawn is None and (box["held"] or box["queue"])
    if play_ended:
        expired = list(box["held"])
        box["queue"].clear()
    if expired or box["queue"]:
        try:
            hwnd = _focus_editor()
            if controller is not None and pawn is not None:
                unreal.WidgetLibrary.set_input_mode_game_only(controller)
                unreal.WidgetLibrary.set_focus_to_game_viewport()
        except Exception:
            hwnd = box.get("hwnd")
        if hwnd:
            for name in expired:
                _release_entries(box["held"][name]["entries"], hwnd)
                box["held"].pop(name, None)
            if box["queue"] and pawn is not None:
                entries = box["queue"].pop(0)
                _press_entries(entries, hwnd)
                box["held"][entries[-1]["name"]] = {
                    "entries": entries,
                    "until": time.monotonic() + 0.05,
                    "label": entries[-1]["name"],
                }
    if not box["held"] and not box["queue"]:
        _stop_tick()


def _remember(entries, duration, label):
    box = _box()
    until = None if duration == 0 else time.monotonic() + duration
    for entry in entries:
        if entry.get("mouse") in ("wheelup", "wheeldown"):
            continue
        box["held"][entry["name"]] = {"entries": [entry], "until": until, "label": label}
    # A chord shares one deadline. Releasing any of its keys releases that key only.
    if len(entries) > 1:
        for entry in entries:
            if entry["name"] in box["held"]:
                box["held"][entry["name"]]["entries"] = [entry]
                box["held"][entry["name"]]["until"] = until
    _ensure_tick()


def press(key, seconds):
    controller, _pawn_actor = _require_pawn()
    entries = _parse_chord(key)
    duration = _duration(seconds)
    label = str(key).strip() or entries[-1]["name"]
    box = _box()
    box["busy"] = True
    try:
        hwnd = _focus_game(controller)
        wheels = [entry for entry in entries if entry.get("mouse") in ("wheelup", "wheeldown")]
        if wheels:
            _emit(wheels[0], True, hwnd)
            return f"Sent {label} to the playing game."
        _press_entries(entries, hwnd)
        _remember(entries, duration, label)
    finally:
        box["busy"] = False
    phrase = "until release_player_key" if duration == 0 else f"for {duration:.2f}s"
    unreal.log(f"PlayerKeyTools sent {label} {phrase}")
    return f"Sent {label} to the playing game, {phrase}."


def release(key):
    _require_pawn()
    entries = _parse_chord(key)
    box = _box()
    hwnd = _focus_editor()
    controller, pawn = _pawn()
    if controller is not None and pawn is not None:
        unreal.WidgetLibrary.set_focus_to_game_viewport()
    names = [entry["name"] for entry in entries]
    for name in names:
        info = box["held"].pop(name, None)
        if info is not None:
            _release_entries(info["entries"], hwnd)
        else:
            _release_entries([entry for entry in entries if entry["name"] == name], hwnd)
    if not box["held"] and not box["queue"]:
        _stop_tick()
    return f"Released {str(key).strip()}."


def release_all():
    box = _box()
    box["queue"].clear()
    names = list(box["held"])
    if not names:
        return "No injected keys were held."
    try:
        hwnd = _focus_editor()
    except Exception:
        hwnd = box.get("hwnd")
    if hwnd:
        for name in names:
            _release_entries(box["held"][name]["entries"], hwnd)
    box["held"].clear()
    _stop_tick()
    return "Released every injected key."


def type_text(text):
    _require_pawn()
    raw = str(text)
    if not raw:
        raise RuntimeError("There is no text to type.")
    if len(raw) > 80:
        raise RuntimeError("Type at most 80 characters at a time.")
    queue = []
    for character in raw:
        queue.append(_entries_for_character(character))
    box = _box()
    box["queue"].extend(queue)
    _ensure_tick()
    return f"Typing {len(queue)} characters into the playing game."


def _seen(controller, name):
    if name not in _KEYS and name not in _MOUSE:
        return None
    try:
        return bool(controller.is_input_key_down(make_key(name)))
    except Exception:
        return None


def describe():
    controller, pawn = _pawn()
    if pawn is None:
        return "Not playing. Call play_game before sending keys."
    box = _box()
    if not box["held"] and not box["queue"]:
        return "No injected keys are held. move_player still walks the pawn."
    parts = []
    for name, info in box["held"].items():
        seen = _seen(controller, name)
        if seen is True:
            state = "down"
        elif seen is False:
            state = "not down"
        else:
            state = "sent"
        parts.append(f"{name} ({state})")
    sentence = "Holding " + ", ".join(parts) + "."
    if box["queue"]:
        sentence += f" {len(box['queue'])} more characters are queued."
    return sentence
