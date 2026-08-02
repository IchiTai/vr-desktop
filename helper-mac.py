#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------
# VR Desktop 操作ヘルパー (Mac)
# このパソコンの中だけで動く小さな受け口 (127.0.0.1:8765) を開き、
# 画面共有ページから届いたマウス操作を実行します。
# 複数モニターに対応します(操作にはモニター番号が添えられます)。
# インストール作業は不要です。終了するには control + C か、
# ターミナルのウィンドウを閉じます。
# ----------------------------------------------------------------
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765


def ensure_pyautogui():
    try:
        import pyautogui  # noqa: F401
        return
    except ImportError:
        pass
    print()
    print("初回セットアップ: マウス操作用の部品 (pyautogui) を取り込みます。")
    print("数分かかることがあります。そのままお待ちください…")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--user", "--quiet", "pyautogui"]
        )
    except Exception:
        print()
        print("部品を取り込めませんでした。")
        print("インターネット接続を確認して、もう一度実行してください。")
        try:
            input("Enterキーで終了 ")
        except EOFError:
            pass
        sys.exit(1)
    print("準備ができました。ヘルパーを起動し直します…")
    os.execv(sys.executable, [sys.executable] + sys.argv)


ensure_pyautogui()
import pyautogui  # noqa: E402

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

try:
    import Quartz  # pyautogui と一緒に入る部品。マウス操作の本体に使う
    QZ = Quartz
except Exception:
    QZ = None

MAIN_W, MAIN_H = pyautogui.size()


def get_monitors():
    """メインを先頭に、残りを左から順に並べたモニター一覧を返す"""
    if QZ is not None:
        try:
            err, ids, _cnt = QZ.CGGetActiveDisplayList(16, None, None)
            if err == 0 and ids:
                main = QZ.CGMainDisplayID()
                others = sorted(
                    [d for d in ids if d != main],
                    key=lambda d: QZ.CGDisplayBounds(d).origin.x,
                )
                ordered = [d for d in ids if d == main] + others
                mons = []
                for d in ordered:
                    b = QZ.CGDisplayBounds(d)
                    mons.append({
                        "x": int(b.origin.x),
                        "y": int(b.origin.y),
                        "w": int(b.size.width),
                        "h": int(b.size.height),
                    })
                if mons:
                    return mons
        except Exception:
            pass
    return [{"x": 0, "y": 0, "w": MAIN_W, "h": MAIN_H}]


MONITORS = get_monitors()
PING_BODY = json.dumps({"ok": 1, "os": "mac", "monitors": MONITORS}).encode("utf-8")

# いまのボタン状態と位置(ドラッグとダブルクリックの判定に使う)
STATE = {"l": False, "r": False, "x": 0.0, "y": 0.0}
CLICK = {"t": 0.0, "n": 0}
try:
    _p = pyautogui.position()
    STATE["x"], STATE["y"] = float(_p[0]), float(_p[1])
except Exception:
    pass


def qz_post(ev):
    QZ.CGEventPost(QZ.kCGHIDEventTap, ev)


def do_move(x, y):
    STATE["x"], STATE["y"] = x, y
    if QZ is not None:
        try:
            if STATE["l"]:
                t, b = QZ.kCGEventLeftMouseDragged, QZ.kCGMouseButtonLeft
            elif STATE["r"]:
                t, b = QZ.kCGEventRightMouseDragged, QZ.kCGMouseButtonRight
            else:
                t, b = QZ.kCGEventMouseMoved, QZ.kCGMouseButtonLeft
            qz_post(QZ.CGEventCreateMouseEvent(None, t, (x, y), b))
            return
        except Exception:
            pass
    pyautogui.moveTo(x, y, _pause=False)


def do_button(down, right):
    if QZ is not None:
        try:
            if right:
                t = QZ.kCGEventRightMouseDown if down else QZ.kCGEventRightMouseUp
                b = QZ.kCGMouseButtonRight
                clicks = 1
            else:
                t = QZ.kCGEventLeftMouseDown if down else QZ.kCGEventLeftMouseUp
                b = QZ.kCGMouseButtonLeft
                if down:
                    now = time.time()
                    if now - CLICK["t"] < 0.4:
                        CLICK["n"] += 1
                    else:
                        CLICK["n"] = 1
                    CLICK["t"] = now
                clicks = max(1, CLICK["n"])
            ev = QZ.CGEventCreateMouseEvent(None, t, (STATE["x"], STATE["y"]), b)
            QZ.CGEventSetIntegerValueField(ev, QZ.kCGMouseEventClickState, clicks)
            qz_post(ev)
            STATE["r" if right else "l"] = down
            return
        except Exception:
            pass
    fn = pyautogui.mouseDown if down else pyautogui.mouseUp
    fn(button="right" if right else "left", _pause=False)
    STATE["r" if right else "l"] = down


import subprocess

# ---- キーボード送信 ----
KEYCODE = {"enter": 36, "esc": 53, "tab": 48, "left": 123, "right": 124,
           "down": 125, "up": 126, "backspace": 51, "delete": 117, "space": 49}
COMBO = {"copy": 8, "paste": 9, "cut": 7, "undo": 6, "selectall": 0,
         "back": 33, "forward": 30}  # Cmd+英字 / Cmd+[ ]
PYKEY = {"enter": "enter", "esc": "esc", "tab": "tab", "left": "left",
         "right": "right", "down": "down", "up": "up",
         "backspace": "backspace", "delete": "delete", "space": "space"}
PYCOMBO = {"copy": "c", "paste": "v", "cut": "x", "undo": "z",
           "selectall": "a", "back": "[", "forward": "]"}


def _qz_key(code, flags=0):
    for down in (True, False):
        ev = QZ.CGEventCreateKeyboardEvent(None, code, down)
        if flags:
            QZ.CGEventSetFlags(ev, flags)
        qz_post(ev)


def do_key(k):
    try:
        if QZ is not None:
            if k in KEYCODE:
                _qz_key(KEYCODE[k])
            elif k in COMBO:
                _qz_key(COMBO[k], QZ.kCGEventFlagMaskCommand)
            elif k == "apptab":
                _qz_key(48, QZ.kCGEventFlagMaskCommand)  # Cmd+Tab
            return
        if k in PYKEY:
            pyautogui.press(PYKEY[k], _pause=False)
        elif k in PYCOMBO:
            pyautogui.hotkey("command", PYCOMBO[k], _pause=False)
        elif k == "apptab":
            pyautogui.hotkey("command", "tab", _pause=False)
    except Exception:
        pass


def do_txt(s):
    # 日本語も確実に入るよう、クリップボード経由で貼り付ける
    try:
        subprocess.run(["pbcopy"], input=s.encode("utf-8"), check=False)
        time.sleep(0.06)
        if QZ is not None:
            _qz_key(9, QZ.kCGEventFlagMaskCommand)  # Cmd+V
        else:
            pyautogui.hotkey("command", "v", _pause=False)
    except Exception:
        pass


def do_scroll(d):
    if QZ is not None:
        try:
            ev = QZ.CGEventCreateScrollWheelEvent(
                None, QZ.kCGScrollEventUnitLine, 1, int(d) * 3
            )
            qz_post(ev)
            return
        except Exception:
            pass
    pyautogui.scroll(int(d) * 3)


paired = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _cors(self):
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _reply(self, code=200, body=b'{"ok":1}'):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_OPTIONS(self):
        self._reply(204, b"")

    def do_GET(self):
        global paired
        if self.path == "/ping":
            origin = self.headers.get("Origin")
            if paired is None and origin:
                paired = origin
                print("   接続されました:", origin)
            self._reply(200, PING_BODY)
        else:
            self._reply(404, b'{"ok":0}')

    def do_POST(self):
        global paired
        if self.path != "/input":
            self._reply(404, b'{"ok":0}')
            return
        origin = self.headers.get("Origin")
        if paired and origin and origin != paired:
            self._reply(403, b'{"ok":0}')
            return
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = 0
        raw = self.rfile.read(n) if n > 0 else b"[]"
        try:
            events = json.loads(raw.decode("utf-8"))
            for e in events:
                t = e.get("t")
                if t == "mv":
                    si = int(e.get("s", 0) or 0)
                    if si < 0 or si >= len(MONITORS):
                        si = 0
                    m = MONITORS[si]
                    nx = max(0.0, min(1.0, float(e.get("x", 0))))
                    ny = max(0.0, min(1.0, float(e.get("y", 0))))
                    do_move(m["x"] + nx * (m["w"] - 1), m["y"] + ny * (m["h"] - 1))
                elif t == "dn":
                    do_button(True, e.get("b") == 2)
                elif t == "up":
                    do_button(False, e.get("b") == 2)
                elif t == "sc":
                    do_scroll(e.get("d", 0))
                elif t == "key":
                    do_key(str(e.get("k", "")))
                elif t == "txt":
                    do_txt(str(e.get("s", "")))
        except Exception:
            pass
        self._reply()


def main():
    print()
    print("  ================================================")
    print("   VR Desktop 操作ヘルパー (Mac)")
    print("  ================================================")
    print(f"   モニター  : {len(MONITORS)}枚")
    for i, m in enumerate(MONITORS):
        tag = "(メイン)" if i == 0 else ""
        print(f"     モニター{i + 1}{tag} : {m['w']} x {m['h']}")
    print("   状態      : 画面共有ページからの接続を待っています")
    print()
    print("   VRで操作している間、このウィンドウは開いたままにしてください。")
    print("   やめるときは control + C を押すか、ウィンドウを閉じます。")
    print()
    print("   ※ マウスが動かないときは、")
    print("      システム設定 → プライバシーとセキュリティ → アクセシビリティ で")
    print("      「ターミナル」をオンにして、ヘルパーを起動し直してください。")
    print()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"   ポート {PORT} が使われています。")
        print("   すでにヘルパーが別のウィンドウで動いていないか確認してください。")
        try:
            input("Enterキーで終了 ")
        except EOFError:
            pass
        return
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        print("終了しました。")


if __name__ == "__main__":
    main()
