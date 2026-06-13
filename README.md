# MicroPython  uFont

基于 [AntonVanke/MicroPython-uFont](https://github.com/AntonVanke/MicroPython-uFont) 的修改版本，用于在 MicroPython 中渲染位图字体。

## 与原版的主要差异
 
- 添加了搬运表缩放算法，在支持的设备上可自动使用 @micropython.native 加速  
- 调整了缓存清理策略，尝试减少文字偶然消失的概率  
- 支持字库版本 4（4 字节编码）  
- 增加上下文管理器（with 语句）支持，可以自动关闭字库文件  
- 缺失字符时动态生成占位符位图，不再依赖硬编码的固定长度数据  
- 增加了可选的字符索引预加载功能（preload_index），以减少文件 I/O  
- 改进了颜色模式检测，优先使用 display.mode 判断 RGB565  
- 增加了 clear_caches() 方法，方便手动释放内存

## 系统要求

- MicroPython ≥ 1.17  
- 已在 **ST7789** (240×284) 屏幕、**ESP32‑S3** 上测试通过  
- 需要有足够的 ROM 空间存放字库文件（通常数百 KB 到数 MB）  
- 默认缓存配置下建议空闲 RAM ≥ 80 KB；若开启 `preload_index`，还需额外 40~80 KB

## 安装

将 `ufont.py` 复制到 MicroPython 设备的文件系统即可。

##已知限制
纯 MicroPython 下，首次使用某个缩放比例（如 16→24）时，生成搬运表约需 18 ms（仅一次，之后会缓存）。

若始终使用原始字号（font_size 等于字库大小），则完全不触发缩放，无此开销。

满清空缓存策略在缓存容量设得过低时，可能导致频繁重建，略有抖动。


仅测试了 ST7789 屏幕，其他屏幕未经完整验证。

## 贡献

本项目是个人学习与修改的产物，欢迎提交 Issue 和 Pull Request，但请理解维护资源有限，响应可能较慢。

## 许可证

基于原始 MIT 许可证修改，详见源仓库。

## 快速开始

```python
from ufont import BMFont

# 推荐用 with 管理字体
with BMFont("font.bmf") as font:
    font.text(display, "你好，世界！", 10, 20, font_size=16)
API 简要说明
BMFont(font_file, preload_index=False, max_bitmap_cache=150, ...)
常用参数：

preload_index – 是否把字符索引全部读入内存（约 40~80 KB），可加快查找但占内存

max_bitmap_cache / max_scaled_cache / max_fb_cache – 各缓存的上限值，满则清空

text(display, string, x, y, color=0xFFFF, bg_color=0, font_size=None, half_char=True, ...)

当 font_size 为 None 时使用字库原始尺寸；

half_char=True 时 ASCII 字符占半宽；

auto_wrap=True 时超出屏幕宽度自动换行。
