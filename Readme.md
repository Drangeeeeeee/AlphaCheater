# AlphaCheater

一个用于拆分带 Alpha 通道视频的桌面原型工具。

它将输入的 RGBA 视频拆分为两路：

- `A_color`：只保留颜色的 RGB 视频。
- `B_alpha`：以灰度保存透明度的 Alpha 视频，黑色为完全透明，白色为完全不透明。

实际编解码由 FFmpeg 完成。程序会读取输入视频的流级色彩参数，并在检测到有效标记时写入输出，避免把未知色彩信息强行猜成某个标准。

## 环境要求

- Python 3.10 或更高版本
- [FFmpeg](https://ffmpeg.org/) 和 `ffprobe`，且两者需可在终端中直接运行
- PySide6

macOS 可通过 Homebrew 安装 FFmpeg：

```bash
brew install ffmpeg
```

安装 Python 依赖：

```bash
python3 -m pip install -r requirements.txt
```

## 运行

```bash
python3 AlphaCheater/AlphaCheater-Prototype.py
```

在界面中选择一个带 Alpha 的视频，分别设置 `A_color` 与 `B_alpha` 的输出位置和编码选项，然后开始拆分。

## 输出含义

`B_alpha` 是透明度遮罩，不是颜色视频。其灰度值归一化后即为 Alpha：

```text
black = 0.0 = fully transparent
white = 1.0 = fully opaque
```

在支持外部 Alpha 的工作流中，透明合成可写为：

```text
C_out = C_fg * alpha + C_bg * (1 - alpha)
```

## 编码与保真边界

- 无损拆分取决于输入像素格式、输出像素格式、色彩转换和所选编码器的组合；FFmpeg 本身不会自动保证无损。
- `B_alpha` 的有损压缩伪影会直接表现为透明边缘的断层、脏边或闪烁，应谨慎设定 CRF。
- Straight / Premultiplied Alpha 通常无法仅凭容器或编码名称可靠判定。程序只在元数据有明确信号时提示推测结果。
- 输入和输出的兼容性由编码、像素格式、封装格式和播放软件共同决定；QuickTime Player 能否播放不是 FFmpeg 能否处理该文件的判据。

## 仓库范围

本仓库只保存程序源码和运行所需文档。测试素材、历史版本、Manim 工程、旁白和视频成片都保留在本地，不会提交到 GitHub。
