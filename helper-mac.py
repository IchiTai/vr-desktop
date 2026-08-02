#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------
# VR Desktop 操作ヘルパー (Mac)
# このパソコンの中だけで動く小さな受け口 (127.0.0.1:8765) を開き、
# 画面共有ページから届いたマウス操作を実行します。
# インストール作業は不要です。終了するには control + C か、
# ターミナルのウィンドウを閉じます。
# ----------------------------------------------------------------
import json
import os
import subprocess
import sys
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

W, H = pyautogui.size()
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
            self._reply(200, b'{"ok":1,"os":"mac"}')
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
                    x = max(0.0, min(1.0, float(e.get("x", 0)))) * (W - 1)
                    y = max(0.0, min(1.0, float(e.get("y", 0)))) * (H - 1)
                    pyautogui.moveTo(x, y, _pause=False)
                elif t == "dn":
                    button = "right" if e.get("b") == 2 else "left"
                    pyautogui.mouseDown(button=button, _pause=False)
                elif t == "up":
                    button = "right" if e.get("b") == 2 else "left"
                    pyautogui.mouseUp(button=button, _pause=False)
                elif t == "sc":
                    pyautogui.scroll(int(e.get("d", 0)) * 3)
        except Exception:
            pass
        self._reply()


def main():
    print()
    print("  ================================================")
    print("   VR Desktop 操作ヘルパー (Mac)")
    print("  ================================================")
    print(f"   画面サイズ: {W} x {H}")
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
