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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765
# ヘルパーの版(通し番号)。ヘルパーの仕様を変えたら +1 し、
# index.html の HELPER_VER_REQUIRED と helper-windows.bat の $helperVer も
# 同じ値に上げること
HELPER_VER = 4  # 版2: 中ボタン / 版3: 横スクロール / 版4: 横スクロールの向きをWindowsと統一
HELPER_BUILD = "2026-08-14b"

# ---- このパソコンを操作してよいページ (2026-08-08) ----
# ヘルパーはマウスもキーボードも動かせるので、対にした相手は
# 事実上このパソコンの操作権を持つ。以前は「最初に話しかけてきた
# 相手」を無条件で対にしていたため、ブラウザで開いている無関係な
# サイトが先に話しかければ、そのサイトが操作できてしまった。
# 自分が公開しているページのURL(https://〜。末尾の / は付けない)を
# ここに書くと、そのページだけを受け付ける:
#     ALLOW_ORIGIN = "https://自分の名前.github.io"
# 空のままなら、従来どおり先着順で対を決める(ファイルを直接開いて
# 試すときに困らないように)。どちらの状態かは起動画面に出る。
ALLOW_ORIGIN = ""


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
MON_T = 0.0


MON_LOCK = threading.Lock()


def refresh_monitors():
    """モニター一覧を読み直す(抜き差し・解像度変更に追随)。2秒に1回まで。
    差し替えは錠の中でまとめて行うこと: 別スレッドの mv が読んでいる最中に
    MONITORS だけ短くなると、画面番号の引き当てが IndexError で落ちる"""
    global MONITORS, BOUND, PING_BODY, MON_T
    with MON_LOCK:
        now = time.time()
        if now - MON_T < 2.0:
            return
        MON_T = now
        try:
            mons = get_monitors()
        except Exception:
            return
        if mons and mons != MONITORS:
            MONITORS = mons
            BOUND = get_bound()
            PING_BODY = json.dumps({"ok": 1, "os": "mac", "hv": HELPER_VER,
                                    "monitors": MONITORS}).encode("utf-8")

# いまのボタン状態と位置(ドラッグとダブルクリックの判定に使う)
STATE = {"l": False, "m": False, "r": False, "x": 0.0, "y": 0.0, "t": 0.0}
CLICK = {"t": 0.0, "n": 0, "x": 0.0, "y": 0.0}
# STATE と CLICK は接続ごとのスレッドから触るので、必ずこの錠を通すこと
STATE_LOCK = threading.RLock()
DBL_SEC = 0.4    # ダブルクリックとみなす間隔
# ダブルクリックとみなす距離。index.html の tvDown は「タップから320ms・40px
# 以内の再タップは2回目のクリックとして成立させる」約束を持つ。その40pxは
# 見る側の画面上の値なので、PC側の画素では画面の幅の比(iPadで約2.5倍)だけ
# 大きくなる。ここを小さくすると、その約束がMacでだけ成立しなくなる(調整点)
DBL_PX = 100.0
# 自分で動かした直後は OS への反映待ちで古い位置が返りうる。便の末尾の mv と
# 次の便の dn の間隔(33msの便待ち + localhost 往復11〜26ms)より長くすること
FRESH_SEC = 0.15
try:
    _p = pyautogui.position()
    STATE["x"], STATE["y"] = float(_p[0]), float(_p[1])
except Exception:
    pass


def qz_post(ev):
    QZ.CGEventPost(QZ.kCGHIDEventTap, ev)


def do_move(x, y):
    with STATE_LOCK:
        STATE["x"], STATE["y"], STATE["t"] = x, y, time.time()
        if QZ is not None:
            try:
                if STATE["l"]:
                    t, b = QZ.kCGEventLeftMouseDragged, QZ.kCGMouseButtonLeft
                elif STATE["r"]:
                    t, b = QZ.kCGEventRightMouseDragged, QZ.kCGMouseButtonRight
                elif STATE["m"]:
                    t, b = QZ.kCGEventOtherMouseDragged, QZ.kCGMouseButtonCenter
                else:
                    t, b = QZ.kCGEventMouseMoved, QZ.kCGMouseButtonLeft
                qz_post(QZ.CGEventCreateMouseEvent(None, t, (x, y), b))
                return
            except Exception:
                pass
        pyautogui.moveTo(x, y, _pause=False)


def cursor_pos():
    # 最終手段で STATE を読む。呼び出し元が STATE_LOCK を持っていることがあるので
    # RLock でも取り直さず、読むだけにとどめる(値がひとつ古くても位置がずれるだけ)
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
    # 押す場所は覚えた座標ではなく、そのつど OS に聞いた実カーソル位置。
    # ただし自分で動かした直後(FRESH_SEC)は OS への反映待ちで古い位置が
    # 返りうるので、その間だけは直前に命じた位置を使う
    px, py = cursor_pos()
    key = "r" if btn == 2 else ("m" if btn == 1 else "l")
    with STATE_LOCK:
        if time.time() - STATE["t"] < FRESH_SEC:
            px, py = STATE["x"], STATE["y"]
        STATE["x"], STATE["y"] = px, py
        clicks = 1
        if key == "l":
            if down:
                now = time.time()
                near = (abs(px - CLICK["x"]) <= DBL_PX
                        and abs(py - CLICK["y"]) <= DBL_PX)
                if near and now - CLICK["t"] < DBL_SEC:
                    CLICK["n"] += 1
                else:
                    CLICK["n"] = 1
                CLICK["t"], CLICK["x"], CLICK["y"] = now, px, py
            clicks = max(1, CLICK["n"])
        if QZ is not None:
            try:
                if btn == 2:
                    t = QZ.kCGEventRightMouseDown if down else QZ.kCGEventRightMouseUp
                    b = QZ.kCGMouseButtonRight
                elif btn == 1:
                    t = QZ.kCGEventOtherMouseDown if down else QZ.kCGEventOtherMouseUp
                    b = QZ.kCGMouseButtonCenter
                else:
                    t = QZ.kCGEventLeftMouseDown if down else QZ.kCGEventLeftMouseUp
                    b = QZ.kCGMouseButtonLeft
                ev = QZ.CGEventCreateMouseEvent(None, t, (px, py), b)
                QZ.CGEventSetIntegerValueField(ev, QZ.kCGMouseEventClickState, clicks)
                qz_post(ev)
                STATE[key] = down
                return
            except Exception:
                pass
        fn = pyautogui.mouseDown if down else pyautogui.mouseUp
        fn(button=("right" if btn == 2 else "middle" if btn == 1 else "left"), _pause=False)
        STATE[key] = down


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


# 1便に合算された値で桁あふれさせないための上限(通常は数十で、届くことはない)
SCROLL_MAX = 100000


def do_scroll(d, h=0):
    # d=縦, h=横(版3から)。横の -3 は helper-windows.bat の HWHEEL(-$hv2 * 120)と
    # 対になっている。**片方だけ符号を変えないこと**(左右がOSごとに食い違う)。
    # Windows 側は実機で確認済み(2026-08-14)。Mac は未確認なので、Mac で逆だった
    # ときは「両方の符号を反転する」か「index.html の送信側(3経路すべて)を反転する」
    # のどちらかにすること
    try:
        dv = int(d)
    except Exception:
        dv = 0
    try:
        hh = int(h)
    except Exception:
        hh = 0
    dv = max(-SCROLL_MAX, min(SCROLL_MAX, dv)) * 3
    hh = max(-SCROLL_MAX, min(SCROLL_MAX, hh)) * -3
    if QZ is not None:
        try:
            ev = QZ.CGEventCreateScrollWheelEvent(
                None, QZ.kCGScrollEventUnitLine, 2, dv, hh
            )
            qz_post(ev)
            return
        except Exception:
            pass
    if dv:
        pyautogui.scroll(dv)
    if hh:
        pyautogui.hscroll(hh)


paired = None
warned_origin = False  # 拒否の知らせは1回だけ(画面を埋めないため)


def same_origin(a, b):
    # 大文字小文字は区別しない(helper-windows.bat の -ne 比較と揃えるため)
    return bool(a) and bool(b) and a.strip().lower() == b.strip().lower()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # 接続を開いたまま使い回す方式なので、相手が黙ったままの接続に
    # スレッドを取られ続けないよう時間切れを設ける。生存確認は3秒ごとに
    # 来るので、30秒あれば使用中の接続が切れることはない。
    timeout = 30
    # ただし「つないだだけで何も言ってこない」接続は話が別で、
    # ブラウザが先回りで開けた空の接続がこれにあたる。これを30秒
    # 抱えると、上限(16本)がその間ふさがる。最初の要求が来るまでは
    # 10秒で見切る(Windows側の空接続10秒と同じ考え方。2026-08-08)。
    FIRST_TIMEOUT = 10

    def setup(self):
        super().setup()
        # 1本目の要求が来るまでは短く待つ
        self.connection.settimeout(self.FIRST_TIMEOUT)

    def handle_one_request(self):
        super().handle_one_request()
        # 1本目を捌いたら、使い回しの待ち時間へ戻す
        try:
            self.connection.settimeout(self.timeout)
        except Exception:
            pass

    def log_message(self, *args):
        pass

    def _cors(self):
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _reply(self, code=200, body=b'{"ok":1}', close=False):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if close:
            # 本文を読み捨てられなかった接続は使い回させない
            self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _drain(self, n):
        # 断る要求でも本文は読み捨てる。残すと使い回しの接続で次の要求が壊れる
        left = n
        try:
            while left > 0:
                chunk = self.rfile.read(min(left, 65536))
                if not chunk:
                    return False
                left -= len(chunk)
        except Exception:
            return False
        return True

    def do_OPTIONS(self):
        self._reply(204, b"")

    def do_GET(self):
        global paired, warned_origin
        if self.path == "/ping":
            origin = self.headers.get("Origin")
            # ALLOW_ORIGIN を設定してあるときは、そのページ以外には
            # 版番号もモニター構成も返さない(返す必要のない情報のため)
            if ALLOW_ORIGIN and not same_origin(origin, ALLOW_ORIGIN):
                if not warned_origin:
                    warned_origin = True
                    print("   拒否しました:", origin, "(許可したページではありません)")
                self._reply(200, b'{"ok":0}')
                return
            if paired is None and origin:
                paired = origin
                print("   接続されました:", origin)
            refresh_monitors()
            self._reply(200, PING_BODY)
        else:
            self._reply(404, b'{"ok":0}')

    def do_POST(self):
        global paired
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = 0
        origin = self.headers.get("Origin")
        # Origin の無い要求は対の確認をすり抜けるので受け付けない
        refuse = 0
        if self.path != "/input":
            refuse = 404
        elif (not origin) or (paired and not same_origin(origin, paired)) or \
             (ALLOW_ORIGIN and not same_origin(origin, ALLOW_ORIGIN)):
            refuse = 403
        elif n > 262144:
            refuse = 413
        if refuse:
            # 断る相手に大きな本文を読まされないこと(04s)。256KBを超える申告は
            # 中身を読まずに閉じる。読み捨てられたときだけ接続を使い回す
            shut = True if n > 262144 else not self._drain(n)
            self._reply(refuse, b'{"ok":0}', shut)
            return
        raw = self.rfile.read(n) if n > 0 else b"[]"
        try:
            events = json.loads(raw.decode("utf-8"))
        except Exception:
            events = []
        # 1件の壊れた操作で、同じ便の残りを巻き添えにしない(2026-08-08)。
        # 以前は便全体を1つの try で囲っていたため、たとえば mv の値が
        # 壊れていると、そのあとの up(離す)まで実行されず、ボタンが
        # 押しっぱなしになりえた。ここは1件ずつ切り離して守る。
        for e in events:
            try:
                t = e.get("t")
                if t == "mv":
                    # 一度だけ読んで使い回すこと。len() と添字で別の一覧を読むと、
                    # 読み直しと重なったとき IndexError で1件だけ落ちる
                    mons = MONITORS
                    si = int(e.get("s", 0) or 0)
                    if si < 0 or si >= len(mons):
                        si = 0
                    m = mons[si]
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
                continue
        self._reply()


class CappedServer(ThreadingHTTPServer):
    """同時接続に上限を設けたサーバ(2026-08-08)。

    ThreadingHTTPServer は接続ごとにスレッドを作るので、暴走した
    相手や不具合で接続が積み上がると、スレッドが際限なく増える。
    Windows側は16本で頭打ちにしてあるので、両OSの性質を揃える。
    実際に使う接続はページ1つにつき1〜2本なので、16本あれば余る。
    """

    daemon_threads = True   # 終了時に残ったスレッドを待たない
    MAX_CONN = 16

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._live = 0
        self._lock = threading.Lock()
        self._warned = False

    def process_request(self, request, client_address):
        # 数を先に増やす。断る側も shutdown_request 経由で close_request が
        # 呼ばれて減るため、増やさずに断つと数が合わず上限が効かなくなる
        with self._lock:
            self._live += 1
            over = self._live > self.MAX_CONN
        if over:
            # 上限に達している間は、新しい接続をその場で閉じる。
            # 使用中の接続は時間切れ(30秒)で自然に空くので、
            # 落ち着けば元どおりつながる。
            if not self._warned:
                self._warned = True
                print(f"   接続が多すぎます({self.MAX_CONN}本)。新しい接続を一時的に断ります。")
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def handle_error(self, request, client_address):
        # 相手が急に切ったときの例外を画面に出さない(利用者が見る窓なので、
        # 赤い長文が出ると不具合と誤解される。既存の log_message と同じ趣旨)
        pass

    def shutdown_request(self, request):
        super().shutdown_request(request)

    def close_request(self, request):
        super().close_request(request)
        with self._lock:
            if self._live > 0:
                self._live -= 1


def main():
    print()
    print("  ================================================")
    print("   VR Desktop 操作ヘルパー (Mac)")
    print(f"   ヘルパーの版: {HELPER_VER}   (updated {HELPER_BUILD})")
    print("  ================================================")
    print("   操作を許可するページ: "
          + (ALLOW_ORIGIN if ALLOW_ORIGIN else "未設定(最初に話しかけたページ)"))
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
        server = CappedServer(("127.0.0.1", PORT), Handler)
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
