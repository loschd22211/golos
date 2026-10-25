"""Вставка распознанного текста в активное поле ввода.

Два способа:
* ``paste`` — текст кладётся в буфер обмена и отправляется Ctrl+V. Быстро и
  одинаково работает в любом приложении, поддерживающем кириллицу.
* ``type`` — посимвольная эмуляция клавиатуры (Unicode-события Windows),
  медленнее, но не трогает буфер обмена.

Реализации буфера и клавиатуры подменяемы, чтобы логику можно было тестировать
без Windows и без GUI.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

log = logging.getLogger(__name__)


class InjectionError(RuntimeError):
    """Не удалось вставить текст; сообщение показывается пользователю."""


class Clipboard(Protocol):
    def get_text(self) -> str: ...
    def set_text(self, text: str) -> None: ...


class Keyboard(Protocol):
    def send(self, combination: str) -> None: ...
    def write(self, text: str, delay: float = 0.0) -> None: ...
    def paste(self) -> None: ...


# --------------------------------------------------------------- Windows impls
class SystemClipboard:
    """pyperclip с мягкой деградацией, если буфер недоступен."""

    def get_text(self) -> str:
        try:
            import pyperclip

            return pyperclip.paste() or ""
        except Exception as exc:
            log.debug("Буфер обмена недоступен для чтения: %s", exc)
            raise InjectionError(f"Буфер обмена недоступен: {exc}") from exc

    def set_text(self, text: str) -> None:
        try:
            import pyperclip
        except Exception as exc:  # pragma: no cover
            raise InjectionError(f"Не установлен pyperclip: {exc}") from exc
        try:
            pyperclip.copy(text)
        except Exception as exc:
            raise InjectionError(f"Не удалось записать в буфер обмена: {exc}") from exc


class SystemKeyboard:
    """Библиотека keyboard: Unicode-ввод и глобальные сочетания."""

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

    def send(self, combination: str) -> None:
        try:
            self._lib().send(combination)
        except Exception as exc:
            raise InjectionError(f"Не удалось отправить {combination}: {exc}") from exc

    def paste(self) -> None:
        """Ctrl+V с паузой между нажатиями.

        ``keyboard.send("ctrl+v")`` жмёт модификатор и клавишу одновременно,
        и часть приложений (браузеры, Electron) такое нажатие не видит.
        Пошаговое нажатие с задержкой работает заметно надёжнее.
        """
        lib = self._lib()
        try:
            if hasattr(lib, "press") and hasattr(lib, "release"):
                lib.press("ctrl")
                try:
                    time.sleep(0.03)
                    lib.press("v")
                    time.sleep(0.03)
                    lib.release("v")
                    time.sleep(0.03)
                finally:
                    lib.release("ctrl")
            else:  # pragma: no cover - очень старые версии библиотеки
                lib.send("ctrl+v")
        except Exception as exc:
            raise InjectionError(f"Не удалось отправить Ctrl+V: {exc}") from exc

    def write(self, text: str, delay: float = 0.0) -> None:
        try:
            self._lib().write(text, delay=delay)
        except Exception as exc:
            raise InjectionError(f"Не удалось напечатать текст: {exc}") from exc


# --------------------------------------------------------------------- injector
class TextInjector:
    """Вставляет текст в приложение, которое сейчас в фокусе."""

    def __init__(
        self,
        method: str = "paste",
        restore_clipboard: bool = True,
        paste_delay: float = 0.15,
        restore_delay: float = 0.6,
        clipboard: Clipboard | None = None,
        keyboard: Keyboard | None = None,
        sleep=time.sleep,
        guard=None,
    ):
        self.method = method
        # Проверка перед вставкой: например, права окна-получателя.
        # Может бросить InjectionError, тогда текст не потеряется молча.
        self.guard = guard
        self.restore_clipboard = restore_clipboard
        self.paste_delay = paste_delay
        self.restore_delay = restore_delay
        self.clipboard = clipboard or SystemClipboard()
        self.keyboard = keyboard or SystemKeyboard()
        self._sleep = sleep

    def apply_config(self, config) -> None:
        self.method = config.insert_method
        self.restore_clipboard = config.restore_clipboard
        self.paste_delay = config.paste_delay

    # ------------------------------------------------------------------- public
    def insert(self, text: str) -> bool:
        """Вставить текст. Возвращает False для пустой строки."""
        if not text or not text.strip():
            return False
        if self.guard is not None:
            self.guard()
        if self.method == "type":
            return self._type(text)
        return self._paste(text)

    # ------------------------------------------------------------------ private
    def _paste(self, text: str) -> bool:
        previous: str | None = None
        try:
            previous = self.clipboard.get_text()
        except Exception as exc:
            log.debug("Не удалось прочитать буфер обмена: %s", exc)

        try:
            self.clipboard.set_text(text)
        except Exception as exc:
            log.warning("Буфер обмена недоступен (%s), печатаю посимвольно", exc)
            return self._type(text)

        try:
            if self.paste_delay:
                self._sleep(self.paste_delay)
            paste = getattr(self.keyboard, "paste", None)
            if paste is not None:
                paste()
            else:  # pragma: no cover - клавиатуры без paste (тесты, старый код)
                self.keyboard.send("ctrl+v")
        except InjectionError as exc:
            log.warning("Ctrl+V не сработал (%s), печатаю посимвольно", exc)
            self._restore(previous)
            return self._type(text)

        if self.restore_clipboard and previous is not None:
            self._sleep(self.restore_delay)
            self._restore(previous)
        return True

    def _type(self, text: str) -> bool:
        try:
            self.keyboard.write(text, delay=0.002)
        except InjectionError as exc:
            raise InjectionError(
                "Не удалось ввести текст. Если активное окно запущено от имени "
                "администратора, запустите «Голос» тоже от администратора.\n"
                f"Детали: {exc}"
            ) from exc
        return True

    def _restore(self, previous: str | None) -> None:
        try:
            if previous is None:
                self.clipboard.set_text("")
            else:
                self.clipboard.set_text(previous)
        except Exception as exc:
            log.debug("Не удалось вернуть буфер обмена: %s", exc)
