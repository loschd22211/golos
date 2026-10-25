"""Поддельный tkinter для тестов интерфейса без графической среды.

Реализованы только те методы, которые использует ``golos.ui``: виджеты,
переменные, холст (кнопка записи и индикатор уровня), простой ttk и
messagebox, запоминающий вызовы.
"""

from __future__ import annotations

import sys
import types


class FakeVar:
    def __init__(self, master=None, value=None, **kwargs):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeWidget:
    created: list = []

    def __init__(self, master=None, **options):
        self.master = master
        self.options = dict(options)
        self.bindings: dict[str, object] = {}
        self.packed = False
        self.grid_options: dict = {}
        FakeWidget.created.append(self)

    # ---------------------------------------------------------------- geometry
    def pack(self, **options):
        self.packed = True
        self.pack_options = options
        return self

    def pack_forget(self):
        self.packed = False
        return None

    def grid(self, **options):
        self.grid_options = options
        self.packed = True
        return self

    def grid_forget(self):
        self.packed = False
        return None

    def place(self, **options):
        return self

    def columnconfigure(self, *args, **kwargs):
        return None

    def rowconfigure(self, *args, **kwargs):
        return None

    # ----------------------------------------------------------------- options
    def configure(self, **options):
        self.options.update(options)
        return self

    config = configure

    def cget(self, key):
        return self.options.get(key)

    def __setitem__(self, key, value):
        self.options[key] = value

    def __getitem__(self, key):
        return self.options[key]

    def bind(self, sequence, handler=None, **kwargs):
        if handler is not None:
            self.bindings[sequence] = handler
        return None

    def unbind(self, sequence):
        self.bindings.pop(sequence, None)

    # ------------------------------------------------------------- имитация
    def event_generate(self, sequence, **kwargs):
        handler = self.bindings.get(sequence)
        if handler is not None:
            handler(types.SimpleNamespace(keysym="", state=0, **kwargs))

    def focus_force(self):
        return None

    def focus_set(self):
        return None

    def lift(self):
        return None

    def transient(self, master=None):
        return None

    def grab_set(self):
        return None

    def grab_release(self):
        return None

    def winfo_viewable(self):
        return True

    def winfo_x(self):
        return 100

    def winfo_y(self):
        return 200

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080

    def overrideredirect(self, value=True):
        self.options["overrideredirect"] = value

    def tk_popup(self, x, y):
        self.options["popup"] = (x, y)

    def tk_popover(self, *args, **kwargs):
        return None

    def resizable(self, width=None, height=None):
        self.options["resizable"] = (width, height)

    def winfo_width(self):
        return 560

    def winfo_height(self):
        return 700

    def destroy(self):
        self.destroyed = True

    def update_idletasks(self):
        return None

    def update(self):
        return None

    def after(self, delay, func=None, *args):
        if delay == 0 and callable(func):
            func(*args)
        return "after-id"


class FakeCanvas(FakeWidget):
    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self.items: list[dict] = []

    def _add(self, kind, args, kwargs):
        item = {"kind": kind, "args": args, **kwargs}
        self.items.append(item)
        return len(self.items)

    def create_oval(self, *args, **kwargs):
        return self._add("oval", args, kwargs)

    def create_rectangle(self, *args, **kwargs):
        return self._add("rectangle", args, kwargs)

    def create_arc(self, *args, **kwargs):
        return self._add("arc", args, kwargs)

    def create_line(self, *args, **kwargs):
        return self._add("line", args, kwargs)

    def create_text(self, *args, **kwargs):
        return self._add("text", args, kwargs)

    def create_polygon(self, *args, **kwargs):
        return self._add("polygon", args, kwargs)

    def delete(self, *args):
        if not args or args[0] == "all":
            self.items.clear()

    def itemconfigure(self, item, **kwargs):
        return None

    itemconfig = itemconfigure

    def coords(self, item, *args):
        return None


class FakeText(FakeWidget):
    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self.content = ""

    def insert(self, index, text):
        self.content += text

    def delete(self, start, end=None):
        self.content = ""

    def get(self, start, end=None):
        return self.content

    def see(self, index):
        return None


class FakeWindow(FakeWidget):
    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self.visible = True

    def title(self, text):
        self.options["title"] = text

    def geometry(self, value=None):
        if value is None:
            return self.options.get("geometry", "560x700")
        self.options["geometry"] = value
        return None

    def minsize(self, width, height):
        self.options["minsize"] = (width, height)

    def configure(self, **options):
        self.options.update(options)
        return self

    def withdraw(self):
        self.visible = False

    def deiconify(self):
        self.visible = True

    def attributes(self, *args, **kwargs):
        if args:
            self.options[f"attr:{args[0]}"] = args[1] if len(args) > 1 else True
        return None

    def iconphoto(self, *args, **kwargs):
        return None

    def protocol(self, name, handler):
        self.options[f"protocol:{name}"] = handler

    def option_add(self, *args, **kwargs):
        return None

    def quit(self):
        self.quit_called = True

    def mainloop(self):
        self.mainloop_called = True


class FakeMenu(FakeWidget):
    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self.entries = []

    def add_command(self, label="", command=None, **kwargs):
        self.entries.append(("command", label, command))

    def add_separator(self, **kwargs):
        self.entries.append(("separator", None, None))

    def add_cascade(self, label="", menu=None, **kwargs):
        self.entries.append(("cascade", label, menu))

    def tk_popup(self, x, y):
        self.options["popup"] = (x, y)


class FakeStyle:
    def __init__(self, master=None):
        self.themes = []

    def theme_use(self, name=None):
        self.themes.append(name)
        return "clam"

    def configure(self, *args, **kwargs):
        return None

    def map(self, *args, **kwargs):
        return None


class FakeCombobox(FakeWidget):
    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self.values = list(options.get("values") or [])

    def configure(self, **options):
        if "values" in options:
            self.values = list(options["values"])
        self.options.update(options)
        return self

    config = configure

    def set_values(self, values):
        self.values = list(values)

    def select(self, value=None):
        if value is not None and self.options.get("textvariable") is not None:
            self.options["textvariable"].set(value)


def build() -> tuple[types.ModuleType, types.ModuleType, types.ModuleType]:
    tk = types.ModuleType("tkinter")
    tk.Tk = FakeWindow
    tk.Toplevel = FakeWindow
    tk.Frame = FakeWidget
    tk.Label = FakeWidget
    tk.Button = FakeWidget
    tk.Entry = FakeWidget
    tk.Spinbox = FakeWidget
    tk.Checkbutton = FakeWidget
    tk.Menu = FakeMenu
    tk.Text = FakeText
    tk.Canvas = FakeCanvas
    tk.PhotoImage = lambda *a, **k: object()
    tk.StringVar = FakeVar
    tk.BooleanVar = FakeVar
    tk.IntVar = FakeVar
    tk.DoubleVar = FakeVar
    tk.END = "end"

    class TclError(Exception):
        pass

    tk.TclError = TclError

    ttk = types.ModuleType("tkinter.ttk")
    ttk.Frame = FakeWidget
    ttk.Label = FakeWidget
    ttk.Button = FakeWidget
    ttk.Entry = FakeWidget
    ttk.Spinbox = FakeWidget
    ttk.Checkbutton = FakeWidget
    ttk.Combobox = FakeCombobox
    ttk.Notebook = FakeWidget
    ttk.Style = FakeStyle
    ttk.Separator = FakeWidget
    tk.ttk = ttk

    messagebox = types.ModuleType("tkinter.messagebox")
    messagebox.calls = []
    messagebox.showinfo = lambda *a, **k: messagebox.calls.append(("info", a))
    messagebox.showwarning = lambda *a, **k: messagebox.calls.append(("warning", a))
    messagebox.showerror = lambda *a, **k: messagebox.calls.append(("error", a))
    messagebox.askyesno = lambda *a, **k: True
    tk.messagebox = messagebox

    return tk, ttk, messagebox


def install(monkeypatch) -> dict:
    """Подменить tkinter в sys.modules и вернуть доступ к фейкам."""
    tk, ttk, messagebox = build()
    monkeypatch.setitem(sys.modules, "tkinter", tk)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", ttk)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", messagebox)
    monkeypatch.delitem(sys.modules, "golos.ui", raising=False)
    return {"tk": tk, "ttk": ttk, "messagebox": messagebox}
