# EasyClip

EasyClip 是一个为生成模型 LoRA 训练数据准备而做的视频切片工具。它的目标不是通用视频剪辑，而是更稳定地切出训练用视频片段。

EasyClip is a video slicing tool designed for preparing training data for video generative models LoRA. Its goal is not general video editing, but rather to help you more consistently cut training clips.

## 工具适用于 / Target Audience

- 在做文生视频 / 图生视频相关训练，想从长视频中切出可用片段
  Doing Text-to-Video / Image-to-Video training and wanting to cut usable clips from long videos.
- 希望帧级别控制切片
  Requiring frame-level precision for slicing.
- 需要按模型要求输出合适尺寸，不想每次手动算分辨率
  Needing appropriate output dimensions according to model requirements, without manually calculating resolutions every time.

## 主要特点 / Main Features

- **只包含切片流程 / Slicing-only Workflow**  
  流程简单，避免不必要的复杂功能。  
  Simple workflow, avoiding unnecessary complex features.

- **为 LTX 系列模型加入 8n+1 自动吸附 / Automatic 8n+1 Snapping for LTX-series Models**  
  可以在切片时快速把片段长度吸附到 `xn+y`，更贴近这类模型常见的数据要求。  
  Allows quickly snapping the clip length to `xn+y` during slicing, better fitting the common data requirements of these models.

- **自动计算输出分辨率 / Automatic Output Resolution Calculation**  
  很多模型要求宽高是某个整数的倍数，EasyClip 会自动处理，减少手动试错。  
  Many models require width and height to be multiples of a specific integer. EasyClip handles this automatically, reducing manual trial and error.

- **帧级精度优先 / Frame-level Precision Priority**  
  为了尽量保证帧级别的准确性，导出时会进行转码。  
  这与一些“尽量不转码”的工具思路不同：后者在某些素材上更快，但可能出现切出来的帧数与预期不完全一致。EasyClip 更偏向“训练前先把帧边界做准”。  
  To ensure maximum frame-level accuracy, videos are transcoded during export.  
  This differs from tools that try to avoid transcoding: those might be faster on some materials, but the exported frame count might not exactly match the expectation. EasyClip leans towards "getting the frame boundaries right before training".

- **兼容老视频的 PAR 非 1:1 场景 / Compatibility with Non-1:1 PAR for Old Videos**  
  针对部分旧素材像素宽高比（PAR）不是 1:1 的情况，导出链路里做了专门处理，减少画面比例异常。  
  For older source materials where the Pixel Aspect Ratio (PAR) is not 1:1, the export pipeline applies special handling to reduce aspect ratio abnormalities.

- **减少音频漂移问题 / Audio Drift Reduction**  
  针对部分素材音视频帧率不匹配做了专门处理，减少音频漂移问题。  
  Special handling is applied for mismatches between audio and video frame rates in certain source materials, reducing audio drift issues.

## 安装与使用 / Installation & Usage

- 当前仅支持 **Windows** / Currently, only **Windows** is supported.
- 其他平台暂未适配 / Other platforms are not yet supported.

### 方式一：克隆仓库直接运行 / Method 1: Clone and Run

前置条件 / Prerequisites:

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
- 需自行配置可用的 `ffmpeg` / `ffprobe` 于系统路径，或放置在`ffmpeg/`下  
  You need to configure a working `ffmpeg` / `ffprobe` in your system PATH, or place them in the `ffmpeg/` directory.

步骤 / Steps:

```bash
git clone https://github.com/Innovaspire/EasyClip.git
cd EasyClip
uv sync
uv run easyclip
```

### 方式二：下载 Release 使用 / Method 2: Download Release

前往 [Releases](https://github.com/Innovaspire/EasyClip/releases) 页面下载最新的打包程序，解压后即可直接运行。  
Go to the [Releases](https://github.com/Innovaspire/EasyClip/releases) page to download the latest packaged program, extract it, and run it directly.

## 许可说明 / License

- 本仓库源码使用 **MIT** 许可，见 `LICENSE`  
  The source code of this repository is licensed under the **MIT** License. See `LICENSE` for details.
- FFmpeg 属于第三方项目，分发时请遵守其许可证要求，详见 `FFMPEG_NOTES.txt`  
  FFmpeg is a third-party project. Please comply with its license requirements when distributing. See `FFMPEG_NOTES.txt` for details.

