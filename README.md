# EasyClip

自用的视频切片工具，用于为视频生成模型（LTX 系列等）准备 LoRA 训练素材。

A personal video slicing tool for preparing LoRA training data for video generative models (LTX series, etc.).

## 功能概述 / Features

- **帧级精确切片 / Frame-accurate Slicing**：逐帧标记起止点，导出时通过转码保证帧边界准确。Mark start/end points with frame-level precision; export via transcoding to ensure accurate frame boundaries.
- **帧数对齐 / Frame Count Alignment**：支持 $x \cdot n + y$ 输出帧数约束（如 LTX 的 $8n+1$），自动吸附。Supports $x \cdot n + y$ output frame count constraints (e.g. $8n+1$ for LTX), with automatic snapping.
- **分辨率处理 / Resolution Handling**：导出时自动 PAR 1:1 修正、尺寸倍数补齐。Automatic PAR 1:1 correction and size-multiple padding during export.
- **字幕同步切割 / Subtitle Cutting**：导出片段时同步裁剪对应的字幕文件。Cut matching subtitle files alongside exported clips.
- **片段标注 / Clip Annotation**：为切好的片段添加文字描述，支持接入 LLM/VLM 自动生成。Add text descriptions to clips, with LLM/VLM integration for auto-generation.

## 运行环境 / Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
- FFmpeg / FFprobe（置于系统 PATH，或 `vendor/` 目录下 / on system PATH or in `vendor/` directory）

```bash
cd EasyClip
uv sync
uv run easyclip
```

## 许可 / License

源码使用 MIT 许可，见 `LICENSE`。FFmpeg 为第三方项目，分发时需遵守其许可证要求，见 `FFMPEG_NOTES.txt`。

Source code under MIT License, see `LICENSE`. FFmpeg is a third-party project; comply with its license when distributing, see `FFMPEG_NOTES.txt`.
