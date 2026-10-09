---
name: jpeg-photo-forensics
description: 本技能用于判定一张 JPEG 是相机直出原图、RAW 转出，还是被修图或被重新编码过，并给出可复核的字节级证据链。当用户问「哪张是原图」「这张有没有修过」「影楼发的是不是原片」「照片被 P 过吗」「两张图哪张是修过的」「这张照片是相机拍的吗」，或需要对照片做来源取证、真伪鉴别、元数据取证、EXIF 溯源时使用。覆盖 EXIF/XMP/IPTC 解析、JPEG 段结构与量化表指纹、ICC 配置溯源、厂商私有 MakerNote 解析、像素级块网格与位移检测。
version: "1.0.0"
display_name: "JPEG 照片取证"
display_name_en: "JPEG Photo Forensics"
description_zh: "判定 JPEG 是相机直出原图、RAW 转出，还是被修图或被重新编码，输出可复核的字节级证据链。覆盖 EXIF/XMP/IPTC 解析、JPEG 段结构与量化表指纹、ICC 配置溯源、厂商私有 MakerNote 解析、像素级块网格与位移检测。"
description_en: "Determine whether a JPEG is camera-original, RAW-converted, or retouched/re-encoded, with a reproducible byte-level evidence chain. Covers EXIF/XMP/IPTC parsing, JPEG segment and quantization-table fingerprints, ICC profile provenance, vendor-private MakerNote parsing, and pixel-level block-grid and displacement detection."
agent_created: true
---

# JPEG 照片取证

判定方向只有两个问题，必须分开回答，绝不能混为一谈：

1. **拍摄来源**：机身直出、RAW 转出，还是根本没到相机（截屏 / 生成 / 翻拍）？
2. **画面是否被修过**：降噪、磨皮、调色、裁切、旋转、去瑕疵？

**「原图」这个词必须拆开说**：画面是原图 ≠ 文件是存储卡上的原始字节。
交付流水线会重写元数据（塞色彩配置、丢缩略图、翻转字节序），这是正常副作用，
**不能当作「修图」的证据**。参照 `references/reference-cases.md` 里的实测对照。

---

## 环境前提（重要，先读）

这类任务常在**受限网络**环境执行，`pip install Pillow numpy` 大概率失败。**不要依赖第三方 Python 库。**

| 用途 | 用什么 |
|---|---|
| 段结构 / EXIF / XMP / MakerNote 解析 | 托管 Python，**仅标准库**（`struct` 足够） |
| 像素级统计 | 托管 Node，原生 typed array |
| 裸像素导出 | PowerShell + `System.Drawing`（绕开 Python 依赖） |

| 工具 | 路径 |
|---|---|
| Python | `C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe` |
| Node | `C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-3/node.exe` |

**沙箱限制（实测）**：Node **无法创建任何子进程**（spawn 托管 Python 报 `EBUSY`，
连 `cmd.exe` 也报 `EBUSY`）；Bash 调用 Python 正常；Bash 不能调 PowerShell
（被安全策略拒绝，必须走 PowerShell 工具）。这些限制决定了下面的流程分工。

---

## 执行流程

### 第 1~4 步：元数据阶段（一条命令跑完）

```
bash scripts/run_forensics.sh "<图片文件或目录>" ["<输出目录>"]
```

输出到 `<图片目录>/_forensics/`（可指定）：`probe.txt`、`meta_<名>.txt`、`struct.txt`、
`makernote.txt`、`manifest.txt`。跑完会打印像素阶段的确切命令。

各步单独调用也可以：

```
python scripts/jpeg_probe.py  a.jpg b.jpg   # 段结构 / SOF 与色度采样 / DQT / EXIF / APPn / 头尾
python scripts/meta_dump.py   a.jpg         # EXIF 全条目 + XMP 全文 + APPP13 IPTC
python scripts/struct_check.py a.jpg        # IFD0 next-IFD 原始字节 / IFD1 / JFIF / DHT / ICC 头
python scripts/canon_mn.py    a.jpg         # 佳能私有 MakerNote（固件/序列号/机型 ID/裁切）
```

**XMP 必须逐字读完**，不能只看「有没有」。它是判断「经手过哪些软件」最直接的证据。

### 第 5 步：像素阶段（仅在第 1~4 步无法定论时做）

先在 PowerShell 里导裸像素，再在 Bash 里跑统计：

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dump_pixels.ps1 `
    -Dir "<图片目录>" -OutDir "<输出目录>/pixels"
```

```
node scripts/pixel_check.js --raw-dir "<输出目录>/pixels" a.jpg [b.jpg]
```

输出四项：**8px 块效应（含 8 个相位）**、**梯度能量**、**平坦区噪声 sigma**、
以及——当正好传入 2 张同尺寸图时——**位移搜索与是否同一帧的判定**。

---

## 判读表

### A. 强烈指向「机身直出」
- 无 `Software` 标签（IFD0 tag 0x0131）
- 厂商 MakerNote 完整：固件版本、内部序列号、机身序列号、传感器几何、`CropInfo`
- `DateTime` = `DateTimeOriginal` = `DateTimeDigitized`，且 `SubSecTime*` 三者一致
- 无 APP13（Photoshop/IPTC）、无 APP14（Adobe）
- 量化表是**相机自己的**（通常比任何编辑器的 quality=100 更细）
- MakerNote 的时区字段有效（如 `TimeInfo` 时区 480 → UTC+8）

### B. 强烈指向「文件被软件重写过」
- 出现 `Software` / XMP `CreatorTool` / `x:xmptk` 工具签名
- XMP 里有 `crs:`（Lightroom/Camera Raw 参数）、`photoshop:`、`History`
- APP13 里有 IPTC 日期或 8BIM 资源；或存在 APP14 Adobe 标记
- **缩略图缺失**（IFD0 next-IFD = `00000000`）而 MakerNote 仍声明缩略图区域
- **EXIF 字节序被翻转**（小端 ↔ 大端）
- 色度采样从 4:4:4 降到 4:2:0，量化表被换成「全 1」或标准表
- **存储像素尺寸与 EXIF `PixelXDimension/YDimension` 或 MakerNote 画幅声明矛盾**
  → 画面被旋转/裁切过而元数据没同步（很硬的证据）
- 同一帧两版之间存在**固定像素位移**（见下）

> `x:xmptk` 是「最后写元数据的库」的签名，不是「制作软件」的签名。
> `XMP Core 4.4.0-Exiv2` = Exiv2 系；`XMP Core 5.x` = Adobe 系。
> 文件被复制/转存也会保留旧签名。

### C. 不能单独定论（别当铁证）
- **内嵌 ICC 色彩配置**。判定方法是看同一批里是否连不同来源的文件都带同一份配置——
  如果是，那就是流水线塞的。见 `references/reference-cases.md` 的跨案例对照。
- **丢缩略图 / 丢 XMP / 元数据被重写**。交付流水线会这么干，与修图无关。
- 文件大小变化、JFIF 分辨率写法（libjpeg 默认 `units=0, 1x1`；Capture One 写 300 dpi）。
- 噪声水平（受曝光与场景影响太大）。
- **8px 块效应 < 1.10**。精量化本身就会让网格消失，所以「检不到网格」**不能**证明被缩放，
  只能说缺乏正向证据。只有 **≥1.10 且峰值集中在单一相位**才说明网格完整、未被重采样。

### D. 同一帧两版的对比（pixel_check.js 的对比模式）
- **对齐后 MAD < 3/255 且相对零位移大幅下降** → 同一帧的两个版本，可继续解读
- **对齐后 MAD > 30/255** → **不是同一帧**。此时梯度能量比、残差分布**全部无效**，
  绝不能说「其中一张被软化/降噪」。脚本会自己拦住并明确警告。
- 存在**非零整体位移** → RAW 转换器单次出片不会产生位移，这是「被另一个程序重排」的强证据
- **梯度能量比**（仅同场景同曝光可比）：<0.95 提示更软（降噪/磨皮）；>1.05 提示更锐；
  0.95~1.05 视为无差异。实测「轻微降噪+重编码」约为 0.979。

---

## 坑（全部实际踩过）

**坑 1：佳能 MakerNote 的值偏移基准是 TIFF 绝对偏移，不是 MakerNote 相对偏移。**
用错基准会读出满屏 `0` / `0xffff` / 乱码，并误判成「字段为空」。
**校验方法**：第一个条目（`0x0001 CanonCameraSettings`）的偏移量减去 MakerNote 在 TIFF 中的
起始偏移，应正好落在 `2 + 条目数×12 + 4` 之后（即紧邻 IFD 目录块之后）。对上了才用这个基准。
`canon_mn.py` 已内置兜底：绝对偏移越界时回退为相对偏移。

**坑 2：不要因为「没有 EXIF」就下「修过」的结论。**
微信 / 微博 / 贴吧等平台转发会无差别压缩并抹除 EXIF，此时「无 EXIF」只说明经过了社交平台。

**坑 3：`pip install` 大概率失败，别浪费时间。** 走原生解析 + PowerShell 取像素 + Node 统计。

**坑 4：像素脚本读错文件——这是最危险的坑（会静默产出假结论）。**
`dump_pixels.ps1` 导出的是**裸像素**（`.bgr`），不是 JPG。分析脚本若把 `.bgr` 当 JPG 解析，
会得到 0 字节或错位数据，所有指标变成 NaN / 0，看起来「图像完全没有噪声」。
`pixel_check.js` 已加字节数断言（必须严格等于 `W×H×3`），不符就报错退出。

**坑 5：`dump_pixels.ps1` 必须写成纯 ASCII。**
Windows PowerShell 5.1 把无 BOM 的 `.ps1` 按 ANSI 解码。中文注释的字节被打乱后可能吃掉
字符串引号，抛出一堆莫名其妙的 `ParseException`。**这个脚本里的注释与报错信息一律用英文。**
Python / Node 脚本不受此限（它们按 UTF-8 读源文件）。

**坑 6：`.ps1` 里不要用 `exit`，用 `throw`。**
`exit` 在通过 `&` 调用或 dot-source 时**会终止调用方的整个会话**，
静默吃掉排在后面的所有工作。调用时优先用独立进程：`powershell -File`。

**坑 7：PowerShell 变量名不区分大小写，参数与局部变量会互相覆盖。**
`param([string]$Src)` 配合局部变量 `$src = New-Object Bitmap($Src)` 会让两者变成同一个变量，
表现为对象「属性取值全为空」而非报错。参数名与局部名要用不同词根
（如参数 `$SrcPath`、局部 `$img`）。

**坑 8：EXIF 里的 `ExifVersion` 与厂商规格书不一致是正常的。**
固件升级会改这个值，不要据此推翻机型判定。机型判定用 `CanonModelID` 这类数值 ID
+ ExifTool 官方表交叉验证。

**坑 9：Node 不能 spawn 子进程（本机沙箱）。** 编排逻辑用 bash 写，不要用 Node 做进程编排。

---

## 输出要求

必须给出**分层结论**，不要给笼统的「是/不是」：

1. **拍摄来源**（机身直出 / RAW 转出 / 非相机），附机型、固件、序列号等硬标识
2. **画面是否被修改**（逐项：调色 / 裁切 / 旋转 / 降噪 / 磨皮），每项给出依据
3. **文件是否被软件转手**，以及证据
4. **明确列出无法确定的部分**（尤其是「像素是否被动过」；量化步长接近 1 时，
   用同一套表重编码在数值上几乎不可测；想清零这层不确定性，只能拿存储卡原始文件做逐字节比对）

对用户的结论必须区分**事实**与**推测**。任何「确凿 / 铁证」级别的措辞都要能对应到具体字节证据；
证据不够时就说不够——宁可给「倾向性结论 + 置信度」，也不要给没有依据的断言。

## 附加资源

- `references/reference-cases.md`：两个已验证案例的完整指纹与实测标尺，
  用作判读标定。「块效应该多大算异常」「梯度比多少才算磨皮」都以那里的实测值为准。
