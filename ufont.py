#   Github: https://github.com/AntonVanke/MicroPython-uFont
__version__ = "4.5"

import time
import struct
import framebuf
from array import array

# ---------- 加速检测 ----------
try:
    import micropython
    _NATIVE_SUPPORT = hasattr(micropython, 'native')
except ImportError:
    _NATIVE_SUPPORT = False

DEBUG = False


def timeit(func):
    try:
        _name = func.__name__
    except AttributeError:
        _name = "Unknown"
    def wrapper(*args, **kwargs):
        if DEBUG:
            t = time.ticks_us()
            result = func(*args, **kwargs)
            delta = time.ticks_diff(time.ticks_us(), t)
            print(f'Function {_name} Time = {delta/1000:6.3f}ms')
            return result
        else:
            return func(*args, **kwargs)
    return wrapper


class BMFont:
    """
    BMFont v4.5 — 零除法搬运表 + 满清空缓存
    """

    def __init__(self, font_file, preload_index=False,
                 max_bitmap_cache=150, max_scaled_cache=50,
                 max_fb_cache=80, max_move_table_cache=8):
        self.font_file = font_file
        self.font = open(font_file, "rb")
        self.bmf_info = self.font.read(16)

        if self.bmf_info[0:2] != b"BM":
            raise TypeError("字体文件格式不正确: " + font_file)

        self.version = self.bmf_info[2]
        if self.version not in (3, 4):
            raise TypeError("不支持的字库版本: " + str(self.version))

        self.map_mode = self.bmf_info[3]
        self.start_bitmap = struct.unpack(">I", b'\x00' + self.bmf_info[4:7])[0]
        self.font_size = self.bmf_info[7]
        self.bitmap_size = ((self.font_size + 7) // 8) * self.font_size

        self._MAX_BITMAP_CACHE = max_bitmap_cache
        self._MAX_SCALED_CACHE = max_scaled_cache
        self._MAX_FB_CACHE = max_fb_cache
        self._MAX_MOVE_TABLE_CACHE = max_move_table_cache

        self._bitmap_cache = {}
        self._scaled_cache = {}
        self._fb_cache = {}
        self._index_cache = {}
        self._move_table_cache = {}

        self._cached_palette = None
        self._expand_bytes = None

        self._use_native = _NATIVE_SUPPORT
        if self._use_native:
            try:
                _test_native()
            except:
                self._use_native = False

        self._preload_index = preload_index
        self._index_mem = None
        self._index_codes = None
        if preload_index:
            self._load_index_to_memory()

    # ------------------ 上下文管理器 ------------------
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ------------------ 预加载索引 ------------------
    def _load_index_to_memory(self):
        if self.version == 4:
            code_bytes, fmt = 4, ">I"
        else:
            code_bytes, fmt = 2, ">H"
        index_size = self.start_bitmap - 0x10
        num = index_size // code_bytes
        self.font.seek(0x10)
        data = self.font.read(index_size)
        idx_list = []
        codes = []
        for i in range(num):
            off = i * code_bytes
            code = struct.unpack(fmt, data[off:off+code_bytes])[0]
            idx_list.append((code, i))
            codes.append(code)
            self._index_cache[code] = i
        self._index_mem = idx_list
        self._index_codes = codes

    # ------------------ 文本绘制 ------------------
    @timeit
    def text(self, display, string, x, y, color=0xFFFF, bg_color=0, font_size=None,
             half_char=True, auto_wrap=False, show=True, clear=False,
             alpha_color=0, reverse=False, color_type=-1, line_spacing=0, **kwargs):
        font_size = font_size or self.font_size
        initial_x = x

        palette = None
        color_key = None
        if color_type == -1:
            color_type = 1 if (hasattr(display, 'mode') and display.mode == 'RGB565') else 0
        if color_type == 1:
            palette = [[bg_color & 0xFF, (bg_color & 0xFF00) >> 8],
                       [color & 0xFF, (color & 0xFF00) >> 8]]
            if self._cached_palette != palette:
                self._cached_palette = palette
                self._build_expand_table(palette)
            color_key = (color, bg_color, reverse)
        else:
            palette = [[bg_color & 0xFF, (bg_color & 0xFF00) >> 8],
                       [color & 0xFF, (color & 0xFF00) >> 8]]

        if (color_type == 0 and color == 0 and bg_color != 0) or (color_type == 0 and reverse):
            reverse = True
            alpha_color = -1
        else:
            reverse = False

        if clear:
            try:
                display.clear()
            except AttributeError:
                print("请自行调用 display.fill() 清屏")

        for ch in string:
            w = font_size // 2 if (ord(ch) < 128 and half_char) else font_size
            if auto_wrap and x + w > display.width and x > initial_x:
                y += font_size + line_spacing
                x = initial_x

            if ch == '\n':
                y += font_size + line_spacing
                x = initial_x
                continue
            elif ch == '\t':
                x = ((x // font_size) + 1) * font_size + initial_x % font_size
                continue
            elif ord(ch) < 16:
                continue

            if x > display.width or y > display.height:
                continue

            fb = self._get_framebuffer(ch, font_size, color_type, palette, color_key, reverse)
            if fb is not None:
                display.blit(fb, x, y, alpha_color)

            x += w

        if show:
            display.show()

    # ------------------ FrameBuffer 生成 ------------------
    def _get_framebuffer(self, ch, size, color_type, palette, color_key, reverse):
        code = ord(ch)
        cache_key = (code, size, color_key) if color_type == 1 else (code, size, color_type, reverse)
        if cache_key in self._fb_cache:
            return self._fb_cache[cache_key]

        raw = self._get_bitmap(ch)

        if color_type == 0:
            if size == self.font_size:
                mono = bytearray(raw)
            else:
                mono = self._get_scaled_mono(code, size, raw)
                if reverse:
                    mono = bytearray(mono)
            if reverse:
                mono = self._reverse_bytes_pure(mono)
            fb = framebuf.FrameBuffer(mono, size, size, framebuf.MONO_HLSB)
        else:
            if size == self.font_size:
                data = bytearray(raw) if reverse else raw
                if reverse:
                    data = self._reverse_bytes_pure(data)
                rgb565 = self._flatten_fast(data)
            else:
                rgb565 = self._scaled_to_rgb565_fast(code, size, raw, reverse)
            fb = framebuf.FrameBuffer(rgb565, size, size, framebuf.RGB565)

        if len(self._fb_cache) >= self._MAX_FB_CACHE:
            self._fb_cache.clear()
        self._fb_cache[cache_key] = fb
        return fb

    # ------------------ 位图读取 ------------------
    def _get_bitmap(self, ch):
        code = ord(ch)
        if code in self._bitmap_cache:
            return self._bitmap_cache[code]
        bmp = self._read_bitmap_from_file(ch)
        if len(self._bitmap_cache) >= self._MAX_BITMAP_CACHE:
            self._bitmap_cache.clear()
        self._bitmap_cache[code] = bmp
        return bmp

    def _read_bitmap_from_file(self, word):
        index = self._get_index(word)
        if index == -1:
            return bytearray([0xFF] * self.bitmap_size)
        self.font.seek(self.start_bitmap + index * self.bitmap_size)
        return self.font.read(self.bitmap_size)

    # ------------------ 单色缩放 ------------------
    def _get_scaled_mono(self, code, new_size, raw):
        key = (code, new_size)
        if key in self._scaled_cache:
            return self._scaled_cache[key]

        scaled = bytearray(new_size * ((new_size + 7) // 8))
        move = self._get_move_table(self.font_size, new_size)
        if self._use_native:
            try:
                _scale_mono_move_native(raw, new_size, scaled, move)
            except:
                self._use_native = False
                _scale_mono_move(raw, new_size, scaled, move)
        else:
            _scale_mono_move(raw, new_size, scaled, move)

        if len(self._scaled_cache) >= self._MAX_SCALED_CACHE:
            self._scaled_cache.clear()
        self._scaled_cache[key] = scaled
        return scaled

    # ------------------ 搬运表（零除法生成） ------------------
    def _get_move_table(self, old_size, new_size):
        key = (old_size, new_size)
        if key in self._move_table_cache:
            return self._move_table_cache[key]

        if len(self._move_table_cache) >= self._MAX_MOVE_TABLE_CACHE:
            self._move_table_cache.clear()

        # 预计算行、列映射
        old_rows = [ (r * old_size) // new_size for r in range(new_size) ]
        old_cols = [ (c * old_size) // new_size for c in range(new_size) ]

        total = new_size * new_size
        src_bytes = array('H', [0] * total)
        src_bits  = array('B', [0] * total)
        dst_bytes = array('H', [0] * total)
        dst_bits  = array('B', [0] * total)

        old_rb = (old_size + 7) // 8
        new_rb = (new_size + 7) // 8
        max_idx = old_size * old_size - 1

        idx = 0
        dst_row_base = 0                     # 当前行的起始字节索引
        for nr in range(new_size):
            or_ = old_rows[nr]
            row_offset = or_ * old_size
            dst_col_byte = dst_row_base       # 当前字节
            dst_bit_cnt = 0                   # 当前字节内的位偏移 (0=最高位)
            for nc in range(new_size):
                oi = row_offset + old_cols[nc]
                if oi > max_idx:
                    oi = max_idx
                src_bytes[idx] = oi >> 3
                src_bits[idx] = 7 - (oi & 0x7)
                dst_bytes[idx] = dst_col_byte
                dst_bits[idx] = 7 - dst_bit_cnt
                idx += 1
                # 推进目标位
                dst_bit_cnt += 1
                if dst_bit_cnt >= 8:
                    dst_bit_cnt = 0
                    dst_col_byte += 1
            dst_row_base += new_rb

        table = (src_bytes, src_bits, dst_bytes, dst_bits)
        self._move_table_cache[key] = table
        return table

    # ------------------ 缩放至 RGB565 ------------------
    def _scaled_to_rgb565_fast(self, code, new_size, raw, reverse):
        move = self._get_move_table(self.font_size, new_size)
        total = new_size * new_size
        rgb565 = bytearray(total * 2)
        palette = self._cached_palette

        if self._use_native:
            try:
                _scale_to_rgb565_native(raw, new_size, palette, move, rgb565, reverse)
                return rgb565
            except:
                self._use_native = False

        src_bytes_tbl, src_bits_tbl, _, _ = move
        c0 = bytes(palette[0])
        c1 = bytes(palette[1])
        src_len = len(raw)
        for i in range(total):
            sb = src_bytes_tbl[i]
            if sb >= src_len:
                sb = src_len - 1
            bit = (raw[sb] >> src_bits_tbl[i]) & 1
            if reverse:
                bit ^= 1
            col = c1 if bit else c0
            d = i * 2
            rgb565[d] = col[0]
            rgb565[d+1] = col[1]
        return rgb565

    # ------------------ RGB565 展开 ------------------
    def _build_expand_table(self, palette):
        buf = bytearray(4096)
        for b in range(256):
            base = b * 16
            for p in range(8):
                idx = base + p * 2
                pix = palette[(b >> (7 - p)) & 1]
                buf[idx] = pix[0]
                buf[idx+1] = pix[1]
        self._expand_bytes = bytes(buf)

    def _flatten_fast(self, data):
        out = bytearray(len(data) * 16)
        if self._use_native and self._expand_bytes:
            try:
                _expand_mono_blk_native(data, out, self._expand_bytes)
                return out
            except:
                self._use_native = False
        _expand_mono_blk(data, out, self._expand_bytes)
        return out

    # ------------------ 字符索引 ------------------
    @timeit
    def _get_index(self, word):
        word_code = ord(word)
        if word_code in self._index_cache:
            return self._index_cache[word_code]

        if self._preload_index and self._index_mem:
            codes = self._index_codes
            low, high = 0, len(codes) - 1
            while low <= high:
                mid = (low + high) // 2
                if codes[mid] == word_code:
                    idx = self._index_mem[mid][1]
                    self._index_cache[word_code] = idx
                    return idx
                elif codes[mid] < word_code:
                    low = mid + 1
                else:
                    high = mid - 1
            self._index_cache[word_code] = -1
            return -1

        code_bytes, fmt = (4, ">I") if self.version == 4 else (2, ">H")
        start, end = 0x10, self.start_bitmap - code_bytes
        while start <= end:
            mid = ((start + end) // (code_bytes * 2)) * code_bytes
            self.font.seek(mid)
            target = struct.unpack(fmt, self.font.read(code_bytes))[0]
            if target == word_code:
                idx = (mid - 16) // code_bytes
                self._index_cache[word_code] = idx
                return idx
            elif target < word_code:
                start = mid + code_bytes
            else:
                end = mid - code_bytes
        self._index_cache[word_code] = -1
        return -1

    @staticmethod
    def _reverse_bytes_pure(data):
        result = bytearray(len(data))
        for i in range(len(data)):
            result[i] = ~data[i] & 0xFF
        return result

    def close(self):
        if self.font:
            self.font.close()
            self.font = None

    def __del__(self):
        self.close()

    def clear_caches(self):
        self._bitmap_cache.clear()
        self._scaled_cache.clear()
        self._fb_cache.clear()
        self._index_cache.clear()
        self._move_table_cache.clear()

    @timeit
    def get_bitmap(self, word):
        return self._get_bitmap(word)


# ========== 纯 Python 搬运/展开 ==========
def _scale_mono_move(src, new_size, dst, move_table):
    sb, sbi, db, dbi = move_table
    src_len = len(src)
    for i in range(len(sb)):
        s_byte = sb[i]
        if s_byte >= src_len:
            s_byte = src_len - 1
        if (src[s_byte] >> sbi[i]) & 1:
            dst[db[i]] |= 1 << dbi[i]


def _expand_mono_blk(src, dst, expand_table):
    for i in range(len(src)):
        bo = i * 16
        bi = src[i] * 16
        for j in range(16):
            dst[bo + j] = expand_table[bi + j]


# ========== Native 加速（若可用） ==========
if _NATIVE_SUPPORT:
    @micropython.native
    def _test_native():
        pass

    @micropython.native
    def _scale_mono_move_native(src, new_size, dst, move_table):
        sb, sbi, db, dbi = move_table
        src_len = len(src)
        n = len(sb)
        for i in range(n):
            s_byte = sb[i]
            if s_byte >= src_len:
                s_byte = src_len - 1
            if (src[s_byte] >> sbi[i]) & 1:
                dst[db[i]] |= 1 << dbi[i]

    @micropython.native
    def _expand_mono_blk_native(src, dst, expand_table):
        n = len(src)
        for i in range(n):
            bo = i * 16
            bi = src[i] * 16
            for j in range(16):
                dst[bo + j] = expand_table[bi + j]

    @micropython.native
    def _scale_to_rgb565_native(raw, new_size, palette, move_table, dst, reverse):
        sb, sbi, _, _ = move_table
        c0_l, c0_h = palette[0][0], palette[0][1]
        c1_l, c1_h = palette[1][0], palette[1][1]
        total = new_size * new_size
        src_len = len(raw)
        for i in range(total):
            s_byte = sb[i]
            if s_byte >= src_len:
                s_byte = src_len - 1
            bit = (raw[s_byte] >> sbi[i]) & 1
            if reverse:
                bit ^= 1
            if bit:
                dst[i*2] = c1_l
                dst[i*2+1] = c1_h
            else:
                dst[i*2] = c0_l
                dst[i*2+1] = c0_h
else:
    def _test_native(): pass
    def _scale_mono_move_native(src, new_size, dst, move_table): pass
    def _expand_mono_blk_native(src, dst, expand_table): pass
    def _scale_to_rgb565_native(raw, new_size, palette, move_table, dst, reverse): pass