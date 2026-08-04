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
# Helper version (integer). Bump this AND HELPER_VER_REQUIRED in
# index.html together whenever the helper protocol changes.
$helperVer = 3
# Build date. Shown at startup so you can confirm the file was replaced.
# Bump this whenever this file changes (the version number above only
# changes when the agreement with the page changes).
$helperBuild = '2026-08-04'
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

while ($true) {
    $client = $null
    try {
        $client = $listener.AcceptTcpClient()
        $client.NoDelay = $true
        $stream = $client.GetStream()
        # Loopback only: a real request arrives right after connect, so a short
        # wait is enough. Keeping this small stops one empty connection (Chrome
        # opens sockets ahead of time) from stalling every ping behind it.
        # Tuning point: if long text sends ever get cut off, raise this to 1000.
        $stream.ReadTimeout = 300
        $stream.WriteTimeout = 1000

        $ms = New-Object System.IO.MemoryStream
        $buf = New-Object byte[] 8192
        $raw = ''
        $headerEnd = -1
        while ($headerEnd -lt 0) {
            $n = $stream.Read($buf, 0, $buf.Length)
            if ($n -le 0) { break }
            $ms.Write($buf, 0, $n)
            $raw = [System.Text.Encoding]::ASCII.GetString($ms.ToArray())
            $headerEnd = $raw.IndexOf("`r`n`r`n")
        }
        if ($headerEnd -lt 0) { $client.Close(); continue }

        $head = $raw.Substring(0, $headerEnd)
        $lines = $head -split "`r`n"
        $reqParts = $lines[0] -split ' '
        $method = $reqParts[0]
        $path = $reqParts[1]
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

        # Check the caller and the declared size BEFORE reading the body,
        # so an unpaired page cannot make us read a huge payload.
        if ($path -eq '/input' -and $method -eq 'POST' -and
            $paired -and $origin -ne '*' -and $origin -ne $paired) {
            $rb = [System.Text.Encoding]::ASCII.GetBytes(
                "HTTP/1.1 403 Forbidden`r`nContent-Length: 0`r`nConnection: close`r`n`r`n")
            $stream.Write($rb, 0, $rb.Length); $stream.Flush()
            $client.Close(); continue
        }

        $clen = 0
        if ($hdr['content-length']) { $clen = [int]$hdr['content-length'] }
        # Upper bound. A real batch is a few KB (the page caps text at 1000 chars).
        if ($clen -gt 262144) {
            $rb = [System.Text.Encoding]::ASCII.GetBytes(
                "HTTP/1.1 413 Payload Too Large`r`nContent-Length: 0`r`nConnection: close`r`n`r`n")
            $stream.Write($rb, 0, $rb.Length); $stream.Flush()
            $client.Close(); continue
        }
        while (($ms.Length - ($headerEnd + 4)) -lt $clen) {
            $n = $stream.Read($buf, 0, $buf.Length)
            if ($n -le 0) { break }
            $ms.Write($buf, 0, $n)
        }
        $all = $ms.ToArray()
        $body = ''
        $avail = $all.Length - ($headerEnd + 4)
        if ($clen -gt 0 -and $avail -gt 0) {
            $take = [Math]::Min($clen, $avail)
            $body = [System.Text.Encoding]::UTF8.GetString($all, $headerEnd + 4, $take)
        }

        $status = '200 OK'
        $resBody = '{"ok":1}'

        if ($method -eq 'OPTIONS') {
            $resBody = ''
        }
        elseif ($path -eq '/ping') {
            if ($null -eq $paired -and $origin -ne '*') {
                $paired = $origin
                Write-Host "   Connected   : $origin"
            }
            $resBody = $pingBody
        }
        elseif ($path -eq '/input' -and $method -eq 'POST') {
            if ($paired -and $origin -ne '*' -and $origin -ne $paired) {
                $status = '403 Forbidden'
                $resBody = '{"ok":0}'
            } else {
                try {
                    $events = $body | ConvertFrom-Json
                    foreach ($e in $events) {
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
                    }
                } catch { }
            }
        }
        else {
            $status = '404 Not Found'
            $resBody = '{"ok":0}'
        }

        $bb = [System.Text.Encoding]::UTF8.GetBytes($resBody)
        $res = "HTTP/1.1 $status`r`n" +
               "Access-Control-Allow-Origin: $origin`r`n" +
               "Vary: Origin`r`n" +
               "Access-Control-Allow-Methods: GET, POST, OPTIONS`r`n" +
               "Access-Control-Allow-Headers: Content-Type`r`n" +
               "Access-Control-Allow-Private-Network: true`r`n" +
               "Content-Type: application/json`r`n" +
               "Content-Length: $($bb.Length)`r`n" +
               "Connection: close`r`n`r`n"
        $rb = [System.Text.Encoding]::ASCII.GetBytes($res)
        $stream.Write($rb, 0, $rb.Length)
        if ($bb.Length -gt 0) { $stream.Write($bb, 0, $bb.Length) }
        $stream.Flush()
    } catch { }
    finally {
        if ($client) { try { $client.Close() } catch { } }
    }
}
