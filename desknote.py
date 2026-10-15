#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
悬浮便签 DeskNote v1.0
======================
功能：
  1. 悬浮便签：无边框、圆角、置顶显示在桌面，可拖动、可调整大小
  2. 多便签：随时新建 / 删除，每个便签独立保存
  3. 颜色主题：黄 / 绿 / 粉 / 蓝 / 紫 / 橙 六色自由切换
  4. 半透明：6 档透明度调节
  5. 贴边隐藏：拖到屏幕边缘自动收起，鼠标移到边缘自动弹出
  6. 开机自启：一键写入注册表，开机静默运行（无黑窗口）
  7. 系统托盘：右键菜单集中管理（新建 / 显示 / 隐藏 / 自启开关 / 退出）
  8. 数据自动保存：内容、位置、大小、颜色全部记住，下次启动原样恢复
  9. 单实例保护：重复启动只唤醒已有程序，不会开多个

运行方式：双击「启动便签.bat」即可（pythonw 静默后台运行）。
"""

import json
import os
import socket
import sys
import threading
import time
import uuid
import winreg

import customtkinter as ctk
import tkinter as tk
from tkinter import Menu

APP_NAME = "DeskNote"
APP_TITLE = "悬浮便签"
SINGLE_INSTANCE_PORT = 8765          # 单实例检测端口
EDGE_THRESHOLD = 6                   # 距离屏幕边缘多少像素内视为"贴边"
EDGE_VISIBLE = 3                     # 贴边隐藏后露出的像素（鼠标可捕捉）
RESIZE_GRIP = 14                     # 右下角拖拽调大小手柄边长
MIN_W, MIN_H = 200, 150              # 便签最小尺寸
DEFAULT_W, DEFAULT_H = 280, 300      # 便签默认尺寸
SAVE_DEBOUNCE_MS = 600               # 输入停顿多久后自动保存
EDGE_POLL_MS = 200                   # 鼠标边缘检测轮询间隔

# 数据文件位置：脚本模式跟随脚本；exe 模式跟随 exe 所在目录
# （PyInstaller 打包后 __file__ 指向临时解压目录，必须用 sys.executable）
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DATA_FILE = os.path.join(DATA_DIR, "notes.json")

AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "DeskNote"

# ---------------------------------------------------------------- 颜色主题
# 每个主题：主背景色 / 标题栏背景色 / 文字颜色 / 按钮悬停色
THEMES = {
    "黄": {"bg": "#FFF3B0", "bar": "#FFE066", "text": "#5B4A00", "hover": "#FFD84D"},
    "绿": {"bg": "#CDEFC5", "bar": "#A8E09E", "text": "#1F4A1A", "hover": "#8FD483"},
    "粉": {"bg": "#FBD3E0", "bar": "#F7B6CD", "text": "#6E2336", "hover": "#F39BB8"},
    "蓝": {"bg": "#C9E4FB", "bar": "#A5CFF4", "text": "#14365C", "hover": "#86BCEA"},
    "紫": {"bg": "#E2D5F5", "bar": "#CDB8EC", "text": "#3B2160", "hover": "#B99FE2"},
    "橙": {"bg": "#FFE2C2", "bar": "#FFCE96", "text": "#6B3A00", "hover": "#FFBC6E"},
}
THEME_NAMES = list(THEMES.keys())
ALPHAS = [0.60, 0.70, 0.80, 0.90, 0.95, 1.0]

# Windows 常见字体（供便签文字自定义使用）
FONT_CHOICES = [
    "Microsoft YaHei UI", "Microsoft YaHei", "SimSun", "SimHei",
    "KaiTi", "FangSong", "DengXian", "LiSu", "YouYuan",
    "Microsoft JhengHei", "STXihei", "STKaiti", "STSong", "STFangsong",
    "Consolas", "Courier New", "Arial", "Times New Roman", "Georgia", "Verdana",
]
# 文字颜色预设色板
TEXT_COLOR_PRESETS = [
    "#1A1A1A", "#4D4D4D", "#C62828", "#E65100", "#6D4C41", "#2E7D32",
    "#00695C", "#1565C0", "#6A1B9A", "#AD1457", "#8A6D00", "#FFFFFF",
]


# ================================================================ 数据管理
class DataManager:
    """便签数据的读写：内容、位置、大小、颜色、透明度、贴边状态等。"""

    def __init__(self):
        self.data = {"notes": [], "autostart": False}
        self._load()

    def _load(self):
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and isinstance(loaded.get("notes"), list):
                    self.data = loaded
        except Exception:
            pass  # 数据文件损坏时使用默认空数据

    def save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


# ================================================================ 开机自启
class AutoStart:
    """通过注册表 HKCU\\...\\Run 实现当前用户级开机自启。"""

    @staticmethod
    def _command():
        """生成开机自启命令行：打包成 exe 用 exe 本身，脚本模式用 pythonw 静默运行。"""
        if getattr(sys, "frozen", False):      # PyInstaller 打包后的 exe
            return f'"{sys.executable}"'
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        return f'"{pythonw}" "{os.path.abspath(__file__)}"'

    @classmethod
    def is_enabled(cls):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ)
            value, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
            winreg.CloseKey(key)
            return bool(value)
        except FileNotFoundError:
            return False
        except OSError:
            return False

    @classmethod
    def enable(cls):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, cls._command())
            winreg.CloseKey(key)
            return True
        except OSError:
            return False

    @classmethod
    def disable(cls):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, AUTOSTART_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False


# ================================================================ 托盘图标
def make_tray_icon():
    """用 PIL 画一个黄色便签小图标。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 8, 58, 60], radius=10, fill=(255, 213, 79, 255))
    d.rounded_rectangle([6, 8, 58, 26], radius=10, fill=(255, 200, 40, 255))
    # 顶部小折角
    d.polygon([(42, 8), (58, 8), (58, 24), (42, 24)], fill=(255, 224, 130, 255))
    d.line([(12, 26), (52, 26)], fill=(150, 110, 0, 255), width=3)
    d.line([(12, 36), (52, 36)], fill=(150, 110, 0, 255), width=3)
    d.line([(12, 46), (40, 46)], fill=(150, 110, 0, 255), width=3)
    return img


# ================================================================ 便签窗口
class NoteWindow:
    """一个悬浮便签窗口：无边框、置顶、半透明、可拖动、可调大小、贴边隐藏。"""

    def __init__(self, app, note_id=None, theme="黄"):
        self.app = app
        self.id = note_id or uuid.uuid4().hex[:12]
        self.theme = theme
        self.topmost = True
        self.alpha = 1.0
        self.edge_mode = False
        # 文字自定义：None = 跟随主题色
        self.font_family = "Microsoft YaHei UI"
        self.font_size = 13
        self.text_color = None
        self._edge_state = None        # 贴边隐藏方向 None/'left'/'right'/'top'
        self._hidden_pos = None        # 隐藏前的窗口位置
        self._drag = None              # 拖动状态 (off_x, off_y)
        self._resize = None            # 调大小状态

        self.win = ctk.CTkToplevel(app.root)
        self.win.title(APP_TITLE)
        self.win.overrideredirect(True)          # 去掉系统标题栏，实现无边框悬浮
        self.win.attributes("-topmost", self.topmost)
        self.win.attributes("-alpha", self.alpha)
        self.win.configure(fg_color=self._c("bg"))
        self.win.geometry(f"{DEFAULT_W}x{DEFAULT_H}+100+100")
        self.win.protocol("WM_DELETE_WINDOW", self.hide)

        self._build_ui()

        # 启动贴边检测轮询
        self.win.after(EDGE_POLL_MS, self._poll_edge)

    # ------------------------------------------------------------- 工具
    def _c(self, key):
        """取当前主题的颜色值。"""
        return THEMES[self.theme][key]

    def _text_color(self):
        """返回文字颜色：自定义优先，未自定义时跟随主题色。"""
        return self.text_color if self.text_color else self._c("text")

    def _text_font(self):
        """返回当前文字字体对象。"""
        return ctk.CTkFont(family=self.font_family, size=self.font_size)

    def apply_font(self):
        """应用文字字体/字号/颜色到编辑区。"""
        self.textbox.configure(font=self._text_font(), text_color=self._text_color())

    def _apply_theme(self):
        """切换主题后刷新整个窗口配色。"""
        self.win.configure(fg_color=self._c("bg"))
        self.bar.configure(fg_color=self._c("bar"))
        self.title_label.configure(text_color=self._c("text"))
        self.textbox.configure(fg_color=self._c("bg"), text_color=self._text_color())
        for btn in self.bar_buttons.values():
            btn.configure(fg_color=self._c("bar"), hover_color=self._c("hover"),
                          text_color=self._c("text"))
        self.grip.configure(fg_color=self._c("bg"))

    def _save(self):
        self.app.save()

    # ------------------------------------------------------------- 界面
    def _build_ui(self):
        # 标题栏（兼作拖动区域）
        self.bar = ctk.CTkFrame(self.win, fg_color=self._c("bar"), corner_radius=0,
                                height=32)
        self.bar.pack(fill="x", side="top")
        self.bar.pack_propagate(False)

        self.title_label = ctk.CTkLabel(self.bar, text="便签", font=ctk.CTkFont(size=12, weight="bold"),
                                        text_color=self._c("text"))
        self.title_label.pack(side="left", padx=(8, 0))

        # 标题栏右侧按钮组
        self.bar_buttons = {}
        btns = [
            ("pin", "钉", "置顶开/关"),
            ("edge", "藏", "贴边隐藏开/关"),
            ("alpha", "透", "透明度"),
            ("color", "色", "便签颜色"),
            ("new", "＋", "新建便签"),
            ("hide", "—", "隐藏到托盘"),
            ("del", "×", "删除便签"),
        ]
        for key, text, tip in btns:
            btn = ctk.CTkButton(
                self.bar, text=text, width=26, height=22, corner_radius=6,
                fg_color=self._c("bar"), hover_color=self._c("hover"),
                text_color=self._c("text"), font=ctk.CTkFont(size=11),
                command=lambda k=key: self._on_bar(k),
            )
            btn.pack(side="right", padx=2, pady=5)
            self.bar_buttons[key] = btn

        # 拖动绑定
        for widget in (self.bar, self.title_label):
            widget.bind("<Button-1>", self._start_move)
            widget.bind("<B1-Motion>", self._do_move)
            widget.bind("<ButtonRelease-1>", self._end_move)
        self.bar.bind("<Button-3>", self._popup_menu)

        # 编辑区
        self.textbox = ctk.CTkTextbox(
            self.win, fg_color=self._c("bg"), text_color=self._text_color(),
            font=self._text_font(),
            corner_radius=0, border_width=0, wrap="word",
        )
        self.textbox.pack(fill="both", expand=True, padx=0, pady=0)
        self.textbox.bind("<KeyRelease>", self._on_edit)

        # 右下角调大小手柄
        self.grip = ctk.CTkLabel(self.win, text="⤡", width=RESIZE_GRIP, height=RESIZE_GRIP,
                                 fg_color=self._c("bg"), text_color=self._c("text"),
                                 font=ctk.CTkFont(size=10), corner_radius=0)
        self.grip.place(relx=1.0, rely=1.0, x=-RESIZE_GRIP, y=-RESIZE_GRIP, anchor="se")
        self.grip.bind("<Button-1>", self._start_resize)
        self.grip.bind("<B1-Motion>", self._do_resize)
        self.grip.bind("<ButtonRelease-1>", self._end_resize)

        # 右键菜单（标题栏上）
        self.menu = Menu(self.win, tearoff=0)
        self._build_menu()

    def _build_menu(self):
        self.menu.delete(0, "end")
        self.menu.add_command(label="新建便签", command=self.app.create_note)
        self.menu.add_separator()
        color_menu = Menu(self.menu, tearoff=0)
        for name in THEME_NAMES:
            color_menu.add_command(
                label=f"● {name}" if name == self.theme else name,
                command=lambda n=name: self.set_theme(n),
            )
        self.menu.add_cascade(label="颜色", menu=color_menu)
        alpha_menu = Menu(self.menu, tearoff=0)
        for a in ALPHAS:
            alpha_menu.add_command(
                label=f"{int(a * 100)}%{' ●' if abs(a - self.alpha) < 0.01 else ''}",
                command=lambda v=a: self.set_alpha(v),
            )
        self.menu.add_cascade(label="透明度", menu=alpha_menu)
        self.menu.add_separator()
        self.menu.add_command(label="字体设置…", command=self.open_font_dialog)
        self.menu.add_separator()
        self.menu.add_command(label=f"置顶：{'开' if self.topmost else '关'}",
                              command=self.toggle_topmost)
        self.menu.add_command(label=f"贴边隐藏：{'开' if self.edge_mode else '关'}",
                              command=self.toggle_edge)
        self.menu.add_separator()
        self.menu.add_command(label="隐藏到托盘", command=self.hide)
        self.menu.add_command(label="删除便签", command=self.delete)

    def _popup_menu(self, event):
        self._build_menu()
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    # ------------------------------------------------------------- 按钮动作
    def _on_bar(self, key):
        if key == "pin":
            self.toggle_topmost()
        elif key == "edge":
            self.toggle_edge()
        elif key == "alpha":
            self._popup_menu_alpha()
        elif key == "color":
            self._popup_menu_color()
        elif key == "new":
            self.app.create_note()
        elif key == "hide":
            self.hide()
        elif key == "del":
            self.delete()

    def _popup_menu_color(self):
        m = Menu(self.win, tearoff=0)
        for name in THEME_NAMES:
            m.add_command(label=name, command=lambda n=name: self.set_theme(n))
        x = self.win.winfo_rootx() + self.win.winfo_width() - 120
        y = self.win.winfo_rooty() + 32
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _popup_menu_alpha(self):
        m = Menu(self.win, tearoff=0)
        for a in ALPHAS:
            m.add_command(label=f"{int(a * 100)}%",
                          command=lambda v=a: self.set_alpha(v))
        x = self.win.winfo_rootx() + self.win.winfo_width() - 120
        y = self.win.winfo_rooty() + 32
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    # ------------------------------------------------------------- 功能
    def toggle_topmost(self):
        self.topmost = not self.topmost
        self.win.attributes("-topmost", self.topmost)
        self._save()

    def set_alpha(self, value):
        self.alpha = value
        self.win.attributes("-alpha", value)
        self._save()

    def toggle_edge(self):
        self.edge_mode = not self.edge_mode
        if not self.edge_mode:
            self._restore_from_edge()
        self._save()

    def open_font_dialog(self):
        """打开字体设置面板（字体 / 字号 / 文字颜色）。"""
        if getattr(self, "_font_dialog", None) is not None:
            try:
                self._font_dialog._close()
            except Exception:
                pass
        self._font_dialog = FontDialog(self)

    def set_theme(self, name):
        self.theme = name
        self._apply_theme()
        self._save()

    def set_text(self, text):
        self.textbox.delete("1.0", "end")
        self.textbox.insert("1.0", text)

    def get_text(self):
        return self.textbox.get("1.0", "end-1c")

    def show(self):
        """显示便签（含从贴边隐藏中恢复）。"""
        self._restore_from_edge()
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.win.after(100, lambda: self.win.attributes("-topmost", self.topmost))

    def hide(self):
        """隐藏到托盘。"""
        self.win.withdraw()

    def delete(self):
        """删除便签。"""
        self.app.delete_note(self.id)

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------- 拖动
    def _start_move(self, event):
        self._drag = (event.x_root - self.win.winfo_x(),
                      event.y_root - self.win.winfo_y())

    def _do_move(self, event):
        if not self._drag:
            return
        x = event.x_root - self._drag[0]
        y = event.y_root - self._drag[1]
        self.win.geometry(f"+{x}+{y}")

    def _end_move(self, event):
        if self._drag:
            self._drag = None
            # 拖动结束后自动检测贴边
            if self.edge_mode:
                self._check_edge()
            self._save()

    # ------------------------------------------------------------- 调整大小
    def _start_resize(self, event):
        self._resize = (self.win.winfo_width(), self.win.winfo_height(),
                        event.x_root, event.y_root)

    def _do_resize(self, event):
        if not self._resize:
            return
        w0, h0, x0, y0 = self._resize
        w = max(MIN_W, w0 + (event.x_root - x0))
        h = max(MIN_H, h0 + (event.y_root - y0))
        self.win.geometry(f"{w}x{h}")

    def _end_resize(self, event):
        if self._resize:
            self._resize = None
            self._save()

    # ------------------------------------------------------------- 贴边隐藏
    def _check_edge(self):
        """判断窗口是否贴到屏幕边缘，贴住则隐藏。"""
        if not self.edge_mode or self._edge_state:
            return
        x, y = self.win.winfo_x(), self.win.winfo_y()
        w, h = self.win.winfo_width(), self.win.winfo_height()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()

        if x <= EDGE_THRESHOLD:
            self._hide_to_edge("left", x, y, w, h)
        elif x + w >= sw - EDGE_THRESHOLD:
            self._hide_to_edge("right", x, y, w, h)
        elif y <= EDGE_THRESHOLD:
            self._hide_to_edge("top", x, y, w, h)

    def _hide_to_edge(self, direction, x, y, w, h):
        """把窗口移到屏幕外，只留一条边可见。"""
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self._hidden_pos = (x, y, w, h)
        self._edge_state = direction
        if direction == "left":
            self.win.geometry(f"{w}x{h}+{-w + EDGE_VISIBLE}+{y}")
        elif direction == "right":
            self.win.geometry(f"{w}x{h}+{sw - EDGE_VISIBLE}+{y}")
        elif direction == "top":
            self.win.geometry(f"{w}x{h}+{x}+{-h + EDGE_VISIBLE}")

    def _restore_from_edge(self):
        if self._edge_state and self._hidden_pos:
            x, y, w, h = self._hidden_pos
            self.win.geometry(f"{w}x{h}+{x}+{y}")
        self._edge_state = None
        self._hidden_pos = None

    def _poll_edge(self):
        """轮询鼠标位置：鼠标移到屏幕边缘时，弹出贴边隐藏的便签。"""
        try:
            if self._edge_state and self.edge_mode:
                mx, my = self.win.winfo_pointerxy()
                sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
                hit = False
                if self._edge_state == "left" and mx <= EDGE_THRESHOLD + 4:
                    hit = True
                elif self._edge_state == "right" and mx >= sw - EDGE_THRESHOLD - 4:
                    hit = True
                elif self._edge_state == "top" and my <= EDGE_THRESHOLD + 4:
                    hit = True
                if hit:
                    self._restore_from_edge()
        except Exception:
            pass
        try:
            self.win.after(EDGE_POLL_MS, self._poll_edge)
        except Exception:
            pass

    # ------------------------------------------------------------- 输入
    def _on_edit(self, event):
        # 防抖保存：停顿 0.6 秒后写入磁盘
        if hasattr(self, "_save_job"):
            try:
                self.win.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.win.after(SAVE_DEBOUNCE_MS, self._save)

    def to_dict(self):
        """导出为可保存的字典。"""
        # 贴边隐藏时窗口坐标在屏幕外，需用隐藏前的位置保存
        if self._edge_state and self._hidden_pos:
            x, y, w, h = self._hidden_pos
        else:
            try:
                x = self.win.winfo_x()
                y = self.win.winfo_y()
                w = self.win.winfo_width()
                h = self.win.winfo_height()
            except Exception:
                x, y, w, h = 100, 100, DEFAULT_W, DEFAULT_H
        return {
            "id": self.id,
            "text": self.get_text(),
            "x": x, "y": y, "w": w, "h": h,
            "theme": self.theme,
            "topmost": self.topmost,
            "alpha": self.alpha,
            "edge": self.edge_mode,
            "font_family": self.font_family,
            "font_size": self.font_size,
            "text_color": self.text_color,
            "hidden": not self.win.winfo_viewable(),
        }


# ================================================================ 字体设置面板
class FontDialog:
    """字体设置窗口：选择字体、字号、文字颜色，实时预览并即时生效。"""

    def __init__(self, note):
        self.note = note
        self._orig = (note.font_family, note.font_size, note.text_color)

        self.win = ctk.CTkToplevel(note.win)
        self.win.title("字体设置")
        self.win.geometry("400x470")
        self.win.resizable(False, False)
        self.win.transient(note.win)
        self.win.attributes("-topmost", True)
        try:
            self.win.grab_set()
        except Exception:
            pass
        self._build()

    def _build(self):
        n = self.note
        pad = 18

        ctk.CTkLabel(self.win, text="字体", font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").pack(fill="x", padx=pad, pady=(16, 4))
        self.font_menu = ctk.CTkOptionMenu(self.win, values=FONT_CHOICES, width=200,
                                           command=lambda _f: self._refresh())
        self.font_menu.set(n.font_family)
        self.font_menu.pack(fill="x", padx=pad)

        ctk.CTkLabel(self.win, text="字号", font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").pack(fill="x", padx=pad, pady=(14, 4))
        size_row = ctk.CTkFrame(self.win, fg_color="transparent")
        size_row.pack(fill="x", padx=pad)
        self.size_slider = ctk.CTkSlider(size_row, from_=10, to=40, number_of_steps=30,
                                         command=lambda _v: self._refresh())
        self.size_slider.set(n.font_size)
        self.size_slider.pack(side="left", fill="x", expand=True)
        self.size_label = ctk.CTkLabel(size_row, text=f"{n.font_size} px", width=56)
        self.size_label.pack(side="left", padx=(10, 0))

        ctk.CTkLabel(self.win, text="文字颜色", font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").pack(fill="x", padx=pad, pady=(14, 4))
        swatch_frame = ctk.CTkFrame(self.win, fg_color="transparent")
        swatch_frame.pack(fill="x", padx=pad)
        for i in range(0, len(TEXT_COLOR_PRESETS), 6):
            row = ctk.CTkFrame(swatch_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            for color in TEXT_COLOR_PRESETS[i:i + 6]:
                ctk.CTkButton(row, text="", width=46, height=28, corner_radius=6,
                              fg_color=color, hover_color=color,
                              command=lambda c=color: self._set_color(c)).pack(side="left", padx=4)

        color_row = ctk.CTkFrame(self.win, fg_color="transparent")
        color_row.pack(fill="x", padx=pad, pady=(10, 0))
        ctk.CTkButton(color_row, text="自定义取色…", width=120,
                      command=self._pick_color).pack(side="left")
        ctk.CTkButton(color_row, text="恢复主题色", width=120, fg_color="#8A8A8A",
                      hover_color="#6E6E6E", command=self._use_theme_color).pack(side="left", padx=(10, 0))

        ctk.CTkLabel(self.win, text="实时预览", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#777777", anchor="w").pack(fill="x", padx=pad, pady=(14, 4))
        self.preview = ctk.CTkLabel(self.win, text="便签文字预览 Aa 你好 123",
                                    height=54, corner_radius=8,
                                    fg_color=THEMES[n.theme]["bg"])
        self.preview.pack(fill="x", padx=pad)

        btn_row = ctk.CTkFrame(self.win, fg_color="transparent")
        btn_row.pack(fill="x", padx=pad, pady=(16, 14))
        ctk.CTkButton(btn_row, text="确定", width=90, command=self._confirm).pack(side="right")
        ctk.CTkButton(btn_row, text="取消", width=90, fg_color="#999999",
                      hover_color="#777777", command=self._cancel).pack(side="right", padx=(0, 10))

        self._refresh()

    def _refresh(self):
        """把当前选择的字体/字号同步到便签正文与预览（实时生效）。"""
        n = self.note
        n.font_family = self.font_menu.get()
        n.font_size = int(self.size_slider.get())
        self.size_label.configure(text=f"{n.font_size} px")
        font = ctk.CTkFont(family=n.font_family, size=n.font_size)
        n.textbox.configure(font=font)
        self.preview.configure(font=font, text_color=n._text_color())

    def _set_color(self, color):
        n = self.note
        n.text_color = color
        n.textbox.configure(text_color=color)
        self.preview.configure(text_color=color)

    def _pick_color(self):
        from tkinter import colorchooser

        result = colorchooser.askcolor(color=self.note._text_color(), parent=self.win,
                                       title="选择文字颜色")
        if result and result[1]:
            self._set_color(result[1])

    def _use_theme_color(self):
        n = self.note
        n.text_color = None
        n.textbox.configure(text_color=n._c("text"))
        self.preview.configure(text_color=n._c("text"))

    def _confirm(self):
        self.note.apply_font()
        self.note._save()
        self._close()

    def _cancel(self):
        # 恢复进入面板前的设置
        n = self.note
        n.font_family, n.font_size, n.text_color = self._orig
        n.apply_font()
        self._close()

    def _close(self):
        try:
            self.win.grab_release()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
        if getattr(self.note, "_font_dialog", None) is self:
            self.note._font_dialog = None


# ================================================================ 主应用
class DeskNoteApp:
    """便签应用总控：管理所有便签窗口、托盘、开机自启、数据持久化。"""

    def __init__(self):
        self.data = DataManager()
        self.notes = {}                 # id -> NoteWindow
        self.root = ctk.CTk()
        self.root.withdraw()            # 主窗口隐藏，只显示便签和托盘
        ctk.set_appearance_mode("light")

        # 单实例服务：收到 "SHOW" 命令就显示全部便签
        self._server_sock = None
        self._start_single_instance_server()

        self.tray_icon = None
        self._build_tray()

        # 恢复已保存的便签
        for nd in self.data.data.get("notes", []):
            try:
                self._restore_note(nd)
            except Exception:
                continue
        if not self.notes:
            self.create_note()

    # ------------------------------------------------------------- 单实例
    def _start_single_instance_server(self):
        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # 注意：Windows 下不能设置 SO_REUSEADDR，否则允许重复绑定同一端口，
            # 导致单实例保护失效
            self._server_sock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
            self._server_sock.listen(2)
            threading.Thread(target=self._instance_listener, daemon=True).start()
        except OSError:
            # 端口被占用说明已有实例在运行，通知它显示便签后退出
            try:
                s = socket.create_connection(("127.0.0.1", SINGLE_INSTANCE_PORT), timeout=1)
                s.sendall(b"SHOW")
                s.close()
            except OSError:
                pass
            sys.exit(0)

    def _instance_listener(self):
        while True:
            try:
                conn, _ = self._server_sock.accept()
                try:
                    data = conn.recv(64)
                    if data.strip() == b"SHOW":
                        self.root.after(0, self.show_all)
                except OSError:
                    pass
                finally:
                    conn.close()
            except OSError:
                break

    # ------------------------------------------------------------- 托盘
    def _build_tray(self):
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("新建便签", lambda icon, item: self.root.after(0, self.create_note)),
            pystray.MenuItem("显示全部便签", lambda icon, item: self.root.after(0, self.show_all)),
            pystray.MenuItem("隐藏全部便签", lambda icon, item: self.root.after(0, self.hide_all)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "开机自启",
                lambda icon, item: self.root.after(0, self.toggle_autostart),
                checked=lambda item: AutoStart.is_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda icon, item: self.root.after(0, self.quit_app)),
        )
        try:
            icon = pystray.Icon(APP_NAME, make_tray_icon(), APP_TITLE, menu)
            self.tray_icon = icon
            threading.Thread(target=icon.run, daemon=True).start()
        except Exception:
            self.tray_icon = None

    # ------------------------------------------------------------- 便签管理
    def _restore_note(self, nd):
        note = NoteWindow(self, note_id=nd.get("id"), theme=nd.get("theme", "黄"))
        note.topmost = bool(nd.get("topmost", True))
        note.alpha = float(nd.get("alpha", 1.0))
        note.edge_mode = bool(nd.get("edge", False))
        note.font_family = nd.get("font_family", "Microsoft YaHei UI")
        note.font_size = int(nd.get("font_size", 13))
        note.text_color = nd.get("text_color") or None
        note.win.attributes("-topmost", note.topmost)
        note.win.attributes("-alpha", note.alpha)
        note.set_text(nd.get("text", ""))
        note.win.geometry(f"{nd.get('w', DEFAULT_W)}x{nd.get('h', DEFAULT_H)}+{nd.get('x', 100)}+{nd.get('y', 100)}")
        note._apply_theme()
        note.apply_font()
        if nd.get("hidden"):
            note.win.withdraw()
        self.notes[note.id] = note

    def create_note(self, theme="黄"):
        note = NoteWindow(self, theme=theme)
        self.notes[note.id] = note
        note.win.lift()
        self.save()
        return note

    def delete_note(self, note_id):
        note = self.notes.pop(note_id, None)
        if note:
            note.destroy()
            self.save()
            # 全部删除完时自动新建一个空白便签
            if not self.notes:
                self.create_note()

    def show_all(self):
        for note in self.notes.values():
            note.show()

    def hide_all(self):
        for note in self.notes.values():
            note.hide()

    # ------------------------------------------------------------- 自启
    def toggle_autostart(self):
        if AutoStart.is_enabled():
            AutoStart.disable()
        else:
            AutoStart.enable()
        self.data.data["autostart"] = AutoStart.is_enabled()
        self.save()

    # ------------------------------------------------------------- 保存
    def save(self):
        self.data.data["notes"] = [n.to_dict() for n in self.notes.values()]
        self.data.data["autostart"] = AutoStart.is_enabled()
        self.data.save()

    # ------------------------------------------------------------- 退出
    def quit_app(self):
        self.save()
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)


# ================================================================ 启动
def main():
    app = DeskNoteApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
