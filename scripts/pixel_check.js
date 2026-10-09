#!/usr/bin/env node
// 像素级核验：JPEG 8px 块网格完整性（判断是否被重采样）+ 平坦区噪声水平 + 梯度能量。
//
// 前置: 先跑 dump_pixels.ps1 生成裸像素, 命名规则 <prefix><文件名去扩展名>.bgr, 默认 prefix = raw_
// 用法:
//   node pixel_check.js a.jpg b.jpg
//   node pixel_check.js --raw-dir <裸像素目录> --prefix raw_ "C:/path/to/a.jpg" "C:/path/to/b.jpg"
//
// 传入恰好 2 张同尺寸图片时, 额外输出两版的位移搜索与逐像素差异（判断是否二次编码 / 重排 / 软化）。

const fs = require('fs');
const path = require('path');

const BLOCK = 8;          // JPEG 亮度块尺寸
const OFFSETS = [0, 1, 2, 3, 4, 5, 6, 7];

function parseArgs(argv) {
  const o = { prefix: 'raw_', rawdir: null, files: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--prefix') o.prefix = argv[++i];
    else if (a.startsWith('--prefix=')) o.prefix = a.slice('--prefix='.length);
    else if (a === '--raw-dir') o.rawdir = argv[++i];
    else if (a.startsWith('--raw-dir=')) o.rawdir = a.slice('--raw-dir='.length);
    else o.files.push(a);
  }
  return o;
}

// ---------- 自解析 SOF: 拿存储像素尺寸与色度采样 ----------
function findSof(buf) {
  let i = 2;
  while (i + 9 < buf.length) {
    if (buf[i] !== 0xff) { i++; continue; }
    const m = buf[i + 1];
    if (m === 0xff || m === 0x01 || (m >= 0xd0 && m <= 0xd9)) { i += 2; continue; }
    if (m === 0xda) break;                    // 进入熵编码数据, 不再有 marker
    const ln = buf.readUInt16BE(i + 2);
    if (ln < 2 || i + 2 + ln > buf.length) break;
    if (m >= 0xc0 && m <= 0xcf && m !== 0xc4 && m !== 0xc8 && m !== 0xcc) {
      const h = buf.readUInt16BE(i + 5), w = buf.readUInt16BE(i + 7);
      const nc = buf[i + 9];
      const comps = [];
      for (let c = 0; c < nc; c++) {
        const samp = buf[i + 10 + c * 3 + 1];
        comps.push(`${samp >> 4}x${samp & 0x0f}`);
      }
      return { w, h, ncomp: nc, sampling: comps.join(' ') };
    }
    i += 2 + ln;
  }
  return null;
}

function sofSize(file) {
  const size = fs.statSync(file).size;
  const n = Math.min(size, 1 << 22);
  const fd = fs.openSync(file, 'r');
  const buf = Buffer.allocUnsafe(n);
  fs.readSync(fd, buf, 0, n, 0);
  fs.closeSync(fd);
  let sof = findSof(buf);
  if (!sof && n < size) {                     // APPn 链异常长, 退化成整文件读
    const whole = fs.readFileSync(file);
    sof = findSof(whole);
  }
  if (!sof) throw new Error(`未找到 SOF 段: ${file}`);
  return sof;
}

// ---------- 单张: 块网格 + 噪声 + 梯度能量 ----------
function analyze(file, prefix, rawdir) {
  const sof = sofSize(file);
  const { w: W, h: H } = sof;
  const STRIDE = W * 3;                       // 裸像素是紧凑行宽, 无填充
  const dir = rawdir || path.dirname(file);
  const rawPath = path.join(dir, prefix + path.basename(file).replace(/\.[^.]+$/, '') + '.bgr');

  if (!fs.existsSync(rawPath)) {
    throw new Error(`缺少裸像素文件: ${rawPath}\n  先执行: powershell -File dump_pixels.ps1 -Dir "<图片目录>" -OutDir "${dir}"`);
  }
  const expect = STRIDE * H;
  const got = fs.statSync(rawPath).size;
  if (got !== expect) {
    throw new Error(`裸像素尺寸不符: ${rawPath}\n  期望 ${expect} (${W}x${H}x3), 实际 ${got}。` +
      `\n  若为行填充导致, 重新用本技能的 dump_pixels.ps1 导出（它会去掉 stride padding）。`);
  }

  const fd = fs.openSync(rawPath, 'r');
  try {
    // --- 8px 块网格: 对 8 个相位分别统计, 取最强相位 ---
    let yStart = Math.min(1000, Math.floor(H * 0.15));
    let yEnd = Math.min(yStart + 2000, H);
    if (yEnd - yStart < 256) { yStart = 0; yEnd = H; }
    const rows = Buffer.allocUnsafe(STRIDE * (yEnd - yStart));
    fs.readSync(fd, rows, 0, rows.length, yStart * STRIDE);

    const sumH = new Float64Array(BLOCK), cntH = new Float64Array(BLOCK);
    const sumV = new Float64Array(BLOCK), cntV = new Float64Array(BLOCK);
    for (let y = 0; y < yEnd - yStart; y++) {
      const o = y * STRIDE, gy = yStart + y;
      for (let x = 1; x < W; x++) {
        const p = o + x * 3;
        sumH[x & 7] += Math.abs(rows[p] - rows[p - 3]) + Math.abs(rows[p + 1] - rows[p - 2]) + Math.abs(rows[p + 2] - rows[p - 1]);
        cntH[x & 7]++;
      }
      if (y > 0) {
        const q = o - STRIDE;
        let d = 0;
        for (let x = 0; x < W; x++) {
          const p = o + x * 3, r = q + x * 3;
          d += Math.abs(rows[p] - rows[r]) + Math.abs(rows[p + 1] - rows[r + 1]) + Math.abs(rows[p + 2] - rows[r + 2]);
        }
        sumV[gy & 7] += d; cntV[gy & 7] += 1;
      }
    }
    const phase = (sum, cnt, off) => {
      const mean = sum.map((s, i) => (cnt[i] ? s / cnt[i] : 0));
      const b = mean[off % BLOCK];
      let others = 0, n = 0;
      for (const i of OFFSETS) if (i !== off % BLOCK) { others += mean[i]; n++; }
      return (others / n) > 0 ? b / (others / n) : 0;
    };
    const best = (sum, cnt) => {
      let bi = 0, bv = -1, all = [];
      for (const o of OFFSETS) { const v = phase(sum, cnt, o); all.push(v); if (v > bv) { bv = v; bi = o; } }
      return { ratio: bv, offset: bi, all };
    };
    const H_ = best(sumH, cntH), V_ = best(sumV, cntV);

    // --- 梯度能量（软化/降噪的整体指标）---
    let grad = 0, gn = 0;
    const gRows = Buffer.allocUnsafe(STRIDE * Math.min(400, H));
    fs.readSync(fd, gRows, 0, Math.min(400, H) * STRIDE, Math.floor(H / 2) * STRIDE);
    const gH = Math.min(400, H);
    for (let y = 0; y < gH; y++) {
      const o = y * STRIDE;
      for (let x = 1; x < W - 1; x++) {
        const p = o + x * 3;
        grad += Math.abs(gRows[p] - gRows[p + 3]) + Math.abs(gRows[p] - gRows[p - 3]);
        gn += 2;
      }
    }

    // --- 平坦区高频噪声 ---
    // 用"梯度最低的十分位块"而不是绝对阈值(如 梯度<1.0)筛选: 后者在高细节/低噪图上
    // 一块都选不出来, 指标直接作废。取相对最平的块, 才能跨图比较。
    const BS = 64, blocks = [];
    for (let by = 64; by + BS < H - 64; by += BS) {
      const blk = Buffer.allocUnsafe(STRIDE * BS);
      fs.readSync(fd, blk, 0, blk.length, by * STRIDE);
      for (let bx = 0; bx + BS < W - 8; bx += BS) {
        let sg = 0, sn = 0;
        for (let y = 1; y < BS - 1; y += 2) {
          const o = y * STRIDE;
          for (let x = 1; x < BS - 1; x += 2) {
            const p = o + (bx + x) * 3;
            sg += Math.abs(blk[p] - blk[p - 3]) + Math.abs(blk[p] - blk[p + 3])
                + Math.abs(blk[p] - blk[p - STRIDE]) + Math.abs(blk[p] - blk[p + STRIDE]);
            sn++;
          }
        }
        if (!sn) continue;
        let acc = 0, acc2 = 0, m = 0;
        for (let y = 2; y < BS - 2; y++) {
          const o = y * STRIDE;
          for (let x = 2; x < BS - 2; x++) {
            const p = o + (bx + x) * 3;
            const avg = (blk[p - STRIDE] + blk[p + STRIDE] + blk[p - 3] + blk[p + 3]) / 4;
            const d = blk[p] - avg;
            acc += d; acc2 += d * d; m++;
          }
        }
        if (m > 1000) {
          blocks.push({
            g: sg / sn,
            s: Math.sqrt(Math.max(0, acc2 / m - (acc / m) ** 2)) * 0.7071,
          });
        }
      }
    }
    blocks.sort((a, b) => a.g - b.g);
    const take = Math.max(3, Math.floor(blocks.length * 0.1));
    const flat = blocks.slice(0, take).map(x => x.s).sort((a, b) => a - b);
    const q = f => (flat.length ? flat[Math.min(flat.length - 1, Math.floor(flat.length * f))] : null);

    return {
      file, W, H, sampling: sof.sampling, rawPath,
      blockH: H_, blockV: V_, blockiness: Math.max(H_.ratio, V_.ratio),
      gradEnergy: grad / gn,
      blocksTotal: blocks.length, flatBlocks: flat.length,
      flatThresh: flat.length ? blocks[take - 1].g : null,
      nMed: q(0.5), nP10: q(0.1), nP90: q(0.9),
    };
  } finally { fs.closeSync(fd); }
}

// ---------- 两张同尺寸: 位移搜索 + 逐像素差异 ----------
// 先判定"是否同一帧"。不同帧的照片强行做逐像素差异会产出完全误导的结论
// (MAD 恒高、梯度能量比看似"被软化")，必须在输出结论前把它拦住。
function compare(fileA, fileB, ra, rb) {
  if (ra.W !== rb.W || ra.H !== rb.H) {
    console.log(`  [对比] 尺寸不同 (${ra.W}x${ra.H} vs ${rb.W}x${rb.H}), 不做逐像素对比。`);
    return;
  }
  const W = ra.W, H = ra.H, STRIDE = W * 3;
  const A = fs.readFileSync(ra.rawPath), B = fs.readFileSync(rb.rawPath);

  const MAXSHIFT = 64;                          // 硬边界, 超出即视为不匹配
  const y0 = Math.floor(H * 0.2), y1 = Math.floor(H * 0.8);
  const x0 = Math.floor(W * 0.2), x1 = Math.floor(W * 0.8);

  function score(dx, dy, s) {
    let acc = 0, n = 0, big = 0;
    for (let yy = y0; yy < y1; yy += s) {
      const oa = yy * STRIDE, ob = (yy + dy) * STRIDE;
      for (let xx = x0; xx < x1; xx += s) {
        const pa = oa + xx * 3, pb = ob + (xx + dx) * 3;
        const d0 = Math.abs(A[pa] - B[pb]);
        const d1 = Math.abs(A[pa + 1] - B[pb + 1]);
        const d2 = Math.abs(A[pa + 2] - B[pb + 2]);
        acc += d0 + d1 + d2;
        if ((d0 > 16 ? d0 : (d1 > d2 ? d1 : d2)) > 16) big++;
        n++;
      }
    }
    return { mad: acc / (n * 3), bigPct: (100 * big) / n };
  }

  // 三阶段搜索: 粗(±64 / 步长8) -> 中(±7 / 步长4) -> 精(±2 / 步长2)
  let best = { mad: Infinity, bigPct: 0, dx: 0, dy: 0 };
  for (let dy = -MAXSHIFT; dy <= MAXSHIFT; dy += 8) {
    for (let dx = -MAXSHIFT; dx <= MAXSHIFT; dx += 8) {
      const r = score(dx, dy, 8);
      if (r.mad < best.mad) best = { mad: r.mad, bigPct: r.bigPct, dx: dx, dy: dy };
    }
  }
  const c1x = best.dx, c1y = best.dy;
  for (let dy = c1y - 7; dy <= c1y + 7; dy++) {
    for (let dx = c1x - 7; dx <= c1x + 7; dx++) {
      if (Math.abs(dx) > MAXSHIFT || Math.abs(dy) > MAXSHIFT) continue;
      const r = score(dx, dy, 4);
      if (r.mad < best.mad) best = { mad: r.mad, bigPct: r.bigPct, dx: dx, dy: dy };
    }
  }
  const c2x = best.dx, c2y = best.dy;
  for (let dy = c2y - 2; dy <= c2y + 2; dy++) {
    for (let dx = c2x - 2; dx <= c2x + 2; dx++) {
      if (Math.abs(dx) > MAXSHIFT || Math.abs(dy) > MAXSHIFT) continue;
      const r = score(dx, dy, 2);
      if (r.mad < best.mad) best = { mad: r.mad, bigPct: r.bigPct, dx: dx, dy: dy };
    }
  }
  const zero = score(0, 0, 2);                  // 同一采样步长, 可比

  console.log(`  [对比] 零位移 MAD = ${zero.mad.toFixed(2)}/255, >16 级像素占 ${zero.bigPct.toFixed(1)}%`);
  console.log(`  [对比] 最佳位移 dx=${best.dx} dy=${best.dy} (搜索上界 ±${MAXSHIFT}) → ` +
    `MAD = ${best.mad.toFixed(2)}/255, >16 级像素占 ${best.bigPct.toFixed(1)}%`);

  // --- 门槛: 只有对齐后残差足够小, 才允许当作"同一帧的两个版本"继续解读 ---
  const sameFrame = best.mad <= 6 && best.mad <= zero.mad * 0.5;
  if (!sameFrame) {
    console.log(`  [对比] ⚠ 判定为【不是同一帧】: 对齐后残差 ${best.mad.toFixed(2)} 仍过高。`);
    console.log(`         这两张的像素差异由"内容不同"主导, 以下结论一律不成立 —— 不要比较梯度能量,`);
    console.log(`         也不要声称其中一张被软化/降噪。需要的是同一帧的两个版本。`);
    return;
  }

  const shift = Math.hypot(best.dx, best.dy);
  console.log(`  [对比] ✔ 判定为【同一帧的两个版本】: 对齐后残差 ${best.mad.toFixed(2)}/255, ` +
    `相对零位移下降 ${(100 - (best.mad / zero.mad) * 100).toFixed(1)}%`);
  if (shift > 1.5) {
    console.log(`  [对比] · 存在整体位移 ${shift.toFixed(1)}px (dx=${best.dx}, dy=${best.dy})。` +
      `\n           RAW 转换器单次出片不会产生非零位移；这是"被另一个程序重排/重采样"的强证据。`);
  } else {
    console.log(`  [对比] · 无整体位移。两版差异来自编码与局部处理, 不含几何重排。`);
  }
  const gRatio = rb.gradEnergy / ra.gradEnergy;
  console.log(`  [对比] · 梯度能量比 B/A = ${gRatio.toFixed(4)} (<0.95 提示后者更软: 降噪/磨皮; ` +
    `>1.05 提示后者更锐: 锐化或高对比; 0.95~1.05 视为无差异)`);
  console.log(`  [对比] 注意: 逐像素残差只说明"哪里变了", 不说明"变了几成"。` +
    `\n         局部修改要看残差的空间分布(成片块状 vs 仅边缘轮廓), 并回到原始分辨率裁剪图目视确认。`);
}

// ---------- main ----------
const opt = parseArgs(process.argv.slice(2));
if (opt.files.length === 0) {
  console.error('用法: node pixel_check.js [--raw-dir <裸像素目录>] [--prefix raw_] <a.jpg> [b.jpg ...]');
  process.exit(1);
}
const results = [];
for (const f of opt.files) {
  try {
    const r = analyze(f, opt.prefix, opt.rawdir);
    results.push(r);
    console.log('='.repeat(76));
    console.log(`FILE: ${path.basename(r.file)}   ${r.W}x${r.H}  色度采样 ${r.sampling}   裸像素 ${path.basename(r.rawPath)}`);
    console.log(`  8px 块网格最强相位: 水平 ${r.blockH.ratio.toFixed(3)} @ 相位${r.blockH.offset}` +
      `   垂直 ${r.blockV.ratio.toFixed(3)} @ 相位${r.blockV.offset}`);
    console.log(`  → 块效应 = ${r.blockiness.toFixed(4)}  全 8 相位: ` +
      OFFSETS.map((o, i) => `${o}:${r.blockH.all[i].toFixed(2)}`).join(' '));
    console.log(`     判读: ${r.blockiness >= 1.10
      ? '>=1.10 且峰值集中在单一相位 → 8px 网格完整, 未被缩放/重采样'
      : '<1.10 → 检不到明显网格。精量化本身也会让网格消失, 因此【不能】据此断言被缩放, 只能说无正向证据'}`);
    console.log(`  中点带梯度能量 = ${r.gradEnergy.toFixed(3)}/级 (越高越锐; 仅同场景同曝光的两版可横向比较)`);
    if (r.flatBlocks) {
      console.log(`  平坦区高频噪声 sigma: 中位 ${r.nMed.toFixed(3)}  P10 ${r.nP10.toFixed(3)}  P90 ${r.nP90.toFixed(3)}` +
        `   (取自梯度最低的 ${r.flatBlocks}/${r.blocksTotal} 个块, 梯度<=${r.flatThresh.toFixed(2)})`);
    } else {
      console.log(`  平坦区高频噪声: 样本不足(图像过小或过于均匀), 本项无数据 —— 不要把它读成"噪声为 0"`);
    }
  } catch (e) {
    console.log('='.repeat(76));
    console.log(`FILE: ${path.basename(f)}  ❌ ${e.message}`);
  }
}
if (results.length === 2) {
  console.log('='.repeat(76));
  compare(results[0].file, results[1].file, results[0], results[1]);
}
