# -*- coding: utf-8 -*-
"""提取 APP1 XMP 全文 + 完整 EXIF 条目（含未识别 tag）+ APP13 IPTC 明细。"""
import struct, sys, os, re

def segments(path):
    with open(path, "rb") as f:
        data = f.read()
    i = 2
    out = []
    while i < len(data) - 1:
        if data[i] != 0xFF:
            i += 1; continue
        m = data[i + 1]
        if m in (0xFF, 0xD8, 0xD9, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2; continue
        if i + 4 > len(data): break
        ln = struct.unpack(">H", data[i + 2:i + 4])[0]
        out.append((m, data[i + 4:i + 2 + ln]))
        if m == 0xDA:
            break
        i += 2 + ln
    return out, data

TYPENAME = {1: "BYTE", 2: "ASCII", 3: "SHORT", 4: "LONG", 5: "RATIONAL", 6: "SBYTE",
            7: "UNDEFINED", 8: "SSHORT", 9: "SLONG", 10: "SRATIONAL", 11: "FLOAT", 12: "DOUBLE"}
TS = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}

def walk(tiff, e, base, depth, seen):
    if base in seen or depth > 3:
        return
    seen.add(base)
    if base + 2 > len(tiff): return
    n = struct.unpack(e + "H", tiff[base:base + 2])[0]
    print(f"{'  ' * depth}IFD @{base}: {n} entries")
    subs = []
    for k in range(n):
        p = base + 2 + k * 12
        if p + 12 > len(tiff): break
        tag, typ, cnt = struct.unpack(e + "HHI", tiff[p:p + 8])
        sz = TS.get(typ, 1) * cnt
        if sz <= 4:
            off = None
            raw = tiff[p + 8:p + 8 + sz]
        else:
            off = struct.unpack(e + "I", tiff[p + 8:p + 12])[0]
            raw = tiff[off:off + sz]
        preview = ""
        if typ == 2:
            preview = repr(raw.split(b"\x00")[0].decode("utf-8", "replace")[:90])
        elif typ in (1, 7):
            preview = f"<{sz}B> " + raw[:24].hex()
        else:
            try:
                if typ == 3: preview = str(struct.unpack(e + f"{min(cnt,8)}H", raw[:2 * min(cnt,8)]))
                elif typ == 4: preview = str(struct.unpack(e + f"{min(cnt,8)}I", raw[:4 * min(cnt,8)]))
                elif typ in (5, 10):
                    v = []
                    for q in range(min(cnt, 4)):
                        a, b = struct.unpack(e + "II", raw[q * 8:q * 8 + 8])
                        v.append(f"{a}/{b}")
                    preview = str(v)
            except Exception as ex:
                preview = f"<err {ex}>"
        print(f"{'  ' * depth}  0x{tag:04X} {TYPENAME.get(typ, typ):9s} n={cnt:<6d} "
              f"sz={sz:<7d} off={off} {preview[:110]}")
        if tag == 0x927C:
            mksig = raw[:20]
            print(f"{'  ' * depth}     >> MakerNote signature: "
                  f"{mksig[:16].hex()} | {mksig[:16]!r}")
            m = re.search(rb"[\x20-\x7e]{4,}", raw[:200])
            if m: print(f"{'  ' * depth}     >> MakerNote text: {m.group()[:60]!r}")
        if tag == 0x9286:
            print(f"{'  ' * depth}     >> UserComment raw: {raw[:60].hex()}")
        if tag in (0x8769, 0x8825, 0xA005):
            subs.append((tag, struct.unpack(e + "I", tiff[p + 8:p + 12])[0]))
    nxt = struct.unpack(e + "I", tiff[base + 2 + n * 12:base + 6 + n * 12])[0] if base + 6 + n * 12 <= len(tiff) else 0
    if nxt:
        print(f"{'  ' * depth}next IFD -> {nxt}")
        walk(tiff, e, nxt, depth, seen)
    for tag, o in subs:
        print(f"{'  ' * depth}sub-IFD 0x{tag:04X} -> {o}")
        walk(tiff, e, o, depth + 1, seen)


def main(path):
    segs, data = segments(path)
    print("#" * 80)
    print("# " + os.path.basename(path))
    print("#" * 80)
    for m, payload in segs:
        if m == 0xE1 and payload[:6] == b"Exif\x00\x00":
            tiff = payload[6:]
            e = "<" if tiff[:2] == b"II" else ">"
            print(f"\n--- EXIF (tiff header {tiff[:8].hex()}, endian {'little' if e=='<' else 'big'}) "
                  f"total APP1 payload={len(payload)}B ---")
            walk(tiff, e, struct.unpack(e + "I", tiff[4:8])[0], 0, set())
            used = set()
        if m == 0xE1 and payload.startswith(b"http://ns.adobe.com/xap/1.0/\x00"):
            xmp = payload[29:]
            print(f"\n--- XMP ({len(xmp)} bytes) ---")
            print(xmp.decode("utf-8", "replace"))
            print("--- END XMP ---")
        if m == 0xE1 and payload[:30].startswith(b"http://ns.adobe.com/xmp/extension"):
            print(f"\n--- XMP EXTENSION ({len(payload)} bytes) ---")
            print(payload[:200])
        if m == 0xED and payload.startswith(b"Photoshop 3.0"):
            print(f"\n--- APP13 Photoshop/IPTC ({len(payload)} bytes) raw ---")
            print(payload.hex())
            # 解析 IPTC IIM
            i = payload.find(b"8BIM")
            while i >= 0 and i < len(payload):
                rid = struct.unpack(">H", payload[i + 4:i + 6])[0]
                namelen = payload[i + 6]
                namelen += 1 if namelen % 2 == 0 else 0
                p = i + 6 + namelen
                size = struct.unpack(">I", payload[p:p + 4])[0]
                body = payload[p + 4:p + 4 + size]
                if rid == 0x0404:
                    j = 0
                    while j + 5 <= len(body) and body[j] == 0x1C:
                        rec, ds = body[j + 1], body[j + 2]
                        dl = struct.unpack(">H", body[j + 3:j + 5])[0]
                        val = body[j + 5:j + 5 + dl]
                        print(f"    IPTC {rec}:{ds} len={dl} = {val.decode('utf-8','replace')!r}")
                        j += 5 + dl
                else:
                    print(f"    8BIM resource 0x{rid:04X} size={size}")
                p += 4 + size
                if p % 2: p += 1
                i = payload.find(b"8BIM", p)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        main(p)
