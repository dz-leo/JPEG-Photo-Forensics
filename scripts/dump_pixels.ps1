# Dump raw pixels from JPEG/PNG files for pixel-level forensics.
#
# IMPORTANT: this file is intentionally ASCII-only. Windows PowerShell 5.1 decodes
# non-BOM .ps1 as ANSI; non-ASCII bytes get mangled and can even break string
# literals, causing bogus ParseException. Keep it ASCII.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File dump_pixels.ps1 -Dir <image-dir>
#   powershell -NoProfile -ExecutionPolicy Bypass -File dump_pixels.ps1 -Dir <dir> -Pattern "*.jpg" -Prefix "raw_" -OutDir <out-dir>
#
# Output: <OutDir>\<Prefix><basename-without-ext>.bgr
#         Tight-packed BGR24, byte size is exactly W*H*3 (no row padding).
#
# Notes: GDI+ does NOT apply EXIF Orientation and does NOT color-manage, so the
#        exported bytes are exactly the stored JPEG pixels, matching the SOF size.
#
# Failure handling: this script throws (never `exit`). `exit` inside a script called
# with & or dot-sourced terminates the CALLER's whole session, silently killing any
# work queued after it. Prefer invoking it as a fresh process:
#   powershell -NoProfile -ExecutionPolicy Bypass -File dump_pixels.ps1 ...

param(
    [string]$Dir = "",
    [string]$Pattern = "*.jpg",
    [string]$Prefix = "raw_",
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

if ([string]::IsNullOrWhiteSpace($Dir)) {
    throw "Missing -Dir. Usage: dump_pixels.ps1 -Dir <image-dir> [-Pattern *.jpg] [-Prefix raw_] [-OutDir <dir>]"
}
if (-not (Test-Path -LiteralPath $Dir)) { throw "Directory not found: $Dir" }
if ([string]::IsNullOrWhiteSpace($OutDir)) { $OutDir = $Dir }
if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

$files = @(Get-ChildItem -LiteralPath $Dir -Filter $Pattern -File)
if ($files.Count -eq 0) { throw "No files matching $Pattern in $Dir" }

foreach ($f in $files) {
    $bmp = New-Object System.Drawing.Bitmap($f.FullName)
    try {
        $w = $bmp.Width
        $h = $bmp.Height
        $rect = New-Object System.Drawing.Rectangle(0, 0, $w, $h)
        $data = $bmp.LockBits($rect,
            [System.Drawing.Imaging.ImageLockMode]::ReadOnly,
            [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
        try {
            $stride = $data.Stride
            $padded = New-Object byte[] ($stride * $h)
            [System.Runtime.InteropServices.Marshal]::Copy($data.Scan0, $padded, 0, $padded.Length)

            # Remove row padding so the row width is exactly W*3.
            # Skipping this misaligns every pixel-level metric when W*3 is not 4-byte aligned.
            $tightW = $w * 3
            if ($stride -eq $tightW) {
                $raw = $padded
            } else {
                $raw = New-Object byte[] ($tightW * $h)
                for ($y = 0; $y -lt $h; $y++) {
                    [System.Array]::Copy($padded, $y * $stride, $raw, $y * $tightW, $tightW)
                }
            }

            $base = [System.IO.Path]::GetFileNameWithoutExtension($f.Name)
            $out = Join-Path $OutDir ($Prefix + $base + ".bgr")
            [System.IO.File]::WriteAllBytes($out, $raw)
            Write-Output ("{0}  {1}x{2}  stride={3} tight={4}  bytes={5}  -> {6}" -f `
                $base, $w, $h, $stride, $tightW, $raw.Length, (Split-Path $out -Leaf))
        } finally { $bmp.UnlockBits($data) }
    } finally { $bmp.Dispose() }
}
