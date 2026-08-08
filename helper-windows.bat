@echo off
title VR Desktop - Control Helper
echo Starting VR Desktop Control Helper ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c=[IO.File]::ReadAllText('%~f0');$i=$c.IndexOf('#PS'+'START#');iex $c.Substring($i)"
exit /b

#PSSTART#
# ----------------------------------------------------------------
# VR Desktop - Control Helper (Windows)
# Runs a tiny HTTP server on 127.0.0.1:8765 (this computer only).
# The screen-share page sends mouse events here.
# Supports multiple monitors: each event carries a monitor index.
# No installation. Close this window to stop.
# ----------------------------------------------------------------
$ErrorActionPreference = 'Stop'

# Helper version (integer). Bump this AND HELPER_VER_REQUIRED in
# index.html together whenever the helper protocol changes.
# Keep these two definitions here at the top: $pingBody below reads
# $helperVer. They used to sit after $pingBody, and PowerShell reads an
# undefined variable as empty, so /ping returned broken JSON
# ("hv": with no value). The page then showed "helper too old",
# dropped middle clicks (b:1) and saw an empty monitor list.
# Fixed 2026-08-08; the page protocol is unchanged (version stays 3).
$helperVer = 3
# Build date. Shown at startup so you can confirm the file was replaced.
# Bump this whenever this file changes (the version number above only
# changes when the agreement with the page changes).
$helperBuild = '2026-08-08e'

# ---- Which page may control this PC (2026-08-08e) ----
# The helper can move the mouse and type, so whoever it pairs with
# effectively gets control of this computer. It used to pair with
# whoever pinged first, which means any site open in your browser
# could grab it. Put your own published page URL here (scheme + host,
# no trailing slash) and only that page will be accepted:
#     $allowOrigin = 'https://yourname.github.io'
# Leave it empty to keep the old first-come behaviour (handy when
# opening the file locally for a quick test). The startup screen
# always shows which mode is active.
$allowOrigin = ''
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$csrc = @'
using System;
using System.Runtime.InteropServices;
public static class VRMouse {
    [DllImport("user32.dll")]
    public static extern void mouse_event(uint dwFlags, uint dx, uint dy, int dwData, int dwExtraInfo);
    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, uint dwExtraInfo);
    public const uint LEFTDOWN  = 0x0002;
    public const uint LEFTUP    = 0x0004;
    public const uint RIGHTDOWN = 0x0008;
    public const uint RIGHTUP   = 0x0010;
    public const uint MIDDLEDOWN = 0x0020;
    public const uint MIDDLEUP   = 0x0040;
    public const uint WHEEL     = 0x0800;
    public const uint HWHEEL    = 0x01000;
}
'@
Add-Type -TypeDefinition $csrc

# Monitors: primary first, the rest ordered left to right
$allScreens = [System.Windows.Forms.Screen]::AllScreens
$prim = $null
$rest = @()
foreach ($s in $allScreens) {
    if ($s.Primary) { $prim = $s } else { $rest += $s }
}
$rest = @($rest | Sort-Object { $_.Bounds.X })
$mons = @()
if ($prim) { $mons += $prim }
$mons += $rest
if ($mons.Count -eq 0) { $mons = @($allScreens[0]) }

$monParts = @()
foreach ($m in $mons) {
    $b = $m.Bounds
    $monParts += ('{"x":' + $b.X + ',"y":' + $b.Y + ',"w":' + $b.Width + ',"h":' + $b.Height + '}')
}
$monJson = '[' + ($monParts -join ',') + ']'
$pingBody = '{"ok":1,"os":"win","hv":' + $helperVer + ',"monitors":' + $monJson + '}'

# All monitors merged into one rectangle.
# Used to keep trackpad-style (relative) moves from leaving the desktop.
$bx0 = $mons[0].Bounds.X
$by0 = $mons[0].Bounds.Y
$bx1 = $mons[0].Bounds.X + $mons[0].Bounds.Width - 1
$by1 = $mons[0].Bounds.Y + $mons[0].Bounds.Height - 1
foreach ($mm in $mons) {
    $bb = $mm.Bounds
    if ($bb.X -lt $bx0) { $bx0 = $bb.X }
    if ($bb.Y -lt $by0) { $by0 = $bb.Y }
    if (($bb.X + $bb.Width - 1) -gt $bx1) { $bx1 = $bb.X + $bb.Width - 1 }
    if (($bb.Y + $bb.Height - 1) -gt $by1) { $by1 = $bb.Y + $bb.Height - 1 }
}

$port = 8765
$paired = $null

try {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $port)
    $listener.Start()
} catch {
    Write-Host ""
    Write-Host "ERROR: port $port is already in use."
    Write-Host "Maybe the helper is already running in another window."
    Read-Host "Press Enter to close"
    exit
}

Write-Host ""
Write-Host "  ================================================"
Write-Host "   VR Desktop - Control Helper (Windows)"
Write-Host ("   Helper version : " + $helperVer + "   (updated " + $helperBuild + ")")
Write-Host "  ================================================"
Write-Host ("   Allowed page: " + $(if ($allowOrigin) { $allowOrigin } else { "(not set - first page that connects wins)" }))
Write-Host ("   Monitors    : " + $mons.Count)
for ($mi = 0; $mi -lt $mons.Count; $mi++) {
    $b = $mons[$mi].Bounds
    $tag = ""
    if ($mi -eq 0) { $tag = " (main)" }
    Write-Host ("     Monitor " + ($mi + 1) + $tag + " : " + $b.Width + " x " + $b.Height)
}
Write-Host "   Status      : waiting for your screen-share page"
Write-Host ""
Write-Host "   Keep this window open while using VR."
Write-Host "   Close this window to turn VR control off."
Write-Host ""

# ----------------------------------------------------------------
# Watch loop (2026-08-08b). Replaces the old one-connection-at-a-time
# loop. Design record: handover doc, section 7 (h)-3.
#  - Nobody waits on anybody: a socket is only read when its data has
#    already arrived, so an empty pre-opened browser socket costs
#    nothing (it used to stall every request behind it for 300ms).
#  - Connections are kept alive and reused (HTTP/1.1 default), so
#    mouse moves no longer open ~30 new connections per second.
#  - Input still executes strictly one event at a time, in arrival
#    order, on this single thread. No runspaces, no races.
#  - A failure on one connection closes only that connection; the
#    outer loop itself never exits.
# The 04s guards are unchanged: origin pair and declared size are
# checked before the body is handled; bodies are capped at 256 KB.
# ----------------------------------------------------------------
$EMPTY_MS = 10000   # accepted, nothing received yet (pre-opened sockets may speak later; waiting is free now)
$STALL_MS = 2000    # a request started but is incomplete (matches the page 2000ms abort)
$IDLE_MS  = 30000   # kept-alive connection with no traffic (same value as the Mac helper)
$MAX_CONN = 16      # safety cap; beyond this the oldest connection is dropped
$HDR_MAX  = 32768   # a request head larger than this is nonsense - drop the connection

$conns     = New-Object 'System.Collections.Generic.List[object]'
$readBuf   = New-Object byte[] 16384
$checkList = New-Object System.Collections.ArrayList

function Close-Conn($cn) {
    try { $cn.client.Close() } catch { }
    try { $cn.buf.Dispose() } catch { }   # Pump-Request also disposes; keep both sides tidy
}

# One HTTP response on a kept-alive connection.
function Send-Res($cn, $status, $origin, $resBody) {
    $bb = [System.Text.Encoding]::UTF8.GetBytes($resBody)
    $res = "HTTP/1.1 $status`r`n" +
           "Access-Control-Allow-Origin: $origin`r`n" +
           "Vary: Origin`r`n" +
           "Access-Control-Allow-Methods: GET, POST, OPTIONS`r`n" +
           "Access-Control-Allow-Headers: Content-Type`r`n" +
           "Access-Control-Allow-Private-Network: true`r`n" +
           "Content-Type: application/json`r`n" +
           "Content-Length: $($bb.Length)`r`n" +
           "Connection: keep-alive`r`n`r`n"
    $rb = [System.Text.Encoding]::ASCII.GetBytes($res)
    $cn.stream.Write($rb, 0, $rb.Length)
    if ($bb.Length -gt 0) { $cn.stream.Write($bb, 0, $bb.Length) }
    $cn.stream.Flush()
}

# A bare refusal; the caller closes the connection afterwards.
# Half-close (shutdown Send) first, so the bytes already written are
# delivered instead of being dropped by an immediate reset.
function Send-Refuse($cn, $status) {
    $rb = [System.Text.Encoding]::ASCII.GetBytes(
        "HTTP/1.1 $status`r`nContent-Length: 0`r`nConnection: close`r`n`r`n")
    $cn.stream.Write($rb, 0, $rb.Length)
    $cn.stream.Flush()
    try { $cn.client.Client.Shutdown([System.Net.Sockets.SocketShutdown]::Send) } catch { }
}

# Serves ONE complete request sitting in $cn.buf, if there is one.
# Returns 'more' (served; leftover may hold the next request),
# 'wait' (incomplete), or 'close' (connection must be closed).
function Pump-Request($cn) {
    $all = $cn.buf.ToArray()
    $raw = [System.Text.Encoding]::ASCII.GetString($all)
    $headerEnd = $raw.IndexOf("`r`n`r`n")
    if ($headerEnd -lt 0) {
        if ($all.Length -gt $HDR_MAX) { return 'close' }
        return 'wait'
    }

    $head = $raw.Substring(0, $headerEnd)
    $lines = $head -split "`r`n"
    $reqParts = $lines[0] -split ' '
    $method = $reqParts[0]
    $path = ''
    if ($reqParts.Count -gt 1) { $path = $reqParts[1] }
    if (-not $method -or -not $path) { return 'close' }
    $hdr = @{}
    if ($lines.Count -gt 1) {
        foreach ($l in $lines[1..($lines.Count - 1)]) {
            $ix = $l.IndexOf(':')
            if ($ix -gt 0) {
                $hdr[$l.Substring(0, $ix).Trim().ToLower()] = $l.Substring($ix + 1).Trim()
            }
        }
    }

    $origin = $hdr['origin']
    if (-not $origin) { $origin = '*' }

    # Check the caller and the declared size BEFORE handling the body,
    # so an unpaired page cannot make us process a huge payload (04s).
    if ($path -eq '/input' -and $method -eq 'POST' -and
        (($script:paired -and $origin -ne '*' -and $origin -ne $script:paired) -or
         ($allowOrigin -and $origin -ne $allowOrigin))) {
        Send-Refuse $cn '403 Forbidden'
        return 'close'
    }

    $clen = 0
    if ($hdr['content-length']) { $clen = [int]$hdr['content-length'] }
    # Upper bound. A real batch is a few KB (the page caps text at 1000 chars).
    if ($clen -gt 262144) {
        Send-Refuse $cn '413 Payload Too Large'
        return 'close'
    }

    $bodyStart = $headerEnd + 4
    if ($all.Length - $bodyStart -lt $clen) { return 'wait' }

    $body = ''
    if ($clen -gt 0) { $body = [System.Text.Encoding]::UTF8.GetString($all, $bodyStart, $clen) }

    $status = '200 OK'
    $resBody = '{"ok":1}'

    if ($method -eq 'OPTIONS') {
        $resBody = ''
    }
    elseif ($path -eq '/ping') {
        # With $allowOrigin set, only that page may become the pair, and
        # nobody else gets the monitor list or the version number back.
        if ($allowOrigin -and $origin -ne $allowOrigin) {
            if (-not $script:warnedOrigin) {
                $script:warnedOrigin = $true
                Write-Host "   Refused     : $origin (not the allowed page)"
            }
            $resBody = '{"ok":0}'
        } else {
            if ($null -eq $script:paired -and $origin -ne '*') {
                $script:paired = $origin
                Write-Host "   Connected   : $origin"
            }
            $resBody = $pingBody
        }
    }
    elseif ($path -eq '/input' -and $method -eq 'POST') {
        if (($script:paired -and $origin -ne '*' -and $origin -ne $script:paired) -or
            ($allowOrigin -and $origin -ne $allowOrigin)) {
            $status = '403 Forbidden'
            $resBody = '{"ok":0}'
        } else {
            # One bad event must not take the rest of the batch with it
            # (2026-08-08d). The per-event try below keeps a malformed 'mv'
            # from skipping a later 'up', which would leave a button held
            # down. The outer try still guards JSON parsing itself.
            try {
                $events = $body | ConvertFrom-Json
                foreach ($e in $events) {
                  try {
                    switch ($e.t) {
                            'mv' {
                                $si = 0
                                if ($null -ne $e.s) { $si = [int]$e.s }
                                if ($si -lt 0 -or $si -ge $mons.Count) { $si = 0 }
                                $mb = $mons[$si].Bounds
                                $nx = [Math]::Max(0.0, [Math]::Min(1.0, [double]$e.x))
                                $ny = [Math]::Max(0.0, [Math]::Min(1.0, [double]$e.y))
                                $px = $mb.X + [int]($nx * ($mb.Width - 1))
                                $py = $mb.Y + [int]($ny * ($mb.Height - 1))
                                $pt = New-Object System.Drawing.Point -ArgumentList $px, $py
                                [System.Windows.Forms.Cursor]::Position = $pt
                            }
                            'mvr' {
                                # Trackpad style: shift from wherever the cursor is now.
                                # The real position is read every time, so moving the
                                # physical mouse never throws the next move off.
                                # Absolute assignment (not MOUSEEVENTF_MOVE) keeps the
                                # distance exact: pointer speed and acceleration
                                # settings would otherwise change how far it travels.
                                $cp = [System.Windows.Forms.Cursor]::Position
                                $rx = $cp.X + [int]$e.x
                                $ry = $cp.Y + [int]$e.y
                                if ($rx -lt $bx0) { $rx = $bx0 }
                                if ($rx -gt $bx1) { $rx = $bx1 }
                                if ($ry -lt $by0) { $ry = $by0 }
                                if ($ry -gt $by1) { $ry = $by1 }
                                $rp = New-Object System.Drawing.Point -ArgumentList $rx, $ry
                                [System.Windows.Forms.Cursor]::Position = $rp
                            }
                            'dn' {
                                # b: 0=left, 1=middle (helper v2+), 2=right
                                if ([int]$e.b -eq 2) { [VRMouse]::mouse_event([VRMouse]::RIGHTDOWN, 0, 0, 0, 0) }
                                elseif ([int]$e.b -eq 1) { [VRMouse]::mouse_event([VRMouse]::MIDDLEDOWN, 0, 0, 0, 0) }
                                else { [VRMouse]::mouse_event([VRMouse]::LEFTDOWN, 0, 0, 0, 0) }
                            }
                            'up' {
                                if ([int]$e.b -eq 2) { [VRMouse]::mouse_event([VRMouse]::RIGHTUP, 0, 0, 0, 0) }
                                elseif ([int]$e.b -eq 1) { [VRMouse]::mouse_event([VRMouse]::MIDDLEUP, 0, 0, 0, 0) }
                                else { [VRMouse]::mouse_event([VRMouse]::LEFTUP, 0, 0, 0, 0) }
                            }
                            'sc' {
                                # d = vertical, h = horizontal (helper v3+).
                                # If horizontal feels reversed on a real PC,
                                # flip the sign of $h below (tuning point).
                                $dv = 0; try { $dv = [int]$e.d } catch { }
                                $hv2 = 0; try { $hv2 = [int]$e.h } catch { }
                                if ($dv -ne 0) { [VRMouse]::mouse_event([VRMouse]::WHEEL, 0, 0, $dv * 120, 0) }
                                if ($hv2 -ne 0) { [VRMouse]::mouse_event([VRMouse]::HWHEEL, 0, 0, $hv2 * 120, 0) }
                            }
                            'key' {
                                $k = [string]$e.k
                                $VK = @{ enter = 13; esc = 27; tab = 9; left = 37; up = 38; right = 39; down = 40; backspace = 8; delete = 46; space = 32 }
                                $CB = @{ copy = 67; paste = 86; cut = 88; undo = 90; selectall = 65 }
                                if ($VK.ContainsKey($k)) {
                                    [VRMouse]::keybd_event([byte]$VK[$k], 0, 0, 0)
                                    [VRMouse]::keybd_event([byte]$VK[$k], 0, 2, 0)
                                } elseif ($CB.ContainsKey($k)) {
                                    [VRMouse]::keybd_event(17, 0, 0, 0)
                                    [VRMouse]::keybd_event([byte]$CB[$k], 0, 0, 0)
                                    [VRMouse]::keybd_event([byte]$CB[$k], 0, 2, 0)
                                    [VRMouse]::keybd_event(17, 0, 2, 0)
                                } elseif ($k -eq 'apptab') {
                                    [VRMouse]::keybd_event(18, 0, 0, 0)
                                    [VRMouse]::keybd_event(9, 0, 0, 0)
                                    [VRMouse]::keybd_event(9, 0, 2, 0)
                                    Start-Sleep -Milliseconds 80
                                    [VRMouse]::keybd_event(18, 0, 2, 0)
                                } elseif ($k -eq 'back') {
                                    [VRMouse]::mouse_event(0x0080, 0, 0, 1, 0)
                                    [VRMouse]::mouse_event(0x0100, 0, 0, 1, 0)
                                } elseif ($k -eq 'forward') {
                                    [VRMouse]::mouse_event(0x0080, 0, 0, 2, 0)
                                    [VRMouse]::mouse_event(0x0100, 0, 0, 2, 0)
                                }
                            }
                            'txt' {
                                $s = [string]$e.s
                                # Cap at 1000 chars, same as the sender (index.html).
                                if ($s.Length -gt 1000) { $s = $s.Substring(0, 1000) }
                                if ($s.Length -gt 0) {
                                    # Save the old text so it can be restored
                                    # (images etc. cannot be saved as text).
                                    $prev = ''
                                    try { $prev = Get-Clipboard -Raw } catch { }
                                    if ($null -eq $prev) { $prev = '' }
                                    try { Set-Clipboard -Value $s } catch { }
                                    Start-Sleep -Milliseconds 80
                                    [VRMouse]::keybd_event(17, 0, 0, 0)
                                    [VRMouse]::keybd_event(86, 0, 0, 0)
                                    [VRMouse]::keybd_event(86, 0, 2, 0)
                                    [VRMouse]::keybd_event(17, 0, 2, 0)
                                    # Restoring too early would paste the OLD text:
                                    # the app reads the clipboard slightly after
                                    # Ctrl+V. Wait, then put the old text back.
                                    Start-Sleep -Milliseconds 250
                                    try {
                                        if ($prev -eq '') { Set-Clipboard -Value $null }
                                        else { Set-Clipboard -Value $prev }
                                    } catch { }
                                }
                            }
                            'kb' {
                                $c = [string]$e.c
                                $m = [int]$e.m
                                $KB = @{ KeyA=65;KeyB=66;KeyC=67;KeyD=68;KeyE=69;KeyF=70;KeyG=71;KeyH=72;KeyI=73;KeyJ=74;KeyK=75;KeyL=76;KeyM=77;KeyN=78;KeyO=79;KeyP=80;KeyQ=81;KeyR=82;KeyS=83;KeyT=84;KeyU=85;KeyV=86;KeyW=87;KeyX=88;KeyY=89;KeyZ=90;Digit0=48;Digit1=49;Digit2=50;Digit3=51;Digit4=52;Digit5=53;Digit6=54;Digit7=55;Digit8=56;Digit9=57;Minus=189;Equal=187;BracketLeft=219;BracketRight=221;Backslash=220;Semicolon=186;Quote=222;Backquote=192;Comma=188;Period=190;Slash=191;Enter=13;Escape=27;Backspace=8;Tab=9;Space=32;Delete=46;ArrowLeft=37;ArrowUp=38;ArrowRight=39;ArrowDown=40;Home=36;End=35;PageUp=33;PageDown=34;F1=112;F2=113;F3=114;F4=115;F5=116;F6=117;F7=118;F8=119;F9=120;F10=121;F11=122;F12=123 }
                                if ($KB.ContainsKey($c)) {
                                    if ($m -band 1) { [VRMouse]::keybd_event(17, 0, 0, 0) }
                                    if ($m -band 2) { [VRMouse]::keybd_event(16, 0, 0, 0) }
                                    if ($m -band 4) { [VRMouse]::keybd_event(18, 0, 0, 0) }
                                    if ($m -band 8) { [VRMouse]::keybd_event(91, 0, 0, 0) }
                                    [VRMouse]::keybd_event([byte]$KB[$c], 0, 0, 0)
                                    [VRMouse]::keybd_event([byte]$KB[$c], 0, 2, 0)
                                    if ($m -band 8) { [VRMouse]::keybd_event(91, 0, 2, 0) }
                                    if ($m -band 4) { [VRMouse]::keybd_event(18, 0, 2, 0) }
                                    if ($m -band 2) { [VRMouse]::keybd_event(16, 0, 2, 0) }
                                    if ($m -band 1) { [VRMouse]::keybd_event(17, 0, 2, 0) }
                                }
                            }
                            'ch' {
                                $s = [string]$e.s
                                if ($s.Length -gt 0 -and $s.Length -le 8) {
                                    $esc = ''
                                    foreach ($chr in $s.ToCharArray()) {
                                        if ('+^%~(){}[]'.Contains([string]$chr)) { $esc += '{' + $chr + '}' } else { $esc += $chr }
                                    }
                                    try { [System.Windows.Forms.SendKeys]::SendWait($esc) } catch { }
                                }
                            }
                        }
                  } catch { }
                }
            } catch { }
        }
    }
    else {
        $status = '404 Not Found'
        $resBody = '{"ok":0}'
    }

    Send-Res $cn $status $origin $resBody

    # Keep the connection. Any leftover bytes are the start of the
    # next request; carry them into a fresh buffer.
    $left = $all.Length - ($bodyStart + $clen)
    $nb = New-Object System.IO.MemoryStream
    if ($left -gt 0) {
        $nb.Write($all, $bodyStart + $clen, $left)
        $cn.phase = 'reading'
        $cn.deadline = [DateTime]::UtcNow.AddMilliseconds($STALL_MS)
    } else {
        $cn.phase = 'idle'
        $cn.deadline = [DateTime]::UtcNow.AddMilliseconds($IDLE_MS)
    }
    try { $cn.buf.Dispose() } catch { }
    $cn.buf = $nb
    return 'more'
}

while ($true) {
    # Sleep until any socket (the listener included) has something,
    # 250ms at most so deadlines are still swept. Zero CPU while idle.
    $checkList.Clear()
    [void]$checkList.Add($listener.Server)
    foreach ($cn in $conns) { [void]$checkList.Add($cn.client.Client) }
    try {
        [System.Net.Sockets.Socket]::Select($checkList, $null, $null, 250000)
    } catch {
        Start-Sleep -Milliseconds 20   # never spin if Select itself fails
    }

    # New arrivals. Pending() does not block. The whole block is wrapped:
    # if Pending() itself ever throws, this loop must not exit ($Error-
    # ActionPreference is 'Stop', so an unguarded throw would end the helper).
    try {
        while ($listener.Pending()) {
            $c = $null
            try {
                $c = $listener.AcceptTcpClient()
                $c.NoDelay = $true
                $s = $c.GetStream()
                $s.WriteTimeout = 1000
                $cn = @{
                    client = $c
                    stream = $s
                    buf = (New-Object System.IO.MemoryStream)
                    phase = 'empty'
                    deadline = [DateTime]::UtcNow.AddMilliseconds($EMPTY_MS)
                }
                $conns.Add($cn)
            } catch {
                try { if ($c) { $c.Close() } } catch { }
            }
            while ($conns.Count -gt $MAX_CONN) {
                Close-Conn $conns[0]
                $conns.RemoveAt(0)
            }
        }
    } catch {
        Start-Sleep -Milliseconds 20
    }

    # Pump every connection that has data; sweep deadlines and closed peers.
    $now = [DateTime]::UtcNow
    foreach ($cn in @($conns)) {
        try {
            $got = $false
            while ($cn.stream.DataAvailable) {
                $n = $cn.stream.Read($readBuf, 0, $readBuf.Length)
                if ($n -le 0) { throw 'closed' }
                $cn.buf.Write($readBuf, 0, $n)
                $got = $true
                if ($cn.phase -ne 'reading') {
                    $cn.phase = 'reading'
                    $cn.deadline = $now.AddMilliseconds($STALL_MS)
                }
            }
            if ($got) {
                $r = 'more'
                while ($r -eq 'more') { $r = Pump-Request $cn }
                if ($r -eq 'close') { throw 'refused' }
            }
            # A vanished peer: readable with zero bytes means closed.
            if ($cn.client.Client.Poll(0, [System.Net.Sockets.SelectMode]::SelectRead) -and
                $cn.client.Client.Available -eq 0) {
                throw 'gone'
            }
            if ($now -gt $cn.deadline) { throw 'expired' }
        } catch {
            Close-Conn $cn
            [void]$conns.Remove($cn)
        }
    }
}
