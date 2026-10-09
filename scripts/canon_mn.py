# -*- coding: utf-8 -*-
"""解析 Canon 私有 MakerNote（不依赖第三方库），提取固件版本 / 内部序列号 / 文件编号等。"""
import struct, sys, os

TS = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}

# ExifTool Canon.pm 的主要 tag 名称
CANON_TAGS = {
    0x0001: "CanonCameraSettings", 0x0002: "CanonFocalLength", 0x0003: "CanonFlashInfo",
    0x0004: "CanonShotInfo", 0x0005: "CanonPanorama", 0x0006: "CanonImageType",
    0x0007: "CanonFirmwareVersion", 0x0008: "FileNumber", 0x0009: "OwnerName",
    0x000C: "SerialNumber", 0x000D: "CameraInfo", 0x000E: "CanonFileLength",
    0x000F: "CustomFunctions", 0x0010: "CanonModelID", 0x0012: "PictureInfo",
    0x0013: "ThumbnailImageValidArea", 0x0015: "SerialNumberFormat", 0x001A: "SuperMacro",
    0x001C: "DateStampMode", 0x001D: "MyColors", 0x0024: "FaceDetect1",
    0x0025: "FaceDetect2", 0x0026: "AFInfo2", 0x0027: "ContrastInfo",
    0x0028: "ImageUniqueID", 0x0029: "WBInfo", 0x0035: "TimeInfo", 0x003C: "AFInfoSize",
    0x0093: "FileInfo", 0x0095: "LensModel", 0x0096: "InternalSerialNumber",
    0x0097: "DustRemovalData", 0x0098: "CropInfo", 0x0099: "CustomFunctions2",
    0x009A: "AspectInfo", 0x00A0: "ProcessingInfo", 0x00A1: "ToneCurveTable",
    0x00B4: "ColorSpace", 0x00B6: "PreviewImageBorders", 0x00D0: "VRDOffset",
    0x00E0: "SensorInfo", 0x4013: "Ambience",
}


def app1_exif(path):
    with open(path, "rb") as f:
        data = f.read()
    i = 2
    while i < len(data) - 4:
        if data[i] != 0xFF:
            i += 1; continue
        m = data[i + 1]
        if m in (0xFF, 0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2; continue
        ln = struct.unpack(">H", data[i + 2:i + 4])[0]
        payload = data[i + 4:i + 2 + ln]
        if m == 0xE1 and payload[:6] == b"Exif\x00\x00":
            return payload[6:]
        if m == 0xDA:
            break
        i += 2 + ln
    return None


def main(path):
    tiff = app1_exif(path)
    print("=" * 78)
    print("FILE:", os.path.basename(path))
    if tiff is None:
        print("  no Exif APP1"); return
    e = "<" if tiff[:2] == b"II" else ">"
    ifd0 = struct.unpack(e + "I", tiff[4:8])[0]
    n0 = struct.unpack(e + "H", tiff[ifd0:ifd0 + 2])[0]

    # 定位 ExifIFD
    exif_ifd = None
    for k in range(n0):
        p = ifd0 + 2 + k * 12
        tag, typ, cnt = struct.unpack(e + "HHI", tiff[p:p + 8])
        if tag == 0x8769:
            exif_ifd = struct.unpack(e + "I", tiff[p + 8:p + 12])[0]
    if exif_ifd is None:
        print("  no ExifIFD"); return

    # 在 ExifIFD 中找 MakerNote 原始字节及其在 TIFF 中的绝对偏移
    nE = struct.unpack(e + "H", tiff[exif_ifd:exif_ifd + 2])[0]
    mn_raw = None; mn_absoff = None
    for k in range(nE):
        p = exif_ifd + 2 + k * 12
        tag, typ, cnt = struct.unpack(e + "HHI", tiff[p:p + 8])
        if tag == 0x927C:
            mn_absoff = struct.unpack(e + "I", tiff[p + 8:p + 12])[0]
            sz = cnt
            mn_raw = tiff[mn_absoff:mn_absoff + sz]
            print(f"  MakerNote: {sz} bytes, TIFF-abs-off={mn_absoff}, "
                  f"head={mn_raw[:8].hex()}")
    if mn_raw is None:
        print("  no MakerNote"); return

    n = struct.unpack(e + "H", mn_raw[:2])[0]
    print(f"  MakerNote IFD entries: {n}")
    for k in range(n):
        p = 2 + k * 12
        if p + 12 > len(mn_raw):
            break
        tag, typ, cnt = struct.unpack(e + "HHI", mn_raw[p:p + 8])
        sz = TS.get(typ, 1) * cnt
        name = CANON_TAGS.get(tag, "0x%04X" % tag)
        # Canon 的值偏移是 TIFF 绝对偏移，需要减去 MakerNote 在 TIFF 中的起点
        if sz <= 4:
            raw = mn_raw[p + 8:p + 8 + sz]
            where = "inline"
        else:
            off_abs = struct.unpack(e + "I", mn_raw[p + 8:p + 12])[0]
            off_mn = off_abs - mn_absoff          # 主解释：TIFF 绝对偏移
            if not (0 <= off_mn and off_mn + sz <= len(mn_raw)):
                off_mn = off_abs                  # 兜底：MakerNote 相对偏移
            raw = mn_raw[off_mn:off_mn + sz]
            where = f"tiff#{off_abs}->mn+{off_mn}"
        prev = ""
        if typ == 2:
            prev = repr(raw.split(b"\x00")[0].decode("utf-8", "replace")[:80])
        elif sz <= 64 and typ in (1, 3, 7, 4, 8, 9):
            try:
                fmt = {1: "B", 3: "H", 7: "B", 4: "I", 8: "h", 9: "i"}[typ]
                prev = str(struct.unpack(e + f"{min(cnt,16)}{fmt}", raw[:TS[typ] * min(cnt, 16)]))
            except Exception as ex:
                prev = f"<err {ex}>"
        else:
            prev = "<%dB> %s" % (sz, raw[:24].hex())
        print(f"    0x{tag:04X} {name:24s} typ={typ} n={cnt:<4d} sz={sz:<6d} {where:10s} {prev[:100]}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        main(p)
