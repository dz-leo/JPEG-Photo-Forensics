#!/usr/bin/env bash
# JPEG 照片取证 · 元数据阶段编排器（SKILL.md 的第 1~4 步）
#
# 依次调用 jpeg_probe.py / meta_dump.py / struct_check.py / canon_mn.py，
# 全部输出落盘到输出目录，最后打印像素阶段（第 5 步）需要执行的确切命令。
#
# 为什么用 bash 而不是 node 编排：本机沙箱禁止 Node 创建任何子进程
# （连 cmd.exe 都会 EBUSY），而 bash -> python 正常。所以元数据阶段由 bash 驱动，
# 像素阶段需要 PowerShell，改为打印命令交给调用方执行。
#
# 用法:
#   bash run_forensics.sh <图片文件或目录> [输出目录]
#
# 可覆盖的环境变量:
#   FORENSICS_PY    python.exe 路径
#   FORENSICS_NODE  node.exe 路径

set -uo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${FORENSICS_PY:-C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe}"
NODE="${FORENSICS_NODE:-C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-3/node.exe}"

TARGET="${1:-}"
if [ -z "$TARGET" ]; then
  echo "用法: bash run_forensics.sh <图片文件或目录> [输出目录]" >&2
  exit 1
fi
if [ ! -e "$TARGET" ]; then
  echo "目标不存在: $TARGET" >&2
  exit 1
fi
if [ ! -x "$PY" ] && [ ! -f "$PY" ]; then
  echo "找不到 python: $PY  (用 FORENSICS_PY 覆盖)" >&2
  exit 1
fi

IMGS=()
if [ -d "$TARGET" ]; then
  IMG_DIR="$TARGET"
  while IFS= read -r f; do [ -n "$f" ] && IMGS+=("$f"); done < <(
    find "$TARGET" -maxdepth 1 -type f \( -iname '*.jpg' -o -iname '*.jpeg' \) | sort
  )
else
  IMG_DIR="$(dirname "$TARGET")"
  IMGS=("$TARGET")
fi

if [ "${#IMGS[@]}" -eq 0 ]; then
  echo "$IMG_DIR 下没有 jpg/jpeg 文件" >&2
  exit 1
fi

OUT="${2:-$IMG_DIR/_forensics}"
mkdir -p "$OUT"

# 转成 Windows 路径，便于拼出可直接粘贴到 PowerShell 的命令
winpath() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
}

echo "目标: $IMG_DIR    图片 ${#IMGS[@]} 张    输出: $OUT"
echo "------------------------------------------------------------------------"

step() {
  local label="$1"; shift
  local outfile="$1"; shift
  local body
  body="$("$PY" "$@" 2>&1)"
  printf '%s\n' "$body" > "$OUT/$outfile"
  printf '[%s] -> %s  (%s 行)\n' "$label" "$outfile" "$(printf '%s\n' "$body" | wc -l | tr -d ' ')"
}

step "1 段结构+EXIF" probe.txt "$SKILL_DIR/scripts/jpeg_probe.py" "${IMGS[@]}"
for f in "${IMGS[@]}"; do
  base="$(basename "$f")"
  base="${base%.*}"
  step "2 EXIF/XMP 全文 [$base]" "meta_${base}.txt" "$SKILL_DIR/scripts/meta_dump.py" "$f"
done
step "3 ICC/缩略图指针" struct.txt "$SKILL_DIR/scripts/struct_check.py" "${IMGS[@]}"
step "4 厂商 MakerNote" makernote.txt "$SKILL_DIR/scripts/canon_mn.py" "${IMGS[@]}"

{
  echo "generated: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "imageDir : $IMG_DIR"
  echo "outDir   : $OUT"
  echo "images   :"
  for f in "${IMGS[@]}"; do echo "  - $(basename "$f")"; done
} > "$OUT/manifest.txt"

echo "------------------------------------------------------------------------"
echo "元数据阶段完成。产物目录: $OUT"
echo "文件清单:"
ls -1 "$OUT"
echo
echo "下一步（仅在元数据不足以定论时才需要）—— 像素阶段必须在 PowerShell 里执行:"
echo
echo "  powershell -NoProfile -ExecutionPolicy Bypass -File \"$(winpath "$SKILL_DIR/scripts/dump_pixels.ps1")\" -Dir \"$(winpath "$IMG_DIR")\" -OutDir \"$(winpath "$OUT/pixels")\""
echo
printf '  "%s" "%s" --raw-dir "%s"' "$(winpath "$NODE")" "$(winpath "$SKILL_DIR/scripts/pixel_check.js")" "$(winpath "$OUT/pixels")"
for f in "${IMGS[@]:0:2}"; do printf ' "%s"' "$(winpath "$f")"; done
echo
echo
echo "把「同一帧的两个版本」两个文件一起传给 pixel_check.js，会自动启用对比模式（位移搜索 + 是否同一帧判定）。"
