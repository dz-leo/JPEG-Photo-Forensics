# -*- coding: utf-8 -*-
"""针对性质询：IFD1 缩略图指针 / FileInfo 明细 / DHT 表 / ICC 配置 / JFIF。
判断依据是"机身原生"与"软件重写"在这些结构上的差异。"""
import struct, sys, os

def load(path):
    with open(path, "rb") as f:
        return f.read()

def segs(data):
    i, out = 2, []
    while i < len(data) - 1:
        if data[i] != 0xFF:
            i += 1; continue
        m = data[i+1]
        if m in (0xFF, 0xD8, 0xD9, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2; continue
        if i + 4 > len(data): break
        ln = struct.unpack(">H", data[i+2:i+4])[0]
        out.append((m, i, ln, data[i+4:i+2+ln]))
        if m == 0xDA: break
        i += 2 + ln
    return out

def main(path):
    data = load(path)
    s = segs(data)
    print("=" * 78)
    print("FILE:", os.path.basename(path))
    print("MARKER ORDER:", " ".join({0xE0:"APP0",0xE1:"APP1",0xE2:"APP2",0xED:"APP13",0xEE:"APP14",0xFE:"COM",0xDB:"DQT",0xC0:"SOF0",0xC4:"DHT",0xDA:"SOS"}.get(m, hex(m)) for m,_,_,_ in s))

    # ---- APP0 JFIF ----
    for m, off, ln, p in s:
        if m == 0xE0 and p[:5] == b"JFIF\x00":
            vmaj, vmin, units, xd, yd = struct.unpack(">BBBH H".replace(" ",""), p[5:12])
            print(f"JFIF: v{vmaj}.{vmin} units={units} density={xd}x{yd} thumb={p[12]}x{p[13]}")
        if m == 0xED:
            print("APP13 Photoshop/IPTC PRESENT  <-- 编辑软件痕迹")
        if m == 0xEE:
            print("APP14 Adobe PRESENT  <-- Adobe 编码器痕迹")
        if m == 0xFE:
            print("COM comment segment PRESENT:", p[:80])

    # ---- Exif: IFD0 next pointer + ExifVersion + IFD1 ----
    for m, off, ln, p in s:
        if m == 0xE1 and p[:6] == b"Exif\x00\x00":
            tiff = p[6:]
            e = "<" if tiff[:2] == b"II" else ">"
            ifd0 = struct.unpack(e + "I", tiff[4:8])[0]
            n0 = struct.unpack(e + "H", tiff[ifd0:ifd0+2])[0]
            nxt_off = ifd0 + 2 + n0*12
            nxt = struct.unpack(e + "I", tiff[nxt_off:nxt_off+4])[0]
            print(f"IFD0 @{ifd0} entries={n0}; next-IFD 指针原始字节={tiff[nxt_off:nxt_off+4].hex()} -> {nxt}")
            print("  IFD1(内嵌缩略图): " + ("存在 @%d" % nxt if nxt else "**不存在**"))
            # ExifIFD
            exif_ifd = None
            for k in range(n0):
                q = ifd0 + 2 + k*12
                tg, ty, cn = struct.unpack(e + "HHI", tiff[q:q+8])
                if tg == 0x8769: exif_ifd = struct.unpack(e + "I", tiff[q+8:q+12])[0]
            nE = struct.unpack(e + "H", tiff[exif_ifd:exif_ifd+2])[0]
            mn_off = None
            for k in range(nE):
                q = exif_ifd + 2 + k*12
                tg, ty, cn = struct.unpack(e + "HHI", tiff[q:q+8])
                if tg == 0x927C: mn_off = struct.unpack(e + "I", tiff[q+8:q+12])[0]
                if tg == 0x9000:
                    print("  ExifVersion =", tiff[struct.unpack(e+"I", tiff[q+8:q+12])[0]:][:4].decode())
                if tg == 0x0131:
                    print("  Software =", tiff[struct.unpack(e+"I", tiff[q+8:q+12])[0]:][:30])
            # MakerNote FileInfo (0x0093)
            if mn_off:
                mn = tiff[mn_off:]
                n = struct.unpack(e + "H", mn[:2])[0]
                for k in range(n):
                    q = 2 + k*12
                    tg, ty, cn = struct.unpack(e + "HHI", mn[q:q+8])
                    if tg in (0x0093, 0x0001):
                        a = struct.unpack(e + "I", mn[q+8:q+12])[0]
                        raw = mn[a-mn_off:a-mn_off+cn*2]
                        name = "FileInfo" if tg == 0x0093 else "CameraSettings"
                        print(f"  MakerNote {name} ({cn*2}B) 全量:")
                        print("    hex:", raw[:88].hex())
                        print("    u32 :", struct.unpack(e + f"{cn*2//4}I", raw[:cn*2 - (cn*2) % 4]))

    # ---- DHT ----
    for m, off, ln, p in s:
        if m == 0xC4:
            q = 0
            while q < len(p):
                tc, th = p[q] >> 4, p[q] & 0x0F
                cnts = list(p[q+1:q+17])
                tot = sum(cnts)
                print(f"DHT class={'AC' if tc else 'DC'} id={th} symbols={tot} counts={cnts}")
                q += 17 + tot

    # ---- ICC ----
    for m, off, ln, p in s:
        if m == 0xE2 and p[:12] == b"ICC_PROFILE\x00":
            seq, tot = p[12], p[13]
            icc = p[14:]
            size = struct.unpack(">I", icc[0:4])[0]
            cmm = icc[4:8]; ver = icc[8:12]
            cls = icc[12:16]; csp = icc[16:20]; pcs = icc[20:24]
            plat = icc[40:44]
            print(f"ICC: seq={seq}/{tot} size={size} cmm={cmm} ver={'.'.join(str(b) for b in ver)} "
                  f"class={cls} colorspace={csp} pcs={pcs} platform={plat}")
            # 找 desc 文本
            for key in (b"desc", b"dmnd", b"dmdd"):
                i = icc.find(key)
                if i > 0:
                    t = icc[i+8:i+80]
                    txt = t[4:].split(b"\x00")[0]
                    print(f"  ICC tag {key.decode()}: {txt!r}")
                    break

if __name__ == "__main__":
    for p in sys.argv[1:]:
        main(p)
