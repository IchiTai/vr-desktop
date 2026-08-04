#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------
# VR Desktop 操作ヘルパー (Mac)
# このパソコンの中だけで動く小さな受け口 (127.0.0.1:8765) を開き、
# 画面共有ページから届いたマウスとキーボードの操作を実行します。
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
# ヘルパーの版(通し番号)。ヘルパーの仕様を変えたら +1 し、
# index.html の HELPER_VER_REQUIRED も同じ値に上げること
HELPER_VER = 3  # 版2: 中ボタン(b:1) / 版3: 横スクロール(sc の h)


def ensure_pyautogui():
    try:
        import pyautogui  # noqa: F401
        return
    except ImportError:
        pass
    print()
    print("初回セットアップ: 操作用の部品 (pyautogui) を取り込みます。")
    print("数分かかることがあります。そのままお待ちください…")

    base = [sys.executable, "-m", "pip", "install", "--user", "--quiet", "pyautogui"]
    # 1回目は今までどおり。これで通る環境の挙動は変えない。
    # 2回目は --break-system-packages を足す。Homebrew などの新しい Python は
    # 「externally-managed-environment」という保護で1回目を拒むため。
    # このフラグは古い pip にはなく、付けると逆に失敗する。だから最初からは付けない。
    for attempt in (base, base + ["--break-system-packages"]):
        try:
            subprocess.check_call(attempt)
            break
        except Exception:
            continue
    else:
        print()
        print("部品を取り込めませんでした。次のどちらかが原因です。")
        print("  1. インターネットにつながっていない")
        print("  2. この Python が部品の追加を拒む設定になっている")
        print()
        print("ターミナルに次の1行を貼り付けて実行すると、様子がわかります。")
        print(f"  {sys.executable} -m pip install --user pyautogui")
        print("「externally-managed-environment」と出たら 2 が原因です。")
        print("その場合は、上の行の末尾に --break-system-packages を足して")
        print("もう一度実行してから、ヘルパーを起動し直してください。")
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
PING_BODY = json.dumps({"ok": 1, "os": "mac", "hv": HELPER_VER, "monitors": MONITORS}).encode("utf-8")


def get_bound():
    """全モニターをまとめて囲む長方形。
    トラックパッド式の移動で、カーソルが画面の外へ出ないようにする枠として使う"""
    x0 = min(m["x"] for m in MONITORS)
    y0 = min(m["y"] for m in MONITORS)
    x1 = max(m["x"] + m["w"] for m in MONITORS) - 1
    y1 = max(m["y"] + m["h"] for m in MONITORS) - 1
    return x0, y0, x1, y1


BOUND = get_bound()

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


def cursor_pos():
    """いまカーソルがある場所を、そのつど OS に聞く。
    自分で覚えた位置ではなく OS に聞くので、途中で実物のマウスを
    動かされても、次の相対移動でカーソルが飛ばない"""
    if QZ is not None:
        try:
            p = QZ.CGEventGetLocation(QZ.CGEventCreate(None))
            return float(p.x), float(p.y)
        except Exception:
            pass
    try:
        p = pyautogui.position()
        return float(p[0]), float(p[1])
    except Exception:
        pass
    return STATE["x"], STATE["y"]  # どちらも失敗したときの保険


def do_move_rel(dx, dy):
    """トラックパッド式の移動。いまの位置から dx, dy だけずらす。
    do_move を通すので、ボタンを押したままならドラッグとして出る"""
    cx, cy = cursor_pos()
    x0, y0, x1, y1 = BOUND
    do_move(min(max(cx + dx, x0), x1), min(max(cy + dy, y0), y1))


def do_button(down, btn):
    # btn: 0=左, 1=中(版2から), 2=右
    if QZ is not None:
        try:
            if btn == 2:
                t = QZ.kCGEventRightMouseDown if down else QZ.kCGEventRightMouseUp
                b = QZ.kCGMouseButtonRight
                clicks = 1
            elif btn == 1:
                t = QZ.kCGEventOtherMouseDown if down else QZ.kCGEventOtherMouseUp
                b = QZ.kCGMouseButtonCenter
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
            STATE["r" if btn == 2 else "l"] = down
            return
        except Exception:
            pass
    fn = pyautogui.mouseDown if down else pyautogui.mouseUp
    fn(button=("right" if btn == 2 else "middle" if btn == 1 else "left"), _pause=False)
    STATE["r" if btn == 2 else "l"] = down


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


# ---- 任意キー(Bluetoothキーボード直送用) ----
KBCODE = {"KeyA": 0, "KeyB": 11, "KeyC": 8, "KeyD": 2, "KeyE": 14, "KeyF": 3,
          "KeyG": 5, "KeyH": 4, "KeyI": 34, "KeyJ": 38, "KeyK": 40, "KeyL": 37,
          "KeyM": 46, "KeyN": 45, "KeyO": 31, "KeyP": 35, "KeyQ": 12, "KeyR": 15,
          "KeyS": 1, "KeyT": 17, "KeyU": 32, "KeyV": 9, "KeyW": 13, "KeyX": 7,
          "KeyY": 16, "KeyZ": 6,
          "Digit1": 18, "Digit2": 19, "Digit3": 20, "Digit4": 21, "Digit5": 23,
          "Digit6": 22, "Digit7": 26, "Digit8": 28, "Digit9": 25, "Digit0": 29,
          "Minus": 27, "Equal": 24, "BracketLeft": 33, "BracketRight": 30,
          "Backslash": 42, "Semicolon": 41, "Quote": 39, "Backquote": 50,
          "Comma": 43, "Period": 47, "Slash": 44,
          "Enter": 36, "Escape": 53, "Backspace": 51, "Tab": 48, "Space": 49,
          "Delete": 117, "ArrowLeft": 123, "ArrowRight": 124, "ArrowDown": 125,
          "ArrowUp": 126, "Home": 115, "End": 119, "PageUp": 116, "PageDown": 121,
          "F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97,
          "F7": 98, "F8": 100, "F9": 101, "F10": 109, "F11": 103, "F12": 111}
KBPY = {"Enter": "enter", "Escape": "esc", "Backspace": "backspace", "Tab": "tab",
        "Space": "space", "Delete": "delete", "ArrowLeft": "left",
        "ArrowRight": "right", "ArrowDown": "down", "ArrowUp": "up"}


def do_kb(c, m):
    try:
        if QZ is not None and c in KBCODE:
            flags = 0
            if m & 1:
                flags |= QZ.kCGEventFlagMaskControl
            if m & 2:
                flags |= QZ.kCGEventFlagMaskShift
            if m & 4:
                flags |= QZ.kCGEventFlagMaskAlternate
            if m & 8:
                flags |= QZ.kCGEventFlagMaskCommand
            _qz_key(KBCODE[c], flags)
            return
        keys = []
        if m & 1:
            keys.append("ctrl")
        if m & 2:
            keys.append("shift")
        if m & 4:
            keys.append("option")
        if m & 8:
            keys.append("command")
        base = KBPY.get(c) or (c[3].lower() if c.startswith("Key") else None)             or (c[5] if c.startswith("Digit") else None)
        if base is None:
            return
        if keys:
            pyautogui.hotkey(*(keys + [base]), _pause=False)
        else:
            pyautogui.press(base, _pause=False)
    except Exception:
        pass


def do_ch(s):
    # 文字をそのまま打つ(配列に依存しないユニコード入力)
    try:
        if QZ is not None:
            for down in (True, False):
                ev = QZ.CGEventCreateKeyboardEvent(None, 0, down)
                QZ.CGEventKeyboardSetUnicodeString(ev, len(s), s)
                qz_post(ev)
        else:
            pyautogui.write(s, _pause=False)
    except Exception:
        pass


def do_txt(s):
    # 日本語も確実に入るよう、クリップボード経由で貼り付ける
    # 送信側(index.html)と同じ1000字で切り詰める(独立した自衛の上限)
    s = s[:1000]
    try:
        # 元の内容を文字として控える(画像などは控えられない)
        try:
            prev = subprocess.run(["pbpaste"], capture_output=True,
                                  timeout=2).stdout
        except Exception:
            prev = b""
        subprocess.run(["pbcopy"], input=s.encode("utf-8"), check=False)
        time.sleep(0.06)
        if QZ is not None:
            _qz_key(9, QZ.kCGEventFlagMaskCommand)  # Cmd+V
        else:
            pyautogui.hotkey("command", "v", _pause=False)
        # すぐ戻すとアプリが読む前に書き換わり、古い内容が貼り付いてしまう。
        # 前面アプリが貼り付けを終えるのを待ってから元の文字内容へ戻す
        time.sleep(0.25)
        subprocess.run(["pbcopy"], input=prev, check=False)
    except Exception:
        pass


def do_scroll(d, h=0):
    # d=縦, h=横(版3から)。横の正方向はOSごとに解釈が割れるため、
    # 実機で逆だったら下の「int(h)」の符号を反転する(調整点)
    if QZ is not None:
        try:
            ev = QZ.CGEventCreateScrollWheelEvent(
                None, QZ.kCGScrollEventUnitLine, 2, int(d) * 3, int(h) * 3
            )
            qz_post(ev)
            return
        except Exception:
            pass
    if int(d):
        pyautogui.scroll(int(d) * 3)
    if int(h):
        pyautogui.hscroll(int(h) * 3)


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
                elif t == "mvr":
                    do_move_rel(float(e.get("x", 0) or 0), float(e.get("y", 0) or 0))
                elif t == "dn":
                    do_button(True, int(e.get("b", 0) or 0))
                elif t == "up":
                    do_button(False, int(e.get("b", 0) or 0))
                elif t == "sc":
                    do_scroll(e.get("d", 0) or 0, e.get("h", 0) or 0)
                elif t == "key":
                    do_key(str(e.get("k", "")))
                elif t == "txt":
                    do_txt(str(e.get("s", "")))
                elif t == "kb":
                    do_kb(str(e.get("c", "")), int(e.get("m", 0) or 0))
                elif t == "ch":
                    do_ch(str(e.get("s", ""))[:8])
        except Exception:
            pass
        self._reply()


def main():
    print()
    print("  ================================================")
    print("   VR Desktop 操作ヘルパー (Mac)")
    print(f"   ヘルパーの版: {HELPER_VER}")
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
