import sys

from os import environ

if sys.platform.startswith("win") and hasattr(sys, "_MEIPASS"):
    environ["GI_TYPELIB_PATH"] = sys._MEIPASS + r"\gi_typelibs"
    gst_plugin_dir = sys._MEIPASS + r"\gst_plugins"
    environ["GST_PLUGIN_PATH"] = gst_plugin_dir
    from os import add_dll_directory
    add_dll_directory(gst_plugin_dir)

import gi
from warnings import filterwarnings
filterwarnings("ignore", category=gi.PyGIWarning)

from gi.repository import Adw, Gtk, Gsk, Graphene, Gdk, Pango, Gio, GLib, GObject

if sys.platform == "linux" and GLib.getenv("TEST"):
    import faulthandler
    faulthandler.enable(all_threads=True)

class classproperty:
    def __init__(self, f): self.f = f
    def __get__(self, obj, owner): return self.f(owner)

class Data(GObject.Object):
    __gtype_name__ = "Data"
    path = GObject.Property(type=str)
    data = GObject.Property(type=object)
    def __init__(self, path, init="", readonly=False):
        super().__init__()
        self.path = path
        make_dirs(get_parent(path))
        from sqlite3 import connect
        if readonly:
            path = uri_encode(path) + "?immutable=1"
        self.data = connect(path, timeout=30, check_same_thread=False, autocommit=False, uri="://" in path)
        if readonly:
            for i in ("query_only",): self.execute(f"PRAGMA {i}").close()
        else:
            for i in ("foreign_keys = ON", "auto_vacuum = full", "optimize = 0x10002", "secure_delete = true", "journal_mode = WAL"): self.execute(f"PRAGMA {i}").close()
        if not readonly:
            if init: self.data.executescript(init).close()
            self.commit()

    def columns(self, table):
        c = self.execute("PRAGMA table_info(" + table + ")")
        v = c.fetchall()
        c.close()
        self.commit()
        return [i[1] for i in v]

    def tables(self):
        c = self.execute("SELECT name FROM sqlite_master WHERE type = \"table\" AND name NOT LIKE \"sqlite_%\"")
        v = c.fetchall()
        c.close()
        self.commit()
        return [i[0] for i in v]

    def update(self, table="", column="*", value=None, conditions="", conds=()):
        if column != "*":
            column = f'"{column}"'
        t = f'UPDATE "{table}" SET {column} = ? WHERE {conditions}'
        self.execute(t, [value, *conds]).close()
        self.commit()

    def delete(self, table="", conditions="", conds=()):
        self.execute(f"DELETE FROM '{table}' WHERE {conditions}", conds).close()
        self.commit()

    def new(self, table="", names=(), values=()):
        self.execute("INSERT INTO '" + table + "' " + (('(' + ', '.join("'" + i + "'" for i in names) + ') ') if names else '') + "VALUES(" + ', '.join('?' for _ in values) + ")", values).close()
        self.commit()

    def exists(self, table="", conditions="", conds=()):
        c = self.execute(f"SELECT EXISTS(SELECT 1 FROM '{table}'{(' WHERE ' + conditions) if conditions else ''})", conds)
        v = c.fetchall()
        c.close()
        self.commit()
        return all(all(i) for i in v) if v else False

    def get(self, names=None, values=(), table="", column="*", conditions="", conds=(), return_dict=False):
        self.set_default(table=table, conditions=conditions, conds=conds, names=names, values=values)
        return self.read(table=table, column=column, conditions=conditions, conds=conds, return_dict=return_dict)

    def read(self, table="", column="*", conditions="", conds=(), force_tuple=False, return_dict=False):
        if return_dict:
            self.data.row_factory = self.__class__.dict_factory
        if column != "*":
            column = f'"{column}"'
        t = f'SELECT {column} FROM "{table}"{((" WHERE " + conditions) if conditions else "")}'
        c = self.execute(t, conds)
        v = c.fetchall()
        c.close()
        self.commit()
        if v and len(v) == 1:
            v = v[0]
        if v and isinstance(v, tuple) and len(v) == 1:
            v = v[0]
        self.data.row_factory = None
        if force_tuple and (isinstance(v, tuple) or isinstance(v, list)) and all(isinstance(i, tuple) and len(i) == 1 for i in v): return tuple(i[0] for i in v)
        if force_tuple and isinstance(v, list): return tuple(i for i in v)
        if force_tuple and not isinstance(v, tuple): return (v,)
        return v

    def swap(self, table, column, v1, v2):
        self.update(table=table, column=column, value=-1, conditions=f"{column} = ?", conds=(v1,))
        self.update(table=table, column=column, value=v1, conditions=f"{column} = ?", conds=(v2,))
        self.update(table=table, column=column, value=v2, conditions=f"{column} = ?", conds=(-1,))
        self.commit()

    def persist_action(self, group, action_name, default, table, name, conditions, conds):
        action_name = action_name.lower().replace(" ", "-")
        if isinstance(default, tuple):
            va = self.get(values=default, table=table, column=name, conditions=conditions, conds=conds)
            default = default[-1]
        else:
            va = self.get(values=(default,), table=table, column=name, conditions=conditions, conds=conds)
            if isinstance(default, float) and isinstance(va, int):
                va = float(va)
        va = "" if type(default) == str and not va else va if type(default) == float else bool(int(va)) if type(default) == bool else int(va) if type(default) == int or default.isdigit() else va
        variant = get_variant(va)
        a = Gio.SimpleAction.new_stateful(action_name, variant.get_type() if type(default) == str else None, variant)
        group.add_action(a)
        for n, vv in tuple((("table", table), ("name", name), ("conditions", conditions), ("conds", conds))): setattr(a, n, vv)
        a.connect("notify::state", self.__class__.do_persist_action, self)
        return a

    @staticmethod
    def do_persist_action(a, p, data): data.update(table=a.table, column=a.name, value=a.get_state().unpack(), conditions=a.conditions, conds=a.conds)

    def set_default(self, table="", conditions="", conds=(), names=(), values=()):
        if not self.exists(table=table, conditions=conditions, conds=conds): self.new(table=table, names=names, values=values)

    def table_clear(self, table):
        self.execute(f"DELETE FROM '{table}'").close()
        self.execute(f"UPDATE sqlite_sequence SET seq = 0 WHERE name = '{table}'").close()
        self.commit()

    def table_exists(self, table):
        c = self.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", table)
        v = c.fetchall()
        c.close()
        self.commit()
        return v

    @staticmethod
    def dict_factory(c, r): return {col[0]: r[i] for i, col in enumerate(c.description)}

    @staticmethod
    def setting(name, default, update=None):
        if update != None: App.default.data.update(table="Settings", column="Value", value=update, conditions="Key = ?", conds=(name,))
        else:
            v = App.default.data.get(table="Settings", column="Value", conditions="Key = ?", conds=(name,), values=(name, default))
            if v and isinstance(v, str) and len(v) == 1 and v.isdigit():
                v = int(v)
            return v

    def commit(self):
        try: self.data.commit()
        except Exception as e:
            if not "no transaction is active" in str(e): print("SQL", e)

    def execute(self, *args): return self.data.execute(*args)
    def close(self): return self.data.close()

class ListObject(GObject.Object):
    __gtype_name__ = "ListObject"
    def __init__(self, d=False, **kwargs):
        super().__init__()
        if d:
            kwargs = d
        self.from_dict(kwargs, connect=True)

    def from_dict(self, d, connect=False):
        if not d: return
        d = dict((a.replace(" ", "_").lower(), b) for a, b in d.items())
        if not hasattr(self.__class__, "_props"):
            self.__class__._props = self.list_properties()
            for i in self.__class__._props:
                i.attr = i.name.replace("-", "_")
                i.name2 = (i.nick or i.attr).lower().replace(" ", "_").replace("-", "_")
                i.should_connect = hasattr(self.__class__, "changed") and hasattr(getattr(self.__class__, i.attr).fset, "__func__")
                i.f = getattr(self, f"{i.attr}_from", False)
                if i.value_type.name in ("gint", "gboolean"):
                    i.default = i.get_default_value()
        for i in self.__class__._props:
            if i.name2 in d:
                v = i.f(d[i.name2]) if i.f else d[i.name2]
                if connect or getattr(self, i.attr, None) != v:
                    if v == None and hasattr(i, "default"):
                        v = i.default
                    setattr(self, i.attr, v)
            if connect and i.should_connect: self.connect(f"notify::{i.name}", self.__class__.changed)

    def to_dict(self):
        v = {}
        for i in self.list_properties():
            attr = i.name.replace("-", "_")
            if not hasattr(self, attr): continue
            n = i.nick or attr.title().replace("_", " ")
            v[n] = getattr(self, attr)
        return v

class List(Gio.ListStore):
    __gtype_name__ = "List"

    item_class = GObject.Property(type=object)
    unique = GObject.Property(type=str)
    static = GObject.Property(type=bool, default=False)

    def __init__(self, t, unique="", list=False, static=False):
        self._dict = {}
        super().__init__(item_class=t, item_type=t, unique=unique, static=static)
        if list:
            if not type(list) == bool: self.extend(list)
            if hasattr(self.item_class, "items_changed"): self.connect("items-changed", self.item_class.items_changed)

    def __getitem__(self, key):
        if type(key) == int:
            if 0 > key:
                key = self.get_n_items() + key
            return self.get_item(key)
        if not self.unique: return
        if self.static: return self._dict.get(key, None)
        for i in self:
            v = i.get_property(self.unique)
            if v == key: return i

    def do_dict(self):
        self._dict = dict(((i.get_property(self.unique), i) for i in self))

    def __setitem__(self, key, kwargs):
        if self[key]: return
        self.append(self.item_class(**kwargs))

    def __delitem__(self, key):
        i = self.get(key, None)
        if i: self.remove(i)

    def __iter__(self):
        for i in range(len(self)):
            i = self.get_item(i)
            if i: yield i

    def __len__(self): return self.get_property("n-items")

    def __contains__(self, i):
        if isinstance(i, int): return self.get_n_items() > i > 0
        if self.unique and isinstance(i, str): return bool(self[i])
        if isinstance(i, self.item_class): return next((it for it in self if it == i), False)
        return False

    def get(self, key, d=None): return self[key] or d

    def index(self, value):
        if self.unique and isinstance(value, str): return self.index(self[value])
        return self.find(value)[-1]

    def new_item(self, d=False, **kwargs):
        if not kwargs and isinstance(d, dict):
            kwargs = d
        i = self.item_class(**kwargs)
        return i

    def insert(self, p, d=False, **kwargs):
        if self.item_class == Gtk.StringObject and isinstance(d, str):
            i = Gtk.StringObject.new(d)
        elif d and not isinstance(d, dict):
            i = d
        else:
            i = self.new_item(d=d, **kwargs)
        if 0 > p:
            p = self.get_n_items() + p + 1
        Gio.ListStore.insert(self, p, i)
        if self.static:
            self._dict[i.get_property(self.unique)] = i
        return i

    def add(self, d=False, **kwargs): return self.insert(-1, d=d, **kwargs)

    def prepend(self, d=False, **kwargs): return self.insert(0, d=d, **kwargs)

    def append(self, d=False, **kwargs): return self.add(d=d, **kwargs)

    def extend(self, list=False):
        if list:
            if self.item_class == Gtk.StringObject and all(type(i) == str for i in list):
                l = [Gtk.StringObject.new(i) for i in list]
            elif issubclass(self.item_class, ListObject):
                l = [self.new_item(i) if isinstance(i, dict) else i for i in list]
            else:
                l = [self.new_item(**dict((a.replace(" ", "_").lower(), b) for a, b in i.items())) for i in list]
            self.replace([i for i in self] + l)

    def remove(self, a):
        if not isinstance(a, int):
            a = self.index(a)
        if isinstance(a, int):
            if len(self) > a: Gio.ListStore.remove(self, a)
        if self.static: self.do_dict()

    def replace(self, l=None):
        if not l:
            l = []
        if issubclass(self.item_class, ListObject):
            l = [self.new_item(i) if isinstance(i, dict) else i for i in l]
        elif self.item_class == Gtk.StringObject and all(type(i) == str for i in l):
            l = [Gtk.StringObject.new(i) for i in l]
        if [i for i in self] == l: return
        self.splice(0, len(self), l)
        if self.static: self.do_dict()

class DBUS:
    def __init__(self, interfaces, name, register, call, bus_type=None):
        super().__init__()
        self.interfaces, self.name, self.register, self.call = interfaces, name, register, call
        self.xml = "<!DOCTYPE node PUBLIC '-//freedesktop//DTD D-BUS Object Introspection 1.0//EN' 'http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd'><node>"
        for i, vv in self.interfaces.items():
            self.xml += f"<interface name='{i}'>"
            if "methods" in vv:
                for n, args in vv["methods"].items():
                    self.xml += f"<method name='{n}'>"
                    for k, v in args.items():
                        self.xml += (f"<arg name='{k}' " if k else "<arg ")
                        for k, v in v.items():
                            self.xml += f"{k}='{v}' "
                        self.xml = self.xml.strip() + "/>"
                    self.xml += "</method>"
            if "properties" in vv:
                for n, args in vv["properties"].items():
                    self.xml += (f"<property name='{n}' " if n else "<property ")
                    for k, v in args.items():
                        self.xml += f"{k}='{v}' "
                    self.xml = self.xml.strip() + "/>"
            if "signals" in vv:
                for n, args in vv["signals"].items():
                    self.xml += f"<signal name='{n}'>"
                    for k, v in args.items():
                        self.xml += f"<arg name='{k}' type='{v}'/>"
                    self.xml += "</signal>"
            self.xml += "</interface>"
        self.xml += "</node>"
        if bus_type:
            self.bus = Gio.bus_get_sync(bus_type)
        else:
            self.bus = App.default.get_dbus_connection()
            if self.bus: Gio.bus_own_name_on_connection(self.bus, self.name, Gio.BusNameOwnerFlags.NONE)
        if self.bus:
            for i in Gio.DBusNodeInfo.new_for_xml(self.xml).interfaces: self.bus.register_object_with_closures2(self.register, i, self.do_call)
        else: print(self.name, "no BUS")

    def do_call(self, *args):
        if self.call: return self.call(self, *args)

    @staticmethod
    def path(i): return "/" + i.replace(".", "/")

class Autocomplete(Gtk.Popover):
    __gtype_name__ = "Autocomplete"
    listview = GObject.Property(type=Gtk.ListView)
    model = GObject.Property(type=List)
    complete = GObject.Property(type=object)
    row_activate_function = GObject.Property(type=object)
    select_function = GObject.Property(type=object)
    text_activate_function = GObject.Property(type=object)

    def __init__(self, text, select=None, text_activate=None, row_activate=None, complete=None, model=None, factory=None, bind=None, setup=None, teardown=None, unbind=None, hexpand=True, max_content_width=-1, max_content_height=400):
        super().__init__(child=Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.EXTERNAL, hscrollbar_policy=Gtk.PolicyType.EXTERNAL, propagate_natural_height=True, propagate_natural_width=True, max_content_width=max_content_width, max_content_height=max_content_height, hexpand=hexpand), can_focus=False, has_arrow=False, autohide=False, hexpand=hexpand)
        self.select_function = self.__class__.default_select if select == True else select
        self.text_activate_function= text_activate
        self.row_activate_function = row_activate or self.__class__.default_row_activate
        self.set_css_classes([])
        self.add_css_class("autocomplete")
        self.model = List(Gtk.StringObject) if model == None else model
        self.complete = complete
        text.connect("changed", self.__class__.text_changed)
        self.listview = Gtk.ListView(model=Gtk.SingleSelection(autoselect=False, can_unselect=True), hexpand=hexpand, single_click_activate=True)
        self.listview.get_model().connect("notify::selected", self.__class__.selected_changed, self)
        self.listview.set_css_classes(["navigation-sidebar"])
        self.listview.get_model().connect("notify::n-items", self.__class__.n_items_changed, self)
        self.bind_property("model", self.listview.get_model(), "model", DEFAULT_BIND)
        self.listview.connect("activate", self.__class__.row_activate)
        self.get_child().set_child(self.listview)
        e = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        e.connect("key-pressed", self.__class__.on_key_pressed)
        text.add_controller(e)
        self.set_parent(text)
        if not factory and any((bind, setup, teardown, unbind)):
            factory = Gtk.SignalListItemFactory.new()
            factory.connect("setup", setup or self.__class__.setup)
            factory.connect("bind", bind or self.__class__.bind)
            factory.connect("unbind", unbind or self.__class__.unbind)
            factory.connect("teardown", teardown or self.__class__.teardown)
        if not factory and not hasattr(self.__class__, "factory"):
            self.__class__.factory = Gtk.SignalListItemFactory.new()
            self.__class__.factory.connect("setup", self.__class__.setup)
            self.__class__.factory.connect("bind", self.__class__.bind)
            self.__class__.factory.connect("unbind", self.__class__.unbind)
            self.__class__.factory.connect("teardown", self.__class__.teardown)
        self.listview.set_factory(factory or self.__class__.factory)

    def set_text(self, t="", p=-1):
        self.changing = True
        self.get_parent().set_text(t)
        self.get_parent().set_position(p)
        self.changing = False

    @staticmethod
    def selected_changed(l, p, self):
        item = self.listview.get_model().get_selected_item()
        if item and self.select_function: self.select_function(self.get_parent(), self, item)

    @staticmethod
    def default_select(text, self, item):
        t = item.get_string()
        self.set_text(t)

    @staticmethod
    def default_row_activate(text, self, item):
        t = item.get_string() if item else ""
        if not item or text.get_text() == t:
            self.popdown()
            self.model.replace()
            return True
        self.set_text(t)
        return True

    @staticmethod
    def on_key_pressed(e, k, kc, s):
        text = e.get_widget()
        if not text.get_mapped(): return
        self = next((i for i in text if isinstance(i, Autocomplete)), False)
        if not self: return
        if k == Gdk.KEY_Return:
            if s == 0 and self.get_mapped():
                self.row_activate(self.listview, self.listview.get_model().get_selected())
                return True
            elif self.text_activate_function: return self.text_activate_function(text, self, s)
        if self.get_mapped() and k in (Gdk.KEY_Down, Gdk.KEY_Up):
            n = self.listview.get_model().get_selected()
            if k == Gdk.KEY_Up:
                if n == 0:
                    n = self.listview.get_model().get_n_items() - 1
                    if n == 0:
                        n = None
                else:
                    if self.listview.get_model().get_n_items():
                        n -= 1
                    else:
                        n = None
            else:
                n += 1
                if not self.listview.get_model().get_n_items():
                    n = None
                elif n >= self.listview.get_model().get_n_items():
                    n = 0
            if n != None: self.listview.scroll_to(n, Gtk.ListScrollFlags.SELECT)
            return True
        if k == Gdk.KEY_Escape and self.get_mapped():
            self.model.replace()
            self.popdown()
            return True

    @staticmethod
    def setup(f, l): l.set_child(Gtk.Label(xalign=0))

    @staticmethod
    def bind(f, l):
        if not l.get_child(): return
        l.get_child().bindings = [l.get_property("item").bind_property("string", l.get_child(), "label", DEFAULT_BIND)]

    @staticmethod
    def unbind(f, l):
        for i in getattr(l.get_child(), "bindings", []): i.unbind()
        if hasattr(l.get_child(), "bindings"): del l.get_child().bindings

    @staticmethod
    def teardown(f, l): l.set_child(None)

    @staticmethod
    def n_items_changed(a, b, self):
        self.popdown()
        if a.get_n_items() != 0: Task.invoke(self.popup)

    @staticmethod
    def text_changed(t):
        if not t.get_mapped(): return
        if getattr(t, "changing", False): return
        self = next((i for i in t if isinstance(i, Autocomplete)), False)
        if not self or getattr(self, "changing", False): return
        if self.complete:
            self.model.replace()
            self.complete(self, self.get_parent())

    @staticmethod
    def row_activate(l, n):
        if not isinstance(n, int): return
        self = l.get_ancestor(Autocomplete)
        if not self: return
        if self.row_activate_function: self.row_activate_function(self.get_parent(), self, l.get_model().get_item(n))

class Window(Adw.ApplicationWindow):
    __gtype_name__ = "UtilsWindow"

    @classproperty
    def current(*_): return next((i for i in Gtk.Window.list_toplevels() if hasattr(i, "get_application") and i.get_application() == App.default and isinstance(i, Window)), None)

    def __init__(self, **kwargs):
        if "title" in kwargs:
            title = kwargs["title"]
        else:
            title = App.default.about.get_application_name() if App.default and App.default.about else App.default.get_application_id().rsplit(".", 1)[-1] if App.default else self.__class__.__gtype_name__
        toast_overlay = Adw.ToastOverlay()
        super().__init__(css_name=kwargs["css_name"] if "css_name" in kwargs else "window", application=App.default, content=toast_overlay, title=title, height_request=1, width_request=1, default_width=100, default_height=100)
        if "content" in kwargs: self.set_content(kwargs["content"])
        self.add_css_class(self.get_title().lower().replace(" ", "-") + "-window")
        self.add_css_class(self.__gtype_name__.lower())
        self.__class__.controller_init(self)

    def add_actions(self, *actions):
        for i in actions:
            name, callback, accels = i[0], i[1], i[2] if len(i) == 3 else ""
            self.add_action(Gio.SimpleAction.new(name, None))
            if accels != "" and self.get_application(): self.get_application().set_accels_for_action(f"win.{name}", accels.split(" "))
            if callback: self.lookup_action(name).connect("activate", callback, self)

    def get_content(self): return Adw.ApplicationWindow.get_content(self).get_child()

    def set_content(self, w): return Adw.ApplicationWindow.get_content(self).set_child(w)

    def get_child(self): return Window.get_content(self)

    def set_child(self, w): return Window.set_content(self, w)

    def get_widget_at_popover(self, popover=None, x=0, y=0):
        if not popover: return
        s = popover.get_first_child().get_first_child()
        p = self.compute_point(s, Graphene.Point.init(Graphene.Point(), x, y))[-1]
        if p and s.get_width() >= p.x >= 0 and s.get_height() >= p.y >= 0: return s.pick(p.x, p.y, Gtk.PickFlags.DEFAULT)

    def show_popover_at_widget(self, popover=None, widget=None, x=0, y=0):
        if not popover or not widget: return
        if popover.get_parent() != widget:
            popover.unparent()
            popover.set_parent(widget)
        popover.present()
        r = Gdk.Rectangle()
        w = max(popover.get_first_child().get_preferred_size()[1].width, popover.get_property("width-request"))
        p = self.compute_point(widget, Graphene.Point.init(Graphene.Point(), min(self.get_width() - 10, x + (min(w, 360) / 2)), y))[-1]
        r.x, r.y = p.x, p.y
        popover.set_pointing_to(r)
        popover.popup()

    def change_fullscreen(self, *_):
        a = self.lookup_action("fullscreen") or Gio.Application.get_default().lookup_action("fullscreen")
        if not a: return
        if hasattr(self, "wide") and hasattr(self, "narrow"):
            for i in (self.wide, self.narrow):
                if a.get_state().unpack(): ToolbarFullscreen.fu(i)
                else: ToolbarFullscreen.un(i)
        elif hasattr(self, "toolbar"):
            if a.get_state().unpack(): ToolbarFullscreen.fu(self.toolbar)
            else: ToolbarFullscreen.un(self.toolbar)

    @staticmethod
    def draw_drag_update(e, x, y):
        if e.get_current_button() != 1: return e.reset()
        self = e.get_widget()
        sx, sy = self.drag_start
        self.drag_to = (sx + x, sy + y)
        self.draw.queue_draw()
        e.set_state(Gtk.EventSequenceState.CLAIMED)

    @staticmethod
    def reset_draw(d, w=0, h=0):
        self = d.get_root()
        self.drag_start = (0, 0)
        self.draw.queue_draw()

    @staticmethod
    def click_to_args(*args):
        e = n = x = y = signal = b = s = self = None
        if isinstance(args[0], Gtk.EventController):
            if isinstance(args[0], Gtk.GestureLongPress):
                e, x, y, signal = args
                n = 1
            else:
                e, n, x, y, signal = args
            b = e.get_current_button()
            s = e.get_current_event_state()
            self = e.get_widget().get_root()
        if isinstance(args[0], Window):
            n = 1
            self, x, y, signal, b, s = args
        return self, e, n, x, y, signal, b, s

    def key_to_click(self, v, signal="released", widget=False, mask=None):
        if not hasattr(self, "on_click"): return
        widget = widget or self.get_focus()
        if not widget: return
        bounds = widget.compute_bounds(self)[1]
        x, y = bounds.get_x() + 1, bounds.get_y() + 1
        if v == Gdk.KEY_Menu: self.on_click(self, x, y, signal, 3, mask)
        if v == Gdk.KEY_space: self.on_click(self, x, y, signal, 1, mask)
        if Gdk.ModifierType.SHIFT_MASK in mask and v == Gdk.KEY_Return: self.on_click(self, x, y, signal, 2, mask)

    def controller_init(self, widget=None):
        widget = widget or self
        if hasattr(self.__class__, "on_click"):
            for e in (Gtk.GestureClick(button=0), Gtk.GestureLongPress(button=0)):
                e.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
                e.set_propagation_limit(Gtk.PropagationLimit.NONE)
                e.connect("pressed", self.__class__.on_click, "pressed")
                if isinstance(e, Gtk.GestureClick): e.connect("released", self.__class__.on_click, "released")
                widget.add_controller(e)
        if hasattr(self.__class__, "on_scroll"):
            e = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE)
            e.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            e.connect("scroll", self.__class__.on_scroll)
            widget.add_controller(e)
        s = ("drag-update", "drag-begin", "drag-end")
        if any((hasattr(self.__class__, "on_gesture_drag_" + i.replace("-", "_")) for i in s)):
            e = Gtk.GestureDrag(button=0)
            for i in s:
                n = "on_gesture_drag_" + i.replace("-", "_")
                if hasattr(self.__class__, n): e.connect(i, getattr(self.__class__, n))
            widget.add_controller(e)
        s = ("prepare", "drag-begin", "drag-end")
        if any((hasattr(self.__class__, "on_drag_source_" + i.replace("-", "_")) for i in s)):
            e = Gtk.DragSource(propagation_phase=Gtk.PropagationPhase.CAPTURE)
            e.set_propagation_limit(Gtk.PropagationLimit.NONE)
            e.set_actions(self.__class__.drag_actions if hasattr(self.__class__, "drag_actions") else Gdk.DragAction.COPY)
            for i in s:
                n = "on_drag_source_" + i.replace("-", "_")
                if hasattr(self.__class__, n): e.connect(i, getattr(self.__class__, n))
            widget.add_controller(e)
        s = ("drop", "enter", "leave", "motion")
        if any((hasattr(self.__class__, "on_drop_target_" + i) for i in s)):
            e = Gtk.DropTarget(preload=True)
            e.set_propagation_limit(Gtk.PropagationLimit.NONE)
            e.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            e.set_actions(self.__class__.drop_actions if hasattr(self.__class__, "drop_actions") else Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
            if hasattr(self.__class__, "drop_target_gtypes"): e.set_gtypes(self.__class__.drop_target_gtypes)
            for i in s:
                n = "on_drop_target_" + i
                if hasattr(self.__class__, n): e.connect(i, getattr(self.__class__, n))
            widget.add_controller(e)

        s = ("enter", "leave", "motion")
        if any((hasattr(self.__class__, "on_event_" + i) for i in s)):
            e = Gtk.EventControllerMotion()
            if isinstance(self, Gtk.Root):
                e.set_propagation_limit(Gtk.PropagationLimit.NONE)
                e.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            for i in s:
                n = "on_event_" + i
                if hasattr(self.__class__, n): e.connect(i, getattr(self.__class__, n))
            widget.add_controller(e)
        if hasattr(self.__class__, "on_key_released") or hasattr(self.__class__, "on_key_pressed"):
            e = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
            if hasattr(self.__class__, "on_key_released"): e.connect("key-released", self.__class__.on_key_released)
            if hasattr(self.__class__, "on_key_pressed"): e.connect("key-pressed", self.__class__.on_key_pressed)
            widget.add_controller(e)
        if hasattr(self.__class__, "on_map"): self.connect("map", self.__class__.on_map)

class PopupDialogButton(Gtk.Button):
    def __init__(self):
        super().__init__()
        self.connect("notify::root", self.__class__.root_changed)
        self.connect("clicked", self.__class__.do)

    @staticmethod
    def do(self):
        self = PopupDialog.get_self(self)
        if not self: return
        if self.window.get_mapped(): self.present()
        else: self.popout()

    @staticmethod
    def root_changed(self, p):
        if isinstance(PopupDialog.get_root(self), Adw.Dialog): self.set_properties(icon_name="external-link-symbolic", tooltip_text="Pop Out")
        else: self.set_properties(icon_name="edit-undo-symbolic", tooltip_text="Back")

class PopupDialog(GObject.Object):
    title = GObject.Property(type=str)
    window = GObject.Property(type=Adw.Window)
    dialog = GObject.Property(type=Adw.Dialog)
    content = GObject.Property(type=Gtk.Widget)

    def get_root(w):
        if isinstance(w, PopupDialog): return (w.window.get_root() if w.window.get_mapped() else w.dialog.get_root() if w.dialog.get_mapped() else None)
        dialog = w if isinstance(w, Adw.Dialog) else w.get_ancestor(Adw.Dialog)
        return dialog if dialog else Gtk.Window.get_root(w)

    @staticmethod
    def get_self(w):
        self = getattr(PopupDialog.get_root(w), "popup_dialog", None)
        return self if self else None

    def __init__(self, title="", content=None, window=None, dialog=None, window_kwargs=None, dialog_kwargs=None, **kwargs):
        super().__init__()
        self.content = content
        dialog_kwargs = dialog_kwargs or {}
        window_kwargs = window_kwargs or {}
        for i in (dialog_kwargs, window_kwargs): i.update(kwargs)
        if not "application" in window_kwargs:
            window_kwargs["application"] = App.default
        self.window = window or Adw.Window(**window_kwargs)
        self.window.set_properties(content=Adw.ToastOverlay())
        self.dialog = dialog or Adw.Dialog(**dialog_kwargs)
        self.dialog.set_properties(child=Adw.ToastOverlay())
        self.dialog.popup_dialog = self.window.popup_dialog = self
        self.title = title
        for i in (self.window, self.dialog): self.bind_property("title", i, "title", DEFAULT_BIND)
        self.window.connect("close-request", self.__class__.do_close)
        self.dialog.connect("close-attempt", self.__class__.do_close)
        self.dialog.connect("closed", self.__class__.do_close)

    def get_child(self):
        if self.content: return self.content.get_parent()

    def remove_css_class(self, i):
        self.dialog.remove_css_class(i)
        self.window.remove_css_class(i)

    def add_css_class(self, i):
        self.dialog.add_css_class(i)
        self.window.add_css_class(i)

    @staticmethod
    def do_close(w):
        self = PopupDialog.get_self(w)
        if not self: return
        if not getattr(self, "doing", False): self.do_dispose()

    def pick(self, *args):
        if self.window.get_mapped(): return self.window.pick(*args)
        if self.dialog.get_mapped(): return self.dialog.pick(*args)
        return None

    def do_dispose(self):
        self.doing = True
        self.reset_content()
        w, d = self.window, self.dialog
        self.content = self.window = self.dialog = None
        if d:
            if d.get_mapped(): d.close()
            d.popup_dialog = None
        if w:
            if not w.get_surface(): w.present()
            w.popup_dialog = None
            w.close()
            w.set_application(None)
            w.destroy()
        self.doing = False

    def set_title(self, t=""): self.set_property("title", t)

    def get_title(self): return self.get_property("title")

    def set_content(self, content=None):
        self.set_property("content", None)
        if self.dialog.get_mapped(): self.present()
        elif self.window.get_mapped(): self.popout()
        else: self.reset_content()

    def get_content(self): return self.get_property("content")

    def reset_content(self):
        if self.window: self.window.get_property("content").set_property("child", None)
        if self.dialog: self.dialog.get_property("child").set_property("child", None)
        if self.content and self.content.get_parent(): self.content.unparent()

    def close(self):
        for i in (self.window, self.dialog):
            if i: i.close()

    def present(self, a=None):
        self.doing = True
        if self.window.get_mapped(): self.window.set_visible(False)
        self.reset_content()
        if self.content:
            if not self.dialog.get_property("child").get_property("child") == self.content: self.dialog.get_property("child").set_property("child", self.content)
            if self.dialog.get_parent() and hasattr(self.dialog.get_parent(), "close"):
                self.dialog.get_parent().close()
                self.dialog.unparent()
                self.dialog.close()
            self.dialog.present(a if a and a != self.window else next((i for i in Gtk.Window.list_toplevels() if not i == self.window and isinstance(i, Adw.ApplicationWindow)), None))
        self.doing = False

    def popout(self):
        self.doing = True
        if self.dialog.get_mapped():
            self.dialog.close()
            if self.dialog.get_parent() and hasattr(self.dialog.get_parent(), "close"):
                self.dialog.get_parent().close()
                self.dialog.unparent()
                self.dialog.close()
        self.reset_content()
        if self.content:
            if not self.window.get_property("content").get_property("child") == self.content: self.window.get_property("content").set_property("child", self.content)
            self.window.present()
        self.doing = False

    @staticmethod
    def get_all():
        l = set()
        for i in Adw.Dialog.get_all() + Gtk.Window.list_toplevels():
            s = getattr(i, "popup_dialog", False)
            if s: l.add(s)
        return list(l)

class BarChartBar(ListObject):
    __gtype_name__ = "BarChartBar"
    title = GObject.Property(type=str)
    value = GObject.Property(type=int)

    @GObject.Property(type=int, flags=GObject.ParamFlags.READABLE)
    def height(self):
        f = max(self.max_height, 1) / (self.max_value or 1)
        return self.value * f

    max_value = GObject.Property(type=int)
    max_height = GObject.Property(type=int)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for i in ("value", "max-value", "max-height"): self.connect(f"notify::{i}", self.__class__.update_height)

    def update_height(self, *_):
        if self.value > self.max_value:
            self.max_value = self.value
            return
        self.notify("height")

    factory = Gtk.SignalListItemFactory.new()
    @staticmethod
    def setup(f, l): l.set_child(Adw.Bin(css_name="bar", valign=Gtk.Align.END))
    factory.connect("setup", setup)
    @staticmethod
    def bind(f, l):
        if not l.get_child(): return
        l.get_child().bindings = []
        l.get_child().get_parent().set_css_classes([])
        l.get_child().bindings.append(l.get_property("item").bind_property("title", l.get_child(), "tooltip-text", DEFAULT_BIND))
        l.get_child().bindings.append(l.get_property("item").bind_property("height", l.get_child(), "height-request", DEFAULT_BIND))
    factory.connect("bind", bind)
    @staticmethod
    def unbind(f, l):
        for i in getattr(l.get_child(), "bindings", []): i.unbind()
        if hasattr(l.get_child(), "bindings"): del l.get_child().bindings
    factory.connect("unbind", unbind)
    @staticmethod
    def teardown(f, l): l.set_child(None)
    factory.connect("teardown", teardown)

class BarChart(Gtk.ListView):
    __gtype_name__ = "BarChart"
    bars = GObject.Property(type=List, flags=GObject.ParamFlags.READWRITE | GObject.ParamFlags.CONSTRUCT_ONLY)
    max_value = GObject.Property(type=int)

    def __init__(self, data=None, height_request=300):
        super().__init__(css_name="bar-chart", orientation=Gtk.Orientation.HORIZONTAL, model=Gtk.NoSelection(), factory=BarChartBar.factory, height_request=height_request, bars=List(BarChartBar), halign=Gtk.Align.CENTER)
        self.set_css_classes([])
        self.bind_property("bars", self.get_model(), "model", DEFAULT_BIND)
        self.bars.connect("items-changed", self.__class__.items_changed, self)
        self.bars.replace(data)

    @staticmethod
    def items_changed(l, p, r, a, self):
        self.max_value = max((i.value for i in self.bars), default=1) or 1
        for i in self.bars:
            for it in getattr(i, "bindings", []): it.unbind()
            i.bindings = [self.bind_property("max-value", i, "max-value", SYNC_BIND), self.bind_property("height-request", i, "max-height", SYNC_BIND)]

class CalendarHeatmapDay(ListObject):
    __gtype_name__ = "CalendarHeatmapDay"
    type = GObject.Property(type=str)
    title = GObject.Property(type=str)
    value = GObject.Property(type=int)

    @GObject.Property(type=float, flags=GObject.ParamFlags.READABLE)
    def opacity(self): return 0 if self.type == "Pad" else 1 if self.value == 0 else max(0.15, float(self.value / (self.max_value or 1)))

    max_value = GObject.Property(type=int)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for i in ("value", "max-value"): self.connect(f"notify::{i}", self.__class__.update_opacity)

    def update_opacity(self, *_):
        if self.value > self.max_value:
            self.max_value = self.value
            return
        self.notify("opacity")

    @staticmethod
    def css_bind(b, v):
        self = b.get_property("source")
        l = []
        if self.type: l.append(self.type.lower())
        if self.value == 0 and not self.type == "Pad": l.append("empty")
        return l

    factory = Gtk.SignalListItemFactory.new()
    @staticmethod
    def setup(f, l): l.set_child(Adw.Bin())
    factory.connect("setup", setup)
    @staticmethod
    def bind(f, l):
        if not l.get_child(): return
        l.get_child().bindings = []
        Task.invoke(l.get_child().get_parent().set_css_classes, [])
        l.get_child().bindings.append(l.get_property("item").bind_property("title", l.get_child(), "tooltip-text", DEFAULT_BIND))
        l.get_child().bindings.append(l.get_property("item").bind_property("opacity", l.get_child(), "opacity", DEFAULT_BIND))
        l.get_child().bindings.append(l.get_property("item").bind_property("opacity", l.get_child(), "css-classes", DEFAULT_BIND, CalendarHeatmapDay.css_bind))
    factory.connect("bind", bind)
    @staticmethod
    def unbind(f, l):
        for i in getattr(l.get_child(), "bindings", []): i.unbind()
        if hasattr(l.get_child(), "bindings"): del l.get_child().bindings
    factory.connect("unbind", unbind)
    @staticmethod
    def teardown(f, l): l.set_child(None)
    factory.connect("teardown", teardown)

class CalendarHeatmap(Gtk.GridView):
    __gtype_name__ = "CalendarHeatmap"

    days = GObject.Property(type=List, flags=GObject.ParamFlags.READWRITE | GObject.ParamFlags.CONSTRUCT_ONLY)
    max_value = GObject.Property(type=int)
    year = GObject.Property(type=int, default=1, minimum=1)

    def __init__(self, data=None, year=1, title=False):
        super().__init__(halign=Gtk.Align.CENTER, min_columns=53, max_columns=53, model=Gtk.NoSelection(), factory=CalendarHeatmapDay.factory, css_name="calendar-heatmap", days=List(CalendarHeatmapDay))
        self.set_css_classes([])
        self.year = year
        self.connect("notify::year", self.__class__.do)
        self.days.connect("items-changed", self.__class__.items_changed, self)
        self.days.replace(data)

    @staticmethod
    def items_changed(l, p, r, a, self): self.do()

    def do(self, *_):
        m = List(CalendarHeatmapDay)
        start = GLib.DateTime.new_local(self.year, 1, 1, 0, 0, 0)
        end = GLib.DateTime.new_local(self.year, 12, 31, 0, 0, 0)
        first_weekday = start.get_day_of_week() % 7
        days_in_year = end.get_day_of_year()
        self.max_value = max((i.value for i in self.days), default=1)
        for i in self.days:
            i.type = "Day"
            for it in getattr(i, "bindings", []): it.unbind()
            i.bindings = [self.bind_property("max-value", i, "max-value", SYNC_BIND)]
        for weekday in range(7):
            for week in range(53):
                day_index = week * 7 + weekday - first_weekday
                if 0 <= day_index < days_in_year:
                    if len(self.days) > day_index:
                        cell = self.days[day_index]
                    else:
                        cell = CalendarHeatmapDay(type="Day")
                else:
                    cell = CalendarHeatmapDay(type="Pad")
                m.append(cell)
        self.get_model().set_model(m)

class RGBA(Gdk.RGBA):
    __gtype_name__ = "RGBA"
    def __init__(self, s="#ffffff", validate=False):
        super().__init__()
        if isinstance(s, str):
            if s.startswith("rgb(") and s.endswith(")") and len(s.split(",")) == 3:
                r = [i.strip() for i in s.strip("rgb() ").split(",")]
                if all("." in i for i in r):
                    r = [str(float(i) * 255) for i in r]
                    s = f"rgb({', '.join(r)})"
        elif s:
            s = "rgba(" + ", ".join(tuple(str(i) for i in RGBA.rgb(s, a=True))) + ")"
        r = self.parse(s or "#ffffff")
        if validate:
            self.valid = r

    def rgb(c, f=False, a=False):
        nu = 4 if a else 3
        if isinstance(c, Gdk.RGBA):
            c = c.to_string()
        if isinstance(c, str):
            c = [float(i.strip()) if "." in i.strip() else int(i.strip()) for i in c.split("(", 1)[-1].rsplit(")", 1)[0].split(",")]
        if not isinstance(c, list):
            c = list(i for i in c)
        if len(c) > nu:
            c = c[:nu]
        n = [i / 255 for i in c] if f and any(i > 1 for i in c) else [i * 255 if type(i) == float and 1 >= i else i for i in c]
        while not len(n) == nu: n.append(1)
        return tuple(i for i in n)

class DonutChart(Gtk.DrawingArea):
    __gtype_name__ = "DonutChart"
    segments = GObject.Property(type=object)
    tooltips = GObject.Property(type=object)
    colors = GObject.Property(type=object)
    hovered = GObject.Property(type=str)
    animation = GObject.Property(type=Adw.TimedAnimation)
    default_colors = [RGBA(i) for i in ("#99c1f1", "#62a0ea", "#3584e4", "#1c71d8", "#1a5fb4")]
    def __init__(self, segments=None, **kwargs):
        if not segments:
            segments = {}
        super().__init__(css_name="donut-chart", has_tooltip=True, **kwargs)
        self.segments = segments
        self.animation = Adw.TimedAnimation(widget=self, value_from=0.0, value_to=1.0, duration=200, target=Adw.CallbackAnimationTarget.new(self.__class__.set_animation_progress, self))
        self.set_draw_func(self.__class__.do_draw_func)
        Window.controller_init(self)
        for i in ("segments", "colors", "tooltips"): self.connect(f"notify::{i}", self.__class__.try_queue_draw)

    @staticmethod
    def try_queue_draw(self, *_): self.queue_draw()

    @staticmethod
    def do_draw_func(self, cr, width, height):
        if not self.segments: return
        if self.segments and sum(self.segments.values()) != 100:
            print("DonutChart:", "segments don't add to 100")
            return
        if self.colors and len(self.segments) > len(self.colors):
            print("DonutChart:", "not enough colors")
            return
        if self.tooltips and len(self.segments) > len(self.tooltips):
            print("DonutChart:", "not enough tooltips")
            return
        total = sum(self.segments.values())
        if total <= 0: return
        from math import radians, pi, tau, cos, sin
        cx = width / 2
        cy = height / 2
        radius = min(width, height) * 0.32
        line_width = min(width, height) * 0.16
        gap = radians(0)
        start_angle = -pi / 2
        for i, segment in enumerate(self.segments.keys()):
            fraction = self.segments[segment] / total
            full_angle = fraction * tau
            hover = 0.0 if not getattr(self, "hover_values", False) else self.hover_values[i]
            angle_start = start_angle + gap / 2
            angle_end = start_angle + full_angle - gap / 2
            mid_angle = start_angle + full_angle / 2
            offset = 10 * hover
            r = radius + 8 * hover
            cr.save()
            cr.translate(cos(mid_angle) * offset, sin(mid_angle) * offset)
            color = self.get_color(i)
            cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
            cr.set_line_width(line_width)
            cr.set_line_cap(0)
            if angle_end > angle_start:
                cr.arc(cx, cy, r, angle_start, angle_end)
                cr.stroke()
            cr.restore()
            start_angle += full_angle

    def get_color(self, i=0):
        if self.colors and len(self.colors) > i:
            color = self.colors[i]
        else:
            color = DonutChart.default_colors[i] if len(DonutChart.default_colors) > i else DonutChart.default_colors[0]
        return color

    def get_tooltip(self, segment=""):
        if not segment: return ""
        t = ""
        if self.tooltips:
            n = list(self.segments.keys()).index(segment)
            if len(self.tooltips) > n:
                t = self.tooltips[n]
        if t == "":
            t = f"{segment} - {self.segments[segment]}%"
        return t

    def get_segment(self, x, y):
        if not self.segments: return
        total = sum(self.segments.values())
        if not total: return
        from math import atan2, sqrt, pi, tau
        w, h = self.get_width(), self.get_height()
        cx, cy = w / 2, h / 2
        radius, width = min(w, h) * .32, min(w, h) * .16
        dx, dy = x - cx, y - cy
        if not radius - width / 2 <= sqrt(dx * dx + dy * dy) <= radius + width / 2 + 15: return
        angle = (atan2(dy, dx) + pi / 2) % tau
        current = 0
        for i, segment in enumerate(self.segments.keys()):
            sweep = self.segments[segment] / total * tau
            if current <= angle < current + sweep: return segment
            current += sweep

    @staticmethod
    def do_query_tooltip(self, x, y, keyboard_mode, tooltip):
        segment = self.get_segment(x, y)
        if not segment: return False
        tooltip.set_text(self.get_tooltip(segment))
        return True

    @staticmethod
    def on_event_motion(e, x, y):
        self = e.get_widget()
        segment = self.get_segment(x, y)
        if segment == self.hovered: return
        self.hovered = segment
        self.animation.play()
        self.set_has_tooltip(False)
        GLib.timeout_add(50, self.set_has_tooltip, True)

    @staticmethod
    def on_event_leave(e):
        self = e.get_widget()
        if not self.hovered: return
        self.hovered = ""
        self.animation.play()

    @staticmethod
    def set_animation_progress(progress, self):
        self.hover_values = [start + (end - start) * progress for start, end in zip(getattr(self, "hover_values", [0.0] * len(self.segments)), [1.0 if i == self.hovered else 0.0 for i in self.segments])]
        self.queue_draw()

class DonutChartLegend(Gtk.Box):
    __gtype_name__ = "DonutChartLegend"
    segments = GObject.Property(type=object)
    tooltips = GObject.Property(type=object)
    colors = GObject.Property(type=object)
    def __init__(self, donut=None, **kwargs):
        super().__init__(css_name="donut-chart-legend", orientation=Gtk.Orientation.VERTICAL, **kwargs)
        for i in ("segments", "colors", "tooltips"): self.connect(f"notify::{i}", self.__class__.do)
        if donut:
            for i in ("tooltips", "colors", "segments"): donut.bind_property(i, self, i, DEFAULT_BIND)

    @staticmethod
    def do(self, p=None):
        while self.get_first_child(): self.get_first_child().unparent()
        if not self.segments: return
        if self.segments and sum(self.segments.values()) != 100: return
        for n, i in enumerate(self.segments.keys()):
            b = Gtk.Box(css_name="item")
            d = Gtk.DrawingArea(overflow=Gtk.Overflow.HIDDEN, css_name="color")
            d.color = DonutChart.get_color(self, n)
            d.set_draw_func(self.__class__.do_draw_func)
            b.append(d)
            b.append(Gtk.Label(label=DonutChart.get_tooltip(self, i)))
            self.append(b)

    @staticmethod
    def do_draw_func(self, cr, width, height):
        color = self.color
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.paint()

class TimedButton(Gtk.Button):
    __gtype_name__ = "TimedButton"
    rgba = GObject.Property(type=RGBA)
    seconds = GObject.Property(type=int, default=5, minimum=1)
    def __init__(self, callback=False, **kwargs):
        super().__init__(child=Gtk.DrawingArea(), css_classes=["flat", "circular", "timed"], **kwargs)
        if callback:
            self.do_reached = callback
        if not self.rgba: Adw.StyleManager.get_default().bind_property("accent-color-rgba", self, "rgba", DEFAULT_BIND)
        self.get_child().set_draw_func(self.__class__.do_draw_func)
        for i in ("sensitive", "visible"): self.connect(f"notify::{i}", self.__class__.changed)

    @staticmethod
    def changed(self, p):
        if self.should(): self.start()
        else: self.stop()

    @staticmethod
    def do_map(self):
        Gtk.Button.do_map(self)
        if self.should(): self.start()

    @staticmethod
    def do_unmap(self):
        Gtk.Button.do_unmap(self)
        self.stop()

    def start(self):
        self.stop()
        self.do_clicked()
        self.source = GLib.timeout_add(16, self.tick)

    def stop(self):
        s = getattr(self, "source", False)
        if s != False:
            try:
                GLib.source_remove(s)
                del self.source
            except: pass

    def do_clicked(self, *_):
        if not self.should(): return
        self._start = GLib.get_monotonic_time()
        self.get_child().queue_draw()

    def get_elapsed(self): return(GLib.get_monotonic_time() - self._start) / 1000000.0

    def should(self): return self and self.get_mapped() and self.get_visible() and self.get_sensitive()

    def tick(self):
        if not self.should():
            self.get_child().queue_draw()
            return False
        elapsed = self.get_elapsed()
        if elapsed >= self.seconds:
            if hasattr(self, "do_reached"): self.do_reached(self)
            self._start = GLib.get_monotonic_time()
        self.get_child().queue_draw()
        return True

    def do_draw_func(area, cr, width, height):
        self = area.get_parent()
        if not self or not self.should(): return
        cx = width / 2.0
        cy = height / 2.0
        radius = min(cx, cy)
        elapsed = self.get_elapsed()
        progress = min(max(elapsed / self.seconds, 0.0), 1.0)
        from math import pi
        start = -0.5 * pi + progress * 2.0 * pi
        cr.move_to(cx, cy)
        cr.arc(cx, cy, radius, start, -0.5 * pi + 2.0 * pi)
        cr.close_path()
        color = self.rgba
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.fill()

class MasonryColumn(Gtk.Box):
    __gtype_name__ = "MasonryColumn"
    def __init__(self): super().__init__(orientation=Gtk.Orientation.VERTICAL, css_name="column", valign=Gtk.Align.START)

    def reorder(box, desired):
        previous = None
        for widget in desired:
            if widget.get_parent() is not box:
                if widget.get_parent(): widget.unparent()
                box.insert_child_after(widget, previous)
            elif widget.get_prev_sibling() is not previous:
                box.reorder_child_after(widget, previous)
            previous = widget

class Masonry(Gtk.ScrolledWindow):
    __gtype_name__ = "Masonry"
    view = GObject.Property(type=Gtk.Box)
    children = GObject.Property(type=object)
    def __iter__(self):
        for i in self.children: yield i

    def __init__(self, **kwargs):
        view = Gtk.Box(css_name="view", homogeneous=True)
        for _ in range(4): view.append(MasonryColumn())
        super().__init__(css_name="masonry", focusable=True, hexpand=True, child=Gtk.Viewport(child=view, vscroll_policy=Gtk.ScrollablePolicy.NATURAL), hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_width=True, **kwargs)
        self.children = []
        self.view = view

    def check_layout(self):
        if getattr(self, "repositioning", False): return
        w = self.get_width()
        columns = 2 if 600 >= w else 3 if 1200 >= w else 4
        columns_widgets = [i for i in self.view]
        column_width = self.view.get_first_child().get_width()
        for n, i in enumerate(self.view): i.set_visible(columns >= n + 1)
        d = dict((str(i + 1), [0, columns_widgets[i], [], [it for it in columns_widgets[i]]]) for i in range(columns))
        for i in self.children:
            size = i.get_preferred_size()[1]
            width, height = size.width, size.height
            s = max(1, column_width) / max(1, width)
            width, height = int(width * s), int(height * s)
            column = "1"
            for it in d:
                if it == column: continue
                if d[column][0] > d[it][0]:
                    column = it
            d[column][0] += height
            d[column][2].append(i)
        do = False
        for v in d.values():
            if len(v[2]) != len(v[3]):
                do = True
                break
            for n, i in enumerate(v[3]):
                if not i in v[2] or n != v[2].index(i):
                    do = True
                    break
        if do:
            self.repositioning = True
            if Task.current() is Task.main(): self.do_layout(d)
            else: Task.invoke(self.do_layout, d)

    def do_layout(self, d):
        for v in d.values(): v[1].reorder(v[2])
        self.repositioning = False

    def do_dispose(self):
        if self.children:
            for i in self.children: Task.invoke(i.unparent)
        for i in ("children", "view",): Task.invoke(setattr, self, i, None)

    def get_first_child(self): return self.children[0] if self.children else None

    def get_last_child(self): return self.children[-1] if self.children else None

    def do_size_allocate(self, w, h, b):
        Gtk.ScrolledWindow.do_size_allocate(self, w, h, b)
        self.check_layout()

    def append(self, child):
        if not child in self.children: self.children.append(child)
        self.check_layout()

    def prepend(self, child):
        if not child in self.children: self.children.insert(0, child)
        self.check_layout()

    def add(self, child): self.append(child)

    def remove(self, child):
        if child in self.children: self.children.remove(child)
        if child.get_ancestor(Masonry) == self: child.unparent()
        self.check_layout()

    def reposition_after(self, child): pass

    def remove_all(self):
        for i in self.children: i.unparent()
        self.check_layout()
        self.children = []

    def get_direction_child(self, w, d):
        if d in (Gtk.DirectionType.UP, Gtk.DirectionType.DOWN): return w.get_prev_sibling() if d == Gtk.DirectionType.UP else w.get_next_sibling()
        else: return get_adjacent_items(self.children, w)[0 if d in (Gtk.DirectionType.TAB_BACKWARD, Gtk.DirectionType.LEFT) else 1]

    def do_focus(self, d):
        for i in self.view:
            if i.get_focus_child():
                w = self.get_direction_child(i.get_focus_child(), d)
                if w:
                    w.grab_focus()
                    return True
        return Gtk.ScrolledWindow.do_focus(self, d)

class GroupsPage(Gtk.ScrolledWindow):
    __gtype_name__ = "GroupsPage"

    def __init__(self): super().__init__(child=Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["groups"]))

    def add(self, i): self.get_child().get_child().append(i)

    def get_group(self, n=0):
        groups = [i for i in self.get_child().get_child()]
        return groups[n] if len(groups) > n else None

    def remove(self, i): self.get_child().get_child().remove(i)

class Prompt(Adw.AlertDialog):
    __gtype_name__ = "Prompt"

    def __init__(self, callback=None, responses=("cancel", "confirm"), appearance=Adw.ResponseAppearance.SUGGESTED, confirm_callback=True, **kwargs):
        self.callback = callback
        self.responses = responses
        self.confirm_callback = confirm_callback
        super().__init__(**kwargs)
        if callback: self.connect("response", self.__class__.callback if confirm_callback else callback)
        for i in self.responses: self.add_response(i, i.title())
        if appearance: self.set_response_appearance(self.responses[-1], appearance)
        Window.controller_init(self)

    def callback(d, r):
        if r == d.responses[-1]: d.callback(d)

class EntryPrompt(Prompt):
    __gtype_name__ = "EntryPrompt"

    def __init__(self, callback=None, multiline=False, appearance=Adw.ResponseAppearance.SUGGESTED, password=False, **kwargs):
        extra_child = Gtk.PasswordEntry(placeholder_text="Password", show_peek_icon=True) if password else Gtk.TextView() if multiline else Gtk.Entry()
        if not multiline: extra_child.connect("activate", self.__class__.activate)
        kwargs["extra_child"] = extra_child
        Prompt.__init__(self, callback=callback, appearance=appearance, **kwargs)
        self.add_css_class("entry-prompt")

    def get_text(self): return self.get_extra_child().get_buffer().get_property("text") if isinstance(self.get_extra_child(), Gtk.TextView) else self.get_extra_child().get_text()

    def set_text(self, v=""): return self.get_extra_child().get_buffer().set_property("text", v or "") if isinstance(self.get_extra_child(), Gtk.TextView) else self.get_extra_child().set_text(v or "")

    @staticmethod
    def on_key_pressed(e, kv, *_):
        if kv == Gdk.KEY_Escape:
            e.get_widget().get_ancestor(Adw.Dialog).close()
            return True

    @staticmethod
    def on_map(d):
        if isinstance(d.get_extra_child(), Gtk.Editable): d.get_extra_child().set_text("")
        else: d.get_extra_child().get_buffer().set_text("")
        if d.get_mapped(): d.get_extra_child().grab_focus()

    @staticmethod
    def activate(e):
        e.get_ancestor(Adw.Dialog).emit("response", "confirm")
        e.get_ancestor(Adw.Dialog).close()

class LabelRow(Adw.ActionRow):
    __gtype_name__ = "LabelRow"
    label = GObject.Property(type=str)
    def __init__(self, title="", subtitle="", label="", **kwargs):
        super().__init__(overflow=Gtk.Overflow.HIDDEN, title=title, subtitle=subtitle)
        if not "selectable" in kwargs:
            kwargs["selectable"] = True
        self.label = label
        self.add_css_class("label-row")
        label = Gtk.Label(**kwargs)
        self.add_suffix(label)
        self.bind_property("label", label, "label", DEFAULT_BIND)

class DateRow(Adw.ActionRow):
    __gtype_name__ = "DateRow"

    date = GObject.Property(type=GLib.DateTime)

    def __init__(self, **kwargs):
        super().__init__(overflow=Gtk.Overflow.HIDDEN, **kwargs)
        self.add_css_class("date")
        if not self.date:
            self.date = GLib.DateTime.new_now_local()
        calendar = Gtk.Calendar()
        self.bind_property("date", calendar, "date", DEFAULT_BIND)
        calendar.connect("notify::date", self.__class__.date_changed)
        box = Gtk.Box(css_classes=["linked"], halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        h = Gtk.Entry(tooltip_text="Hour")
        m = Gtk.Entry(tooltip_text="Minute")
        s = Gtk.Entry(tooltip_text="Seconds")
        for i in (h, m, s, Button(t=Gtk.MenuButton, icon_name="x-office-calendar", popover=Gtk.Popover(child=calendar), tooltip_text="Date")):
            if isinstance(i, Gtk.Entry):
                i.set_properties(max_length=2, input_purpose=Gtk.InputPurpose.DIGITS)
                self.bind_property("date", i, "text", SYNC_BIND, self.__class__.number_bind, self.__class__.number_bind)
            i.set_properties(valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
            box.append(i)
        self.add_suffix(Adw.Clamp(maximum_size=140, tightening_threshold=140, unit=Adw.LengthUnit.PX, child=box))

    @staticmethod
    def date_changed(calendar, prop):
        date = calendar.get_date()
        self = calendar.get_ancestor(DateRow)
        self.date = GLib.DateTime.new(self.date.get_timezone(), date.get_year(), date.get_month(), date.get_day_of_month(), self.date.get_hour(), self.date.get_minute(), self.date.get_second())

    @staticmethod
    def number_bind(b, v):
        l = b.get_property("target")
        if isinstance(v, str):
            if not v.isdigit(): return b.get_property("source").get_date()
            date = b.get_property("source").get_date()
            if not date: return
            tz = date.get_timezone()
            date = date.to_local()
            n = int(v)
            hour = date.get_hour()
            minute = date.get_minute()
            seconds = date.get_second()
            if l.get_tooltip_text() == "Hour":
                hour = max(0, min(n, 23))
            else:
                if l.get_tooltip_text() == "Minute":
                    minute = max(0, min(n, 59))
                if l.get_tooltip_text() == "Seconds":
                    seconds = max(0, min(n, 59))
            return GLib.DateTime.new(date.get_timezone(), date.get_year(), date.get_month(), date.get_day_of_month(), hour, minute, seconds).to_timezone(tz)
        elif isinstance(v, GLib.DateTime):
            v = v.to_local()
            if l.get_tooltip_text() == "Hour": return f"{v.get_hour():02d}"
            if l.get_tooltip_text() == "Minute": return f"{v.get_minute():02d}"
            if l.get_tooltip_text() == "Seconds": return f"{v.get_second():02d}"
        return ""

    def get_date(self): return self.date
    def set_date(self, d=None):
        self.date = d

class Tag(Gtk.Button):
    __gtype_name__ = "Tag"
    tag = GObject.Property(type=str)
    def __init__(self, tagrow=None, tag="", close=True, css_name="tag"):
        box = Gtk.Box()
        super().__init__(child=box, focusable=bool(tagrow.activate) if tagrow else False, halign=Gtk.Align.START, css_name=css_name)
        self.tag = tag
        l = Gtk.Label(use_markup=False, ellipsize=Pango.EllipsizeMode.END)
        self.bind_property("tag", l, "label", DEFAULT_BIND)
        l.bind_property("label", l, "tooltip-text", DEFAULT_BIND, self.__class__.tooltip_bind)
        box.append(l)
        if close: box.append(Button(css_name="remove", callback=self.__class__.remove, css_classes=("circular",), icon_name="window-close"))
        if tagrow: tagrow.wrapbox.append(self)

    @staticmethod
    def tooltip_bind(b, v): return v if len(v) > 20 else ""

    @staticmethod
    def do_clicked(b):
        self = b.get_ancestor(TagRow)
        if not self: return
        w = self.get_root().get_focus()
        if w == b:
            if self.activate: self.activate(b, 1)
        else: b.__class__.remove(w)

    @staticmethod
    def remove(b):
        if not b: return
        self = b.get_ancestor(TagRow)
        if not self: return
        tag = b.get_ancestor(Tag)
        self.set_property("tags", [i.tag for i in self.wrapbox if isinstance(i, Tag) and not i == tag])

class TagRow(Adw.PreferencesRow):
    __gtype_name__ = "TagRow"

    @GObject.Property(type=GObject.Strv)
    def tags(self): return getattr(self, "_tags", [])

    @tags.setter
    def tags(self, value):
        if self.allow_duplicates:
            self._tags = value
        else:
            self._tags = []
            for i in value:
                if not i in self._tags: self._tags.append(i)
        Task.invoke(self.sync_tags)

    allow_duplicates = GObject.Property(type=bool, default=False)
    new = GObject.Property(type=Gtk.Button)
    button = GObject.Property(type=Gtk.Button)
    wrapbox = GObject.Property(type=Adw.WrapBox)
    prompt = GObject.Property(type=Gtk.Widget)

    def __init__(self, tags=None, activate=None, title="Tag", tag="", **kwargs):
        self.activate = activate
        super().__init__(overflow=Gtk.Overflow.HIDDEN, focusable=False, activatable=False, title=title, **kwargs)
        self.add_css_class("tags")
        self.new = Button(callback=self.__class__.button_activate, css_classes=("circular",), tooltip_text="Add", icon_name="list-add")
        self.prompt = EntryPrompt(self.__class__.prompt_activate, heading=f"New {tag or title.rstrip('s')}")
        self.prompt.tagrow = self
        self.button, self.wrapbox = Button(overflow=Gtk.Overflow.HIDDEN, callback=self.__class__.button_activate, css_classes=("flat",), child=Gtk.Box(halign=Gtk.Align.CENTER)), Adw.WrapBox(child_spacing=4, line_spacing=4)
        self.wrapbox.tag = Gtk.Label(css_name="dragtag")
        self.button.get_child().append(Gtk.Image(icon_name="list-add-symbolic"))
        self.button.get_child().append(Gtk.Label(css_classes=("heading",), label=f"Add {title}"))
        d = Gtk.DragSource(actions=Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        for i in ("prepare", "drag-begin", "drag-end"): d.connect(i, getattr(self.__class__, i.replace("-", "_")))
        self.wrapbox.add_controller(d)
        d = Gtk.DropTarget(preload=True, actions=Gdk.DragAction.COPY | Gdk.DragAction.MOVE, formats=Gdk.ContentFormats.parse("gchararray"))
        for it in ("drop", "motion"): d.connect(it, getattr(self.__class__, it))
        self.wrapbox.add_controller(d)
        click = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("released", self.__class__.tagrow_clicked)
        self.add_controller(click)
        self.tags = tags

    def sync_tags(self):
        self.wrapbox.remove_all()
        self.set_child(self.wrapbox if self.tags else self.button)
        for i in self.tags: Tag(self, i)
        self.wrapbox.append(self.new)
        self.take_focus()

    def take_focus(self):
        if self.prompt.get_mapped(): self.prompt.get_extra_child().grab_focus()
        elif self.get_mapped(): self.grab_focus()

    @staticmethod
    def button_activate(b):
        self = b.get_ancestor(TagRow)
        if not self: return
        self.prompt.present(self.get_root())
        self.take_focus()

    @staticmethod
    def prepare(e, x, y):
        w = e.get_widget().pick(x, y, Gtk.PickFlags.DEFAULT)
        e.get_widget().tag = w = w.get_ancestor(Tag) if w else None
        if not w:
            e.set_actions(Gdk.DragAction.NONE)
            return None
        c = Gdk.ContentProvider.new_for_value(w.tag)
        e.set_actions(Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        e.set_content(c)
        return c

    @staticmethod
    def drag_begin(e, d):
        if e.get_widget().tag: Gtk.DragIcon.get_for_drag(d).set_child(Tag(tag=e.get_widget().tag.tag, close=False, css_name="dragtag"))

    @staticmethod
    def drag_end(e, d=None, de=None):
        if hasattr(e, "set_content"): e.set_content(None)
        e.get_widget().tag = None
        e.get_widget().get_ancestor(TagRow).remove_css_class("drag-move")
        e.get_widget().get_ancestor(TagRow).sync_tags()

    @staticmethod
    def drop(e, v, x, y):
        self = e.get_widget().get_ancestor(TagRow)
        if v in self.tags:
            self.tags = [i.tag for i in self.wrapbox if isinstance(i, Tag)]
        else: self.tags.append(v)
        self.__class__.drag_end(e)
        return True

    @staticmethod
    def motion(e, x, y):
        if e.get_widget().tag:
            h = e.get_widget().pick(x, y, Gtk.PickFlags.DEFAULT)
            h = h.get_ancestor(Tag) if h else None
            context_widget = e.get_widget().tag
            if h and h != context_widget:
                h = h.get_prev_sibling() or h
                if h == context_widget:
                    h = h.get_next_sibling() or h.get_prev_sibling()
                if h == e.get_widget().get_first_child():
                    context_widget.unparent()
                    e.get_widget().prepend(context_widget)
                else: e.get_widget().reorder_child_after(context_widget, h)
                e.get_widget().get_ancestor(TagRow).add_css_class("drag-move")
            return Gdk.DragAction.MOVE
        e.get_widget().get_ancestor(TagRow).remove_css_class("drag-move")
        return Gdk.DragAction.COPY

    @staticmethod
    def prompt_activate(d):
        d.tagrow.set_property("tags", d.tagrow.tags + [d.get_extra_child().get_text()])
        d.close()

    @staticmethod
    def tagrow_clicked(e, n, x, y):
        self = e.get_widget()
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if e.get_current_button() == 1:
            if isinstance(w, Adw.WrapBox): self.__class__.button_activate(w)
            return False
        if w and w.get_ancestor(Tag) and self.activate:
            self.activate(w.get_ancestor(Tag), e.get_current_button())
            return True
        return False

class MenuFontRow(Gtk.Box):
    __gtype_name__ = "MenuFontRow"
    def __init__(self, label, name):
        name = name.replace("-", "_")
        super().__init__(css_name="menufontrow", name=name)
        self.append(Gtk.Label(label=label))
        self.append(Gtk.FontDialogButton(dialog=Gtk.FontDialog(modal=False), hexpand=True, halign=Gtk.Align.END))

class MenuColorRow(Gtk.Box):
    __gtype_name__ = "MenuColorRow"
    def __init__(self, label, name):
        name = name.replace("-", "_")
        super().__init__(css_name="menucolorrow", name=name)
        self.append(Gtk.Label(label=label))
        self.append(Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(modal=False), hexpand=True, valign=Gtk.Align.CENTER, halign=Gtk.Align.END))

class MenuSpinRow(Gtk.Box):
    __gtype_name__ = "MenuSpinRow"
    def __init__(self, parent, label, name, value=0, step_increment=1):
        name = name.replace("-", "_")
        super().__init__(css_name="menuspinrow", name=name)
        self.append(Gtk.Label(label=label))
        prop = parent.__class__.find_property(name)
        self.append(Gtk.SpinButton(digits=len(str(prop.default_value)) - 1 if isinstance(prop.default_value, float) else 1, halign=Gtk.Align.END, hexpand=True, adjustment=Gtk.Adjustment.new(prop.default_value, prop.minimum, prop.maximum, step_increment, 0, 0)))
        parent.bind_property(prop.name, self.get_last_child(), "value", SYNC_BIND)

class TextRow(Adw.PreferencesRow):
    __gtype_name__ = "TextRow"
    text = GObject.Property(type=str)
    text_widget = GObject.Property(type=Gtk.TextView)
    def __init__(self, title=""):
        text = Gtk.TextView(accepts_tab=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, justification=Gtk.Justification.CENTER)
        overlay = Gtk.Overlay(child=text)
        super().__init__(overflow=Gtk.Overflow.HIDDEN, selectable=True, focusable=False, activatable=True, title=title, child=overlay)
        self.text_widget = text
        self.add_css_class("entry")
        text.set_css_classes([])
        placeholder = Gtk.Label(can_target=False, can_focus=False, css_name="placeholder", label=title, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        overlay.add_overlay(placeholder)
        text.connect("notify::has-focus", self.__class__.text_changed)
        self.bind_property("text", self.text_widget.get_buffer(), "text", SYNC_BIND)
        self.connect("notify::text", self.__class__.text_changed)
        self.add_css_class("text")

    def get_text(self): return self.get_property("text")
    def set_text(self, t=""): return self.set_property("text", t)
    def do_activate(self): self.text_widget.grab_focus()

    @staticmethod
    def text_changed(self, b):
        self = self.get_ancestor(TextRow)
        if not self: return
        (self.add_css_class if self.get_text() else self.remove_css_class)("has-text")
        (self.add_css_class if self.text_widget.get_property("has-focus") else self.remove_css_class)("focused")

class URIRow(Adw.PreferencesRow):
    __gtype_name__ = "URIRow"
    text = GObject.Property(type=str)
    text_widget = GObject.Property(type=Gtk.Widget)
    placeholder = GObject.Property(type=Gtk.Label)
    button = GObject.Property(type=Gtk.Button)
    multiple = GObject.Property(type=bool, default=True)
    def __init__(self, placeholder="", **kwargs):
        overlay = Gtk.Overlay()
        if kwargs.get("multiple", True):
            child = Gtk.ScrolledWindow(propagate_natural_height=True, child=overlay)
        else:
            child = overlay
        super().__init__(overflow=Gtk.Overflow.HIDDEN, selectable=True, focusable=False, activatable=True, child=child, **kwargs)
        self.add_css_class("entry")
        self.add_css_class("uri")
        if self.multiple:
            self.add_css_class("multiple")
            self.text_widget = Gtk.TextView(vscroll_policy=Gtk.ScrollablePolicy.NATURAL, wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        else:
            self.text_widget = Gtk.Text()
        self.text_widget.set_css_classes([])
        self.placeholder = Gtk.Label(can_target=False, can_focus=False, css_name="placeholder", label=placeholder or self.get_title() or "URI" + ("s" if self.multiple else ""), valign=Gtk.Align.START, halign=Gtk.Align.START)
        self.button = Button(focusable=False, can_focus=False, valign=Gtk.Align.START, halign=Gtk.Align.END, icon_name="folder-open", css_classes=["flat"], tooltip_text="Open File" + ("s" if self.multiple else ""), callback=self.__class__.show_prompt)
        self.text_widget.connect("notify::has-focus", self.__class__.text_changed)
        self.bind_property("text", self.text_widget.get_buffer(), "text", SYNC_BIND)
        self.connect("notify::text", self.__class__.text_changed)
        overlay.add_overlay(self.placeholder)
        overlay.add_overlay(self.button)
        overlay.set_child(self.text_widget)

    def get_text(self): return self.get_property("text")
    def set_text(self, t=""): return self.set_property("text", t)
    def do_activate(self): self.text_widget.grab_focus()

    @staticmethod
    def show_prompt(b):
        self = b.get_ancestor(URIRow)
        if not self: return
        self.text_widget.grab_focus()
        if self.multiple: Gtk.FileDialog().open_multiple(Window.current, None, self.__class__.do_open, self)
        else: Gtk.FileDialog().open(Window.current, None, self.__class__.do_open, self)

    @staticmethod
    def do_open(d, r, self):
        if self.multiple:
            try:
                l = str_to_list(self.get_text()) + [i.get_uri() for i in d.open_multiple_finish(r)]
            except:
                l = []
            ll = []
            for i in l:
                if not i in ll: ll.append(i)
            self.set_text(list_to_str(ll))
        else:
            try: self.set_text(d.open_finish(r).get_uri())
            except: self.set_text("")

    @staticmethod
    def text_changed(self, b):
        self = self.get_ancestor(URIRow)
        if not self: return
        (self.add_css_class if self.get_text() else self.remove_css_class)("has-text")
        (self.add_css_class if self.text_widget.get_property("has-focus") else self.remove_css_class)("focused")
        self.button.set_visible(not self.get_text() or not self.text_widget.get_property("has-focus"))

class ToggleGroupRow(Adw.ActionRow):
    __gtype_name__ = "ToggleGroupRow"
    togglegroup = GObject.Property(type=Adw.ToggleGroup)
    active = GObject.Property(type=str)
    def __init__(self, toggles=None, **kwargs):
        toggles = toggles or []
        super().__init__(overflow=Gtk.Overflow.HIDDEN, **kwargs)
        self.add_css_class("togglegroup")
        self.togglegroup = Adw.ToggleGroup(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.togglegroup.bind_property("active-name", self, "active", SYNC_BIND)
        for i in toggles: self.togglegroup.add(i)
        self.add_suffix(self.togglegroup)

class ScaleRow(Adw.ActionRow):
    __gtype_name__ = "ScaleRow"
    value = GObject.Property(type=float)
    def __init__(self, minimum=0, maximum=100, step=1, **kwargs):
        super().__init__(overflow=Gtk.Overflow.HIDDEN, **kwargs)
        self.add_css_class("scale")
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum, maximum, step)
        scale.set_properties(hexpand=True, valign=Gtk.Align.CENTER)
        self.add_suffix(scale)
        self.bind_property("value", scale.get_adjustment(), "value", SYNC_BIND)

class ColorRow(Adw.ActionRow):
    __gtype_name__ = "ColorRow"
    button = GObject.Property(type=Gtk.ColorDialogButton)
    rgba = GObject.Property(type=Gdk.RGBA)
    with_alpha = GObject.Property(type=bool, default=True)

    def __init__(self, **kwargs):
        super().__init__(overflow=Gtk.Overflow.HIDDEN, **kwargs)
        self.add_css_class("color")
        self.rgba = self.rgba or RGBA()
        dialog = Gtk.ColorDialog(modal=False)
        self.bind_property("with-alpha", dialog, "with-alpha", DEFAULT_BIND)
        self.button = Gtk.ColorDialogButton(dialog=dialog, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.set_rgba = self.button.set_rgba
        self.get_rgba = self.button.get_rgba
        self.bind_property("rgba", self.button, "rgba", SYNC_BIND)
        self.add_suffix(self.button)

class FontRow(Adw.ActionRow):
    __gtype_name__ = "FontRow"
    font_desc = GObject.Property(type=Pango.FontDescription)
    def __init__(self, title="", subtitle="", label="", font_desc="", button_kwargs=None, dialog_kwargs=None, **kwargs):
        if not dialog_kwargs:
            dialog_kwargs = {}
        if not button_kwargs:
            button_kwargs = {}
        super().__init__(overflow=Gtk.Overflow.HIDDEN, title=title, subtitle=subtitle, **kwargs)
        self.font_desc = font_desc or Pango.FontDescription.new()
        self.add_css_class("font-row")
        f = Gtk.FontDialogButton(valign=Gtk.Align.CENTER, dialog=Gtk.FontDialog(**dialog_kwargs), **button_kwargs)
        self.bind_property("font-desc", f, "font-desc", SYNC_BIND)
        self.add_suffix(f)

    def get_font_desc(self): return self.font_desc

    def set_font_desc(self, v): setattr(self, "font_desc", v)

class Picture(Gtk.Widget, Gtk.Scrollable):
    __gtype_name__ = "Picture"
    animation = GObject.Property(type=Adw.TimedAnimation)
    best_fit = GObject.Property(type=bool, default=True)
    hscroll_policy, vscroll_policy = GObject.Property(type=Gtk.ScrollablePolicy, default=Gtk.ScrollablePolicy.MINIMUM), GObject.Property(type=Gtk.ScrollablePolicy, default=Gtk.ScrollablePolicy.MINIMUM)
    hadjustment, vadjustment = GObject.Property(type=Gtk.Adjustment), GObject.Property(type=Gtk.Adjustment)

    shortcuts = {
        "Move Up": "<shift>w <shift>Up",
        "Move Down": "<shift>s <shift>Down",
        "Move Left": "a Left",
        "Move Right": "d Right",
        "Zoom In": "w Up",
        "Zoom Out": "s Down"
    }

    @GObject.Property(type=Gdk.Paintable)
    def paintable(self): return self._paintable

    @paintable.setter
    def paintable(self, paintable):
        self._paintable = paintable
        if paintable:
            paintable.connect("invalidate-contents", self.__class__.paintable_contents_changed, self)
            paintable.connect("invalidate-size", self.__class__.paintable_size_changed, self)
        self.zoom = 0

    @GObject.Property(type=float)
    def zoom(self): return self._zoom

    @zoom.setter
    def zoom(self, zoom):
        if not self.get_mapped() or self.get_width() == 0 or self.get_height() == 0: return
        zoom = min(max(zoom, self.zoom_level_best_fit()), 20)
        if zoom == self.zoom: return
        cur_x, cur_y = self.mouse if self.mouse != None else (self.get_width() / 2, self.get_height() / 2)
        img_x, img_y = self.mouse_target if self.mouse_target else self.widget_to_img_coord((self.get_width() / 2, self.get_height() / 2))
        self.best_fit = round(zoom, 2) == round(self.zoom_level_best_fit(), 2)
        self._zoom = zoom
        self.configure_adjustments()
        self.hadjustment.set_value(max(0, (img_x * zoom) - cur_x))
        self.vadjustment.set_value(max(0, (img_y * zoom) - cur_y))
        self.queue_draw()

    def __init__(self, **kwargs):
        self._paintable = self.mouse = self.mouse_target = None
        self._zoom = 0
        motion = Gtk.EventControllerMotion(propagation_limit=Gtk.PropagationLimit.NONE, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        for i in ("enter", "motion"): motion.connect(i, self.__class__.update_mouse)
        motion.connect("leave", self.__class__.reset_mouse)
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.set_properties(propagation_limit=Gtk.PropagationLimit.NONE, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self.__class__.do_scroll)
        drag = Gtk.GestureDrag(button=0)
        drag.connect("drag-begin", self.__class__.drag_begin)
        drag.connect("drag-update", self.__class__.drag_update)
        drag.connect("drag-end", self.__class__.drag_end)
        e = Gtk.EventControllerKey()
        e.connect("key-pressed", self.__class__.on_key_pressed)
        super().__init__(css_name=self.__gtype_name__.lower(), focusable=True, overflow=Gtk.Overflow.HIDDEN, hexpand=True, vexpand=True, **kwargs)
        for i in (e, motion, scroll, drag): self.add_controller(i)
        self.animation = Adw.TimedAnimation(widget=self, duration=200, target=Adw.PropertyAnimationTarget.new(self, "zoom"))
        self.animation.connect("done", self.__class__.reset_mouse_target)
        for i in ("vadjustment", "hadjustment"): self.connect(f"notify::{i}", self.__class__.adjustment_changed)

    @staticmethod
    def on_key_pressed(e, v, kc, s):
        self = e.get_widget()
        if not Gdk.ModifierType.SHIFT_MASK in s and v in (Gdk.KEY_w, Gdk.KEY_s, Gdk.KEY_Up, Gdk.KEY_Down):
            self.do_zoom(1 if v in (Gdk.KEY_s, Gdk.KEY_Down) else -1, f=1.3)
            return True
        if self.get_parent(): return handle_scroll(self.get_parent(), v, s, page=False)

    def do_size_allocate(self, w, h, b):
        if self.best_fit:
            self.animation.pause()
            self._zoom = self.zoom_level_best_fit()
        self.configure_adjustments()

    def do_snapshot(self, snapshot):
        if not self.paintable: return
        if hasattr(self.paintable, "snapshot_symbolic"): self.paintable.snapshot_symbolic(snapshot, self.get_width(), self.get_height(), tuple())
        else: snapshot.translate(Graphene.Point.init(Graphene.Point(), -(self.hadjustment.get_value() - (self.hadjustment.get_upper() - self.image_displayed_width()) / 2), -(self.vadjustment.get_value() - (self.vadjustment.get_upper() - self.image_displayed_height()) / 2)))
        snapshot.translate(Graphene.Point.init(Graphene.Point(), self.image_rendering_x(), self.image_rendering_y()))
        if not hasattr(self.paintable, "snapshot_symbolic"):
            rr = Gsk.RoundedRect()
            Gsk.RoundedRect.init_from_rect(rr, Graphene.Rect.init(Graphene.Rect(), 0, 0, self.image_displayed_width(), self.image_displayed_height())
, 13)
            snapshot.push_rounded_clip(rr)
            self.paintable.snapshot(snapshot, self.image_displayed_width(), self.image_displayed_height())
            snapshot.pop()

    def configure_adjustments(self):
        if not self.paintable: return
        w, h = self.image_displayed_width(), self.image_displayed_height()
        for a, v, d in ((self.hadjustment, w, self.get_width()), (self.vadjustment, h, self.get_height())):
            if not a: continue
            a.configure(max(0, min(a.get_value(), max(v - d, 0))), 0, v, d * 0.1, d * 0.9, min(d, v),)

    def image_rendering_x(self): return round(max((self.get_width() - self.image_displayed_width()) / 2, 0))

    def image_rendering_y(self): return round(max((self.get_height() - self.image_displayed_height()) / 2, 0))

    def image_displayed_height(self): return (self.paintable.get_intrinsic_height() * round(self.zoom, 6)) - 20

    def image_displayed_width(self): return (self.paintable.get_intrinsic_width() * round(self.zoom, 6)) - 20

    def zoom_level_best_fit(self):
        if not self.paintable or isinstance(self.paintable, Adw.SpinnerPaintable): return 1
        image_width, image_height = self.paintable.get_intrinsic_width(), self.paintable.get_intrinsic_height()
        if image_width == 0 or image_height == 0 or not self.get_mapped() or self.get_width() == 0 or self.get_height() == 0: return 1
        texture_aspect_ratio = image_width / image_height
        widget_aspect_ratio = self.get_width() / self.get_height()
        max_zoom_factor = 1
        return min(self.get_width() / image_width, max_zoom_factor) if texture_aspect_ratio > widget_aspect_ratio else min(self.get_height() / image_height, max_zoom_factor)

    def widget_to_img_coord(self, cur_coords):
        zoom = round(self.zoom, 6)
        cur_x, cur_y = cur_coords if cur_coords else (self.get_width() / 2, self.get_height() / 2)
        return ((cur_x - self.image_rendering_x() + self.hadjustment.get_value()) / zoom, (cur_y - self.image_rendering_y() + self.vadjustment.get_value()) / zoom)

    @staticmethod
    def drag_begin(e, x, y):
        self = e.get_widget()
        if not self.paintable or not self.get_parent(): return e.set_state(Gtk.EventSequenceState.DENIED)
        self.last_drag_value = (0, 0)
        if not self.hadjustment or not self.vadjustment or (0 >= min(self.hadjustment.get_value(), max(self.image_displayed_width() - self.get_width(), 0)) and 0 >= min(self.vadjustment.get_value(), max(self.image_displayed_height() - self.get_height(), 0))): e.set_state(Gtk.EventSequenceState.DENIED)
        else:
            self.get_parent().set_kinetic_scrolling(False)
            self.get_parent().set_kinetic_scrolling(True)
            self.set_cursor(Gdk.Cursor.new_from_name("grabbing", None))

    @staticmethod
    def drag_update(e, x, y):
        self = e.get_widget()
        if not self.paintable: return
        if self.last_drag_value:
            px, py = self.last_drag_value
            self.hadjustment.set_value(self.hadjustment.get_value() - x + px);
            self.vadjustment.set_value(self.vadjustment.get_value() - y + py);
        self.last_drag_value = x, y

    @staticmethod
    def drag_end(e, x, y):
        self = e.get_widget()
        self.set_cursor(None)
        self.last_drag_value = None

    @staticmethod
    def do_scroll(e, x, y):
        self = e.get_widget()
        if not self.paintable or e.get_current_event_state() in (Gdk.ModifierType.CONTROL_MASK, Gdk.ModifierType.SHIFT_MASK): return
        s = e.get_current_event_device().get_source()
        if s == Gdk.InputSource.TOUCHPAD: return
        if y == 0: return True
        self.do_zoom(y, f=1.3 if e.get_unit() == Gdk.ScrollUnit.WHEEL else False)
        return True

    def do_zoom(self, y, f=False):
        from math import log, exp
        f = f or 1.005
        zoom = self.animation.get_value_to() * exp(-y * log(f))
        zoom = min(max(zoom, self.zoom_level_best_fit()), 20)
        if self.mouse_target == None:
            self.mouse_target = self.widget_to_img_coord(self.mouse)
        self.animation.set_value_from(self.zoom)
        self.animation.set_value_to(zoom)
        self.animation.play()

    @staticmethod
    def paintable_contents_changed(_, self): self.queue_draw()

    @staticmethod
    def paintable_size_changed(_, self): self.set_property("zoom", 0)

    @staticmethod
    def reset_mouse(e): setattr(e.get_widget(), "mouse", None)

    @staticmethod
    def reset_mouse_target(e): setattr(e.get_widget(), "mouse_target", None)

    @staticmethod
    def update_mouse(e, x, y): setattr(e.get_widget(), "mouse", (x, y))

    @staticmethod
    def adjustment_changed(self, b):
        self.get_property(b.name).connect("value-changed", self.__class__.adjustment_value_changed, self)
        self.configure_adjustments()

    @staticmethod
    def adjustment_value_changed(_, self): self.queue_draw()

class MediaStream(GObject.Object, Gdk.Paintable):
    __gtype_name__ = "UtilsMediaStream"

    uri = GObject.Property(type=str)
    paintable = GObject.Property(type=Gdk.Paintable)
    pipeline = GObject.Property(type=GObject.Object)
    event = GObject.Property(type=object)
    lock_until_done = GObject.Property(type=bool, default=True)
    loop = GObject.Property(type=bool, default=True)
    playing = GObject.Property(type=bool, default=True)
    has_video = GObject.Property(type=bool, default=False)
    has_audio = GObject.Property(type=bool, default=False)
    can_change_speed = GObject.Property(type=bool, default=False)
    volume = GObject.Property(type=float, minimum=0.0, default=0.0, maximum=1.0)
    speed = GObject.Property(type=float, minimum=-10.0, default=1.0, maximum=10.0)

    PAINTABLE_SINK = False

    @GObject.Property(type=GObject.GType.from_name("gint64"), flags=GObject.ParamFlags.READWRITE)
    def duration(self): return self._duration if self._duration > 1 else 0

    @duration.setter
    def duration(self, n):
        self._duration = n

    @GObject.Property(type=GObject.GType.from_name("gint64"), flags=GObject.ParamFlags.READWRITE)
    def position(self): return (self.paintable.get_property("timestamp") * 1e3) if isinstance(self.paintable, Gtk.MediaFile) else self._position

    @position.setter
    def position(self, timestamp):
        if isinstance(self.paintable, Gtk.MediaFile): self.paintable.seek(timestamp / 1e3 if timestamp else timestamp)
        elif timestamp >= 0: self.seek(timestamp, flags=Gst.SeekFlags.ACCURATE | Gst.SeekFlags.FLUSH)

    def __init__(self, data=None, stream=True, **kwargs):
        do_gst()
        self._position = self._duration = 0
        super().__init__(**kwargs)
        self.connect("notify::playing", self.__class__.playing_changed)
        self.connect("notify::volume", self.__class__.volume_changed)
        self.connect("notify::speed", self.__class__.speed_changed)
        playing = self.playing
        self.playing = False
        self.do_dispose()
        if not (data or self.uri): return
        s, s2, org = "", "", False
        if not data and self.uri:
            if self.uri.startswith("file://"):
                s = f"filesrc location=\"{uri_decode(self.uri)}\""
            else:
                s = f"urisourcebin uri=\"{self.uri}\""
            s2 = "! "
        else:
            org = data
            if isinstance(data, bytes):
                if stream and Gst.ElementFactory.find("giostreamsrc"):
                    data = Gio.MemoryInputStream.new_from_data(data)
                else:
                    s = "appsrc"
            if isinstance(data, GLib.Bytes):
                if stream and Gst.ElementFactory.find("giostreamsrc"):
                    data = Gio.MemoryInputStream.new_from_bytes(data)
                else:
                    data = Online.get_data(data)
                    s = "appsrc"
            if isinstance(data, Gio.InputStream):
                s = "giostreamsrc"
        try:
            self.pipeline = Gst.parse_launch(f"{s} name=Source {s2}decodebin3 async_handling=false message_forward=false name=Decode ! fakesink sync=false qos=false name=Fake")
            self.pipeline.set_bus(None)
            self.pipeline.set_properties(async_handling=False, message_forward=False)
            self.pipeline.get_by_name("Decode").connect("select-stream", self.__class__.select_stream, self)
            if s == "appsrc": MediaStream.appsrc(self.pipeline.get_by_name("Source"), data)
            elif s == "giostreamsrc": self.pipeline.get_by_name("Source").set_property("stream", data)
            self.pipeline.get_by_name("Source").link(self.pipeline.get_by_name("Decode"))
            self.pipeline.set_state(Gst.State.PLAYING)
            self.pipeline.get_state(10 * Gst.SECOND)
            self.duration = self.pipeline.query_duration(Gst.Format.TIME)[-1]
            if self.lock_until_done:
                self.event = Event()
            self.pipeline.set_state(Gst.State.PAUSED)
            self.pipeline.get_by_name("Fake").set_state(Gst.State.NULL)
            self.pipeline.remove(self.pipeline.get_by_name("Fake"))
        except Exception as e: print("MediaStream:", e)
        if self.duration:
            if not self.__class__.PAINTABLE_SINK and self.has_video:
                self.playing = playing
                self.media_file(org)
                return
            else:
                if self.has_video: self.add_video()
                if self.has_audio: self.add_audio()
                self.start_pipeline()
                self.playing = playing
                self.can_change_speed = True
        elif self.has_video:
            if s == "appsrc": self.pipeline.get_by_name("Source").set_state(Gst.State.READY)
            if self.__class__.PAINTABLE_SINK:
                self.add_video()
                self.start_pipeline()
        elif org:
            self.do_dispose()
            self.paintable = Gdk.Texture.new_from_bytes(org if isinstance(org, GLib.Bytes) else GLib.Bytes.new(org))
            self.has_video = True
            return
        if self.has_video and self.is_empty() and self.event and not self.event.is_set() and self.__class__.PAINTABLE_SINK: self.event.wait(timeout=10)
        if not self.duration and self.is_empty():
            if self.event and not self.event.is_set() and Log.mediastream: print("MediaStream timed out")
            try:
                paintable = None
                if isinstance(org, bytes):
                    org = GLib.Bytes.new(org)
                if isinstance(org, GLib.Bytes):
                    paintable = Gdk.Texture.new_from_bytes(org)
                    self.has_video = True
                else:
                    self.playing = playing
                    self.media_file(org)
                    return
            except:
                self.playing = playing
                self.media_file(org)
                return
            self.do_dispose()
            self.paintable = paintable
            self.emit("invalidate-size")
            self.emit("invalidate-contents")
        elif not self.duration and self.has_video: Task.invoke(self.finish, self.get_current_image())

    def is_empty(self):
        s = str(self.get_current_image())
        return "Empty" in s or "None" in s

    def media_file(self, org=None):
        self.do_dispose()
        if not (org or self.uri): return
        if self.lock_until_done:
            self.event = Event()
        if isinstance(org, bytes):
            org = Gio.MemoryInputStream.new_from_data(org)
        if isinstance(org, GLib.Bytes):
            org = Gio.MemoryInputStream.new_from_bytes(org)
        self.do_media_file(Gtk.MediaFile.new_for_input_stream(org) if isinstance(org, Gio.InputStream) else Gtk.MediaFile.new_for_file(Gio.File.new_for_uri(self.uri)))

    def do_media_file(self, paintable):
        for i in ("has-video", "has-audio"): paintable.bind_property(i, self, i, DEFAULT_BIND)
        paintable.bind_property("duration", self, "duration", DEFAULT_BIND, self.__class__.duration_bind)
        self.bind_property("volume", paintable, "volume", SYNC_BIND)
        self.paintable = paintable
        self.connects[self.paintable] = [self.paintable.connect("notify::timestamp", self.__class__.timestamp_changed, self), self.paintable.connect("invalidate-contents", self.__class__.paintable_invalidate_contents, self), self.paintable.connect("invalidate-size", self.__class__.paintable_invalidate_size, self)]
        paintable.pause()
        paintable.play()
        if self.event: self.event.wait(timeout=10)
        self.bind_property("playing", paintable, "playing", SYNC_BIND)

    @staticmethod
    def duration_bind(b, v): return v * 1e3

    @staticmethod
    def timestamp_changed(p, n, self):
        self.notify("position")
        self.try_loop()

    def try_loop(self):
        if self.loop and self.playing and self.duration > 1 and self.position > 1 and 0.1 > (self.duration - self.position) / 1e9:
            self.position = 0

    def finish(self, paintable):
        self.do_dispose()
        self.paintable = paintable
        self.emit("invalidate-size")
        self.emit("invalidate-contents")

    @staticmethod
    def destroy_pipeline(pipeline):
        if not pipeline: return
        try:
            global Gst
            from gi.repository import Gst
        except:
            Gst = False
        if not Gst: return
        if hasattr(pipeline, "set_state"): pipeline.set_state(Gst.State.NULL)
        if hasattr(pipeline, "video") and isinstance(pipeline.video, Gst.Element):
            del pipeline.video
        if hasattr(pipeline, "children"):
            for i in [i for i in pipeline.children]:
                if i.name == "Source":
                    if getattr(i.props, "stream", False):
                        stream = i.get_property("stream")
                        i.set_property("stream", None)
                        if stream: stream.close()
                    if hasattr(i, "current_offset"): del i.current_offset
                    if hasattr(i, "data"): del i.data
                i.set_state(Gst.State.NULL)
                pipeline.remove(i)
                i.do_dispose()
                i = None
        if hasattr(pipeline, "do_dispose"): pipeline.do_dispose()

    @staticmethod
    def appsrc(appsrc=None, data=None):
        if not appsrc:
            appsrc = Gst.ElementFactory.make("appsrc", "Source")
        appsrc.data = data if isinstance(data, bytes) else Online.get_data(data)
        appsrc.set_properties(stream_type=1, format=Gst.Format.BYTES, size=len(appsrc.data), block=True)
        appsrc.connect("need-data", MediaStream.on_need_data)
        appsrc.connect("seek-data", MediaStream.on_seek_data)
        return appsrc

    @staticmethod
    def on_need_data(appsrc, length):
        if not hasattr(appsrc, "data"): return
        offset = getattr(appsrc, "current_offset", 0)
        if offset >= appsrc.get_property("size"): return appsrc.emit("end-of-stream")
        chunk_size = min(length, appsrc.get_property("size") - offset)
        appsrc.current_offset = offset + chunk_size
        buffer = Gst.Buffer.new_wrapped(appsrc.data[offset : offset + chunk_size])
        appsrc.emit("push-buffer", buffer)

    @staticmethod
    def on_seek_data(appsrc, offset):
        if hasattr(appsrc, "data") and 0 <= offset <= appsrc.get_property("size"):
            appsrc.current_offset = offset
            return True
        return False

    def start_pipeline(self):
        self.pipeline.set_state(Gst.State.PAUSED)
        self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.ACCURATE | Gst.SeekFlags.FLUSH, 0)

    def add_audio(self):
        for t, n in (("audioconvert", "Audio"), ("volume", "Volume"), ("pulsesink", "AudioSink")): self.pipeline.add(Gst.ElementFactory.make(t, n))
        self.pipeline.get_by_name("Volume").set_property("volume", self.volume)
        self.pipeline.get_by_name("Volume").link(self.pipeline.get_by_name("AudioSink"))
        self.pipeline.get_by_name("Audio").link(self.pipeline.get_by_name("Volume"))
        self.pipeline.get_by_name("Decode").link_pads("audio_0", self.pipeline.get_by_name("Audio"), "sink")
        if not self.has_video: self.pipeline.get_by_name("Audio").get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, self.__class__.update_position, self)

    def add_video(self):
        self.set_paintable()
        self.pipeline.video = self.pipeline.get_by_name("Paintable")
        if 1 > self.duration:
            self.pipeline.set_clock(None)
            d, p = self.pipeline.get_by_name("Decode").children[0].get_factory(), self.pipeline.get_by_name("Decode").children[1].children[0].get_factory()
            self.pipeline.get_by_name("Decode").set_state(Gst.State.NULL)
            self.pipeline.remove(self.pipeline.get_by_name("Decode"))
            self.pipeline.add(p.create("Decode"))
            self.pipeline.add(d.create("Parsed"))
            self.pipeline.get_by_name("Source").link(self.pipeline.get_by_name("Decode"))
            self.pipeline.get_by_name("Decode").link(self.pipeline.get_by_name("Parsed"))
            self.pipeline.get_by_name("Parsed").link(self.pipeline.video)
        else: self.pipeline.get_by_name("Decode").link_pads("video_0", self.pipeline.video, "sink")

    def set_paintable(self, t=False):
        if Task.current() is Task.main():
            paintable = Gst.ElementFactory.make("gtk4paintablesink", "Paintable")
            if paintable.find_property("reconfigure-on-window-resize"): paintable.set_property("reconfigure-on-window-resize", 0)
            paintable.set_property("qos", False)
            if 1 > self.duration:
                paintable.set_property("async", False)
                paintable.set_property("sync", False)
            self.pipeline.add(paintable)
            self.paintable = paintable.get_property("paintable")
            self.connects[self.paintable] = [self.paintable.connect("invalidate-contents", self.__class__.paintable_invalidate_contents, self), self.paintable.connect("invalidate-size", self.__class__.paintable_invalidate_size, self), self.paintable.connect("notify::position", self.__class__.do_update_position, self)]
            if t: Task.invoke(t.set)
            return False
        t = Event()
        Task.invoke(self.set_paintable, t)
        t.wait(timeout=120)

    def do_dispose(self):
        self.paintable = None
        if self.event and not self.event.is_set(): self.event.set()
        if hasattr(self, "connects"):
            for i, v in self.connects.items():
                for it in v: i.disconnect(it)
        self.connects = {}
        if self.pipeline: self.__class__.destroy_pipeline(self.pipeline)
        self.event = self.task = self.pipeline = None
        GObject.Object.do_dispose(self)

    def seek(self, timestamp=0, flags=None):
        if not self.pipeline: return
        if timestamp != -1:
            timestamp = min(self.duration, max(0, timestamp))
            self._position = timestamp
            self.notify("position")
        if flags == None:
            flags = Gst.SeekFlags.FLUSH
        self.pipeline.seek(self.speed, Gst.Format.TIME, flags, Gst.SeekType.NONE if timestamp == -1 else Gst.SeekType.SET, timestamp, Gst.SeekType.NONE, -1)

    def play(self): setattr(self, "playing", True)
    def pause(self): setattr(self, "playing", False)
    def playpause(self): setattr(self, "playing", not self.playing)
    def set_volume(self, v): setattr(self, "volume", v)
    def backward(self):
        self.position = self.position - (2 * 1e9)
    def forward(self):
        self.position = self.position + (2 * 1e9)
    def page_backward(self):
        self.position = self.position - (10 * 1e9)
    def page_forward(self):
        self.position = self.position + (10 * 1e9)
    def volumeup(self): setattr(self, "volume", min(1.0, self.volume + 0.1))
    def volumedown(self): setattr(self, "volume", max(0.0, self.volume - 0.1))

    @staticmethod
    def select_stream(d, c, s, self):
        if s.get_stream_type() == Gst.StreamType.VIDEO and not self.has_video:
            self.has_video = True
            return 1
        if s.get_stream_type() == Gst.StreamType.AUDIO and not self.has_audio:
            self.has_audio = True
            return 1
        return 0

    @staticmethod
    def speed_changed(self, p):
        if not self.pipeline: return
        if self.speed == 0.0: return
        self.seek(-1, flags=Gst.SeekFlags.INSTANT_RATE_CHANGE)

    @staticmethod
    def volume_changed(self, p=None):
        if self.has_audio and self.pipeline and self.pipeline.get_by_name("Volume"): self.pipeline.get_by_name("Volume").set_property("volume", self.volume)

    @staticmethod
    def playing_changed(self, p):
        if not self.pipeline: return
        self.pipeline.set_state(Gst.State.PLAYING if self.playing else Gst.State.PAUSED)

    def do_update_position(*args):
        if len(args) == 1:
            self = args[0]
        else:
            self = args[-1]
            self._position = args[0].get_timestamp()
        self.notify("position")
        self.try_loop()

    @staticmethod
    def update_position(pad, info, self):
        try:
            p, op = info.get_buffer().pts, self._position
            self._position = p
            if p != op: Task.invoke(self.do_update_position)
        except Exception as e: print(e)
        return Gst.PadProbeReturn.OK

    @staticmethod
    def paintable_invalidate_contents(paintable, self):
        if self.is_empty(): return
        if self.event and not self.event.is_set():
            self.event.set()
            self.event = None
        if self.pipeline and hasattr(self.pipeline, "video"):
            self._position = self.pipeline.video.query_position(Gst.Format.TIME)[-1]
            self.do_update_position()
        self.emit("invalidate-contents")

    @staticmethod
    def paintable_invalidate_size(paintable, self):
        if self.is_empty(): return
        if self.event and not self.event.is_set():
            self.event.set()
            self.event = None
        self.emit("invalidate-size")

    def do_get_intrinsic_width(self): return self.paintable.get_intrinsic_width() if self.paintable else 0

    def do_get_intrinsic_height(self): return self.paintable.get_intrinsic_height() if self.paintable else 0

    def do_get_intrinsic_aspect_ratio(self): return self.paintable.get_intrinsic_aspect_ratio() if self.paintable else 0

    def do_get_flags(self): return 0

    def do_get_current_image(self): return self.paintable.get_current_image() if self.paintable else None

    def do_snapshot(self, s, w, h):
        if self.paintable: self.paintable.snapshot(s, w, h)

class LoopButton(Gtk.ToggleButton):
    __gtype_name__ = "LoopButton"

    def __init__(self, media=None, **kwargs):
        super().__init__(tooltip_text="Loop", icon_name="media-playlist-repeat", **kwargs)
        self.add_css_class(self.__gtype_name__.lower())
        self.add_css_class("flat")
        self.add_css_class("circular")
        self.bindings = []
        self.bind_media(media)

    def bind_media(self, media):
        for i in self.bindings: i.unbind()
        if media:
            self.bindings = [media.bind_property("loop", self, "active", SYNC_BIND)]

class VolumeButton(Gtk.MenuButton):
    __gtype_name__ = "VolumeButton"
    value = GObject.Property(type=float, minimum=0.0, default=0.5, maximum=1.0)
    def __init__(self, media=None, **kwargs):
        scale = Gtk.Scale(draw_value=True, digits=2, height_request=200, inverted=True, orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER)
        for i in (0.25, 0.5, 0.75): scale.add_mark(i, Gtk.PositionType.TOP)
        scale.set_range(0.0, 1.0)
        super().__init__(tooltip_text="Volume", popover=Gtk.Popover(child=scale), **kwargs)
        self.add_css_class(self.__gtype_name__.lower())
        self.add_css_class("flat")
        self.add_css_class("circular")
        self.bindings = []
        self.bind_media(media)

    def bind_media(self, media):
        for i in self.bindings: i.unbind()
        if media:
            self.bindings = [media.bind_property("volume", self, "value", SYNC_BIND), self.bind_property("value", self.get_popover().get_child().get_adjustment(), "value", SYNC_BIND),  self.get_popover().get_child().get_adjustment().bind_property("value", self, "icon-name", DEFAULT_BIND, self.__class__.icon_bind)]

    @staticmethod
    def icon_bind(b, v): return "audio-volume-muted-symbolic" if v == 0 else "audio-volume-high-symbolic" if v >= 0.6 else "audio-volume-medium-symbolic"

class SpeedButton(Gtk.MenuButton):
    __gtype_name__ = "SpeedButton"

    def __init__(self, media=None, **kwargs):
        scale = Gtk.Scale(draw_value=True, digits=2, height_request=200, inverted=True, orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER)
        for i in (0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75): scale.add_mark(i, Gtk.PositionType.TOP)
        scale.set_range(-0.1, 2.0)
        super().__init__(tooltip_text="Speed", popover=Gtk.Popover(child=scale), **kwargs)
        self.add_css_class(self.__gtype_name__.lower())
        self.add_css_class("flat")
        self.add_css_class("circular")
        self.bindings = []
        self.bind_media(media)

    def bind_media(self, media):
        for i in self.bindings: i.unbind()
        if media:
            self.bindings = [media.bind_property("speed", self.get_popover().get_child().get_adjustment(), "value", SYNC_BIND), self.get_popover().get_child().get_adjustment().bind_property("value", self, "icon-name", DEFAULT_BIND, self.__class__.icon_bind)]
            if hasattr(media, "can_change_speed"): self.bindings.append(media.bind_property("can-change-speed", self, "sensitive", DEFAULT_BIND))

    @staticmethod
    def icon_bind(b, v):
        return "speedometer-medium-symbolic" if v == 1 else "speedometer-low-symbolic" if 1 > v else "speedometer-high-symbolic"

class MediaSeekbar(Gtk.Scale):
    __gtype_name__ = "MediaSeekbar"

    def __init__(self, media=None, **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, valign=Gtk.Align.CENTER, **kwargs)
        self.add_css_class(self.__gtype_name__.lower())
        self.connect("change-value", self.__class__.change_value)
        self.bindings = []
        self.bind_media(media)
        self.get_adjustment().connect("notify::value", self.__class__.tooltip_bind, self)

    def bind_media(self, media):
        for i in self.bindings: i.unbind()
        if media:
            self.bindings = [media.bind_property("duration", self.get_adjustment(), "upper", DEFAULT_BIND), media.bind_property("position", self.get_adjustment(), "value", DEFAULT_BIND)]

    @staticmethod
    def change_value(self, t, v):
        media = self.bindings[0].get_property("source")
        if t == Gtk.ScrollType.STEP_BACKWARD: media.backward()
        elif t == Gtk.ScrollType.STEP_FORWARD: media.forward()
        elif t == Gtk.ScrollType.PAGE_BACKWARD: media.page_backward()
        elif t == Gtk.ScrollType.PAGE_FORWARD: media.page_forward()
        else:
            media.position = v
        return True

    @staticmethod
    def tooltip_bind(a, p, self):
        if not self.get_mapped(): return
        v = f"{format_time(int(a.get_value()))} / {format_time(int(a.get_upper()))}"
        if self.get_tooltip_text() != v: self.set_tooltip_text(v)

class PlaybackButton(Gtk.Button):
    __gtype_name__ = "PlaybackButton"

    def __init__(self, media=None, **kwargs):
        if not "tooltip_text" in kwargs:
            kwargs["tooltip_text"] = "Pause" if media and media.playing else "Play"
        super().__init__(**kwargs)
        self.add_css_class(self.__gtype_name__.lower())
        self.add_css_class("flat")
        self.add_css_class("circular")
        self.connect("clicked", self.__class__.callback)
        self.bindings = []
        self.bind_media(media)

    def bind_media(self, media):
        for i in self.bindings: i.unbind()
        if media:
            self.set_tooltip_text(self.__class__.playing_bind2(None, media.playing))
            self.bindings = [self.bind_property("tooltip-text", media, "playing", SYNC_BIND, self.__class__.playing_bind, self.__class__.playing_bind2), self.bind_property("tooltip-text", self, "icon-name", DEFAULT_BIND, self.__class__.icon_bind)]

    @staticmethod
    def callback(b): b.set_tooltip_text("Pause" if b.get_tooltip_text() == "Play" else "Play")

    @staticmethod
    def icon_bind(b, v): return "media-playback-pause-symbolic" if v == "Pause" else "media-playback-start-symbolic"

    @staticmethod
    def playing_bind(b, v): return v == "Pause"

    @staticmethod
    def playing_bind2(b, v): return "Pause" if v else "Play"

class MediaControls(Gtk.Box):
    __gtype_name__ = "MediaControls"
    loop_button = GObject.Property(type=LoopButton)
    volume_button = GObject.Property(type=VolumeButton)
    playback_button = GObject.Property(type=PlaybackButton)
    speed_button = GObject.Property(type=SpeedButton)
    seekbar = GObject.Property(type=MediaSeekbar)
    media_stream = GObject.Property(type=Gdk.Paintable)

    shortcuts = {
        "Volume Up": "w",
        "Volume Down": "s",
        "Backward": "a",
        "Forward": "d",
        "Play/Pause": "space"
    }

    def __init__(self, **kwargs):
        super().__init__(css_name=self.__gtype_name__.lower(), focusable=True, **kwargs)
        e = Gtk.EventControllerKey()
        e.connect("key-pressed", self.__class__.on_key_pressed)
        self.add_controller(e)
        self.media_bindings = []
        self.loop_button, self.volume_button, self.playback_button, self.speed_button, self.seekbar = LoopButton(), VolumeButton(), PlaybackButton(), SpeedButton(), MediaSeekbar(hexpand=True)
        for i in (self.volume_button, self.playback_button, self.speed_button, self.seekbar, self.loop_button):
            i.connect("notify::visible", self.__class__.child_visible_changed)
            self.append(i)
        self.bindings = [self.seekbar.bind_property("visible", self.loop_button, "visible", DEFAULT_BIND), self.seekbar.bind_property("visible", self.speed_button, "visible", DEFAULT_BIND, self.__class__.speed_bind), self.seekbar.bind_property("visible", self.playback_button, "visible", DEFAULT_BIND)]
        if self.media_stream: self.__class__.bind_media(self, self.media_stream)
        self.connect("notify::media-stream", self.__class__.bind_media)

    @staticmethod
    def speed_bind(b, v):
        stream = b.get_property("source").get_ancestor(MediaControls).media_stream
        return v and stream and stream.__class__.find_property("speed") and getattr(stream, "can_change_speed", True)

    @staticmethod
    def on_key_pressed(e, v, kc, s):
        self = e.get_widget()
        if not self.media_stream: return
        if v == Gdk.KEY_space: self.media_stream.playpause()
        if v in (Gdk.KEY_a, Gdk.KEY_d): self.media_stream.backward() if v == Gdk.KEY_a else self.media_stream.forward()
        if v in (Gdk.KEY_w, Gdk.KEY_s): self.media_stream.volumeup() if v == Gdk.KEY_w else self.media_stream.volumedown()

    @staticmethod
    def child_visible_changed(child, param=None):
        self = child.get_ancestor(MediaControls)
        if not self: return
        self.set_visible(any(i.get_visible() for i in self))

    @staticmethod
    def bind_media(self, p=None):
        self.seekbar.set_visible(False)
        for i in self.media_bindings: i.unbind()
        for i in (self.loop_button, self.volume_button, self.playback_button, self.speed_button, self.seekbar): i.bind_media(self.media_stream)
        if self.media_stream:
            self.media_bindings = [self.media_stream.bind_property("has-audio", self.volume_button, "visible", DEFAULT_BIND), self.media_stream.bind_property("duration", self.seekbar, "visible", DEFAULT_BIND, self.__class__.duration_bind)]
        else:
            self.media_bindings = []

    @staticmethod
    def duration_bind(b, v): return v > 0

class Media(Gtk.Overlay):
    __gtype_name__ = "Media"
    picture = GObject.Property(type=Gtk.Widget)
    controls = GObject.Property(type=MediaControls)
    scrolled_window = GObject.Property(type=Gtk.ScrolledWindow)
    media_stream = GObject.Property(type=Gdk.Paintable)

    def __init__(self, zoom=False, **kwargs):
        super().__init__(child=Gtk.GraphicsOffload(), css_name=self.__gtype_name__.lower(), **kwargs)
        if zoom:
            self.picture = Picture()
            self.scrolled_window = Gtk.ScrolledWindow(child=self.picture)
        else:
            self.picture = Gtk.Picture()
        self.connect("notify::media-stream", self.__class__.media_changed)
        self.__class__.media_changed(self)
        Window.controller_init(self)

    @staticmethod
    def on_scroll(e, x, y):
        self = e.get_widget()
        if not isinstance(self.picture, Picture) and isinstance(self.media_stream, MediaStream) and self.media_stream.has_audio and self.media_stream.has_video:
            self.media_stream.volume = float(max(0.0, min(self.media_stream.volume + -y * 0.02, 1.0)))
            return True

    def do_dispose(self):
        if hasattr(self.media_stream, "pause"): Task.invoke(self.media_stream.pause)
        Task.invoke(self.set_child, None)
        for i in ("media_stream", "scrolled_window", "controls", "picture"): Task.invoke(setattr, self, i, None)

    def grab_focus(self):
        if self.controls: Task.invoke(self.controls.grab_focus)
        elif self.picture: Task.invoke(self.picture.grab_focus)

    @staticmethod
    def set_tooltip(l, p=None):
        s = []
        for i in (l.get_value(), l.get_adjustment().get_upper()):
            i = int(i)
            hours = i // 3600
            minutes = (i % 3600) // 60
            seconds = i % 60
            s.append(f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}")
        Task.invoke(l.set_tooltip_text, " / ".join(s))

    def update_child(self):
        off = self.get_child()
        if not off: return
        off.set_child(None)
        if not self.media_stream:
            if self.scrolled_window: off.set_child(Adw.Spinner())
            else:
                self.picture.set_paintable(Adw.SpinnerPaintable.new(self))
                off.set_child(self.picture)
            return
        child = self.scrolled_window if self.scrolled_window else self.picture
        if self.media_stream.find_property("duration") and self.media_stream.get_property("duration") > 1 and not self.controls:
            self.controls = MediaControls(hexpand=True)
        if self.controls:
            self.controls.unparent()
            if self.controls.media_stream: self.controls.media_stream.pause()
            self.controls.media_stream = self.media_stream
        if (isinstance(self.media_stream, MediaStream) and self.media_stream.paintable) or self.media_stream and not isinstance(self.media_stream, MediaStream):
            self.picture.set_property("paintable", self.media_stream)
            if self.controls:
                self.controls.set_valign(Gtk.Align.END)
                self.controls.set_vexpand(True)
                self.add_overlay(self.controls)
            off.set_child(child)
        elif self.controls:
            self.controls.set_valign(Gtk.Align.CENTER)
            self.controls.set_vexpand(False)
            off.set_child(self.controls)

    @staticmethod
    def media_changed(a, p=None): a.update_child()

class CSS(Gtk.CssProvider):
    @GObject.Property(type=str, flags=GObject.ParamFlags.READWRITE)
    def css(self): return self._css

    @css.setter
    def css(self, value):
        if hasattr(self.__class__, "remove_backdrop"):
            value = self.__class__.remove_backdrop.sub("", value).replace("backdrop-filter", "")
        self._css = value.replace("    ", "")
        self.load_from_string(self._css)

    def __init__(self, css="", priority=Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION):
        self._css = ""
        super().__init__()
        if not hasattr(self.__class__, "remove_backdrop"):
            if 21 >= Gtk.get_minor_version():
                from re import compile as Regex
                self.__class__.remove_backdrop = Regex(r"backdrop-filter:.*;")
        self.css = css
        try: Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self, priority)
        except Exception as e: print(e)
        try: Gtk.Settings.get_default().bind_property("gtk-interface-color-scheme", self, "prefers-color-scheme", DEFAULT_BIND)
        except Exception as e: print(e)

    def dark_bind(b, v): Gtk.InterfaceColorScheme.DARK if v else Gtk.InterfaceColorScheme.LIGHT

class App(Adw.Application):
    __gtype_name__ = "UtilsApp"

    @classproperty
    def default(*_): return Adw.Application.get_default()

    data = GObject.Property(type=Data)
    data_folder = GObject.Property(type=Gio.File)
    default_menu = GObject.Property(type=Gio.Menu)
    shortcuts = GObject.Property(type=Gtk.Widget)
    about = GObject.Property(type=Adw.AboutDialog)
    color_css = GObject.Property(type=str)
    css = GObject.Property(type=str)
    utils_css = GObject.Property(type=str)

    def __init__(self, shortcuts=None, data=None, activate=None, shutdown=None, file_open=None, command_line=None, info=None, actions=(), about=True, window_removed=None, install=True, log=True, data_folder=False, **kwargs):
        shortcuts = shortcuts or {}
        if kwargs.get("flags", None) == None and "flags" in kwargs:
            del kwargs["flags"]
        if file_open and not "flags" in kwargs:
            kwargs["flags"] = Gio.ApplicationFlags.HANDLES_OPEN
        if command_line and not "flags" in kwargs:
            kwargs["flags"] = Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        if info: App.default_info(info)
        super().__init__(application_id=(info["ID"] if info else "") or "org.example.Example", **kwargs)
        self.set_default()
        if install and ARGS[-1] == "--install":
            Install.info = info
            if FLATPAK_ID:
                global SYSTEM_DATA
                SYSTEM_DATA = [SYSTEM_DATA[0]]
                Install.bin_location = Path("/app", Install.bin_location)
                global PACKAGE_LOCATION
                PACKAGE_LOCATION = Path("/app", PACKAGE_LOCATION)
            Install()
            exit()
        if Gio.ApplicationFlags.NON_UNIQUE in self.get_flags():
            App.default = self
        else:
            self.register()
            if self.get_is_remote():
                self.run(ARGS)
                exit()
        if FLATPAK_ID: Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(Path(SYSTEM_DATA[0], self.get_application_id()))
        if hasattr(sys, "_MEIPASS"): Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(Path(sys._MEIPASS, "assets"))
        self.utils_css = self.__class__.UTILS_CSS + "\n"
        self.bind_property("css", CSS(), "css", SYNC_BIND)
        for i in ("utils-css", "color-css"): self.bind_property(i, CSS(priority=Gtk.STYLE_PROVIDER_PRIORITY_THEME), "css", SYNC_BIND)
        self.data_folder = data_folder or Gio.File.new_for_path(DATA)
        self.add_actions(*actions)
        if info:
            if not FLATPAK_ID:
                flatpak_data = Path(HOME, ".var", "app", self.get_application_id(), "data")
                self.data_folder = Gio.File.new_for_path(flatpak_data) if exists(flatpak_data) else self.data_folder.get_child(info["Name"])
            if about and shortcuts:
                self.add_actions(("about", self.__class__.default_show_about), ("shortcuts", self.__class__.default_show_shortcuts, "<primary>question"))
            if about: self.about = Adw.AboutDialog(application_name=info["Name"], issue_url=info["Issue URL"], website=info["Website"], application_icon=info["ID"], license_type=info["License"], developer_name=info["Developer"], version=info["Version"], release_notes=info["Release Notes"][-1][-1])
            if shortcuts:
                self.shortcuts = Adw.ShortcutsDialog() if hasattr(Adw, "ShortcutsDialog") else Gtk.ShortcutsWindow()
                if not hasattr(self.shortcuts, "add"):
                    s = Gtk.ShortcutsSection()
                    self.shortcuts.add_section(s)
                for title, v in shortcuts.items():
                    if hasattr(self.shortcuts, "add"):
                        section = Adw.ShortcutsSection(title=title)
                        for t, a in v.items():
                            if a.startswith(("win.", "app.")):
                                a = " ".join(self.get_accels_for_action(a))
                            section.add(Adw.ShortcutsItem(title=t, accelerator=a))
                        self.shortcuts.add(section)
                    else:
                        section = Gtk.ShortcutsGroup(title=title)
                        for t, a in v.items():
                            if a.startswith(("win.", "app.")):
                                a = " ".join(self.get_accels_for_action(a))
                            section.add_shortcut(Gtk.ShortcutsShortcut(title=t, accelerator=a))
                        s.add_group(section)
            if about and shortcuts:
                self.default_menu = Menu({"Keyboard Shortcuts": "shortcuts", f"About {self.about.get_application_name()}": "about"})
        if data:
            if "CREATE TABLE IF NOT EXISTS Windows" in data and not window_removed:
                window_removed = self.__class__.persist_windows_window_removed
            shutdown = shutdown or self.__class__.default_data_shutdown
            self.__class__.default.data = Data(Path(self.__class__.default.data_folder, "data"), data)
        if activate: self.connect("activate", activate)
        if shutdown: self.connect("shutdown", shutdown)
        if file_open: self.connect("open", file_open)
        if command_line: self.connect("command-line", command_line)
        if window_removed: self.connect("window-removed", window_removed)
        if log and GLib.getenv("NO_LOG") != "1":
            Task(Log).start()
            self.add_actions(("log", self.__class__.log, "F8"))
        Adw.StyleManager.get_default().bind_property("dark", Gtk.Settings.get_default(), "gtk-interface-color-scheme", DEFAULT_BIND, CSS.dark_bind)

    @staticmethod
    def log(*_):
        if Log.instance:
            if Log.instance.get_mapped(): Log.instance.set_visible(False)
            else:
                Log.instance.opened = True
                Log.instance.present()

    @staticmethod
    def quit_default(*args):
        if App.default: App.default.quit()

    @staticmethod
    def persist_windows_window_removed(a, w):
        wi = [i for i in Gtk.Window.list_toplevels() if isinstance(i, Window)]
        if not wi or not isinstance(w, Window): return
        App.default.data.delete(table="Windows", conditions="ID = ?", conds=(w.get_id(),))

    def add_actions(self, *actions):
        for i in actions:
            name, callback, accels = i[0], i[1], i[2] if len(i) == 3 else ""
            self.add_action(Gio.SimpleAction.new(name, None))
            if accels != "": self.set_accels_for_action(f"app.{name}", accels.split(" "))
            if callback: self.lookup_action(name).connect("activate", callback)

    @staticmethod
    def default_info(info):
        for k, v in {
            "ID": "org.example.Example",
            "Description": "<p>No description</p>",
            "Developer": "Example",
            "Version": "1.0",
            "Comment": "No comment",
            "Keywords": [],
            "Categories": [],
            "Ratings": [],
            "MimeTypes": [],
            "Screenshots": [],
            "Release Notes": [("1.0", GLib.DateTime.new_now_local().format("%Y-%m-%d"), "<ul><li>First release</li></ul>")],
            "Supports": {"control": ["pointing", "keyboard", "touch"]},
            "Issue URL": "https://example.org/issues",
            "Website": "https://example.org/example",
            "License": Gtk.License.MIT_X11,
        }.items(): info.setdefault(k, v)
        info.setdefault("Name", info["ID"].rsplit(".", 1)[-1])
        info.setdefault("Binary", info["Name"].lower())
        info.setdefault("Branding", ["#000000", "#ffffff"])

    @staticmethod
    def default_show_about(*_):
        if App.default.about.get_mapped(): App.default.about.close()
        App.default.about.present(Window.current)

    @staticmethod
    def default_show_shortcuts(*_):
        if App.default.shortcuts.get_mapped(): App.default.shortcuts.close()
        if isinstance(App.default.shortcuts, Adw.Dialog): App.default.shortcuts.present(Window.current)
        else: App.default.shortcuts.present()

    @staticmethod
    def default_data_shutdown(a): a.data.close()

    def main(self=None, css=""):
        self = self or App.default
        if not self: return "No App"
        if css:
            self.css = css
        self.run(ARGS)

App.UTILS_CSS = ""

class Menu(Gio.Menu):
    def __init__(self, *args):
        super().__init__()
        self.parse(*args)

    def parse(self=None, d=None):
        if isinstance(self, dict):
            d = self
            self = None
        d = d or {}
        self = self or Gio.Menu.new()
        for k, v in d.items():
            if isinstance(v, str):
                if v.startswith("custom::"):
                    it = Gio.MenuItem.new(k, k.lower().replace(" ", "-"))
                    it.set_attribute_value("custom", get_variant(v.split("custom::", 1)[-1]))
                    self.append_item(it)
                else:
                    self.append(k, v if "." in v else f"app.{v}")
            elif isinstance(v, dict): self.append_submenu(k if not k.startswith("!") else None, Menu(v))
            elif isinstance(v, Gio.Menu): self.append_section(k if not k.startswith("!") else None, v)
        return self

class PaintableTask(GObject.Object):
    __gtype_name__ = "PaintableTask"

    headers = GObject.Property(type=object)
    func = GObject.Property(type=object)
    data = GObject.Property(type=object)
    uri = GObject.Property(type=str)
    error = GObject.Property(type=object)
    media_stream_args = GObject.Property(type=object)

    def __init__(self, func=None, data=None, uri="", error=None, start=True, media_stream_args=None, headers=None):
        super().__init__()
        do_gst()
        self.headers = headers or {}
        self.media_stream_args = media_stream_args or {}
        self.func = func
        self.data = data
        if isinstance(uri, Gio.File):
            uri = uri.get_path() if uri.get_uri().startswith("file://") else uri.get_uri()
        self.uri = uri
        self.error = error or self.__class__.handle_load_error
        if start: self.start()

    def start(self):
        if not self.uri: return
        try:
            if self.uri.startswith("archive://") and f"|{SEPARATOR}" in self.uri:
                Task(self.__class__.get_zip_bytes, self).start()
                return
            if not "://" in self.uri:
                self.uri = uri_relative_encode(self.uri)
            mimetype = Gio.content_type_guess(self.uri)[0] if self.uri else None
            if self.uri.startswith("file://") and mimetype.endswith("zip"): Task(self.__class__.get_zip_bytes, self).start()
            else: Task(self.__class__.get_data, self).start()
        except Exception as e: print(self.uri, e)

    @staticmethod
    def handle_load_error(*args):
        if len(args) >= 6:
            data, uri, _async, user_data, media_stream_args, error = args
            if is_online(uri) and isinstance(data, bytes):
                try:
                    h = data.decode("utf-8")
                    if not "<!DOCTYPE html>" in h:
                        h = ""
                except:
                    h = ""
                Task.invoke(Online.Intervention, uri, h)
            else: print("paintable task:", uri, error)
        else: print("paintable task:", args)

    def get_zip_bytes(self):
        if not self.uri: return
        from zipfile import ZipFile
        if self.uri.startswith("archive://"):
            zz, i = self.uri.split("|" + SEPARATOR, 1)
            z = zipfile.ZipFile(zz.split("archive://", 1)[-1], "r")
        else:
            z = zipfile.ZipFile(self.uri, "r")
            i = sorted((i for i in z.namelist() if Gio.content_type_guess(i)[0].startswith(("image", "video"))), key=alphabetical_sort)[0]
        self.data_to_paintable(GLib.Bytes.new(z.read(i)), uri=self.uri, _async=self.func, user_data=self.data, error=self.error, media_stream_args=self.media_stream_args)

    def get_data(self):
        try:
            if self.uri.startswith("file://"):
                data = None
            else:
                data = Online.Request(self.uri, headers=self.headers)
        except Exception as e: return Task.invoke(self.error, *(self.data, self.uri, None, None, self.media_stream_args, e)) if self.error else print("paintable task:", self.uri, e)
        p = self.data_to_paintable(data, self.uri, error=self.error, media_stream_args=self.media_stream_args)
        if self.func and p and self.data: self.func(p, self.data)
        elif self.func and p: self.func(p)
        if hasattr(self.data, "task") and self.data.task == self:
            self.data.task = None
        self.func = None
        self.data = None
        self.uri = None
        self.error = None
        self.run_dispose()

    @staticmethod
    def data_to_paintable(data, uri="", _async=None, user_data=None, error=None, media_stream_args=None):
        media_stream_args = media_stream_args or {}
        try:
            if uri.startswith("file://") and uri.endswith(".svg"):
                t = Gtk.IconPaintable.new_for_file(Gio.File.new_for_uri(uri), 1, 1)
                t.uri = uri
                if _async: _async(t, user_data)
                else: return t
                return
            t = MediaStream(data, uri=uri, stream=not ".mp3" in uri, **media_stream_args)
            event = Event()
            Task.invoke(event.set)
            event.wait()
            if _async: _async(t, user_data)
            else: return t
        except Exception as e:
            if error:
                if Log.paintabletask: print("data_to_paintable error", e)
                Task.invoke(error, *(data, uri, _async, user_data, media_stream_args, e))
                import traceback
                traceback.print_exc()
            return None

class Palette:
    @staticmethod
    def set_colors(arg=None):
        App.default.color_css = (":root { " + " ".join(tuple(fr"--color-{n + 1}: rgb{c};" for n, c in enumerate(arg.colors))) + " }") if arg and hasattr(arg, "colors") and arg.colors else ""
        for w in App.default.get_windows():
            if App.default.color_css:
                if not "colored" in w.get_css_classes(): w.add_css_class("colored")
            elif "colored" in w.get_css_classes(): w.remove_css_class("colored")

    @staticmethod
    def do_renderer():
        if hasattr(Palette, "renderer"): return
        Palette.renderer = Gsk.CairoRenderer.new()
        Palette.renderer.realize_for_display(Gdk.Display.get_default())
        global pythonapi, gdk, c_ubyte
        from ctypes import POINTER, CDLL, pythonapi, util, c_void_p, py_object, c_size_t, c_ubyte
        pythonapi.PyCapsule_GetPointer.restype = c_void_p
        pythonapi.PyCapsule_GetPointer.argtypes = [py_object]
        if sys.platform.startswith("win") and hasattr(sys, "_MEIPASS"):
            gdk = CDLL(sys._MEIPASS + r"\libgtk-4-1.dll")
        else:
            gdk = CDLL(util.find_library("gdk-4"))
        gdk.gdk_texture_download.argtypes = [c_void_p, POINTER(c_ubyte), c_size_t]
        gdk.gdk_texture_download.restype = None

    @staticmethod
    def download_texture(texture):
        Palette.do_renderer()
        stride = texture.get_width() * 4
        buffer = (c_ubyte * (stride * texture.get_height()))()
        gdk.gdk_texture_download(pythonapi.PyCapsule_GetPointer(texture.__gpointer__, None), buffer, stride)
        return buffer

    @staticmethod
    def paintable_to_pixels(paintable=None, size=False):
        paintable = get_paintable(paintable)
        if not paintable or MediaStream.is_empty(paintable): return []
        if size:
            width, height = clamp_size(size, paintable.get_intrinsic_width(), paintable.get_intrinsic_height())
        else:
            width, height = paintable.get_intrinsic_width(), paintable.get_intrinsic_height()
        snapshot = Gtk.Snapshot.new()
        paintable.snapshot(snapshot, width, height)
        node = snapshot.to_node()
        if node:
            Palette.do_renderer()
            data = Palette.download_texture(Palette.renderer.render_texture(node))
        else:
            print("Could not get the node from", paintable)
            return []
        l = []
        stride = width * 4
        for y in range(height):
            row_start = y * stride
            for x in range(width):
                i = row_start + x * 4
                l.append((data[i + 2], data[i + 1], data[i], data[i + 3]))
        return l

    @staticmethod
    def from_paintable(value, size=64, colors=3, duration=10, minimum_light=10, maximum_light=60, minimum_saturation=50, distance=40):
        if hasattr(value, "colors") and isinstance(value.colors, list) and len(value.colors) == colors: return value.colors
        start = GLib.get_monotonic_time() if duration else 0
        try:
            pixel_colors = Palette.paintable_to_pixels(paintable=value, size=size)
        except Exception as e:
            print("Palette:", e)
            return []
        if colors > len(pixel_colors):
            print("Palette:", "Not enough pixels")
            return []
        else: return Palette.from_pixels(pixel_colors, colors=colors, duration=duration, start=start, minimum_light=minimum_light, maximum_light=maximum_light, minimum_saturation=minimum_saturation, distance=distance)

    @staticmethod
    def most_common(pixels=None, alpha=False, as_rgba=True):
        pixels = pixels or []
        from collections import Counter
        pixel_counts = Counter(pixels)
        l = [col for col, _ in pixel_counts.most_common() if col[3] >= alpha] if alpha else [col for col, _ in pixel_counts.most_common()]
        return l if as_rgba else [(i[0], i[1], i[2]) for i in l]

    @staticmethod
    def from_pixels(pixels, colors=3, duration=0, start=0, minimum_light=10, maximum_light=60, minimum_saturation=50, distance=40):
        pixels = Palette.most_common(pixels, alpha=30, as_rgba=False)
        minimum_light = minimum_light / 100
        maximum_light = maximum_light / 100
        minimum_saturation = minimum_saturation / 100
        result = []
        distance = (distance / 100) * 255
        from colorsys import rgb_to_hls
        rgb_hls =  tuple((i, rgb_to_hls(*[it / 255 for it in i])) for i in pixels)
        skipped = [0, 0, 0, 0]
        n = len(pixels) / 2
        if colors > len(rgb_hls):
            print("Palette:", "Not enough colors")
            return []
        while colors > len(result):
            if duration and GLib.get_monotonic_time() - start > duration * GLib.TIME_SPAN_SECOND:
                print("Palette:", "Timed out")
                return []
            for rgb, hls in rgb_hls:
                if len(result) >= colors: break
                if rgb in result: continue
                if minimum_light > hls[1]:
                    if skipped[0] > n:
                        minimum_light -= 0.1
                        skipped[0] = 0
                    skipped[0] += 1
                    continue
                if hls[1] > maximum_light:
                    if skipped[1] > n:
                        maximum_light += 0.1
                        skipped[1] = 0
                    skipped[1] += 1
                    continue
                if minimum_saturation > hls[2]:
                    if skipped[2] > n:
                        minimum_saturation -= 0.1
                        skipped[2] = 0
                    skipped[2] += 1
                    continue
                for selected_color in result:
                    if sum((c1 - c2) ** 2 for c1, c2 in zip(rgb, selected_color)) < distance**2:
                        if skipped[3] > n:
                            distance -= 5
                            skipped[3] = 0
                        skipped[3] += 1
                        break
                else: result.append(rgb)
        position = {c: i for i, c in enumerate(pixels)}
        def original_pos(i): return position[i]
        result = sorted(result, key=original_pos)
        if Log.palette: Palette.log_result(result)
        return result

    @staticmethod
    def log_result(result):
        s = str([f"rgb({', '.join([str(it) for it in i])})" for i in result])
        print("Palette", s)
        Task.invoke(Gdk.Display.get_default().get_clipboard().set, s)

class Online:
    headers = {}
    @staticmethod
    def init():
        try:
            global Soup
            from gi.repository import Soup
        except: return print("Soup not available")
        Online.Cookies = Soup.CookieJarText.new(Path(App.default.data_folder if App.default else DATA, "cookies"))
        Online.Session = Soup.Session(max_conns_per_host=10, max_conns=30)
        Online.Session.add_feature(Online.Cookies)
        user = Online.get_cookie(domain="user.agent", name="user_agent")
        if user: Online.headers.setdefault("User-Agent", user)
        return True

    @staticmethod
    def has_init(): return hasattr(Online, "Cookies")

    @staticmethod
    def Request(url, headers=None):
        headers = headers or {}
        if not Online.has_init() and not Online.init(): return
        he = {"Referer": url}
        he.update(Online.headers)
        he.update(headers)
        message = Soup.Message.new("GET", url)
        h = message.get_request_headers()
        for k, v in he.items():
            if h.get_one(k): h.replace(k, v)
            else: h.append(k, v)
        if Log.online: print("Fetching:", message.get_uri().to_string(), "Headers:", he)
        try: return Online.get_data(Online.Session.send_and_read(message))
        except Exception as e: print(e)

    @staticmethod
    def get_data(b, e=None):
        if Task.current() is Task.main():
            d = b.unref_to_data()
            if e:
                e.d = bytes(d)
                Task.invoke(e.set)
            else: return d
        else:
            e = Event()
            Task.invoke(Online.get_data, b, e)
            e.wait()
            return e.d

    @staticmethod
    def MultipartRequest(url, headers=None, fields=None, files=None):
        headers = headers or {}
        fields = fields or {}
        files = files or {}
        if not Online.has_init() and not Online.init(): return
        multipart = Soup.Multipart.new("multipart/form-data")
        for name, value in fields.items(): multipart.append_form_string(name, value)
        for name, file_path, mime_type, content in files: multipart.append_form_file(name, file_path, mime_type, content)
        message = Soup.Message.new_from_multipart(url, multipart)
        he = {"Referer": url}
        he.update(Online.headers)
        he.update(headers)
        h = message.get_request_headers()
        for k, v in he.items():
            if h.get_one(k): h.replace(k, v)
            else: h.append(k, v)
        if Log.online: print("Fetching:", message.get_uri().to_string(), "Headers:", he)
        try: return Online.get_data(Online.Session.send_and_read(message))
        except Exception as e: print(e)

    @staticmethod
    def add_cookie(name="", value="", domain="", path="/", expires=9999999999):
        if not Online.has_init() and not Online.init(): return
        c = Soup.Cookie.new(name, value, domain, path, 1)
        if expires: c.set_expires(expires if isinstance(expires, GLib.DateTime) else GLib.DateTime.new_from_unix_utc(expires))
        Online.Cookies.add_cookie(c)

    @staticmethod
    def get_cookie(name="", domain=""):
        if not Online.has_init() and not Online.init(): return
        for i in Online.Cookies.all_cookies():
            if i.get_name() == name and i.get_domain() == domain: return i.get_value()

    @staticmethod
    def remove_cookie(name="", domain=""):
        if not Online.has_init() and not Online.init(): return
        c = Online.get_cookie(name, domain)
        if c: Online.Cookies.delete_cookie(c)

    @staticmethod
    def do_replace(a, r, t):
        if a.replace_cookies_finish(r): Task.invoke(Online.do_manual_intervention, *t)

    @staticmethod
    def do_manual_intervention(uri, content="", function=None, function_data=None, settings=None, context=None):
        if not Online.has_init() and not Online.init(): return
        if any(i.get_title().split("://", 1)[-1].split("/")[0] == uri.split("://", 1)[-1].split("/")[0] for i in Adw.Dialog.get_all()): return
        if not settings:
            settings = WebKit.Settings.new()
            settings.set_properties(allow_file_access_from_file_urls=True, allow_top_navigation_to_data_urls=True,  allow_universal_access_from_file_urls=True, default_charset="utf-8", allow_modal_dialogs=True, javascript_can_open_windows_automatically=True, javascript_can_access_clipboard=True, enable_encrypted_media=True, enable_webrtc=True, enable_developer_extras=True, enable_write_console_messages_to_stdout=True, webrtc_udp_ports_range="", disable_web_security=True)
            user_agent = Online.headers.get("User-Agent", "").strip()
            if user_agent and not ("AppleWebKit" in user_agent and "Safari" in user_agent): settings.set_user_agent(user_agent)
            l = settings.get_all_features()
            for i in [l.get(i) for i in range(l.get_length())]:
                if i.get_identifier() in ("SafeBrowsing", "LinkPreconnect", "LinkPreload"): settings.set_feature_enabled(i, False)
                if i.get_identifier() in ("ForceCompositingMode", "DeveloperExtras", "StorageAccessAPIPerPageScope", "AllowUniversalAccessFromFileURLs", "IsFirstPartyWebsiteDataRemovalDisabled", "AllowWebGLInWorkers", "IsThirdPartyCookieBlockingDisabled", "StorageAPI", "StorageAPIEstimate", "DynamicSiteInterventions"): settings.set_feature_enabled(i, True)
        if not context:
            context = WebKit.WebContext()
            context.set_preferred_languages(["en"])
            context.set_cache_model(WebKit.CacheModel.WEB_BROWSER)
            context.set_web_process_extensions_directory(App.default.data_folder.get_path())
        view = WebKit.WebView(web_context=context, settings=settings, network_session=Online.NetworkSession)
        w = Adw.Dialog(child=Adw.ToastOverlay(child=Adw.ToolbarView(extend_content_to_top_edge=True, content=view)), title=uri, content_width=800, content_height=800)
        w.view = view
        w.add_css_class("toolbar-fullscreen")
        w.get_child().get_child().add_top_bar(Adw.HeaderBar())
        w.connect("closed", Online.do_write_manual_intervention_data)
        if function and function_data: function(w, function_data)
        elif function: function(w)
        w.present(Window.current)
        if content: Task.invoke(view.load_html, content, uri)
        else: Task.invoke(view.load_uri, uri)

    @staticmethod
    def Intervention(uri, content="", function=None, function_data=None):
        if not Online.has_init() and not Online.init(): return
        if any(i.get_title().split("://", 1)[-1].split("/")[0] == uri.split("://", 1)[-1].split("/")[0] for i in Adw.Dialog.get_all()): return
        Task.invoke(Online.start_intervention, [uri, content, function, function_data])

    @staticmethod
    def start_intervention(args, **kwargs):
        if not hasattr(Online, "NetworkSession"):
            try:
                global WebKit
                from gi.repository import WebKit
            except Exception as e: return print(e, "No WebKit")
            Online.NetworkSession = WebKit.NetworkSession.new_ephemeral()
            Online.NetworkSession.set_itp_enabled(False)
            Online.NetworkSession.set_tls_errors_policy(WebKit.TLSErrorsPolicy.IGNORE)
            l = [i for i in Online.Cookies.all_cookies() if not i.get_name() == "user_agent" and i.get_domain() == "user.agent"]
            if l:
                Online.NetworkSession.get_cookie_manager().replace_cookies(l, None, Online.do_replace, args)
                return
        Task.invoke(Online.do_manual_intervention, *args, **kwargs)

    @staticmethod
    def write_cookies(a, r, user_agent):
        l = a.get_all_cookies_finish(r)
        for i in l: Online.add_cookie(name=i.get_name(), value=i.get_value(), domain=i.get_domain(), path=i.get_path(), expires=i.get_expires())
        Online.add_cookie(name="user_agent", value=user_agent, domain="user.agent")
        Online.headers.setdefault("User-Agent", user_agent)

    @staticmethod
    def do_write_manual_intervention_data(w):
        w.view.get_network_session().get_cookie_manager().get_all_cookies(None, Online.write_cookies, w.view.get_settings().get_user_agent())
        Task.invoke(w.view.load_uri, "")
        Task.invoke(w.view.terminate_web_process)
        w.view = None

from threading import Event, Thread, current_thread, main_thread

class Task:
    def __init__(self, f, *args, gio=False, **kwargs):
        super().__init__()
        self.gio = gio
        self.target = f
        self.args = args
        self.kwargs = kwargs

    @staticmethod
    def gio_run(t, *args):
        try:
            t.target(*t.args, **t.kwargs)
        except Exception as e: print(e)
        del t.target
        del t.args
        del t.kwargs

    def start(self):
        if Log.task: print("Task start", self.target, self.args, self.kwargs)
        if self.gio:
            t = Gio.Task()
            t.target = self.target
            t.args = self.args
            t.kwargs = self.kwargs
            Task.invoke(t.run_in_thread, self.__class__.gio_run)
        else:
            t = Thread(daemon=True)
            t._target = self.target
            t._args = self.args
            t._kwargs = self.kwargs
            t.start()
        del self.target
        del self.args
        del self.kwargs

    current = current_thread
    main = main_thread

    @staticmethod
    def invoke(function, *args, priority=GLib.PRIORITY_DEFAULT_IDLE, log=True, **kwargs):
        if log and Log.task: print(f"Task invoke ({priority}): ", function, args, kwargs)
        def f():
            if function:
                if args and kwargs: function(*args, **kwargs)
                elif kwargs: function(**kwargs)
                elif args: function(*args)
                else: function()
        if function:
            if priority == GLib.PRIORITY_DEFAULT_IDLE: GLib.idle_add(f)
            else: GLib.MainContext.default().invoke_full(priority, f)

class Button:
    def __new__(cls, t=Gtk.Button, callback=None, bindings=(), **kwargs):
        for i in ("icon_name", "start_icon_name", "end_icon_name"):
            if i in kwargs and not kwargs[i].endswith("-symbolic"):
                kwargs[i] = kwargs[i] + "-symbolic"
        button = t(**kwargs)
        if callback: button.connect("activated" if t == Adw.ButtonRow else "clicked" if type(button) == Gtk.Button else "notify::active", callback)
        for b in bindings:
            source = b[0] if b[0] else button
            source.bind_property(b[1], b[2] if b[2] else button, b[3], b[4] if len(b) >= 5 and b[4] else DEFAULT_BIND, b[5] if len(b) >= 6 else None)
        return button

class Toast:
    def __new__(cls, title="", message=None, toast_overlay=False, **kwargs):
        title = str(title)
        if Log.toast:
            if message: print(message)
            else: print(title)
        window = App.default.get_active_window()
        if not (window and window.get_mapped()):
            window = next((w for w in Gtk.Window.list_toplevels() if w.get_mapped()), False)
        if isinstance(window, Adw.Window) or isinstance(window, Adw.ApplicationWindow):
            if isinstance(window.get_visible_dialog(), Adw.PreferencesDialog):
                toast_overlay = window.get_visible_dialog()
            elif window.get_visible_dialog() and isinstance(window.get_visible_dialog().get_child(), Adw.ToastOverlay):
                toast_overlay = window.get_visible_dialog().get_child()
        toast_overlay = toast_overlay if hasattr(toast_overlay, "add_toast") else Adw.ApplicationWindow.get_content(window) if isinstance(window, Window) else window.get_content() if hasattr(window, "get_content") else window.get_child() if hasattr(window, "get_child") else None
        if hasattr(toast_overlay, "add_toast"): Task.invoke(toast_overlay.add_toast, Adw.Toast(title=title, use_markup=False, **kwargs))
        return False

class Progress:
    @staticmethod
    def start(p):
        Progress.stop(p)
        if p:
            p.task = True
            p.task = GLib.timeout_add(100, Progress.do_pulse, p)

    @staticmethod
    def stop(p):
        if hasattr(p, "task"):
            if isinstance(p.task, int):
                GLib.source_remove(p.task)
            del p.task

    @staticmethod
    def do_pulse(p):
        p.pulse()
        if hasattr(p, "task"): return True
        else: Progress.stop(p)

class ToolbarFullscreen:
    def __init__(self, w, default=False):
        w.fullscreen = default
        for i in w:
            if isinstance(i, Gtk.Revealer):
                i.set_property("height-request", 2)
                e = Gtk.EventControllerMotion(propagation_limit=Gtk.PropagationLimit.NONE, propagation_phase=Gtk.PropagationPhase.CAPTURE)
                e.connect("notify::contains-pointer", self.__class__.revealer_contains)
                i.add_controller(e)
        if default: self.__class__.fu(w)
        else: self.__class__.un(w)

    @staticmethod
    def revealer_contains(e, p=None):
        r = e.get_widget()
        t = r.get_ancestor(Adw.ToolbarView)
        if not t.fullscreen: return
        (t.set_reveal_top_bars if "top-bar" in r.get_css_classes() else t.set_reveal_bottom_bars)(e.get_property("contains-pointer"))

    @staticmethod
    def fu(t):
        t.fullscreen = True
        t.add_css_class("fullscreen")
        if t.get_root(): t.get_root().add_css_class("toolbar-fullscreen")
        t.set_extend_content_to_top_edge(True)
        t.set_extend_content_to_bottom_edge(True)
        t.set_reveal_top_bars(False)
        t.set_reveal_bottom_bars(False)

    @staticmethod
    def un(t):
        t.fullscreen = False
        t.remove_css_class("fullscreen")
        if t.get_root(): t.get_root().remove_css_class("toolbar-fullscreen")
        t.set_extend_content_to_top_edge(False)
        t.set_extend_content_to_bottom_edge(False)
        t.set_reveal_top_bars(True)
        t.set_reveal_bottom_bars(True)

class Install:
    info = False
    icons = True
    utils = True
    app = True
    bin = True
    desktop = True
    metainfo = True
    bin_location = "bin"
    extra = False

    def __init__(self):
        if ARGS[0].startswith(PACKAGE_LOCATION):
            print("Already installed...")
            exit()
        if Install.icons: Install.do_icons()
        if Install.utils: Install.do_utils()
        if Install.app: Install.do_app()
        if Install.info and Install.desktop: Install.do_desktop(Install.info, force=True)
        if Install.info and Install.metainfo: Install.do_metainfo(Install.info)
        if Install.info and Install.bin: Install.do_bin(Install.info)
        if Install.extra: Install.extra()
        exit()

    @staticmethod
    def do_desktop(info, force=False):
        p = Path(SYSTEM_DATA[-1], "applications", f"{info['ID']}.desktop")
        if exists(p):
            if force: delete(p)
            else: return
        print("Installing Desktop Entry")
        desktop = f'[Desktop Entry]\nType=Application\nName={info["Name"]}\nComment={info["Comment"]}\nIcon={info["ID"]}\nTerminal=false\nExec={info["Binary"]}'
        if info["MimeTypes"]:
            desktop += f" %U"
        for i in ("Categories", "Keywords", "MimeTypes"):
            if info[i]:
                desktop += f"\n{i.strip('s') if i == 'MimeTypes' else i}={';'.join(info[i])}"
        set_contents(p, contents=desktop)

    @staticmethod
    def do_metainfo(info):
        print("Installing MetaInfo")
        d = "<?xml version='1.0' encoding='utf-8'?><component type='desktop-application'><metadata_license>MIT</metadata_license>"
        for i in ("ID", "Name"):
            d += f"<{i.lower()}>{info[i]}</{i.lower()}>"
        d += "<branding>"
        for n, i in enumerate(info["Branding"]):
            d += f"<color type='primary' scheme_preference='{'light' if n == 0 else 'dark'}'>{i}</color>"
        d += f"</branding><description>{info['Description']}</description><developer id='{info['ID'].rsplit('.', 1)[0]}'><name>{info['Developer']}</name></developer>"
        for t in ("Keywords", "Categories"):
            d += f"<{t.lower()}>"
            tt = t.replace("ies", "y").rstrip("s").lower()
            for i in info[t]:
                d += f"<{tt}>{i}</{tt}>"
            d += f"</{t.lower()}>"
        d += f"<summary>{info['Comment']}</summary><launchable type='desktop-id'>{info['ID']}.desktop</launchable><icon type='stock'>{info['ID']}</icon><url type='bugtracker'>{info['Issue URL']}</url>"
        for i in ("homepage", "vcs-browser"):
            d += f"<url type='{i}'>{info['Website']}</url>"
        d += "<content_rating type='oars-1.1'>"
        for i in info["Ratings"]:
            for k, v in i:
                d += f"<content_attribute id='{k}'>{v}</content_attribute>"
        d += "</content_rating><releases>"
        for i in info["Release Notes"]:
            d += f"<release version='{i[0]}' date='{i[1]}'><description>{i[2]}</description></release>"
        d += "</releases>"
        if info["Screenshots"]:
            d += "<screenshots>"
            for n, i in enumerate(info["Screenshots"]):
                d += "<screenshot{' type=\'default\'' if if n == 0 else ''><image>{i[0]}</image><caption>{i[1]}</caption></screenshot>"
            d += "</screenshots>"
        d += "<supports>"
        for k, v in info["Supports"].items():
            for i in v:
                d += f"<{k}>{i}</{k}>"
        d += f"</supports><provides><binary>{info['Binary']}</binary>"
        for i in info["MimeTypes"]:
            d += f"<mediatype>{i}</mediatype>"
        d += f"</provides><project_license>{info['License'].name.split('_')[0]}</project_license></component>"
        set_contents(Path(SYSTEM_DATA[-1], "metainfo", f"{info['ID']}.metainfo.xml"), contents=d)

    @staticmethod
    def do_app():
        folder = get_parent(__file__)
        pys = [i for i in list_dir(folder) if i.endswith(".py") and not i == "Utils.py"]
        for i in pys:
            print(f"Installing {i.rstrip('.py')}")
            copy(Path(folder, i), Path(PACKAGE_LOCATION, i))

    @staticmethod
    def do_bin(info):
        print("Installing Binary")
        folder = get_parent(__file__)
        pys = [i for i in list_dir(folder) if i.endswith(".py") and not i == "Utils.py"]
        file = next((i.rstrip(".py") for i in pys if i.rstrip(".py").lower() == info["Binary"].lower()), False)
        if not file: return
        c = f"#!/bin/python\nfrom {file} import main\nmain()"
        f = Path(Install.bin_location, info["Binary"])
        set_contents(f, contents=c)
        GLib.chmod(f, 0o755)

    @staticmethod
    def do_utils():
        print("Installing Utils")
        folder = get_parent(__file__)
        f = "Utils.py"
        copy(Path(folder, f), Path(PACKAGE_LOCATION, f))

    @staticmethod
    def do_icons():
        print("Installing Icons")
        folder = get_parent(__file__)
        icons = [i for i in list_dir(folder) if i.endswith(".svg")]
        if not icons: return
        icons_folder = Path(SYSTEM_DATA[-1], "icons", "hicolor")
        app_icon = next((i for i in icons if i.startswith(App.default.get_application_id()) and not i.endswith("-symbolic.svg")), None) if App.default else None
        app_symbolic_icon = next((i for i in icons if i.startswith(App.default.get_application_id()) and i.endswith("-symbolic.svg")), None) if App.default else None
        icons = [i for i in icons if not i == app_icon and not i == app_symbolic_icon]
        symbolic = [i for i in icons if i.endswith("-symbolic.svg")]
        normal = [i for i in icons if not i in symbolic]
        if app_icon: copy(Path(folder, app_icon), Path(icons_folder, "scalable", "apps", app_icon))
        if app_symbolic_icon: copy(Path(folder, app_symbolic_icon), Path(icons_folder, "symbolic", "apps", app_symbolic_icon))
        if FLATPAK_ID:
            icons_folder = Path(SYSTEM_DATA[-1], App.default.get_application_id())
            for i in normal: copy(Path(folder, i), Path(icons_folder, i))
            for i in symbolic: copy(Path(folder, i), Path(icons_folder, i))
        else:
            for i in symbolic: copy(Path(folder, i), Path(icons_folder, "symbolic", "actions", i))
            for i in normal: copy(Path(folder, i), Path(icons_folder, "scalable", "actions", i))

def handle_scroll(content, v, s, page=True):
    if hasattr(content, "get_hadjustment") and hasattr(content, "get_vadjustment") and (s == 0 or Gdk.ModifierType.SHIFT_MASK in s) and v in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_a, Gdk.KEY_d, Gdk.KEY_w, Gdk.KEY_s, Gdk.KEY_A, Gdk.KEY_D, Gdk.KEY_W, Gdk.KEY_S):
        a = content.get_hadjustment() if v in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_a, Gdk.KEY_d, Gdk.KEY_A, Gdk.KEY_D) else content.get_vadjustment()
        if not hasattr(a, "anim"):
            a.anim = Adw.TimedAnimation(widget=content, duration=200, target=Adw.NoneAnimationTarget())
        a.anim.set_duration(200)
        a.anim.set_target(Adw.PropertyAnimationTarget.new(a, "value"))
        a.anim.set_value_from(a.get_value())
        va = a.get_page_increment() if page and Gdk.ModifierType.SHIFT_MASK in s else a.get_step_increment()
        a.anim.set_value_to((-va if v in (Gdk.KEY_Left, Gdk.KEY_Up, Gdk.KEY_a, Gdk.KEY_w, Gdk.KEY_A, Gdk.KEY_W) else va) + a.get_value())
        a.anim.play()
        return True
    return False

handle_scroll.shortcuts = {
    "Move Up": "w Up",
    "Move Down": "s Down",
    "Move Left": "a Left",
    "Move Right": "d Right",
}

Adw.TabView.shortcuts = {
    "Next Page": "<primary>Tab <primary>Page_Down",
    "Previous Page": "<primary><shift>Tab <primary>Page_Up",
    "First Page": "<primary>Home",
    "Last Page": "<primary>End",
    "Shift Page Up": "<primary><shift>Page_Up",
    "Shift Page Down": "<primary><shift>Page_Down",
    "Shift Page Home": "<primary><shift>Home",
    "Shift Page End": "<primary><shift>End",
    "Switch to Page 1-9": "<alt>1...9",
    "Switch to Page 10": "<alt>0",
}

def unique_name(i, d, suffix=" ", start=False, end="", n=1):
    new = i
    if start:
        new = f"{i}{suffix}{n}{end}"
    if isinstance(d, Gio.File):
        new = d.get_child(i + end)
        while exists(new):
            n += 1
            new = d.get_child(f"{i}{suffix}{n}{end}")
    else:
        while new in d:
            n += 1
            new = f"{i}{suffix}{n}{end}"
    return new

def after_launch(a, r):
    try:
        r = a.launch_finish(r)
    except Exception as e:
        r = False
        if not "dismissed" in str(e): Toast(str(e).split(":", 1)[-1])
    print("Launched" if r else "Could not launch", a.get_uri() if isinstance(a, Gtk.UriLauncher) else a.get_file())

def launch(arg, folder=False, ask=True):
    if isinstance(arg, str):
        if "://" in arg:
            arg = Gio.File.new_for_uri(arg)
        else:
            arg = Gio.File.new_for_path(arg)
    f = Gtk.FileLauncher.new()
    f.set_always_ask(ask)
    f.set_file(arg)
    if not FLATPAK_ID and folder: f.open_containing_folder()
    else: f.launch(callback=after_launch)

def get_paintable(value):
    if hasattr(value, "get_custom_image"):
        value = value.get_custom_image()
    if hasattr(value, "get_media_stream"):
        value = value.get_media_stream()
    if hasattr(value, "get_paintable"):
        value = value.get_paintable()
    if hasattr(value, "get_static_image"):
        value = value.get_static_image() or value
    if hasattr(value, "get_current_image"):
        value = value.get_current_image() or value
    return value

def paintable_to_value(v):
    if not isinstance(v, Gdk.Texture):
        v = get_paintable(v)
        if v:
            v = paintable_get_texture(v)
    if v: return Gdk.ContentProvider.new_for_bytes("image/png", v.save_to_png_bytes())

def paintable_get_texture(v):
    snapshot = Gtk.Snapshot.new()
    v.snapshot(snapshot, v.get_intrinsic_width(), v.get_intrinsic_height())
    v = snapshot.to_node()
    Palette.do_renderer()
    v = Palette.renderer.render_texture(v)
    return v

def clamp_size(size, w, h):
    if size and max(w, h) > size:
        s = size / max(w, h)
        w, h = int(w * s), int(h * s)
    return (w, h)

def get_adjacent_items(lst, item):
    if item not in lst:
        return None, None
    index = lst.index(item)
    previous_item = lst[index - 1] if index > 0 else None
    next_item = lst[index + 1] if index < len(lst) - 1 else None
    return previous_item, next_item

def Path(*args):
    if not args: return SEPARATOR
    s = get_path(args[0]).rstrip(SEPARATOR) + SEPARATOR
    if SEPARATOR == "/" and not s.startswith(SEPARATOR):
        s = SEPARATOR + s
    for i in args[1:]:
        if not i or i == SEPARATOR: continue
        s += get_path(i).strip(SEPARATOR) + SEPARATOR
    return s.rstrip(SEPARATOR) or SEPARATOR

def basename(*args):
    for i in args:
        p = get_path(i).rstrip(SEPARATOR)
        return p.rsplit(SEPARATOR, 1)[-1] or p

def get_parent(*args, path=True):
    for i in args: return ("" if path else (i.split("://", 1)[0] + "://")) + (get_path(i) if path else i.split("://", 1)[-1]).rsplit(SEPARATOR, 1)[0]

def get_path(i):
    if isinstance(i, str): return uri_decode(i) if "://" in i else i
    if isinstance(i, Gio.File): return i.get_path()

def exists(*args):
    for i in args: return Gio.File.new_for_path(get_path(i)).query_exists()

def is_dir(*args, cancellable=None): return all(Gio.File.new_for_path(get_path(i)).query_file_type(Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, cancellable) == Gio.FileType.DIRECTORY for i in args)
def is_link(*args, cancellable=None): return all(Gio.File.new_for_path(get_path(i)).query_file_type(Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, cancellable) == Gio.FileType.SYMBOLIC_LINK for i in args)

def set_contents(*args, contents=""):
    for i in args:
        parent = get_parent(get_path(i))
        if not exists(parent): make_dirs(parent)
        GLib.file_set_contents(get_path(i), contents.encode("utf-8") if isinstance(contents, str) else contents)

def get_contents(*args, b=False):
    for i in args: return Gio.File.new_for_path(get_path(i)).load_bytes()[0] if b else GLib.file_get_contents(get_path(i))[-1]

def delete(*args, cancellable=None, progress=None):
    Progress.start(progress)
    for i in args:
        if cancellable and cancellable.is_cancelled(): return
        i = get_path(i)
        if is_dir(i):
            for it in list_dir(i): delete(Path(i, it), cancellable=cancellable, progress=progress)
            Gio.File.new_for_path(i).delete(cancellable)
        else:
            Gio.File.new_for_path(i).delete(cancellable)
    Progress.stop(progress)

def copy(o, n, cancellable=None, progress=None):
    if cancellable and cancellable.is_cancelled(): return
    Progress.start(progress)
    o, n = get_path(o), get_path(n)
    if is_dir(o):
        for i in list_dir(o): copy(Path(o, i), Path(n, i), cancellable=cancellable, progress=progress)
    else:
        n = Gio.File.new_for_path(n)
        make_dirs(n.get_parent())
        Gio.File.new_for_path(o).copy(n, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, cancellable)
    Progress.stop(progress)

def make_dirs(*args, exist_ok=True):
    for i in args:
        if exists(i) and exist_ok: continue
        Gio.File.new_for_path(get_path(i)).make_directory_with_parents()

def list_dir(*args):
    for i in args:
        d = GLib.Dir.open(get_path(i), 0)
        l = []
        while not l or l[-1] != None: l.append(d.read_name())
        d.close()
        return l[:-1]

def walk(*args, t=False, sort=None):
    sort = sort or alphabetical_sort
    if t == False:
        t = []
    for i in args:
        r = get_path(i)
        l = sorted(list_dir(r), key=sort)
        d = sorted([it for it in l if is_dir(Path(r, it))], key=sort)
        t.append((r, d, [it for it in l if not it in d]))
        for it in d: walk(Path(r, it), t=t, sort=sort)
    return t

def format_time(nanoseconds):
    if nanoseconds is None or nanoseconds < 0: return "--:--"
    try:
        from gi.repository import Gst
        seconds = nanoseconds // Gst.SECOND
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        if hours: return f"{hours}:{minutes:02}:{seconds:02}"
        return f"{minutes}:{seconds:02}"
    except: return "--:--"

def format_duration(seconds=0):
    seconds = int(seconds)
    seconds_in_minute = 60
    seconds_in_hour = 60 * seconds_in_minute
    seconds_in_day = 24 * seconds_in_hour
    seconds_in_month = 30 * seconds_in_day
    seconds_in_year = 365 * seconds_in_day
    years, remainder = divmod(seconds, seconds_in_year)
    months, remainder = divmod(remainder, seconds_in_month)
    days, remainder = divmod(remainder, seconds_in_day)
    hours, remainder = divmod(remainder, seconds_in_hour)
    minutes, seconds = divmod(remainder, seconds_in_minute)
    parts = []
    if years: parts.append(f"{years} year" if years == 1 else f"{years} years")
    if months: parts.append(f"{months} month" if months == 1 else f"{months} months")
    if days: parts.append(f"{days} day" if days == 1 else f"{days} days")
    if hours: parts.append(f"{hours} hour" if hours == 1 else f"{hours} hours")
    if minutes: parts.append(f"{minutes} minute" if minutes == 1 else f"{minutes} minutes")
    if seconds: parts.append(f"{seconds} second" if seconds == 1 else f"{seconds} seconds")
    if not parts: return "0 seconds"
    return human_readable_join(parts)

def get_enum(enum): return dict((i, getattr(enum, i)) for i in dir(enum) if i.isupper() and isinstance(getattr(enum, i), int))
def list_to_str(s=[]): return "\n".join([i.strip() for i in s if i.strip()])
def str_to_list(s=""): return [i.strip() for i in (s or "").split("\n") if i.strip()]
def has_parent(a, b): return get_parent(a.rstrip("/")).rstrip("/") == b.rstrip("/")
def has_prefix(a, b): return a.startswith(b.rstrip("/") + "/")
def uri_decode(i):
    v = i
    if i.startswith("file://"):
        try:
            v = GLib.filename_from_uri(i)[0]
        except:
            v = GLib.Uri.unescape_string(i).split("file://", 1)[-1]
    else:
        v = GLib.Uri.unescape_string(i)
    return v
def uri_encode(i):
    if not i.startswith("/"): return "file://" + GLib.Uri.escape_string(i)
    return GLib.filename_to_uri(i)
def uri_relative_encode(i):
    if not "://" in i and not i.startswith("/"): return uri_encode(Path(App.default.data_folder if App.default and App.default.data_folder else DATA, i))
    if i.startswith("file://") and not i.startswith("file:///"): return uri_encode(Path(App.default.data_folder if App.default and App.default.data_folder else DATA)).rstrip("/") + "/" + i.split("file://", 1)[-1]
    return i if "://" in i else uri_encode(i)
def file_str(i): return i.get_path() or i.get_uri()
def file_repr(i): return f"Gio.File ({i.get_path() or i.get_uri()})"
def human_readable_join(l): return " and ".join(", ".join(l).rsplit(", ", 1))
def split_into_n(lst, n): return [lst[i:i + n] for i in range(0, len(lst), n)]
def str_to_int(i): return int(i) if type(i) == str and i.isdigit() else 0
def unpack_bind(b, v): return v.unpack()
def bool_bind(b, v): return bool(v)
def int_bind(b, v): return int(v)
def str_bind(b, v): return str(v)
def invert_bool_bind(b, v): return not bool(v)
def invert_unpack_bind(b, v): return not v.unpack()
def selected_bind(b, v):
    target = b.get_property("target")
    model = target if isinstance(target, Gio.ListModel) else target.get_model() if hasattr(target, "get_model") else False
    if not model: return Gtk.INVALID_LIST_POSITION
    return model.find(v)
def selected_item_bind(b, v):
    model = False
    for i in ("target", "source"):
        target = b.get_property(i)
        model = target if isinstance(target, Gio.ListModel) else target.get_model() if hasattr(target, "get_model") else False
        if model: break
    if type(v) == int:
        if not model: return ""
        i = model.get_item(v)
        if isinstance(i, Gtk.StringObject):
            i = i.get_string()
        return i
    else:
        if not model: return Gtk.INVALID_LIST_POSITION
        return model.find(v)
def get_variant(v): return GLib.Variant("d" if type(v) == float else "s" if type(v) == str else "i" if type(v) == int else "b", v)
def variant_bind(b, v): return get_variant(v)
def convert_bind(b, v): return v
def alphabetical_sort(e): return GLib.utf8_collate_key_for_filename(e.get_path() if isinstance(e, Gio.File) else str(e), -1)
def datetime_sort(i): return i.to_unix()
def current_page(n, l): return (n - 1) // l + 1
def total_pages(t, l): return (t + l - 1) // l
def is_online(i): return (i.get_uri() if isinstance(i, Gio.File) else i).startswith(("http://", "https://"))
def print_args(*args): print(args)
def return_0(*_): return 0
def return_1(*_): return 1
def return_random_negative_500(*_): return GLib.random_int_range(-500, 500)
def return_random_500(*_): return GLib.random_int_range(0, 500)
def return_false(*_): return False
def return_true(*_): return True
def nothing(*_): return

def get_all_dialog():
    l = set()
    for i in Gtk.Window.list_toplevels():
        if hasattr(i, "get_visible_dialog") and i.get_visible_dialog(): l.add(i.get_visible_dialog())
        if hasattr(i, "get_child") and isinstance(i.get_child(), Adw.Dialog): l.add(i.get_child())
    return list(l)

Adw.Dialog.get_all = staticmethod(get_all_dialog)

Gio.File.__str__ = file_str
Gio.File.__repr__ = file_repr

VersionInfo = sys.version_info
ARGS = sys.argv

SEPARATOR = GLib.DIR_SEPARATOR_S
HERE = GLib.get_current_dir()
HOME = GLib.get_home_dir()
SYSTEM_DATA = GLib.get_system_data_dirs()
SYSTEM_CONFIG = GLib.get_system_config_dirs()
DATA = GLib.get_user_data_dir()
CONFIG = GLib.get_user_config_dir()
CACHE = GLib.get_user_cache_dir()
TRASH = Path(DATA, "Trash")
FLATPAK_ID = GLib.getenv("FLATPAK_ID")

if not GLib.getenv("GST_PLUGIN_FEATURE_RANK"): GLib.setenv("GST_PLUGIN_FEATURE_RANK", "gifdec:0,avdec_gif:256")

DEFAULT_BIND = GObject.BindingFlags.DEFAULT | GObject.BindingFlags.SYNC_CREATE
SYNC_BIND = DEFAULT_BIND | GObject.BindingFlags.BIDIRECTIONAL
INVERT_BIND = DEFAULT_BIND | GObject.BindingFlags.INVERT_BOOLEAN

PACKAGE_LOCATION = Path("lib", f"python{VersionInfo.major}.{VersionInfo.minor}", "site-packages")

def do_gst():
    try:
        global Gst
        from gi.repository import Gst
        if not Gst.is_initialized(): Gst.init()
        if not MediaStream.PAINTABLE_SINK:
            MediaStream.PAINTABLE_SINK = Gst.ElementFactory.find("gtk4paintablesink")
    except:
        Gst = False

class Log(Adw.ApplicationWindow):
    __gtype_name__ = "Log"
    instance = None
    box = GObject.Property(type=object)
    opened = GObject.Property(type=bool, default=False)
    empty = GObject.Property(type=bool, default=True)
    command = GObject.Property(type=Gtk.TextView)
    time = GObject.Property(type=Gtk.Button)
    def __init__(self):
        super().__init__(hide_on_close=True, visible=False, title="Log", default_width=360, default_height=364, content=Adw.ToastOverlay())
        self.time = Button(t=Gtk.ToggleButton, tooltip_text="Time", icon_name="preferences-system-time")
        Window.controller_init(self)
        self.__class__.instance = self
        self.add_css_class("log")
        from re import compile as Regex
        self.__class__.ansi_escape = Regex(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        overlay = Gtk.Overlay(child=Gtk.ScrolledWindow(child=self.box))
        empty = Adw.StatusPage(title="Empty", icon_name="preferences-system-notifications-symbolic")
        self.bind_property("empty", empty, "visible", DEFAULT_BIND)
        toolbar = Adw.ToolbarView(vexpand=True, content=overlay)
        overlay.add_overlay(empty)
        header = Adw.HeaderBar()
        d = dict((i, "win." + i.lower().replace(" ", "-")) for i in ("Toast", "Online", "Task", "Palette", "MediaStream", "PaintableTask"))
        m = Menu(d)
        f = get_variant(False)
        for i in d: self.add_action(Gio.SimpleAction.new_stateful(i.lower().replace(" ", "-"), None, f))
        header.pack_start(self.time)
        header.pack_end(Button(t=Gtk.MenuButton, css_classes=("flat",), icon_name="open-menu", menu_model=m, tooltip_text="Options"))
        header.pack_end(Button(callback=self.__class__.clear_all, tooltip_text="Clear All", icon_name="edit-clear-all", css_classes=("flat", "circular", "destructive-action",)))
        toolbar.add_top_bar(header)
        if getattr(sys, "stderr", False):
            self.__class__.stderr = sys.stderr
            sys.stderr.original_write = sys.stderr.write
            sys.stderr.write = self.__class__.add_error
        if getattr(sys, "stdout", False):
            self.__class__.stdout = sys.stdout
            sys.stdout.original_write = sys.stdout.write
            sys.stdout.write = self.__class__.add_out
        if getattr(sys, "excepthook", False):
            self.__class__.default_excepthook = sys.excepthook
            sys.excepthook = self.__class__.handle_error
        self.command = Gtk.TextView(valign=Gtk.Align.END)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(toolbar)
        box.append(self.command)
        self.get_content().set_child(box)

    @staticmethod
    def on_key_pressed(e, v, c, s):
        if v == Gdk.KEY_Return and not Gdk.ModifierType.SHIFT_MASK in s:
            g = globals()
            from inspect import currentframe
            frame = currentframe()
            n = 0
            while frame and 4 > n:
                n += 1
                g = frame.f_globals
                frame = frame.f_back
            exec(e.get_widget().command.get_buffer().get_property("text"), g, g)
            return True

    @staticmethod
    def handle_error(*args):
        Log.default_excepthook(*args)
        Log.instance.present()

    @classproperty
    def palette(*_): return Log.instance.lookup_action("palette").get_state().unpack() if Log.instance else False

    @classproperty
    def online(*_): return Log.instance.lookup_action("online").get_state().unpack() if Log.instance else False

    @classproperty
    def toast(*_): return Log.instance.lookup_action("toast").get_state().unpack() if Log.instance else False

    @classproperty
    def task(*_): return Log.instance.lookup_action("task").get_state().unpack() if Log.instance else False

    @classproperty
    def mediastream(*_): return Log.instance.lookup_action("mediastream").get_state().unpack() if Log.instance else False

    @classproperty
    def paintabletask(*_): return Log.instance.lookup_action("paintabletask").get_state().unpack() if Log.instance else False

    @staticmethod
    def clear_all(b):
        self = b.get_root()
        while self.box.get_first_child(): self.box.get_first_child().unparent()
        self.empty = True

    def text_to_label(self, text, **kwargs):
        box = Gtk.Box(**kwargs)
        text = Log.ansi_escape.sub("", text)
        t = Gtk.Label(css_name="time", yalign=0, xalign=0, label=GLib.DateTime.new_now_local().format("%T"), selectable=True)
        box.append(t)
        self.time.bind_property("active", t, "visible", DEFAULT_BIND)
        box.append(Gtk.Label(css_name="message",  yalign=0, xalign=0, label=text, wrap=True, selectable=True))
        return box

    @staticmethod
    def add_error(text):
        self = Log.instance
        if getattr(self, "stderr", False): Task.invoke(self.stderr.original_write, text, log=False)
        if text.strip():
            Task.invoke(self.check_size)
            Task.invoke(setattr, self, "empty", False)
            Task.invoke(self.box.append, self.text_to_label(text.strip(), css_name="error"), log=False)
            Task.invoke(self.present, log=False)

    def check_size(self):
        if self.box and len([i for i in self.box]) > 100: self.box.remove(self.box.get_first_child())

    @staticmethod
    def add_out(text):
        self = Log.instance
        if getattr(self, "stdout", False): Task.invoke(self.stdout.original_write, text, log=False)
        if text.strip():
            Task.invoke(self.check_size)
            Task.invoke(setattr, self, "empty", False)
            Task.invoke(self.box.append, self.text_to_label(text.strip(), css_name="log"), log=False)
            if self.opened and not self.get_visible(): Task.invoke(self.present, log=False)

if not hasattr(Adw, "ToggleGroup"):
    class Toggle(GObject.Object):
        __gtype_name__ = "Toggle"
        child = GObject.Property(type=Gtk.Widget)
        description = GObject.Property(type=str)
        enabled = GObject.Property(type=bool, default=True)
        icon_name = GObject.Property(type=str)
        label = GObject.Property(type=str)
        name = GObject.Property(type=str)
        tooltip = GObject.Property(type=str)
        use_underline = GObject.Property(type=bool, default=False)
        def __init__(self, **kwargs): super().__init__(**kwargs)
    Adw.Toggle = Toggle

    class ToggleButton(Gtk.ToggleButton):
        @GObject.Property(type=Adw.Toggle)
        def toggle(self): return getattr(self, "_toggle", None)

        @toggle.setter
        def toggle(self, value):
            self._toggle = value
            for i in self.bindings: i.unbind()
            self.bindings = []
            icon, label, child = [i for i in self.get_child()]
            if self.toggle:
                self.bindings.append(self.toggle.bind_property("icon-name", icon, "icon-name", DEFAULT_BIND))
                self.bindings.append(self.toggle.bind_property("label", label, "label", DEFAULT_BIND))
                self.bindings.append(self.toggle.bind_property("child", child, "child", DEFAULT_BIND))
                self.bindings.append(self.toggle.bind_property("tooltip", self, "tooltip-text", DEFAULT_BIND))
                self.bindings.append(self.toggle.bind_property("enabled", self, "sensitive", DEFAULT_BIND))
                self.bindings.append(self.toggle.bind_property("use-underline", self, "use-underline", DEFAULT_BIND))
                self.bindings.append(self.toggle.bind_property("name", self, "name", DEFAULT_BIND))

        def __init__(self, toggle=None):
            box = Gtk.Box(halign=Gtk.Align.CENTER)
            super().__init__(css_name="toggle", css_classes=["toggle"], child=box)
            self.bindings = []
            icon = Gtk.Image()
            icon.bind_property("icon-name", icon, "visible", DEFAULT_BIND, bool_bind)
            label = Gtk.Label()
            label.bind_property("label", label, "visible", DEFAULT_BIND, bool_bind)
            child = Adw.Bin()
            child.bind_property("child", child, "visible", DEFAULT_BIND, bool_bind)
            for i in (icon, label, child): box.append(i)
            self.toggle = toggle
            self.connect("notify::active", self.__class__.changed)

        @staticmethod
        def changed(self, p=None):
            group = self.get_parent()
            if not group or group.changing: return
            Task.invoke(group.set_active, [i for i in group.get_toggles()].index(self))

    class ToggleGroup(Gtk.Box):
        __gtype_name__ = "ToggleGroup"

        @GObject.Property(type=float)
        def active(self): return getattr(self, "_active", Gtk.INVALID_LIST_POSITION)

        @active.setter
        def active(self, value):
            self.changing = True
            self._active = value
            for n, i in enumerate(self.get_toggles()): i.set_active(n == value)
            self.changing = False

        @GObject.Property(type=str)
        def active_name(self):
            if not self.toggles or self.active == Gtk.INVALID_LIST_POSITION or self.active > len(self.toggles): return None
            return self.toggles[int(self.active)].get_property("name")

        @active_name.setter
        def active_name(self, value):
            if not value: return self.set_active(Gtk.INVALID_LIST_POSITION)
            for n, i in enumerate(self.toggles):
                if i.get_property("name") == value: return self.set_active(n)
            self.set_active(Gtk.INVALID_LIST_POSITION)

        can_shrink = GObject.Property(type=bool, default=True)

        def __init__(self, **kwargs):
            super().__init__(css_name="toggle-group", **kwargs)
            self.toggles = []

        def do(self):
            while self.get_first_child(): self.get_first_child().unparent()
            self._active = Gtk.INVALID_LIST_POSITION
            if not self.toggles: return
            for i in self.toggles: Gtk.Box.append(self, ToggleButton(toggle=i))

        def add(self, t=None):
            if isinstance(t, Gtk.Widget): return Gtk.Box.append(self, t)
            if not isinstance(t, Adw.Toggle): return
            self.toggles.append(t)
            self.do()

        def remove(self, t=None):
            if isinstance(t, Gtk.Widget):
                if hasattr(t, "bindings"):
                    for i in t.bindings: i.unbind()
                    del t.bindings
                return Gtk.Box.remove(self, t)
            if not isinstance(t, Adw.Toggle): return
            if t in self.toggles: self.toggles.remove(t)
            self.do()

        def remove_all(self):
            self.toggles = []
            self.do()

        def get_toggles(self): return [i for i in self if isinstance(i, ToggleButton)]

        def set_active(self, n): self.set_property("active", n)
        def set_active_name(self, n): self.set_property("active-name", n)
        def get_active(self): return self.get_property("active")
        def get_active_name(self): return self.get_property("active-name")

    Adw.ToggleGroup = ToggleGroup

    class InlineViewSwitcher(Adw.Bin):
        __gtype_name__ = "InlineViewSwitcher"
        display_mode = GObject.Property(type=int)

        @GObject.Property(type=Adw.ViewStack)
        def stack(self): return getattr(self, "_stack", None)

        @stack.setter
        def stack(self, value):
            if self.stack == value: return
            if self.stack:
                for n in range(2):
                    s = f"viewswitcher_signal_{n + 1}"
                    if hasattr(self.stack, s): self.stack.get_pages().disconnect(getattr(self.stack, s))
            self._stack = value
            if self.stack:
                self.stack.viewswitcher_signal_1 = self.stack.get_pages().connect("items-changed", self.__class__.do, self)
                self.stack.viewswitcher_signal_2 = self.stack.connect("notify::visible-child", self.__class__.visible_changed, self)
            Task.invoke(self.do)

        def __init__(self, stack=None, display_mode=1, **kwargs):
            super().__init__(css_name="inline-view-switcher", child=Adw.ToggleGroup(), **kwargs)
            self.bindings = []
            self.get_child().connect("notify::active", self.__class__.do_change)
            self.display_mode = display_mode
            self.stack = stack
            self.connect("notify::display-mode", self.__class__.do)

        @staticmethod
        def visible_changed(stack, b, self):
            visible = stack.get_visible_child_name()
            group = self.get_child()
            group.changing = True
            for i in group.get_toggles(): i.set_active(i.toggle.get_property("name") == visible)
            group.changing = False

        @staticmethod
        def do_change(group, c=None):
            self = group.get_parent()
            if not self or not self.stack: return
            if group.active != Gtk.INVALID_LIST_POSITION:
                pages = [i for i in self.stack.get_pages()]
                self.stack.set_visible_child(pages[int(group.active)].get_child())

        def do(self, a=None, c=None):
            for i in self.bindings: i.unbind()
            self.bindings = []
            if c:
                self = c
            d = "labels" if self.display_mode == Adw.InlineViewSwitcherDisplayMode.LABELS else "icons" if self.display_mode == Adw.InlineViewSwitcherDisplayMode.ICONS else "both"
            self.get_child().set_css_classes([d])
            l = []
            selected_n = Gtk.INVALID_LIST_POSITION
            if self.stack:
                for n, i in enumerate(self.stack.get_pages()):
                    t = Adw.Toggle(name=i.get_name())
                    if d == "icons": self.bindings.append(i.bind_property("title", t, "tooltip", DEFAULT_BIND))
                    if d in ("icons", "both"): self.bindings.append(i.bind_property("icon-name", t, "icon-name", DEFAULT_BIND))
                    if d in ("labels", "both"): self.bindings.append(i.bind_property("title", t, "label", DEFAULT_BIND))
                    l.append(t)
                    if self.stack.get_pages().is_selected(n):
                        selected_n = n
            if hasattr(self.get_child(), "do"):
                self.get_child().toggles = l
                self.get_child().do()
                if  d in ("labels", "both"):
                    for i in self.get_child().get_toggles():
                        if not i == self.get_child().get_last_child(): self.get_child().insert_child_after(Gtk.Separator(), i)
            else:
                self.get_child().remove_all()
                for i in l: self.get_child().add(i)
            self.get_child().set_active(selected_n)

    Adw.InlineViewSwitcherDisplayMode = Adw.InlineViewSwitcher = InlineViewSwitcher
    Adw.InlineViewSwitcherDisplayMode.LABELS = 0
    Adw.InlineViewSwitcherDisplayMode.ICONS = 1
    Adw.InlineViewSwitcherDisplayMode.BOTH = 2
    App.UTILS_CSS += """
    toggle-group { border-spacing: 2px; }
    toggle-group:not(.icons) > toggle > box { border-spacing: 6px; }
    toggle-group:not(.icons) > toggle { padding: 0px 10px; }
    """.lstrip()

App.UTILS_CSS += """
:root { --color-1: var(--window-bg-color); --color-2: var(--window-bg-color); --color-3: var(--window-bg-color); }
toolbarview.fullscreen .bottom-bar > windowhandle > box,
toolbarview.fullscreen .top-bar > windowhandle > box { background: color-mix(in srgb, #222226 35%, transparent); color: white; }
masonry view { border-spacing: 10px; margin: 10px; }
masonry view > column { border-spacing: 10px; }
.entry-prompt entry text { margin: 12px 5px 12px 5px; }
.log textview,
.entry-prompt textview { background: color-mix(in srgb, var(--window-fg-color) 8%, transparent); border-radius: 10px; padding: 12px; font-size: 16px; }
.entry-prompt .message-area  { border-spacing: 16px; }
media picture, masonry media, masonry picture { border-radius: 13px; }

mediacontrols { border-radius: 10px; margin: 6px; padding: 6px; background: var(--card-bg-color); border-spacing: 6px; }
mediacontrols > scale { padding: 6px 0px; }
media > mediacontrols { opacity: 0; transition: opacity 0.5s; color: white; background: color-mix(in srgb, #222226 35%, transparent); }

toolbarview.fullscreen .top-bar > windowhandle > box,
media > mediacontrols { backdrop-filter: blur(20px) brightness(0.9); }

media > mediacontrols:hover { opacity: 1; }

dragtag { background: color-mix(in srgb, var(--window-fg-color) 20%, transparent); color: inherit; border-radius: 99px; padding: 6px 12px 6px 12px; }
row.tags:only-child > button { border-radius: 12px; }
row.tags:first-child > button { border-radius: 12px 12px 0px 0px; }
row.tags:last-child > button { border-radius: 0px 0px 12px 12px; }
row.tags { padding: 0px; }
row.tags > button { padding: 0px; border-radius: 0px; min-height: 52px; }
row.tags > button > box { border-spacing: 8px; }
row.tags.drag-move wrap-box { box-shadow: none; }
row.tags wrap-box { padding: 9px; }
tag { background: color-mix(in srgb, var(--window-fg-color) 10%, transparent); color: inherit; border-radius: 99px; padding-left: 10px; }
tag remove { margin: 4px; padding: 4px; border-radius: 100px; }
tag,
tag remove { outline: 0 solid transparent; transition: outline-color 200ms cubic-bezier(0.25, 0.46, 0.45, 0.94), outline-width 200ms cubic-bezier(0.25, 0.46, 0.45, 0.94), outline-offset 200ms cubic-bezier(0.25, 0.46, 0.45, 0.94), background-color 200ms; }
tag remove:hover { background: color-mix(in srgb, var(--window-fg-color) 10%, transparent); }
tag:focus,
tag remove:focus { outline-color: color-mix(in srgb, var(--accent-color) 50%, transparent); outline-width: 2px; outline-offset: -2px; }

row.uri { padding: 0px; min-height: 54px; }
row.uri text { margin: 22px 14px 0px 14px; }
row.uri textview { margin: 28px 14px 0px 14px; }
row.uri placeholder { transition: all 0.2s; margin: 20px 0px 0px 14px; opacity: var(--dim-opacity); }
row.uri.has-text placeholder,
row.uri.focused placeholder { margin-top: 12px; font-size: 12px;  }
row.uri button { margin: 12px 12px 0px 0px; background: none; }
row.uri.multiple scrolledwindow scrollbar { padding: 0px; }
row.uri:not(.focused) text,
row.uri:not(.focused) textview { margin-right: 54px; }

row.text { padding: 0px; min-height: 54px; }
row.text textview { padding: 14px; font-size: 16px; }
row.text placeholder { transition: all 0.2s; opacity: var(--dim-opacity); }
row.text.has-text placeholder,
row.text.focused placeholder { opacity: 0; }

menuspinrow { padding: 0px 3px 0px 12px; }
menuspinrow spinbutton { background: none; }
menuspinrow spinbutton button { border: none; }
menucolorrow,
menufontrow { padding: 0px 2px 0px 12px; }
menucolorrow button,
menufontrow button { background: none; }

.log time,
.log message { font-size: 18px; padding-left: 8px; }
.log time { font-weight: bold; }
.log error message { color: var(--error-color); font-weight: bold; }
.log viewport { padding-bottom: 8px; }

row.destructive-action { background: var(--destructive-bg-color); color: white; }

bar-chart { background: none; border-spacing: 4px; }
bar-chart > row { padding: 0px; }
bar-chart > row > bar { background: var(--accent-color); min-width: 30px; }
bar-chart > row:nth-child(even) > bar { opacity: 0.5; }

calendar-heatmap { border-spacing: 4px; }
calendar-heatmap child { padding: 0px; }
calendar-heatmap .day,
calendar-heatmap .pad,
calendar-heatmap .empty { min-height: 16px; min-width: 16px; border-radius: 4px; }
calendar-heatmap .day { background: var(--accent-bg-color); }
calendar-heatmap .empty { background: var(--shade-color); }

donut-chart { min-height: 300px; min-width: 300px; }
donut-chart-legend { border-spacing: 6px; padding: 6px; }
donut-chart-legend item { border-spacing: 4px; }
donut-chart-legend color { min-height: 18px; min-width: 18px; border-radius: 100%; }

row.color colorbutton,
row.color colorbutton > button > colorswatch,
row.color colorbutton > button { padding: 0px; min-width: 0px; min-height: 0px; background: none; }
row.color colorbutton > button > colorswatch { border-radius: 100%; min-height: 38px; }
row.color button.color { border-radius: 100%; }

button.timed > widget { margin: 8px; }
.log textview { margin: 6px; }

box.groups { border-spacing: 24px; padding: 2px 24px 24px 24px; }

window.alert.dialog-window,
window.alert { min-height: 0px; }

.autocomplete scrolledwindow scrollbar,
.autocomplete listview { padding: 0px; }
.autocomplete listview row { margin: 0px 0px 2px 0px; }
.autocomplete listview row:only-child,
.autocomplete listview row:last-child { margin: 0px; }
""".strip()