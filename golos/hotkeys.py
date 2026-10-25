"""Глобальные горячие клавиши (библиотека keyboard).

Клавиши работают в любом приложении, пока «Голос» запущен. Бэкенд подменяем,
чтобы логику можно было тестировать без Windows.
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol

from .inject import InjectionError

log = logging.getLogger(__name__)

HotkeyCallback = Callable[[], None]


class HotkeyBackend(Protocol):
    def add(self, combination: str, callback: HotkeyCallback, suppress: bool = False): ...
    def remove(self, handle) -> None: ...
    def validate(self, combination: str) -> bool: ...


class KeyboardBackend:
    """Обёртка над библиотекой keyboard."""

    def __init__(self) -> None:
        self._kb = None

    def _lib(self):
        if self._kb is None:
            try:
                import keyboard

                self._kb = keyboard
            except Exception as exc:  # pragma: no cover
                raise InjectionError(
                    "Не установлена библиотека keyboard. Выполните: pip install keyboard"
                ) from exc
        return self._kb

    def add(self, combination: str, callback: HotkeyCallback, suppress: bool = False):
        return self._lib().add_hotkey(combination, callback, suppress=suppress)

    def remove(self, handle) -> None:
        try:
            self._lib().remove_hotkey(handle)
        except Exception as exc:  # pragma: no cover
            log.debug("Не удалось снять горячую клавишу: %s", exc)

    def validate(self, combination: str) -> bool:
        if not combination:
            return False
        try:
            self._lib().parse_hotkey(combination)
            return True
        except Exception:
            return False


class HotkeyManager:
    """Регистрирует набор горячих клавиш и умеет перерегистрировать их."""

    def __init__(self, backend: HotkeyBackend | None = None):
        self.backend = backend or KeyboardBackend()
        self._handles: dict[str, object] = {}
        self._active: dict[str, str] = {}

    @property
    def registered(self) -> dict[str, str]:
        return dict(self._active)

    def register(self, name: str, combination: str, callback: HotkeyCallback) -> bool:
        """Зарегистрировать одну клавишу. False — если сочетание неверное."""
        combination = normalize_hotkey(combination)
        if not combination:
            return False
        try:
            valid = self.backend.validate(combination)
        except Exception as exc:  # библиотека keyboard недоступна
            log.error("Проверка клавиши %s невозможна: %s", combination, exc)
            return False
        if not valid:
            log.warning("Некорректное сочетание клавиш: %r", combination)
            return False
        try:
            handle = self.backend.add(combination, callback)
        except Exception as exc:
            log.error("Не удалось назначить %s на %s: %s", name, combination, exc)
            return False
        self._handles[name] = handle
        self._active[name] = combination
        log.info("Горячая клавиша %s: %s", name, combination)
        return True

    def unregister(self, name: str) -> None:
        handle = self._handles.pop(name, None)
        if handle is not None:
            self.backend.remove(handle)
        self._active.pop(name, None)

    def unregister_all(self) -> None:
        for name in list(self._handles):
            self.unregister(name)

    def rebind(self, bindings: dict[str, tuple[str, HotkeyCallback]]) -> dict[str, bool]:
        """Полностью перерегистрировать клавиши.

        ``bindings`` = {"toggle": ("ctrl+alt+space", func), ...}
        Возвращает статус по каждой клавише.
        """
        self.unregister_all()
        return {
            name: self.register(name, combination, callback)
            for name, (combination, callback) in bindings.items()
        }


_ALIASES = {
    "control": "ctrl",
    "ctl": "ctrl",
    "win": "windows",
    "windows key": "windows",
    "esc": "escape",
    "del": "delete",
    "ins": "insert",
    "pgup": "page up",
    "pgdn": "page down",
    "spacebar": "space",
    "return": "enter",
}


def normalize_hotkey(combination: str) -> str:
    """Привести «Ctrl+Alt+Space» к виду, который понимает keyboard."""
    parts = [p.strip().lower() for p in (combination or "").split("+") if p.strip()]
    parts = [_ALIASES.get(p, p) for p in parts]
    return "+".join(parts)


_PRETTY = {
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "windows": "Win",
    "escape": "Esc",
    "enter": "Enter",
    "space": "Space",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Del",
    "insert": "Ins",
    "page up": "PgUp",
    "page down": "PgDn",
    "home": "Home",
    "end": "End",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
}


def pretty_hotkey(combination: str) -> str:
    """«ctrl+alt+space» → «Ctrl+Alt+Space» для интерфейса."""
    parts = [p.strip() for p in (combination or "").split("+") if p.strip()]
    out: list[str] = []
    for part in parts:
        low = part.lower()
        if low in _PRETTY:
            out.append(_PRETTY[low])
        elif len(part) == 1:
            out.append(part.upper())
        else:
            out.append(part[:1].upper() + part[1:])
    return "+".join(out)
