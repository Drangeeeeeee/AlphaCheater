#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Split an RGBA video into A_color RGB and B_alpha grayscale streams.

Requires PySide6 plus FFmpeg and FFprobe on the system PATH.
"""

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# =========================
# FFmpeg 工具函数
# =========================

def run_cmd_capture(cmd: List[str]) -> Tuple[int, str]:
    """运行命令并返回输出。"""
    try:
        process = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return process.returncode, process.stdout
    except FileNotFoundError as e:
        return 127, str(e)


def check_ffmpeg_available() -> bool:
    """检查 ffmpeg / ffprobe 是否可用。"""
    for exe in ["ffmpeg", "ffprobe"]:
        code, _ = run_cmd_capture([exe, "-version"])
        if code != 0:
            return False
    return True


def probe_video_info(input_path: str) -> dict:
    """读取视频信息。"""
    code, out = run_cmd_capture([
        "ffprobe",
        "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        input_path,
    ])
    if code != 0:
        raise RuntimeError(out)
    return json.loads(out)


def video_summary_text(input_path: str) -> str:
    """生成视频信息摘要。"""
    try:
        info = probe_video_info(input_path)
        streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
        if not streams:
            return "没有检测到视频流。"

        s = streams[0]
        pix_fmt = s.get("pix_fmt", "")
        alpha_hint = (
            "可能包含 Alpha"
            if any(key in pix_fmt for key in ["yuva", "rgba", "bgra", "argb", "abgr"])
            else "不确定是否包含 Alpha"
        )

        duration = s.get("duration") or info.get("format", {}).get("duration")
        bit_rate = s.get("bit_rate") or info.get("format", {}).get("bit_rate")

        return (
            f"codec: {s.get('codec_name')}\n"
            f"pix_fmt: {pix_fmt}  [{alpha_hint}]\n"
            f"alpha_type: {describe_alpha_interpretation(s, info.get('format', {}))}\n"
            f"size: {s.get('width')} x {s.get('height')}\n"
            f"frame_rate: {s.get('r_frame_rate')}\n"
            f"duration: {duration}\n"
            f"bit_rate: {bit_rate}\n"
            f"color_space: {s.get('color_space')}\n"
            f"color_transfer: {s.get('color_transfer')}\n"
            f"color_primaries: {s.get('color_primaries')}\n"
            f"color_range: {s.get('color_range')}\n"
        )
    except Exception as e:
        return f"读取视频信息失败：{e}"


def get_input_video_stream(input_path: str) -> dict:
    """返回输入文件的第一个视频流信息。"""
    info = probe_video_info(input_path)
    streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise RuntimeError("没有检测到视频流。")
    return streams[0]


def display_value(value) -> str:
    """把 ffprobe 可能缺失的值显示成稳定文本。"""
    if value is None:
        return "未标注"
    text = str(value).strip()
    return text if text else "未标注"


def has_alpha_pixel_format(pix_fmt: str) -> bool:
    """根据像素格式粗略判断是否包含 Alpha 平面/通道。"""
    pix_fmt = (pix_fmt or "").lower()
    alpha_markers = ["yuva", "rgba", "bgra", "argb", "abgr", "gbrap"]
    return any(marker in pix_fmt for marker in alpha_markers)


def pixel_bit_depth(stream: dict) -> str:
    """从 ffprobe stream 或 pix_fmt 推测位深，仅用于提示。"""
    bits = stream.get("bits_per_raw_sample") or stream.get("bits_per_sample")
    if bits:
        return str(bits)

    pix_fmt = stream.get("pix_fmt") or ""
    match = re.search(r"(?:p|gray|gbrp)(\d+)(?:le|be)?", pix_fmt)
    if match:
        return match.group(1)

    if pix_fmt:
        return "8"
    return "未知"


def describe_alpha_interpretation(stream: dict, format_info: dict | None = None) -> str:
    """
    尽量从元数据判断 Alpha 解释方式。

    大多数视频不会可靠标注 Straight / Premultiplied，因此这里宁可提示未知，
    也不凭编码名称或容器做强猜测。
    """
    format_info = format_info or {}
    tag_text_parts = []
    for tags in [stream.get("tags", {}), format_info.get("tags", {})]:
        if isinstance(tags, dict):
            for key, value in tags.items():
                tag_text_parts.append(str(key).lower())
                tag_text_parts.append(str(value).lower())

    tag_text = " ".join(tag_text_parts)
    if "straight" in tag_text or "unassociated alpha" in tag_text or "unpremult" in tag_text:
        return "Straight Alpha（从元数据推测）"
    if "premult" in tag_text or "associated alpha" in tag_text:
        return "Premultiplied Alpha（从元数据推测）"

    pix_fmt = stream.get("pix_fmt") or ""
    if has_alpha_pixel_format(pix_fmt):
        return "无法可靠检测（检测到 Alpha 像素格式，但未标注 Straight/Premultiplied）"
    return "未检测到 Alpha 像素格式"


def input_video_attributes_text(input_path: str) -> str:
    """生成适合显示在编码选项区域的输入属性摘要。"""
    try:
        info = probe_video_info(input_path)
        format_info = info.get("format", {})
        streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
        if not streams:
            return "未检测到视频流。"

        stream = streams[0]
        profile = stream.get("profile")
        codec_parts = [display_value(stream.get("codec_name"))]
        if profile:
            codec_parts.append(f"profile={profile}")

        container = format_info.get("format_long_name") or format_info.get("format_name")
        duration = stream.get("duration") or format_info.get("duration")
        bit_rate = stream.get("bit_rate") or format_info.get("bit_rate")
        frame_rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate")

        lines = [
            f"封装：{display_value(container)}",
            f"原编码：{'，'.join(codec_parts)}",
            f"像素格式：{display_value(stream.get('pix_fmt'))}，位深：{pixel_bit_depth(stream)} bit",
            f"Alpha：{describe_alpha_interpretation(stream, format_info)}",
            f"尺寸/帧率：{display_value(stream.get('width'))} x {display_value(stream.get('height'))}，{display_value(frame_rate)}",
            f"时长/码率：{display_value(duration)}，{display_value(bit_rate)}",
            f"色彩空间：{display_value(stream.get('color_space'))}",
            f"传递函数：{display_value(stream.get('color_transfer'))}",
            f"色彩原色：{display_value(stream.get('color_primaries'))}",
            f"色彩范围：{display_value(stream.get('color_range'))}",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"读取输入属性失败：{e}"


def container_name_from_suffix(suffix: str) -> str:
    """根据输出扩展名显示封装格式。"""
    suffix = suffix.lower()
    names = {
        ".mov": "MOV / QuickTime",
        ".mp4": "MP4 / ISO BMFF",
        ".m4v": "MP4 / ISO BMFF",
        ".mkv": "MKV / Matroska",
        ".avi": "AVI",
        ".webm": "WebM",
    }
    return names.get(suffix, f"未知封装（{suffix or '无扩展名'}）")


def output_container_note(out_rgb: str, out_alpha: str) -> str:
    """输出 A/B 实际路径对应的封装格式日志。"""
    rgb_suffix = Path(out_rgb).suffix
    alpha_suffix = Path(out_alpha).suffix
    return (
        "[输出封装格式]\n"
        f"A 颜色视频：{container_name_from_suffix(rgb_suffix)} ({rgb_suffix or '无扩展名'})\n"
        f"B Alpha 视频：{container_name_from_suffix(alpha_suffix)} ({alpha_suffix or '无扩展名'})"
    )


def valid_color_value(value) -> bool:
    """判断 ffprobe 返回的色彩参数是否有效。"""
    if value is None:
        return False
    if not isinstance(value, str):
        return False
    value = value.strip().lower()
    return value not in ["", "unknown", "unspecified", "n/a", "na", "none"]


def safe_output_color_flag(probe_key: str, value) -> bool:
    """判断色彩参数是否适合直接作为 FFmpeg 输出标签写入。"""
    if not valid_color_value(value):
        return False

    normalized = str(value).strip().lower()
    if probe_key == "color_space" and normalized in ["gbr", "rgb"]:
        return False

    return True


def build_inherited_stream_color_flags(stream: dict) -> tuple[list[str], dict]:
    """
    从输入视频流中读取并显式继承色彩数学参数。

    注意：
      - 全局容器元数据会清除，不作为色彩参数继承手段。
      - 这里只在 ffprobe 检测到有效且适合作为输出标签的 stream 值时添加对应参数。
      - 检测不到就不写任何该项色彩标签，避免凭空猜测。
      - RGB/GBR 是输入像素颜色模型，不适合直接写成 YUV 输出的 -colorspace。
    """
    mapping = [
        ("color_primaries", "-color_primaries"),
        ("color_transfer", "-color_trc"),
        ("color_space", "-colorspace"),
        ("color_range", "-color_range"),
    ]

    # Preserve stream-level color interpretation explicitly, but do not copy
    # container metadata such as a title, author, or location to shared output.
    flags = ["-map_metadata", "-1"]
    detected = {}

    for probe_key, ffmpeg_arg in mapping:
        value = stream.get(probe_key)
        detected[probe_key] = value
        if safe_output_color_flag(probe_key, value):
            flags.extend([ffmpeg_arg, value])

    return flags, detected


def format_applied_color_flags(flags: list[str]) -> str:
    """格式化实际传给 FFmpeg 的色彩/元数据参数。"""
    lines = ["[实际传给 FFmpeg 的色彩参数]"]
    color_flags = [flag for flag in flags if flag not in {"-map_metadata", "-1"}]
    if len(flags) == 2 and flags == ["-map_metadata", "-1"]:
        lines.append("已清除输入文件的全局元数据；没有写入 stream 级色彩标签。")
    elif color_flags:
        lines.append(" ".join(shlex.quote(x) for x in flags))
    else:
        lines.append("已清除输入文件的全局元数据；没有写入 stream 级色彩标签。")
    lines.append("说明：color_space 为 gbr/rgb 时不会直接写入 -colorspace，避免 ProRes/H.264/H.265 等 YUV 输出编码器报错。")
    return "\n".join(lines)


def format_detected_color_params(detected: dict) -> str:
    """格式化原始色彩参数日志。"""
    lines = ["[输入视频流色彩参数检测]"]
    lines.append(f"color_primaries: {detected.get('color_primaries')}")
    lines.append(f"color_transfer : {detected.get('color_transfer')}")
    lines.append(f"colorspace     : {detected.get('color_space')}")
    lines.append(f"color_range    : {detected.get('color_range')}")
    lines.append("说明：检测到有效且适合作为输出标签的值时会显式写入；检测不到或为 unknown/unspecified 时不强行写入。")
    return "\n".join(lines)


def codec_quality_note(rgb_mode: str, alpha_mode: str) -> str:
    """
    输出当前编码质量参数。
    重点让用户明确知道 CRF / qscale 的实际数值。
    """
    notes = ["[编码质量参数]"]

    if rgb_mode == RGB_MODE_PRORES444:
        notes.append("A 颜色视频：ProRes 4444，qscale=4。")
    elif rgb_mode == RGB_MODE_COLOR_SAFE:
        notes.append("A 颜色视频：ProRes 4444，qscale=1。")
    elif rgb_mode == RGB_MODE_PRORES422:
        notes.append("A 颜色视频：ProRes 422 HQ，qscale=5。")
    elif rgb_mode == RGB_MODE_H265_10BIT:
        notes.append("A 颜色视频：H.265 10bit，CRF=14。")
    elif rgb_mode == RGB_MODE_H265_MIN:
        notes.append("A 颜色视频：H.265，CRF=28。")
    elif rgb_mode == RGB_MODE_H264:
        notes.append("A 颜色视频：H.264，CRF=14。")
    elif rgb_mode == RGB_MODE_FFV1_ARCHIVE:
        notes.append("A 颜色视频：FFV1 无损留档，yuv444p16le，文件较大。")

    if alpha_mode == ALPHA_MODE_FFV1:
        notes.append("B Alpha 视频：FFV1，无损。")
    elif alpha_mode == ALPHA_MODE_FFV1_HIGHBIT:
        notes.append("B Alpha 视频：FFV1 高位深无损灰度，gray16le，文件较大。")
    elif alpha_mode == ALPHA_MODE_PRORES:
        notes.append("B Alpha 视频：ProRes，qscale=4。")
    elif alpha_mode == ALPHA_MODE_PRORES_EDGE:
        notes.append("B Alpha 视频：ProRes，qscale=1。")
    elif alpha_mode == ALPHA_MODE_H264_HIGH:
        notes.append("B Alpha 视频：H.264，CRF=15。")
        notes.append("提示：当前 B Alpha 视频 CRF 为 15，属于较高质量灰度压缩。")
    elif alpha_mode == ALPHA_MODE_H265_SMALL:
        notes.append("B Alpha 视频：H.265 10bit，CRF=12。")
        notes.append("提示：当前 B Alpha 视频 CRF 为 12，透明边缘质量优先。")
    elif alpha_mode == ALPHA_MODE_H265_MIN:
        notes.append("B Alpha 视频：H.265，CRF=23。")
        notes.append("提示：当前 B Alpha 视频 CRF 为 23，这是灰度 Alpha 的低质量极限阈值。")

    if alpha_mode in [ALPHA_MODE_H264_HIGH, ALPHA_MODE_H265_SMALL, ALPHA_MODE_H265_MIN]:
        crf_value = {
            ALPHA_MODE_H264_HIGH: 15,
            ALPHA_MODE_H265_SMALL: 12,
            ALPHA_MODE_H265_MIN: 23,
        }[alpha_mode]
        if crf_value > 23:
            notes.append(f"警告：当前 B Alpha 视频 CRF 为 {crf_value}，超过 23，平滑透明边缘将出现明显断层。")
        else:
            notes.append(f"Alpha CRF 检查：当前 B Alpha 视频 CRF 为 {crf_value}，未超过 23。")

    return "\n".join(notes)



# =========================
# 预设与编码
# =========================

RGB_MODE_COLOR_SAFE = "ProRes 4444 色彩最少变化"
RGB_MODE_PRORES444 = "ProRes 4444 RGB 高质量"
RGB_MODE_PRORES422 = "ProRes 422 HQ"
RGB_MODE_H265_10BIT = "H.265 10bit 小体积"
RGB_MODE_H265_MIN = "H.265 最小体积"
RGB_MODE_H264 = "H.264 兼容"
RGB_MODE_FFV1_ARCHIVE = "FFV1 无损留档"

ALPHA_MODE_FFV1 = "FFV1 无损灰度 推荐"
ALPHA_MODE_FFV1_HIGHBIT = "FFV1 高位深无损灰度 留档"
ALPHA_MODE_PRORES = "ProRes 灰度"
ALPHA_MODE_PRORES_EDGE = "ProRes 灰度 边缘最少变化"
ALPHA_MODE_H264_HIGH = "H.264 高质量"
ALPHA_MODE_H265_SMALL = "H.265 小体积"
ALPHA_MODE_H265_MIN = "H.265 最小体积"


def recommended_ext_for_rgb_mode(mode: str) -> str:
    """根据 A 颜色视频编码模式推荐后缀。"""
    if mode == RGB_MODE_FFV1_ARCHIVE:
        return ".mkv"
    if mode in [RGB_MODE_COLOR_SAFE, RGB_MODE_PRORES444, RGB_MODE_PRORES422]:
        return ".mov"
    if mode in [RGB_MODE_H265_10BIT, RGB_MODE_H265_MIN, RGB_MODE_H264]:
        return ".mp4"
    return ".mov"


def recommended_ext_for_alpha_mode(mode: str) -> str:
    """根据 B Alpha 灰度视频编码模式推荐后缀。"""
    if mode in [ALPHA_MODE_FFV1, ALPHA_MODE_FFV1_HIGHBIT]:
        return ".mkv"
    if mode in [ALPHA_MODE_PRORES, ALPHA_MODE_PRORES_EDGE]:
        return ".mov"
    if mode in [ALPHA_MODE_H264_HIGH, ALPHA_MODE_H265_SMALL, ALPHA_MODE_H265_MIN]:
        return ".mp4"
    return ".mov"


def build_rgb_codec_args(rgb_mode: str) -> List[str]:
    """生成 A_color 的 FFmpeg 编码参数。"""
    if rgb_mode == RGB_MODE_FFV1_ARCHIVE:
        return [
            "-vf", "format=yuv444p16le",
            "-c:v", "ffv1",
            "-level", "3",
            "-g", "1",
            "-coder", "1",
            "-context", "1",
        ]

    if rgb_mode == RGB_MODE_PRORES444:
        return [
            "-vf", "format=yuv444p10le",
            "-c:v", "prores_ks",
            "-profile:v", "4",
            "-qscale:v", "4",
            "-vendor", "apl0",
        ]

    if rgb_mode == RGB_MODE_COLOR_SAFE:
        return [
            "-vf", "format=yuv444p10le",
            "-c:v", "prores_ks",
            "-profile:v", "4",
            "-qscale:v", "1",
            "-vendor", "apl0",
        ]

    if rgb_mode == RGB_MODE_PRORES422:
        return [
            "-vf", "format=yuv422p10le",
            "-c:v", "prores_ks",
            "-profile:v", "3",
            "-qscale:v", "5",
            "-vendor", "apl0",
        ]

    if rgb_mode == RGB_MODE_H265_10BIT:
        return [
            "-vf", "format=yuv420p10le",
            "-c:v", "libx265",
            "-preset", "slow",
            "-crf", "14",
            "-x265-params", "profile=main10",
            "-tag:v", "hvc1",
        ]

    if rgb_mode == RGB_MODE_H265_MIN:
        return [
            "-vf", "format=yuv420p",
            "-c:v", "libx265",
            "-preset", "slower",
            "-crf", "28",
            "-tag:v", "hvc1",
        ]

    if rgb_mode == RGB_MODE_H264:
        return [
            "-vf", "format=yuv420p",
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", "14",
        ]

    raise ValueError(f"未知 A 编码模式：{rgb_mode}")


def build_alpha_codec_args(alpha_mode: str) -> List[str]:
    """生成 B_alpha 的 FFmpeg 编码参数。"""
    if alpha_mode == ALPHA_MODE_FFV1_HIGHBIT:
        return [
            "-vf", "alphaextract,format=gray16le",
            "-c:v", "ffv1",
            "-level", "3",
            "-g", "1",
            "-coder", "1",
            "-context", "1",
        ]

    if alpha_mode == ALPHA_MODE_FFV1:
        return [
            "-vf", "alphaextract,format=gray",
            "-c:v", "ffv1",
            "-level", "3",
            "-g", "1",
            "-coder", "1",
            "-context", "1",
        ]

    if alpha_mode == ALPHA_MODE_PRORES:
        return [
            "-vf", "alphaextract,format=yuv444p10le",
            "-c:v", "prores_ks",
            "-profile:v", "4",
            "-qscale:v", "4",
            "-vendor", "apl0",
        ]

    if alpha_mode == ALPHA_MODE_PRORES_EDGE:
        return [
            "-vf", "alphaextract,format=yuv444p10le",
            "-c:v", "prores_ks",
            "-profile:v", "4",
            "-qscale:v", "1",
            "-vendor", "apl0",
        ]

    if alpha_mode == ALPHA_MODE_H264_HIGH:
        return [
            "-vf", "alphaextract,format=yuv420p",
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", "15",
        ]

    if alpha_mode == ALPHA_MODE_H265_SMALL:
        return [
            "-vf", "alphaextract,format=yuv420p10le",
            "-c:v", "libx265",
            "-preset", "slow",
            "-crf", "12",
            "-tag:v", "hvc1",
        ]

    if alpha_mode == ALPHA_MODE_H265_MIN:
        return [
            "-vf", "alphaextract,format=yuv420p",
            "-c:v", "libx265",
            "-preset", "slower",
            "-crf", "23",
            "-tag:v", "hvc1",
        ]

    raise ValueError(f"未知 B 编码模式：{alpha_mode}")


def split_alpha_video(
    input_path: str,
    out_rgb: str,
    out_alpha: str,
    rgb_mode: str,
    alpha_mode: str,
    overwrite: bool,
) -> Tuple[bool, str]:
    """用 FFmpeg 拆分 Alpha。"""
    if not check_ffmpeg_available():
        return False, "没有找到 ffmpeg 或 ffprobe。请先安装 FFmpeg，并确认终端可直接运行 ffmpeg -version。"

    overwrite_flag = "-y" if overwrite else "-n"

    try:
        input_stream = get_input_video_stream(input_path)
        color_flags, detected_color = build_inherited_stream_color_flags(input_stream)
    except Exception as e:
        return False, f"读取输入视频流色彩参数失败：{e}"

    try:
        rgb_codec = build_rgb_codec_args(rgb_mode)
        alpha_codec = build_alpha_codec_args(alpha_mode)
    except Exception as e:
        return False, str(e)

    Path(out_rgb).parent.mkdir(parents=True, exist_ok=True)
    Path(out_alpha).parent.mkdir(parents=True, exist_ok=True)

    rgb_cmd = [
        "ffmpeg",
        overwrite_flag,
        "-i", input_path,
        "-map", "0:v:0",
        "-an",
        *rgb_codec,
        *color_flags,
        out_rgb,
    ]

    alpha_cmd = [
        "ffmpeg",
        overwrite_flag,
        "-i", input_path,
        "-map", "0:v:0",
        "-an",
        *alpha_codec,
        *color_flags,
        out_alpha,
    ]

    logs = []
    logs.append("[输入视频信息]\n" + video_summary_text(input_path))

    logs.append(format_detected_color_params(detected_color))
    logs.append(format_applied_color_flags(color_flags))
    logs.append(codec_quality_note(rgb_mode, alpha_mode))
    logs.append(output_container_note(out_rgb, out_alpha))

    logs.append("[执行 A 颜色视频导出]\n" + " ".join(shlex.quote(x) for x in rgb_cmd))
    code1, out1 = run_cmd_capture(rgb_cmd)
    logs.append(out1)

    if code1 != 0:
        return False, "\n\n".join(logs)

    logs.append("[执行 B Alpha 灰度视频导出]\n" + " ".join(shlex.quote(x) for x in alpha_cmd))
    code2, out2 = run_cmd_capture(alpha_cmd)
    logs.append(out2)

    if code2 != 0:
        return False, "\n\n".join(logs)

    logs.append(
        "[完成]\n"
        f"A 颜色视频：{out_rgb}\n"
        f"B Alpha 灰度视频：{out_alpha}\n\n"
        "提醒：\n"
        "1. 如果 B 全白，说明原视频可能没有 Alpha，或 Alpha 没有被 FFmpeg 正确识别。\n"
        "2. 如果 A 边缘发黑/发灰，通常和 Premultiplied Alpha 或源素材垫底有关。\n"
        "3. A 与 B 会尽量显式继承输入视频流中检测到的色彩参数。\n"
        "4. 对 Alpha 灰度视频使用有损编码时，不建议让 CRF 超过 23。"
    )

    return True, "\n\n".join(logs)


# =========================
# 后台拆分线程
# =========================

class SplitWorker(QThread):
    finished_with_result = Signal(bool, str)

    def __init__(
        self,
        input_path: str,
        out_rgb: str,
        out_alpha: str,
        rgb_mode: str,
        alpha_mode: str,
        overwrite: bool,
    ):
        super().__init__()
        self.input_path = input_path
        self.out_rgb = out_rgb
        self.out_alpha = out_alpha
        self.rgb_mode = rgb_mode
        self.alpha_mode = alpha_mode
        self.overwrite = overwrite

    def run(self):
        ok, logs = split_alpha_video(
            input_path=self.input_path,
            out_rgb=self.out_rgb,
            out_alpha=self.out_alpha,
            rgb_mode=self.rgb_mode,
            alpha_mode=self.alpha_mode,
            overwrite=self.overwrite,
        )
        self.finished_with_result.emit(ok, logs)


# =========================
# GUI 主程序
# =========================

class AlphaSplitterGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Alpha 视频拆分工具 - 纯拆分版")
        self.resize(980, 720)

        self.worker = None
        self.build_ui()
        self.update_ffmpeg_status()

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)

        # 输入输出区域
        file_group = QGroupBox("输入与输出")
        file_layout = QVBoxLayout(file_group)
        main_layout.addWidget(file_group)

        self.ffmpeg_status_label = QLabel("")
        file_layout.addWidget(self.ffmpeg_status_label)

        input_row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("选择带 Alpha 的 MOV / ProRes 4444 / Animation 等视频")
        self.input_button = QPushButton("选择输入")
        self.input_button.clicked.connect(self.choose_input_video)
        input_row.addWidget(QLabel("输入："))
        input_row.addWidget(self.input_edit)
        input_row.addWidget(self.input_button)
        file_layout.addLayout(input_row)

        out_rgb_row = QHBoxLayout()
        self.out_rgb_edit = QLineEdit()
        self.out_rgb_edit.setPlaceholderText("A：颜色视频输出路径")
        self.out_rgb_button = QPushButton("选择 A")
        self.out_rgb_button.clicked.connect(self.choose_out_rgb)
        out_rgb_row.addWidget(QLabel("A 输出："))
        out_rgb_row.addWidget(self.out_rgb_edit)
        out_rgb_row.addWidget(self.out_rgb_button)
        file_layout.addLayout(out_rgb_row)

        out_alpha_row = QHBoxLayout()
        self.out_alpha_edit = QLineEdit()
        self.out_alpha_edit.setPlaceholderText("B：Alpha 灰度视频输出路径")
        self.out_alpha_button = QPushButton("选择 B")
        self.out_alpha_button.clicked.connect(self.choose_out_alpha)
        out_alpha_row.addWidget(QLabel("B 输出："))
        out_alpha_row.addWidget(self.out_alpha_edit)
        out_alpha_row.addWidget(self.out_alpha_button)
        file_layout.addLayout(out_alpha_row)

        # 预设区域
        preset_group = QGroupBox("编码选项")
        preset_layout = QFormLayout(preset_group)
        main_layout.addWidget(preset_group)

        self.rgb_mode_combo = QComboBox()
        self.rgb_mode_combo.addItems([
            RGB_MODE_FFV1_ARCHIVE,
            RGB_MODE_PRORES444,
            RGB_MODE_COLOR_SAFE,
            RGB_MODE_PRORES422,
            RGB_MODE_H265_10BIT,
            RGB_MODE_H265_MIN,
            RGB_MODE_H264,
        ])
        self.rgb_mode_combo.currentTextChanged.connect(self.on_codec_changed)

        self.alpha_mode_combo = QComboBox()
        self.alpha_mode_combo.addItems([
            ALPHA_MODE_FFV1_HIGHBIT,
            ALPHA_MODE_FFV1,
            ALPHA_MODE_PRORES,
            ALPHA_MODE_PRORES_EDGE,
            ALPHA_MODE_H264_HIGH,
            ALPHA_MODE_H265_SMALL,
            ALPHA_MODE_H265_MIN,
        ])
        self.alpha_mode_combo.currentTextChanged.connect(self.on_codec_changed)

        # 默认使用高质量 ProRes 组合
        self.rgb_mode_combo.blockSignals(True)
        self.alpha_mode_combo.blockSignals(True)
        self.rgb_mode_combo.setCurrentText(RGB_MODE_COLOR_SAFE)
        self.alpha_mode_combo.setCurrentText(ALPHA_MODE_PRORES_EDGE)
        self.rgb_mode_combo.blockSignals(False)
        self.alpha_mode_combo.blockSignals(False)

        self.overwrite_check = QCheckBox("覆盖已有文件")
        self.overwrite_check.setChecked(True)

        self.lossless_archive_button = QPushButton("应用无损留档模式")
        self.lossless_archive_button.clicked.connect(self.apply_lossless_archive_mode)

        self.input_attributes_label = QLabel("未选择输入视频。")
        self.input_attributes_label.setWordWrap(True)
        self.input_attributes_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.rgb_container_label = QLabel("")
        self.alpha_container_label = QLabel("")
        self.out_rgb_edit.textChanged.connect(self.update_output_container_labels)
        self.out_alpha_edit.textChanged.connect(self.update_output_container_labels)

        preset_layout.addRow("A 编码：", self.rgb_mode_combo)
        preset_layout.addRow("A 封装：", self.rgb_container_label)
        preset_layout.addRow("B 编码：", self.alpha_mode_combo)
        preset_layout.addRow("B 封装：", self.alpha_container_label)
        preset_layout.addRow("", self.lossless_archive_button)
        preset_layout.addRow("输入属性：", self.input_attributes_label)
        preset_layout.addRow("", self.overwrite_check)

        # 操作按钮
        button_row = QHBoxLayout()
        self.info_button = QPushButton("读取输入视频信息")
        self.info_button.clicked.connect(self.show_input_info)

        self.split_button = QPushButton("开始拆分")
        self.split_button.clicked.connect(self.start_split)

        button_row.addWidget(self.info_button)
        button_row.addStretch(1)
        button_row.addWidget(self.split_button)
        main_layout.addLayout(button_row)

        # 说明
        tip = QLabel(
            "说明：A 是颜色视频；B 是 Alpha 灰度视频，黑色为完全透明，白色为完全不透明。"
            "本版本已去掉图层预览、播放逻辑和所有用途预设；色彩标签会从输入视频流自动继承。"
        )
        tip.setWordWrap(True)
        main_layout.addWidget(tip)

        # 日志
        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        main_layout.addWidget(log_group, 1)

        self.on_codec_changed()

    # ---------- 文件选择 ----------
    def choose_input_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择带 Alpha 的视频",
            "",
            "Video Files (*.mov *.mp4 *.mkv *.avi *.webm);;All Files (*)",
        )
        if path:
            self.input_edit.setText(path)
            p = Path(path)
            rgb_ext = recommended_ext_for_rgb_mode(self.rgb_mode_combo.currentText())
            alpha_ext = recommended_ext_for_alpha_mode(self.alpha_mode_combo.currentText())
            self.out_rgb_edit.setText(str(p.with_name(p.stem + "_A_color" + rgb_ext)))
            self.out_alpha_edit.setText(str(p.with_name(p.stem + "_B_alpha" + alpha_ext)))
            self.update_input_attributes(path)
            self.log("[输入视频信息]\n" + video_summary_text(path))
            self.on_codec_changed()

    def choose_out_rgb(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "选择 A 颜色视频输出路径",
            self.out_rgb_edit.text() or "A_color.mov",
            "MOV (*.mov);;MP4 (*.mp4);;MKV (*.mkv);;All Files (*)",
        )
        if path:
            self.out_rgb_edit.setText(path)
            self.update_output_container_labels()

    def choose_out_alpha(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "选择 B Alpha 灰度视频输出路径",
            self.out_alpha_edit.text() or "B_alpha.mov",
            "MOV (*.mov);;MP4 (*.mp4);;MKV (*.mkv);;All Files (*)",
        )
        if path:
            self.out_alpha_edit.setText(path)
            self.update_output_container_labels()

    # ---------- 预设 ----------
    def replace_suffix_safely(self, path_text: str, new_suffix: str) -> str:
        if not path_text.strip():
            return path_text
        p = Path(path_text)
        return str(p.with_suffix(new_suffix))

    def update_input_attributes(self, input_path: str):
        """把输入视频关键属性显示到编码选项区域。"""
        if not hasattr(self, "input_attributes_label"):
            return
        if not input_path or not Path(input_path).exists():
            self.input_attributes_label.setText("未选择输入视频。")
            return
        self.input_attributes_label.setText(input_video_attributes_text(input_path))

    def update_output_container_labels(self, *_):
        """根据当前输出路径或编码模式显示 A/B 封装格式。"""
        if not hasattr(self, "rgb_container_label"):
            return

        rgb_suffix = Path(self.out_rgb_edit.text().strip()).suffix
        alpha_suffix = Path(self.out_alpha_edit.text().strip()).suffix

        if not rgb_suffix:
            rgb_suffix = recommended_ext_for_rgb_mode(self.rgb_mode_combo.currentText())
        if not alpha_suffix:
            alpha_suffix = recommended_ext_for_alpha_mode(self.alpha_mode_combo.currentText())

        self.rgb_container_label.setText(f"{container_name_from_suffix(rgb_suffix)} ({rgb_suffix})")
        self.alpha_container_label.setText(f"{container_name_from_suffix(alpha_suffix)} ({alpha_suffix})")

    def apply_lossless_archive_mode(self):
        """一键切换到 A/B FFV1 高位深留档组合。"""
        self.rgb_mode_combo.blockSignals(True)
        self.alpha_mode_combo.blockSignals(True)
        self.rgb_mode_combo.setCurrentText(RGB_MODE_FFV1_ARCHIVE)
        self.alpha_mode_combo.setCurrentText(ALPHA_MODE_FFV1_HIGHBIT)
        self.rgb_mode_combo.blockSignals(False)
        self.alpha_mode_combo.blockSignals(False)
        self.on_codec_changed()

    def on_codec_changed(self):
        """编码变化时自动调整推荐后缀。"""
        rgb_ext = recommended_ext_for_rgb_mode(self.rgb_mode_combo.currentText())
        alpha_ext = recommended_ext_for_alpha_mode(self.alpha_mode_combo.currentText())

        if self.out_rgb_edit.text().strip():
            self.out_rgb_edit.setText(self.replace_suffix_safely(self.out_rgb_edit.text(), rgb_ext))
        if self.out_alpha_edit.text().strip():
            self.out_alpha_edit.setText(self.replace_suffix_safely(self.out_alpha_edit.text(), alpha_ext))

        self.update_output_container_labels()

        if hasattr(self, "log_text"):
            self.log(codec_quality_note(self.rgb_mode_combo.currentText(), self.alpha_mode_combo.currentText()))

    # ---------- 拆分 ----------
    def show_input_info(self):
        input_path = self.input_edit.text().strip()
        if not input_path or not Path(input_path).exists():
            QMessageBox.warning(self, "缺少输入", "请先选择一个存在的视频文件。")
            return
        self.update_input_attributes(input_path)
        self.log("[输入视频信息]\n" + video_summary_text(input_path))

    def start_split(self):
        input_path = self.input_edit.text().strip()
        out_rgb = self.out_rgb_edit.text().strip()
        out_alpha = self.out_alpha_edit.text().strip()

        if not input_path or not Path(input_path).exists():
            QMessageBox.warning(self, "缺少输入", "请先选择一个存在的带 Alpha 视频。")
            return
        if not out_rgb or not out_alpha:
            QMessageBox.warning(self, "缺少输出", "请设置 A 和 B 的输出路径。")
            return

        self.update_input_attributes(input_path)
        self.update_output_container_labels()
        self.log(codec_quality_note(self.rgb_mode_combo.currentText(), self.alpha_mode_combo.currentText()))
        self.log(output_container_note(out_rgb, out_alpha))
        self.log("[色彩标签提醒]\n本版本会用 ffprobe 读取输入视频流的 color_primaries / color_transfer / colorspace / color_range；检测到有效值时显式继承，检测不到则不强行写入。")

        self.set_busy(True)
        self.log("开始拆分，FFmpeg 正在后台运行。")

        self.worker = SplitWorker(
            input_path=input_path,
            out_rgb=out_rgb,
            out_alpha=out_alpha,
            rgb_mode=self.rgb_mode_combo.currentText(),
            alpha_mode=self.alpha_mode_combo.currentText(),
            overwrite=self.overwrite_check.isChecked(),
        )
        self.worker.finished_with_result.connect(self.on_split_finished)
        self.worker.start()

    def on_split_finished(self, ok: bool, logs: str):
        self.set_busy(False)
        self.log(logs)

        if ok:
            QMessageBox.information(self, "完成", "拆分完成。")
        else:
            QMessageBox.warning(self, "拆分失败", "拆分失败，请查看日志。")

    def set_busy(self, busy: bool):
        self.split_button.setEnabled(not busy)
        self.info_button.setEnabled(not busy)
        self.input_edit.setEnabled(not busy)
        self.input_button.setEnabled(not busy)
        self.out_rgb_edit.setEnabled(not busy)
        self.out_rgb_button.setEnabled(not busy)
        self.out_alpha_edit.setEnabled(not busy)
        self.out_alpha_button.setEnabled(not busy)
        self.rgb_mode_combo.setEnabled(not busy)
        self.alpha_mode_combo.setEnabled(not busy)
        self.lossless_archive_button.setEnabled(not busy)
        self.overwrite_check.setEnabled(not busy)
        self.split_button.setText("正在拆分..." if busy else "开始拆分")

    # ---------- 辅助 ----------
    def update_ffmpeg_status(self):
        if check_ffmpeg_available():
            self.ffmpeg_status_label.setText("FFmpeg 状态：可用")
            self.ffmpeg_status_label.setStyleSheet("color: #2e7d32;")
        else:
            self.ffmpeg_status_label.setText("FFmpeg 状态：未找到。请先安装 FFmpeg。")
            self.ffmpeg_status_label.setStyleSheet("color: #c62828;")

    def log(self, text: str):
        if hasattr(self, "log_text"):
            self.log_text.append(text)
            self.log_text.append("-" * 80)
        else:
            print(text)
            print("-" * 80)


def main():
    app = QApplication(sys.argv)
    win = AlphaSplitterGUI()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
