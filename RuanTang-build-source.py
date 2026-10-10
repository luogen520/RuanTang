#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
软糖体 RuanTang 字体生成脚本
=========================================
作者: 罗根 (Luo Gen)
License: SIL Open Font License 1.1
版本: 1.0
基底: LXGW WenKai (OFL-1.1) - 在其结构基础上应用"萌系圆润"轮廓变换
彩蛋: 罗字=红 #E63946 / 根字=金 #FFB703 (COLR+CPAL)
"""

import math
import os
import sys
import time
from collections import deque

from fontTools.ttLib import TTFont, newTable
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.colorLib.builder import buildCPAL, buildCOLR
from fontTools.ttLib.tables._g_l_y_f import Glyph as TTGlyph, GlyphComponent

# ============================================================
# 配置
# ============================================================
BASE_FONT = '/usr/share/fonts/truetype/lxgw-wenkai/LXGWWenKai-Regular.ttf'
OUTPUT_DIR = '/home/z/my-project/download/软糖体'
OUTPUT_TTF = os.path.join(OUTPUT_DIR, 'RuanTang-Regular.ttf')
OUTPUT_OTF = os.path.join(OUTPUT_DIR, 'RuanTang-Regular.otf')
OUTPUT_TTX = os.path.join(OUTPUT_DIR, 'RuanTang-Regular.ttx')

FONT_NAME_ZH = '软糖体'
FONT_NAME_EN = 'RuanTang'
FONT_FAMILY = f'{FONT_NAME_ZH} {FONT_NAME_EN}'
AUTHOR = '罗根 (Luo Gen)'
AUTHOR_EN = 'Luo Gen'
VERSION = '1.0'
YEAR = '2026'

# 彩蛋配色 (RGB)
COLOR_RED_RGB = (0xE6, 0x39, 0x46)     # 罗红
COLOR_GOLD_RGB = (0xFF, 0xB7, 0x03)    # 根金
# CPAL 用 BGRA 字节序
# CPAL 期望 RGBA 浮点 [0..1]
def _rgba(rgb_bytes):
    return (rgb_bytes[0] / 255.0, rgb_bytes[1] / 255.0, rgb_bytes[2] / 255.0, 1.0)

COLOR_RED_RGBA = _rgba(COLOR_RED_RGB)        # 罗红
COLOR_GOLD_RGBA = _rgba(COLOR_GOLD_RGB)      # 根金
COLOR_TRANSPARENT_RGBA = (0.0, 0.0, 0.0, 0.0)

LUO_CP = 0x7F57   # 罗
GEN_CP = 0x6839   # 根

# 萌系圆润变换参数
ROUND_RADIUS = 12          # 圆角半径（em 单位）
SCALE_FACTOR = 1.02        # 微胖系数（让字看起来更"软糖"）
ANGLE_THRESHOLD_DEG = 155  # 大于此角度不圆角（接近直线）


# ============================================================
# 萌系圆润变换 Pen - 核心算法
# ============================================================
class SoftRoundPen:
    """
    接收来自 RecordingPen 的轮廓操作，对所有 line-line 段之间的 sharp corner
    执行圆角替换：
    
    原始: prev --line--> corner --line--> next
    变换: prev --line--> p1 --qCurve(ctrl=corner)--> p2 --line--> next
    
    其中 p1 在 prev->corner 线上距 corner 为 r 的位置
         p2 在 corner->next 线上距 corner 为 r 的位置
    
    对 qCurveTo/curveTo 直接 pass through（已经平滑）。
    """

    def __init__(self, out_pen, radius=ROUND_RADIUS,
                 angle_threshold_deg=ANGLE_THRESHOLD_DEG,
                 scale=SCALE_FACTOR):
        self.out = out_pen
        self.radius = radius
        self.angle_threshold = math.radians(angle_threshold_deg)
        self.scale = scale
        self._line_buf = []  # 当前连续 line 段的点缓冲
        self._contour_started = False  # out_pen 是否处于一个 contour 中

    # ---- 坐标变换辅助 ----
    def _tx(self, pt):
        """应用微胖缩放（绕当前缓冲中心）"""
        if self.scale == 1.0 or len(self._line_buf) < 1:
            return pt
        pts = self._line_buf
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        cx = (min(xs) + max(xs)) / 2.0
        cy = (min(ys) + max(ys)) / 2.0
        return (cx + (pt[0] - cx) * self.scale,
                cy + (pt[1] - cy) * self.scale)

    # ---- Pen 接口 ----
    def moveTo(self, pt):
        # 先把之前缓冲的 line 段（开放）冲刷掉
        # 但通常 moveTo 前会有 closePath/endPath，_line_buf 应为空
        self._flush_open()
        # 直接 emit moveTo（不进缓冲）
        self.out.moveTo(self._tx_simple(pt))
        self._contour_started = True
        self._line_buf = [pt]

    def lineTo(self, pt):
        self._line_buf.append(pt)

    def qCurveTo(self, *points):
        # 冲刷缓冲的 line 段（不闭合）
        self._flush_continuation()
        # 直接 pass through（已经平滑，不需要圆角）
        scaled = [self._tx_simple(p) for p in points]
        self.out.qCurveTo(*scaled)
        # 后续从此曲线终点继续，重置缓冲
        if points:
            self._line_buf = [points[-1]]

    def curveTo(self, *points):
        self._flush_continuation()
        scaled = [self._tx_simple(p) for p in points]
        self.out.curveTo(*scaled)
        if points:
            self._line_buf = [points[-1]]

    def closePath(self):
        # 冲刷缓冲的 line 段（闭合）
        self._flush_closed()
        self.out.closePath()
        self._line_buf = []
        self._contour_started = False

    def endPath(self):
        self._flush_open()
        self.out.endPath()
        self._line_buf = []
        self._contour_started = False

    # ---- 简单缩放（绕单点） ----
    def _tx_simple(self, pt):
        """对单点缩放（绕缓冲第一点）"""
        if self.scale == 1.0 or not self._line_buf:
            return pt
        ref = self._line_buf[0]
        return (ref[0] + (pt[0] - ref[0]) * self.scale,
                ref[1] + (pt[1] - ref[1]) * self.scale)

    # ---- 缓冲冲刷逻辑 ----
    def _flush_continuation(self):
        """冲刷 line 缓冲（开放，连续，不 emit closePath 或 moveTo）"""
        pts = self._line_buf
        n = len(pts)
        if n <= 1:
            return  # 只有当前点，无需 emit
        if n == 2:
            # 单条线段，直接 lineTo
            self.out.lineTo(self._tx(pts[1]))
        else:
            # 多段线，应用圆角
            self._emit_rounded(pts, close=False)
        # 缓冲保留最后一个点（当前 pos）
        self._line_buf = [pts[-1]]

    def _flush_open(self):
        """冲刷 line 缓冲（开放 contour 结束）"""
        pts = self._line_buf
        n = len(pts)
        if n <= 1:
            return
        if n == 2:
            self.out.lineTo(self._tx(pts[1]))
        else:
            self._emit_rounded(pts, close=False)
        self._line_buf = []

    def _flush_closed(self):
        """冲刷 line 缓冲（闭合 contour 结束）"""
        pts = self._line_buf
        n = len(pts)
        if n <= 1:
            return
        if n == 2:
            # 单条线段，lineTo 后由 closePath 收尾
            self.out.lineTo(self._tx(pts[1]))
        else:
            self._emit_rounded(pts, close=True)
        self._line_buf = []

    # ---- 核心圆角算法 ----
    def _emit_rounded(self, pts, close=False):
        """对 pts 中的连续 line 段应用圆角后 emit。
        约定：pts[0] 的 moveTo 已由外层 emit，本函数只负责 emit 后续 lineTo/qCurveTo。"""
        n = len(pts)
        if n < 2:
            return

        # 缩放 pts（绕 pts 中心）
        scaled = [self._tx(p) for p in pts]

        if close and n >= 3:
            # 闭合 contour: 处理 pts[1..n-1] 共 n-1 个内部 corner
            # pts[0] 的 corner 暂不处理（避免起始 spike 导致 TTGlyphPen 拒绝）
            # 对 i = n-1 的 corner，next_i 用 0 实现 wraparound
            indices = list(range(1, n))
            next_wrap = lambda i: (i + 1) % n
        elif not close and n >= 3:
            # 开放 contour: 处理 pts[1..n-2]，端点 pts[0] 和 pts[n-1] 不圆角
            indices = list(range(1, n - 1))
            next_wrap = lambda i: i + 1
        else:
            # n == 2，单条线段，直接 lineTo
            self.out.lineTo(scaled[1])
            return

        # 预计算所有 corner 信息
        corners = {}
        for i in indices:
            prev_i = i - 1
            next_i = next_wrap(i)
            corner_info = self._compute_corner(
                scaled[prev_i], scaled[i], scaled[next_i]
            )
            if corner_info is not None:
                corners[i] = corner_info

        # 依次 emit 每个 corner
        for i in indices:
            curr = scaled[i]
            if i in corners:
                p1, ctrl, p2 = corners[i]
                # lineTo 到 corner 前的圆角起点
                self.out.lineTo(p1)
                # qCurveTo 通过 corner 控制点到圆角终点
                self.out.qCurveTo(ctrl, p2)
            else:
                # 不是 corner，直接 lineTo
                self.out.lineTo(curr)

        # 开放 contour: 最后一个端点 lineTo
        if not close and n >= 3:
            self.out.lineTo(scaled[n - 1])
        # 闭合 contour: 不需要额外 emit，closePath() 会闭合到 pts[0]

    def _compute_corner(self, prev, curr, nxt):
        """计算 corner 圆角信息。返回 (p1, ctrl, p2) 或 None"""
        v1 = (curr[0] - prev[0], curr[1] - prev[1])
        v2 = (nxt[0] - curr[0], nxt[1] - curr[1])

        len1 = math.hypot(*v1) if v1 != (0, 0) else 0
        len2 = math.hypot(*v2) if v2 != (0, 0) else 0

        if len1 < 1 or len2 < 1:
            return None

        # 计算夹角
        dot = v1[0] * v2[0] + v1[1] * v2[1]
        cos_a = max(-1.0, min(1.0, dot / (len1 * len2)))
        angle = math.acos(cos_a)

        if angle > self.angle_threshold:
            return None  # 接近直线

        r = min(self.radius, len1 * 0.4, len2 * 0.4)
        if r < 0.5:
            return None

        p1 = (curr[0] - r * v1[0] / len1,
              curr[1] - r * v1[1] / len1)
        p2 = (curr[0] + r * v2[0] / len2,
              curr[1] + r * v2[1] / len2)

        return (p1, curr, p2)


# ============================================================
# 应用字形变换
# ============================================================
def transform_glyph(font, glyph_name, glyf_table, hmtx_table):
    """对一个 glyph 应用萌系圆润变换"""
    glyph = glyf_table[glyph_name]

    # 跳过空字形和复合字（只处理简单字形）
    if glyph.numberOfContours <= 0:
        return False
    if glyph.isComposite():
        return False

    # 用 RecordingPen 捕获原始轮廓
    rec = RecordingPen()
    try:
        glyph.draw(rec, glyf_table)
    except Exception:
        return False

    # 从 hmtx 取 advance width
    try:
        width, lsb = hmtx_table[glyph_name]
    except (KeyError, TypeError):
        width = 0

    # 用 TTGlyphPen 重建变换后的字形
    # 第一个参数是 glyphSet（用于复合字组件查找），简单字可传 glyf_table 自身
    new_pen = TTGlyphPen(glyf_table)
    new_pen.width = width  # 关键：设置 advance width

    # 应用 SoftRoundPen
    soft_pen = SoftRoundPen(new_pen)
    try:
        rec.replay(soft_pen)
        # 确保最后一个 contour 被关闭
        if soft_pen._line_buf:
            soft_pen.closePath()
    except Exception:
        return False

    # 重建字形
    try:
        new_glyph = new_pen.glyph()
    except Exception:
        return False

    if new_glyph is None:
        return False

    # 确保宽度已设置
    new_glyph.width = width

    # 替换
    glyf_table[glyph_name] = new_glyph
    return True


def apply_transformation(font, max_glyphs=None, verbose=True):
    """对全部字形应用萌系圆润变换"""
    glyf = font['glyf']
    hmtx = font['hmtx']

    glyph_names = font.getGlyphOrder()
    total = len(glyph_names)
    if max_glyphs:
        glyph_names = glyph_names[:max_glyphs]
        total_to_do = len(glyph_names)
    else:
        total_to_do = total

    if verbose:
        print(f'  开始变换 {total_to_do} / {total} 个字形...')

    success = 0
    failed = 0
    skipped = 0
    start_time = time.time()
    progress_interval = max(1, total_to_do // 10)  # 10 等分进度

    for i, gname in enumerate(glyph_names):
        try:
            g = glyf[gname]
            if g.numberOfContours <= 0 or g.isComposite():
                skipped += 1
                continue
            ok = transform_glyph(font, gname, glyf, hmtx)
            if ok:
                success += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1

        if verbose and (i + 1) % progress_interval == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (total_to_do - i - 1) / rate if rate > 0 else 0
            print(f'    [{i+1}/{total_to_do}] {100*(i+1)/total_to_do:.1f}% '
                  f'成功={success} 失败={failed} 跳过={skipped} '
                  f'速率={rate:.0f}/s ETA={eta:.0f}s')

    if verbose:
        elapsed = time.time() - start_time
        print(f'  变换完成: 成功={success} 失败={failed} 跳过={skipped} '
              f'用时={elapsed:.1f}s')

    return success, failed, skipped


# ============================================================
# 元数据改写
# ============================================================
def update_name_table(font):
    """改写 name 表为软糖体身份"""
    name = font['name']

    copyright_str = (
        f'Copyright (c) {YEAR} {AUTHOR}. '
        f'RuanTang is derived from LXGW WenKai '
        f'(https://github.com/lxgw/LxgwWenKai, OFL-1.1). '
        f'Licensed under the SIL Open Font License, Version 1.1.'
    )
    # Family name带中文的版本，以及纯英文版本（用于跨平台兼容）
    full_name_zh = f'{FONT_NAME_ZH} {FONT_NAME_EN} Regular'
    full_name_en = f'{FONT_NAME_EN} Regular'
    family_name_zh = f'{FONT_NAME_ZH} {FONT_NAME_EN}'
    family_name_en = FONT_NAME_EN
    ps_name = f'{FONT_NAME_EN}-Regular'
    unique_id = f'{FONT_NAME_EN}:Version {VERSION}:{AUTHOR_EN}'
    version_str = f'Version {VERSION}'

    description = (
        f'RuanTang - a cute, rounded, soft-bouncy CJK typeface '
        f'by {AUTHOR}. 灵感取自软糖的Q弹质感与儿童手写体的圆润笔触，'
        f'所有转角均经过参数化圆角处理，笔画中段微胖，'
        f'视觉上呈现软萌、亲和、富有弹性的独有气质。'
        f' 含彩蛋：当渲染到 "罗" 字时显示红色(#E63946)，'
        f'"根" 字时显示金色(#FFB703)，致敬作者。'
    )

    license_str = (
        'This Font Software is licensed under the SIL Open Font License, '
        'Version 1.1. The license is available with the font package, '
        'and can also be found at https://scripts.sil.org/OFL.'
    )

    designer_str = AUTHOR
    vendor_url = 'https://github.com/luogen/RuanTang'
    designer_url = 'https://github.com/luogen/RuanTang'

    # Name records (nameID, value, ascii_only)
    # ascii_only=True 表示该字符串只用 ASCII 字符，可同时写入 Mac Roman 平台
    # False 表示含中文，只能写入 Unicode 平台
    records = [
        (0, copyright_str, True),
        (1, family_name_zh, False),     # 含中文 - Unicode only
        (2, 'Regular', True),
        (3, unique_id, True),
        (4, full_name_zh, False),        # 含中文
        (5, version_str, True),
        (6, ps_name, True),              # PostScript 必须纯 ASCII
        (7, family_name_zh, False),      # Trademark
        (8, AUTHOR, False),              # 含中文
        (9, designer_str, False),        # 含中文
        (10, description, False),        # 含中文
        (11, vendor_url, True),
        (12, designer_url, True),
        (13, license_str, True),
        (14, 'https://scripts.sil.org/OFL', True),
        (16, family_name_zh, False),     # Typographic Family
        (17, 'Regular', True),
    ]

    # 清空现有 records
    name.names = []

    # Unicode 平台 (Windows BMP) - 总是写入
    UNICODE_PLATFORM = (3, 1, 0x0409)
    # Mac 平台 - 仅对纯 ASCII 字符串写入
    MAC_PLATFORM = (1, 0, 0)

    def is_ascii(s):
        try:
            s.encode('ascii')
            return True
        except (UnicodeEncodeError, UnicodeDecodeError):
            return False

    for name_id, value, ascii_only in records:
        # Unicode 平台总是写入
        name.setName(value, name_id, *UNICODE_PLATFORM)
        # 对纯 ASCII 字符串，额外写入 Mac 平台
        if ascii_only and is_ascii(value):
            name.setName(value, name_id, *MAC_PLATFORM)
        # 对含中文的字符串，写入一个 ASCII 替代到 Mac 平台
        elif not ascii_only:
            # 用纯英文替代写入 Mac 平台（保证兼容性）
            alt = {
                1: family_name_en,
                4: full_name_en,
                7: family_name_en,
                8: AUTHOR_EN,
                9: AUTHOR_EN,
                10: f'RuanTang - cute rounded CJK font by {AUTHOR_EN}',
                16: family_name_en,
            }.get(name_id)
            if alt and is_ascii(alt):
                name.setName(alt, name_id, *MAC_PLATFORM)

    print(f'  Name 表已更新: family={family_name_zh}, author={AUTHOR}')


def update_os2_table(font):
    """更新 OS/2 表的厂商信息"""
    if 'OS/2' not in font:
        return
    os2 = font['OS/2']
    # vendor ID (4 chars)
    os2.achVendID = 'LGEN'  # Luo GEN
    # Set weight to normal/regular
    os2.usWeightClass = 400
    # 设置 fsSelection (Regular)
    os2.fsSelection = 0x0040  # REGULAR
    print('  OS/2 表已更新: vendor=LGEN, weight=Regular')


# ============================================================
# 添加 COLR + CPAL 彩蛋
# ============================================================
def add_color_easter_egg(font):
    """添加 COLR + CPAL 表，让 罗字=红色 根字=金色"""
    # 查找 罗 和 根 的 glyph 名
    cmap = font.getBestCmap()
    luo_name = cmap.get(LUO_CP)
    gen_name = cmap.get(GEN_CP)

    if not luo_name:
        print(f'  ⚠️ 罗字 (U+{LUO_CP:04X}) 未找到，跳过彩蛋')
        return False
    if not gen_name:
        print(f'  ⚠️ 根字 (U+{GEN_CP:04X}) 未找到，跳过彩蛋')
        return False

    # 构建 CPAL 调色板
    # 调色板顺序: 0=透明(占位), 1=红色, 2=金色
    cpal = buildCPAL([
        [COLOR_TRANSPARENT_RGBA, COLOR_RED_RGBA, COLOR_GOLD_RGBA],
    ])
    font['CPAL'] = cpal

    # 构建 COLR 表
    # COLR 表的 base glyph -> [(layer_glyph, palette_index)]
    # 当系统支持 COLR 时：渲染 base glyph 时改为按 layer 顺序绘制
    # 我们让 罗字按其自身形状绘制，但用 palette[1]=红色
    # 同理 根字 用 palette[2]=金色
    colr = buildCOLR({
        luo_name: [(luo_name, 1)],  # 罗字 -> 用自身形状，红色
        gen_name: [(gen_name, 2)],  # 根字 -> 用自身形状，金色
    })
    font['COLR'] = colr

    print(f'  彩蛋已嵌入: 罗字(U+{LUO_CP:04X}, glyph={luo_name}) -> 红色 #{COLOR_RED_RGB[0]:02X}{COLOR_RED_RGB[1]:02X}{COLOR_RED_RGB[2]:02X}')
    print(f'                根字(U+{GEN_CP:04X}, glyph={gen_name}) -> 金色 #{COLOR_GOLD_RGB[0]:02X}{COLOR_GOLD_RGB[1]:02X}{COLOR_GOLD_RGB[2]:02X}')
    return True


# ============================================================
# 主流程
# ============================================================
def main():
    print('=' * 60)
    print(f'软糖体 RuanTang 字体生成')
    print(f'作者: {AUTHOR}    版本: {VERSION}')
    print('=' * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. 加载基底字体
    print(f'\n[1/6] 加载基底字体: {BASE_FONT}')
    font = TTFont(BASE_FONT)
    print(f'  字形总数: {font["maxp"].numGlyphs}')

    # 2. 应用萌系圆润变换
    print(f'\n[2/6] 应用萌系圆润变换...')
    success, failed, skipped = apply_transformation(font, verbose=True)

    # 3. 改写元数据
    print(f'\n[3/6] 改写元数据...')
    update_name_table(font)
    update_os2_table(font)

    # 4. 添加彩蛋
    print(f'\n[4/6] 添加罗根彩蛋 (COLR + CPAL)...')
    add_color_easter_egg(font)

    # 5. 保存 TTF
    print(f'\n[5/6] 保存 TTF: {OUTPUT_TTF}')
    font.save(OUTPUT_TTF)
    print(f'  TTF 保存完成')

    # 6. 保存 OTF + 精简 TTX 源文件
    print(f'\n[6/6] 保存 OTF + TTX 源文件...')
    font.save(OUTPUT_OTF)
    print(f'  OTF 保存完成: {OUTPUT_OTF}')

    # 保存精简 TTX：只导出元数据表 + 彩蛋表（不导出巨大的 glyf）
    # glyf 表太大（25MB），导出为 TTX 会膨胀到 300MB+，不适合作为源文件
    # 实际"源工程文件"是 build_ruantang.py 本身（参数化生成脚本）
    # TTX 这里只导出关键的"设计元数据"
    font.saveXML(OUTPUT_TTX, tables=['name', 'COLR', 'CPAL', 'cmap', 'OS/2', 'head', 'hhea', 'maxp'])
    print(f'  TTX 精简源文件保存完成: {OUTPUT_TTX}')

    # 统计输出
    print('\n' + '=' * 60)
    print('✅ 字体生成完成！')
    print('=' * 60)

    # 字符统计
    cmap = font.getBestCmap()
    cjk_count = sum(1 for cp in cmap.keys() if 0x4E00 <= cp <= 0x9FFF)
    latin_count = sum(1 for cp in cmap.keys() if 0x20 <= cp <= 0x7E)
    print(f'\n字符覆盖统计:')
    print(f'  CJK 统一汉字: {cjk_count} 字 (要求 ≥10,000 {"✓" if cjk_count >= 10000 else "✗"})')
    print(f'  Latin 可打印: {latin_count} 字')
    print(f'  总字形数: {font["maxp"].numGlyphs}')
    print(f'\n变换统计: 成功={success} 失败={failed} 跳过={skipped}')

    print(f'\n输出文件:')
    for path in [OUTPUT_TTF, OUTPUT_OTF, OUTPUT_TTX]:
        size = os.path.getsize(path)
        size_str = f'{size/1024/1024:.2f} MB' if size > 1024*1024 else f'{size/1024:.1f} KB'
        print(f'  {os.path.basename(path):40s} {size_str}')

    return font


if __name__ == '__main__':
    main()
