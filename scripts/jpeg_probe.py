# -*- coding: utf-8 -*-
"""JPEG 结构探针：不依赖第三方库，直接解析 marker 段 / DQT / APPn / EXIF 头。"""
import struct, sys, os, hashlib

MARKERS = {
    0xD8: "SOI", 0xD9: "EOI", 0xDA: "SOS", 0xC4: "DHT", 0xDB: "DQT",
    0xDD: "DRI", 0xFE: "COM",
}
for i in range(16):
    MARKERS[0xE0 + i] = f"APP{i}"
for i in range(4):
    MARKERS[0xC0 + i] = ["SOF0(baseline)", "SOF1(extended)", "SOF2(progressive)", "SOF3"][i]
MARKERS[0xC5] = "SOF5"; MARKERS[0xC6] = "SOF6"; MARKERS[0xC7] = "SOF7"
MARKERS[0xC9] = "SOF9"; MARKERS[0xCA] = "SOF10(progressive)"; MARKERS[0xCB] = "SOF11"


def read_segments(path):
    with open(path, "rb") as f:
        data = f.read()
    segs = []
    i = 0
    if data[0:2] != b"\xff\xd8":
        return data, [("NOT_JPEG", 0, 0, b"")], {}
    i = 2
    quant = {}
    huff = {}
    sof = {}
    while i < len(data) - 1:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m == 0xD8:
            i += 2; continue
        if m in (0xD9,):
            i += 2; continue
        if 0xD0 <= m <= 0xD7:  # RSTn
            i += 2; continue
        if m == 0x01:  # TEM
            i += 2; continue
        if i + 4 > len(data):
            break
        ln = struct.unpack(">H", data[i + 2:i + 4])[0]
        payload = data[i + 4:i + 2 + ln]
        name = MARKERS.get(m, f"0x{m:02X}")
        segs.append((name, i, ln, payload))
        if m == 0xDB:
            p = 0
            while p < len(payload):
                pq = payload[p] >> 4
                tq = payload[p] & 0x0F
                p += 1
                n = 64 * (2 if pq else 1)
                tbl = payload[p:p + n]
                p += n
                if pq == 0:
                    quant[tq] = list(tbl)
                else:
                    quant[tq] = list(struct.unpack(f">{n//2}H", tbl))
        if m == 0xC4:
            p = 0
            counts = {}
            while p < len(payload):
                tc = payload[p] >> 4
                th = payload[p] & 0x0F
                p += 1
                if p + 16 > len(payload):
                    break
                cnts = list(payload[p:p + 16])
                p += 16
                total = sum(cnts)
                counts[(tc, th)] = (cnts, payload[p:p + total])
                p += total
            huff = counts
        if m in (0xC0, 0xC1, 0xC2, 0xC5, 0xC6, 0xC9, 0xCA):
            prec = payload[0]
            h, w = struct.unpack(">HH", payload[1:5])
            ncomp = payload[5]
            comps = []
            for c in range(ncomp):
                cid = payload[6 + c * 3]
                samp = payload[7 + c * 3]
                tq = payload[8 + c * 3]
                comps.append((cid, samp >> 4, samp & 0x0F, tq))
            sof = {"marker": name, "precision": prec, "width": w, "height": h, "components": comps}
        if m == 0xDA:
            break
        i += 2 + ln
    return data, segs, {"quant": quant, "huff": huff, "sof": sof}


def parse_tiff(tiff, endian_hint="<"):
    """极简 TIFF/EXIF 解析，返回 {tag:(name,value)}。"""
    if tiff[0:2] == b"II":
        e = "<"
    elif tiff[0:2] == b"MM":
        e = ">"
    else:
        return {}
    magic = struct.unpack(e + "H", tiff[2:4])[0]
    if magic != 42:
        return {}
    off = struct.unpack(e + "I", tiff[4:8])[0]

    TAGS = {
        0x010F: "Make", 0x0110: "Model", 0x0112: "Orientation", 0x011A: "XResolution",
        0x0128: "ResolutionUnit", 0x0131: "Software", 0x0132: "DateTime",
        0x013B: "Artist", 0x8298: "Copyright", 0x8769: "ExifIFD", 0x8825: "GPSIFD",
        0x9003: "DateTimeOriginal", 0x9004: "DateTimeDigitized", 0x829A: "ExposureTime",
        0x829D: "FNumber", 0x8822: "ExposureProgram", 0x8827: "ISO",
        0x9000: "ExifVersion", 0x9201: "ShutterSpeedValue", 0x9202: "ApertureValue",
        0x9204: "ExposureCompensation", 0x9207: "MeteringMode", 0x9209: "Flash",
        0x920A: "FocalLength", 0x9286: "UserComment", 0xA001: "ColorSpace",
        0xA002: "PixelXDimension", 0xA003: "PixelYDimension", 0xA402: "ExposureMode",
        0xA403: "WhiteBalance", 0xA406: "SceneCaptureType", 0xA430: "CameraOwnerName",
        0xA431: "BodySerialNumber", 0xA432: "LensSpecification", 0xA433: "LensMake",
        0xA434: "LensModel", 0xA435: "LensSerialNumber", 0x927C: "MakerNote",
        0x0100: "ImageWidth", 0x0101: "ImageLength", 0x011C: "PlanarConfig",
    }
    TYPESIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}

    def read_ifd(base, limit=400):
        out = {}
        order = []
        try:
            n = struct.unpack(e + "H", tiff[base:base + 2])[0]
        except Exception:
            return out, order
        if n > limit:
            return out, order
        for k in range(n):
            p = base + 2 + k * 12
            if p + 12 > len(tiff):
                break
            tag, typ, cnt = struct.unpack(e + "HHI", tiff[p:p + 8])
            sz = TYPESIZE.get(typ, 1) * cnt
            if sz <= 4:
                raw = tiff[p + 8:p + 8 + sz]
            else:
                vo = struct.unpack(e + "I", tiff[p + 8:p + 12])[0]
                raw = tiff[vo:vo + sz]
            val = None
            try:
                if typ == 2:
                    val = raw.split(b"\x00")[0].decode("utf-8", "replace").strip()
                elif typ == 3:
                    val = list(struct.unpack(e + f"{cnt}H", raw))
                    if cnt == 1: val = val[0]
                elif typ == 4:
                    val = list(struct.unpack(e + f"{cnt}I", raw))
                    if cnt == 1: val = val[0]
                elif typ in (5, 10):
                    vals = []
                    for q in range(cnt):
                        a, b = struct.unpack(e + "II", raw[q * 8:q * 8 + 8])
                        vals.append(a / b if b else a)
                    val = vals[0] if cnt == 1 else vals
                elif typ in (1, 7, 6):
                    val = raw
                elif typ == 9:
                    val = struct.unpack(e + f"{cnt}i", raw)
                else:
                    val = raw
            except Exception:
                val = raw
            nm = TAGS.get(tag, f"0x{tag:04X}")
            out[tag] = (nm, val, typ, cnt)
            order.append(tag)
        return out, order

    root, order = read_ifd(off)
    result = {"_endian": e, "_order": order, "_tags": root}
    # 子 IFD
    for key, ifd_tag in (("exif", 0x8769), ("gps", 0x8825)):
        if ifd_tag in root:
            sub_off = root[ifd_tag][1]
            sub, _ = read_ifd(sub_off)
            result[key] = sub
    return result


def app_payloads(segs):
    out = {}
    for name, pos, ln, payload in segs:
        if name.startswith("APP"):
            key = payload[:32].split(b"\x00")[0].decode("latin-1", "replace")
            out.setdefault(name, []).append((len(payload), key))
    return out


def analyze(path):
    data, segs, info = read_segments(path)
    print("=" * 78)
    print(f"FILE: {os.path.basename(path)}")
    print(f"SIZE: {len(data):,} bytes  ({len(data)/1048576:.2f} MB)")
    print(f"MD5 : {hashlib.md5(data).hexdigest()}")
    print(f"SHA1: {hashlib.sha1(data).hexdigest()}")
    print("-" * 78)
    print("MARKER SEGMENTS:")
    for name, pos, ln, payload in segs:
        extra = ""
        if name.startswith("APP"):
            key = payload[:40]
            txt = key.split(b"\x00")[0].decode("latin-1", "replace")
            extra = f"  id='{txt}'"
        print(f"  {name:20s} len={ln:>7d} @{pos}{extra}")
    sof = info["sof"]
    if sof:
        print(f"\nSOF: {sof['marker']}  {sof['width']}x{sof['height']}  "
              f"comps={sof['components']}  precision={sof['precision']}")
    q = info["quant"]
    if q:
        print("\nQUANTIZATION TABLES:")
        for t in sorted(q):
            tbl = q[t]
            print(f"  DQT table {t} (zigzag, first 16): {tbl[:16]}")
            print(f"  DQT table {t} (zigzag, last 16) : {tbl[-16:]}")
            print(f"  DQT table {t} SUM={sum(tbl)}  ALL64={tbl}")
    if info["huff"]:
        print(f"\nHUFFMAN TABLES: {sorted(info['huff'].keys())}")
    # EXIF
    exif_app1 = None
    for name, pos, ln, payload in segs:
        if name == "APP1" and payload[:6] == b"Exif\x00\x00":
            exif_app1 = payload[6:]
            break
    if exif_app1:
        t = parse_tiff(exif_app1)
        print(f"\nEXIF TIFF endian: {t.get('_endian')}")
        def dump(d, title):
            if not d:
                print(f"  [{title}] (empty)")
                return
            print(f"  [{title}]")
            for tag in sorted(d.keys(), key=lambda x: d[x][0]):
                nm, val, typ, cnt = d[tag]
                if nm == "MakerNote":
                    v = f"<binary {len(val) if isinstance(val,(bytes,bytearray)) else '?'} bytes>"
                elif isinstance(val, bytes):
                    v = val[:64].hex() if len(val) <= 64 else val[:64].hex() + "..."
                else:
                    v = val
                print(f"    {nm:22s} = {v}")
        dump(t.get("_tags"), "IFD0")
        dump(t.get("exif"), "ExifIFD")
        dump(t.get("gps"), "GPS")
    else:
        print("\nEXIF: (APP1 Exif 段不存在)")
    # XMP / ICC / others
    print("\nOTHER APP SEGMENTS:")
    for name, pos, ln, payload in segs:
        if name == "APP1" and payload[:6] == b"Exif\x00\x00":
            continue
        if not name.startswith("APP"):
            continue
        head = payload[:120].decode("latin-1", "replace").replace("\n", " ")
        print(f"  {name} len={ln}: {head!r}")
    # 尾部
    print(f"\nTAIL 32 bytes: {data[-32:].hex()}")
    print(f"HEAD 32 bytes: {data[:32].hex()}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        analyze(p)
