# JPEG Photo Forensics

> 判断一张 JPEG 是**相机直出原图**、**RAW 转出**，还是**被修图 / 被重新编码过**，并给出可复核的
> 字节级证据链。
>
> Determine whether a JPEG is **camera-original**, **RAW-converted**, or **retouched / re-encoded**,
> with a reproducible byte-level evidence chain.

一个免第三方依赖的 JPEG 取证工具集，可单独作为命令行工具使用，也可作为 Skill 装载进
WorkBuddy / CodeBuddy 等支持 `SKILL.md` 的智能体平台。

---

## 它解决什么问题

「哪张是原图」「这张有没有修过」「影楼发的是不是原片」——这类问题的答案**不能被压成一个是 / 否**，
必须分开回答两个互不相干的问题：

| # | 问题 | 典型证据 |
|---|---|---|
| 1 | **拍摄来源**：机身直出 / RAW 转出 / 根本不是相机（截屏、生成、翻拍） | 厂商私有 MakerNote、固件版本、内部序列号、`CropInfo` |
| 2 | **画面是否被修过**：降噪、磨皮、调色、裁切、旋转、去瑕疵 | 量化表指纹、色度采样、像素位移、频域能量分布 |

**关键前提**：「画面是原图」≠「文件是存储卡上的原始字节」。交付流水线会重写元数据
（塞入色彩配置、摘掉缩略图、翻转 EXIF 字节序），这是正常副作用，**不能当作「修图」的证据**。

---

## 环境要求

设计目标是**不依赖任何第三方库**，因此在受限网络（`pip install` 失败）下同样可用。

| 用途 | 依赖 |
|---|---|
| 段结构 / EXIF / XMP / MakerNote 解析 | Python 3（**仅标准库**，`struct` 足够） |
| 像素级统计 | Node.js（原生 typed array） |
| 裸像素导出 | PowerShell + `System.Drawing`（Windows） |

> 本工具集在 Windows + Git Bash + PowerShell 5.1 环境下开发与实测。
> 元数据阶段（第 1~4 步）纯 Python 标准库，跨平台可用；像素阶段（第 5 步）依赖 Windows。

---

## 快速开始

### 元数据阶段（一条命令）

```bash
bash scripts/run_forensics.sh "<图片文件或目录>" ["<输出目录>"]
```

输出到 `<图片目录>/_forensics/`：

| 文件 | 内容 |
|---|---|
| `probe.txt` | JPEG 段结构、SOF 与色度采样、DQT 量化表、APPn、文件头尾 |
| `meta_<名>.txt` | EXIF 全条目 + XMP 全文 + APP13 IPTC |
| `struct.txt` | IFD0 next-IFD 原始字节、IFD1、JFIF、DHT、ICC 头 |
| `makernote.txt` | 厂商私有 MakerNote（已适配佳能） |
| `manifest.txt` | 本次分析的文件清单与哈希 |

跑完会打印像素阶段的**可直接粘贴命令**。

### 分步调用

```bash
python scripts/jpeg_probe.py  a.jpg b.jpg   # 段结构 / 色度采样 / DQT / EXIF / APPn
python scripts/meta_dump.py   a.jpg         # EXIF 全条目 + XMP + IPTC
python scripts/struct_check.py a.jpg        # IFD0 指针 / IFD1 / DHT / ICC 头
python scripts/canon_mn.py    a.jpg         # 佳能 MakerNote（固件 / 序列号 / 机型 ID / 裁切）
```

### 像素阶段（仅在第 1~4 步无法定论时）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dump_pixels.ps1 `
    -Dir "<图片目录>" -OutDir "<输出目录>/pixels"
```

```bash
node scripts/pixel_check.js --raw-dir "<输出目录>/pixels" a.jpg [b.jpg]
```

输出四项：**8px 块效应（含 8 个相位）**、**梯度能量**、**平坦区噪声 sigma**，
以及——当正好传入 2 张同尺寸图时——**位移搜索与「是否同一帧」的判定**。

---

## 判读要点

完整判读表见 [`SKILL.md`](SKILL.md)，实测标尺见
[`references/reference-cases.md`](references/reference-cases.md)。三条最容易踩的：

1. **8px 块效应 < 1.10 不能证明被缩放。** 精量化本身就会让网格消失，检不到网格只说明
   「缺乏正向证据」。只有 **≥1.10 且峰值集中在单一相位**才说明网格完整、未被重采样。
2. **内嵌 ICC 色彩配置不是修图证据。** 判定方法是看同一批里是否连不同来源的文件都带
   同一份配置——如果是，那是流水线塞的。
3. **丢缩略图 / 丢 XMP / 元数据被重写也不是。** 交付流水线会这么干。要定案必须靠
   **多条独立证据同时成立**（元数据血缘 + 编码指纹 + 像素位移）。

关键的量化门槛：

| 指标 | 含义 |
|---|---|
| 对齐后 MAD < 3/255 | 同一帧的两个版本，可继续解读 |
| 对齐后 MAD > 30/255 | **不是同一帧**，后续指标全部无效 |
| 非零整体位移 | RAW 转换器单次出片不会产生，强证据 |
| 梯度能量比 < 0.95 | 提示更软（降噪 / 磨皮）；0.95~1.05 视为无差异 |

---

## 已知局限

- **无法确定「具体是哪个软件」处理的。** 只能确定编码器类别（libjpeg 系 / Photoshop /
  Capture One）与「最后写元数据的库」（`x:xmptk` 签名）——两者都不是「制作软件」。
- **无法证明像素绝对没被动过。** 当量化步长接近 1 时，任何程序用同一套量化表重编码，
  差异小到不可测。要清零这层不确定性，唯一办法是**拿存储卡上的原始文件做逐字节比对**。
- **无法区分「自动化增强」与「人工精修」。** 同一套指纹可能来自画质增强服务，也可能来自人。
- 裸像素为全分辨率原始数据，单张 4000×6000 约 **72 MB**，分析后请及时清理。

工具输出的是**「证据支持的倾向性结论 + 置信度」**，不是没有依据的断言。

---

## 目录结构

```
SKILL.md                    # Skill 主文件（流程 + 判读表 + 已知坑）
scripts/
  run_forensics.sh          # 元数据阶段编排器（一条命令跑完 4 步）
  jpeg_probe.py             # 段结构 / SOF / DQT / EXIF / APPn
  meta_dump.py              # EXIF 全条目 + XMP 全文 + IPTC
  struct_check.py           # IFD 指针 / IFD1 / DHT / ICC
  canon_mn.py               # 佳能厂商私有 MakerNote
  dump_pixels.ps1           # 裸像素导出（PowerShell，纯 ASCII）
  pixel_check.js            # 块网格 / 梯度能量 / 噪声 / 位移比对
references/
  reference-cases.md        # 两个已验证案例的完整指纹与实测标尺
```

---

## 作为 Skill 使用

把本仓库内容放入智能体的技能目录即可，例如：

```
~/.workbuddy/skills/jpeg-photo-forensics/
~/.codebuddy/skills/jpeg-photo-forensics/
```

技能包结构为 `SKILL.md` + `scripts/` + `references/`，符合标准 Skill 规范。

---

## 注意

`references/reference-cases.md` 中的案例来源于真实取证任务，包含相机内部序列号、
机身序列号等标识。这些标识用于说明**证据的可复核性**，不指向任何具体个人。
如需用于公开教学，请自行评估是否需要脱敏。

## 许可

本仓库尚未附加开源许可证。在添加 `LICENSE` 之前，默认保留全部权利。
