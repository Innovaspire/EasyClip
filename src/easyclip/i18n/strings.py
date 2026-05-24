"""Lightweight zh_CN / en_US strings."""

from __future__ import annotations

from PySide6.QtCore import QSettings

MESSAGES: dict[str, dict[str, str]] = {
    "app.title": {"zh_CN": "EasyClip", "en_US": "EasyClip"},
    "tab.slicing": {"zh_CN": "视频切片", "en_US": "Video Slicing"},
    "tab.annotation": {"zh_CN": "Clip 标注", "en_US": "Clip Annotation"},
    "annotation.coming_soon": {"zh_CN": "Clip 标注功能即将推出…", "en_US": "Clip Annotation coming soon…"},
    "menu.file": {"zh_CN": "文件", "en_US": "File"},
    "menu.open": {"zh_CN": "打开视频…", "en_US": "Open video…"},
    "menu.export": {"zh_CN": "导出全部片段…", "en_US": "Export all clips…"},
    "menu.export_strong": {
        "zh_CN": "导出全部片段（强纠偏）…",
        "en_US": "Export all clips (strong correction)…",
    },
    "menu.generate_proxy": {"zh_CN": "生成预览代理…", "en_US": "Generate preview proxy…"},
    "menu.clear_proxy": {"zh_CN": "清除预览代理…", "en_US": "Clear preview proxies…"},
    "menu.settings": {"zh_CN": "设置", "en_US": "Settings"},
    "menu.preferences": {"zh_CN": "偏好设置…", "en_US": "Preferences…"},
    "menu.quit": {"zh_CN": "退出", "en_US": "Quit"},
    "menu.load_subtitle": {"zh_CN": "加载字幕…", "en_US": "Load subtitle…"},
    "menu.unload_subtitle": {"zh_CN": "卸载字幕", "en_US": "Unload subtitle"},
    "menu.help": {"zh_CN": "帮助", "en_US": "Help"},
    "menu.about": {"zh_CN": "关于", "en_US": "About"},
    "dialog.about_text": {
        "zh_CN": "EasyClip {version}\n\nMIT License — 详见 LICENSE 文件\nFFmpeg 不属于 MIT 授权范围；分发时请附带 LGPL 构建及声明。",
        "en_US": "EasyClip {version}\n\nMIT License — see LICENSE\nFFmpeg is not part of MIT; bundle LGPL builds and notices.",
    },
    "open_video": {"zh_CN": "打开视频", "en_US": "Open video"},
    "drop.replace.title": {"zh_CN": "替换当前视频？", "en_US": "Replace current video?"},
    "drop.replace.body": {
        "zh_CN": "已打开「{current}」。要打开「{filename}」吗？",
        "en_US": "«{current}» is open. Switch to «{filename}»?",
    },
    "drop.replace.save": {"zh_CN": "保存并打开", "en_US": "Save, then open"},
    "drop.replace.discard": {"zh_CN": "不保存并打开", "en_US": "Open without saving"},
    "drop.replace.cancel": {"zh_CN": "取消", "en_US": "Cancel"},
    "drop.hint": {"zh_CN": "拖放视频文件到此处", "en_US": "Drop a video file here"},
    "waveform.ui.loading": {"zh_CN": "波形加载中…", "en_US": "Loading waveform…"},
    "waveform.ui.none": {"zh_CN": "无音频波形", "en_US": "No audio waveform"},
    "waveform.ui.empty": {"zh_CN": "—", "en_US": "—"},
    "waveform.ui.failed": {"zh_CN": "波形生成失败", "en_US": "Waveform failed"},
    "clips": {"zh_CN": "片段列表", "en_US": "Clips"},
    "clips.show_time": {
        "zh_CN": "显示时间",
        "en_US": "Show time",
    },
    "clips.show_frames": {
        "zh_CN": "显示帧",
        "en_US": "Show frames",
    },
    "clips.switch_to_time_tip": {
        "zh_CN": "片段时间码（mm:ss.mmm）显示",
        "en_US": "Show in/out timecode (mm:ss.mmm)",
    },
    "clips.switch_to_frames_tip": {
        "zh_CN": "片段帧号与吸附预测下的输出帧数估计",
        "en_US": "Show frame numbers and estimated output frames (snap preview FPS)",
    },
    "clips.row_tooltip": {
        "zh_CN": "[帧序号:{start}-{end}](源帧数:{src},输出帧数:{out})",
        "en_US": "[frames {start}-{end}](source frames: {src}, output est.: {out})",
    },
    "clips.delete": {"zh_CN": "删除片段", "en_US": "Delete clip"},
    "thumbs": {"zh_CN": "首尾帧", "en_US": "First / last frame"},
    "preview.volume": {"zh_CN": "预览音量", "en_US": "Preview volume"},
    "toolbar.export_all": {"zh_CN": "导出全部片段", "en_US": "Export all clips"},
    "transport.btn.timeline_left": {"zh_CN": "时间轴左移 (,)", "en_US": "Pan left (,)"},
    "transport.btn.timeline_right": {"zh_CN": "时间轴右移 (.)", "en_US": "Pan right (.)"},
    "transport.btn.zoom_out": {"zh_CN": "时间轴缩小 (-)", "en_US": "Zoom out (-)"},
    "transport.btn.zoom_in": {"zh_CN": "时间轴放大 (=/+)", "en_US": "Zoom in (=/+)"},
    "transport.btn.zoom_reset": {"zh_CN": "时间轴复位 (0)", "en_US": "Reset (0)"},
    "transport.btn.start": {"zh_CN": "起点（S）", "en_US": "Start (S)"},
    "transport.btn.end": {"zh_CN": "终点（E）", "en_US": "End (E)"},
    "transport.btn.middle": {"zh_CN": "中点（M）", "en_US": "Middle (M)"},
    "transport.btn.snap_ceil": {"zh_CN": "🧲→（C）", "en_US": "🧲→ (C)"},
    "transport.btn.snap_floor": {"zh_CN": "←🧲（F）", "en_US": "←🧲 (F)"},
    "transport.btn.clip_nudge_left": {"zh_CN": "←🎞️（A）", "en_US": "←🎞️ (A)"},
    "transport.btn.clip_nudge_right": {"zh_CN": "🎞️→（D）", "en_US": "🎞️→ (D)"},
    "transport.btn.boundary_nudge_left": {"zh_CN": "←|（Z）", "en_US": "←| (Z)"},
    "transport.btn.boundary_nudge_right": {"zh_CN": "|→（X）", "en_US": "|→ (X)"},
    "transport.btn.fixed_1": {"zh_CN": "121", "en_US": "121"},
    "transport.btn.fixed_2": {"zh_CN": "241", "en_US": "241"},
    "transport.btn.fixed_3": {"zh_CN": "361", "en_US": "361"},
    "transport.btn.fixed_4": {"zh_CN": "481", "en_US": "481"},
    "transport.btn.fixed_5": {"zh_CN": "601", "en_US": "601"},
    "transport.tip.seek_back": {
        "zh_CN": "快退{seconds}秒（快捷键：播放时←）",
        "en_US": "Rewind {seconds}s (shortcut: ← when playing)",
    },
    "transport.tip.prev_frame": {
        "zh_CN": "回退 1 帧（快捷键：暂停时←）",
        "en_US": "Step back one frame (shortcut: ← when paused)",
    },
    "transport.tip.play_toggle": {"zh_CN": "播放 / 暂停（快捷键：空格）", "en_US": "Play / pause (Shortcut: Space)"},
    "transport.tip.next_frame": {
        "zh_CN": "前进 1 帧（快捷键：暂停时→）",
        "en_US": "Step forward one frame (shortcut: → when paused)",
    },
    "transport.tip.seek_forward": {
        "zh_CN": "快进{seconds}秒（快捷键：播放时→）",
        "en_US": "Fast-forward {seconds}s (shortcut: → when playing)",
    },
    "transport.tip.loop_clip": {"zh_CN": "循环播放片段（快捷键：L）", "en_US": "Loop clip (shortcut: L)"},
    "transport.tip.timeline_left": {
        "zh_CN": "时间轴放大窗口左移（快捷键：, / Shift+滚轮下）",
        "en_US": "Pan timeline zoomed window left (Shortcut: , / Shift+wheel down)",
    },
    "transport.tip.timeline_right": {
        "zh_CN": "时间轴放大窗口右移（快捷键：. / Shift+滚轮上）",
        "en_US": "Pan timeline zoomed window right (Shortcut: . / Shift+wheel up)",
    },
    "transport.tip.zoom_out": {
        "zh_CN": "时间轴缩小（快捷键：- / Ctrl+滚轮下）",
        "en_US": "Zoom timeline out (Shortcut: - / Ctrl+wheel down)",
    },
    "transport.tip.zoom_in": {
        "zh_CN": "时间轴放大（快捷键：= 或 + / Ctrl+滚轮上）",
        "en_US": "Zoom timeline in (Shortcut: = or + / Ctrl+wheel up)",
    },
    "transport.tip.zoom_reset": {
        "zh_CN": "时间轴复位（快捷键：0 / 双击时间轴）",
        "en_US": "Reset timeline view (Shortcut: 0 / double-click timeline)",
    },
    "transport.tip.start": {
        "zh_CN": "以当前帧为起点新建片段，若已选中首帧则更新（快捷键：S）",
        "en_US": "Create start at current frame, or update selected start edge (Shortcut: S)",
    },
    "transport.tip.end": {
        "zh_CN": "将当前列表选中片段的尾帧设为当前帧（含仅选中片段 / 仅选中首帧缩略图）；未选中片段时闭合全局未闭合片段（快捷键：E）",
        "en_US": "Set end of the selected clip to current frame (row selection or start-thumb counts); if none selected, close the open clip (Shortcut: E)",
    },
    "transport.tip.middle": {
        "zh_CN": "以当前帧为中点截取片段（前{before}帧，后{after}帧，快捷键：M）",
        "en_US": "Cut with current frame as center ({before} before, {after} after, Shortcut: M)",
    },
    "transport.tip.snap_ceil": {
        "zh_CN": "向后吸附到最近的合法 x·n+y 输出长度（快捷键：C）",
        "en_US": "Snap forward to nearest valid x·n+y output length (Shortcut: C)",
    },
    "transport.tip.snap_floor": {
        "zh_CN": "向前吸附到最近的合法 x·n+y 输出长度（快捷键：F）",
        "en_US": "Snap backward to nearest valid x·n+y output length (Shortcut: F)",
    },
    "transport.tip.clip_nudge_left": {
        "zh_CN": "选中片段整体左移 1 帧；未闭合时仅移动起点（快捷键：A）",
        "en_US": "Slip selected clip −1 frame (open clips move start only) (Shortcut: A)",
    },
    "transport.tip.clip_nudge_right": {
        "zh_CN": "选中片段整体右移 1 帧；未闭合时仅移动起点（快捷键：D）",
        "en_US": "Slip selected clip +1 frame (open clips move start only) (Shortcut: D)",
    },
    "transport.tip.clip_nudge_disabled": {
        "zh_CN": "请先在左侧列表选中片段后再使用；未选中时快捷键 A / D 无效",
        "en_US": "Select a clip in the list first. Shortcuts A / D do nothing without a selection.",
    },
    "transport.tip.boundary_nudge_left": {
        "zh_CN": "选中边界左移 1 帧（快捷键：Z）",
        "en_US": "Move selected boundary −1 frame (Shortcut: Z)",
    },
    "transport.tip.boundary_nudge_right": {
        "zh_CN": "选中边界右移 1 帧（快捷键：X）",
        "en_US": "Move selected boundary +1 frame (Shortcut: X)",
    },
    "transport.tip.boundary_nudge_disabled": {
        "zh_CN": "请先点击首帧或尾帧缩略图选中边界后再使用；未选中时快捷键 Z / X 无效",
        "en_US": "Click a start/end thumbnail to select a boundary first. Shortcuts Z / X do nothing without a boundary selected.",
    },
    "transport.tip.fixed": {
        "zh_CN": "以当前帧为起点截取 {frames} 帧片段（快捷键：{hotkey}）",
        "en_US": "Create a {frames}-frame clip from current frame (Shortcut: {hotkey})",
    },
    "no_clip": {"zh_CN": "未选择片段", "en_US": "No clip selected"},
    "proxy.generating": {"zh_CN": "正在生成预览代理…", "en_US": "Generating proxy…"},
    "waveform.generating": {"zh_CN": "正在生成波形…", "en_US": "Generating waveform…"},
    "align.title": {
        "zh_CN": "预测输出长度需符合 N=x·n+y（x={x}, y={y}）",
        "en_US": "Predicted output length should match N=x·n+y (x={x}, y={y})",
    },
    "align.body": {
        "zh_CN": "在当前帧率基准下预测长度为 {length} 帧。是否吸附到最近的合法长度？（x={x}, y={y}）",
        "en_US": "Predicted length is {length} frames (baseline FPS). Snap to nearest valid length? (x={x}, y={y})",
    },
    "align.ceil": {"zh_CN": "向上 (C)", "en_US": "Ceil (C)"},
    "align.floor": {"zh_CN": "向下 (F)", "en_US": "Floor (F)"},
    "align.no": {"zh_CN": "否", "en_US": "No"},
    "align.skip_future": {"zh_CN": "不再提示", "en_US": "Don't ask again"},
    "settings.ui_align_group": {"zh_CN": "C / F 吸附预测", "en_US": "C / F snap preview"},
    "settings.ui_align_block_hint": {
        "zh_CN": "输出帧数满足 N = x·n + y（n 为非负整数）。\n以下仅影响主界面 C/F 吸附与闭合片段时的长度提示；成片帧率与帧数以导出窗口（或预设）为准。",
        "en_US": "Output length follows N = x·n + y (n ≥ 0).\nThese values only affect C/F snapping and length prompts when closing clips. Exported FPS and frame count follow the export dialog (or preset).",
    },
    "settings.ui_align_current_pattern": {
        "zh_CN": "当前为 {x}n+{y}",
        "en_US": "Currently {x}n+{y}",
    },
    "settings.ui_align_fps": {"zh_CN": "帧率基准", "en_US": "FPS baseline"},
    "settings.ui_align_x": {"zh_CN": "倍数 x", "en_US": "Multiple x"},
    "settings.ui_align_y": {"zh_CN": "偏移 y", "en_US": "Offset y"},
    "export.options.align_enabled": {"zh_CN": "启用帧数对齐（导出时强制帧数）", "en_US": "Enable frame-count alignment (strict export)"},
    "export.options.align_x": {"zh_CN": "x", "en_US": "x"},
    "export.options.align_y": {"zh_CN": "y", "en_US": "y"},
    "export.options.align_round": {"zh_CN": "取整", "en_US": "Rounding"},
    "export.options.align_round.ceil": {"zh_CN": "向上", "en_US": "Ceil"},
    "export.options.align_round.floor": {"zh_CN": "向下", "en_US": "Floor"},
    "export.options.align_apply": {"zh_CN": "调整端点", "en_US": "Adjust endpoints"},
    "export.options.align_apply.tail": {"zh_CN": "仅尾帧", "en_US": "Tail only"},
    "export.options.align_apply.head": {"zh_CN": "仅首帧", "en_US": "Head only"},
    "export.options.align_apply.symmetric": {"zh_CN": "首尾对称", "en_US": "Symmetric"},
    "export.options.align_mismatch": {
        "zh_CN": "提示：与「设置 → 吸附/快捷切片」中的设置不一致（{details}）。本次导出以本窗口为准。",
        "en_US": "Note: differs from Settings → Snap/Quick Slice ({details}). This export uses this dialog.",
    },
    "export.options.align_mismatch_fps": {
        "zh_CN": "帧率 {ui_fps}≠{efps}",
        "en_US": "FPS {ui_fps}≠{efps}",
    },
    "export.options.align_mismatch_x": {
        "zh_CN": "x {ui_x}≠{ex}",
        "en_US": "x {ui_x}≠{ex}",
    },
    "export.options.align_mismatch_y": {
        "zh_CN": "y {ui_y}≠{ey}",
        "en_US": "y {ui_y}≠{ey}",
    },
    "export.options.align_mismatch_sep": {
        "zh_CN": "，",
        "en_US": ", ",
    },
    "settings.align_preset": {"zh_CN": "帧数对齐（导出）", "en_US": "Frame Alignment (Export)"},
    "export.align_warn.floor_fallback_minimum": {
        "zh_CN": "向下取整时低于最小合法长度，已使用最小合法输出帧数。",
        "en_US": "Floor requested a length below the minimum valid; used the smallest valid frame count.",
    },
    "export.align_warn.constraint_reduced_by_span": {
        "zh_CN": "片段边界内无法达到目标对齐帧数，已按素材跨度减少输出帧数。",
        "en_US": "Target aligned length exceeds clip bounds; exported fewer frames.",
    },
    "export.align_warn.source_too_short_for_pattern": {
        "zh_CN": "素材跨度不足以满足当前 x·n+y 模式，已尽量输出全部可用帧。",
        "en_US": "Source span too short for the x·n+y pattern; exported all frames possible.",
    },
    "export.align_warn.output_not_matching_pattern": {
        "zh_CN": "实际输出帧数未满足 x·n+y（可能由边界钳制导致）。",
        "en_US": "Output frame count does not match x·n+y (likely due to clamping).",
    },
    "export.align_warn.duration_insufficient": {
        "zh_CN": "内部校正：源时长不足以在严格模式下容纳目标输出帧数，已取消 -frames:v 强制。",
        "en_US": "Internal: source duration too short for strict -frames:v; export falls back to natural length.",
    },
    "export.align_warn.zero_output_span": {
        "zh_CN": "在当前 CFR 模型下，素材跨度不足一整帧输出；已取消本片段的严格帧数强制。",
        "en_US": "Source span shorter than one output CFR frame under the current model; strict frame count skipped.",
    },
    "export.align_warn.head_clamped": {
        "zh_CN": "首帧已钳制到 0（偏移 {shift}）。",
        "en_US": "Start frame clamped to 0 (shifted by {shift}).",
    },
    "export.align_warn.tail_clamped": {
        "zh_CN": "尾帧已钳制到最后一帧（偏移 {shift}）。",
        "en_US": "End frame clamped to last source frame (shifted by {shift}).",
    },
    "export.align_warn.both_clamped": {
        "zh_CN": "首尾均被钳制：片段贴齐素材边界。",
        "en_US": "Both endpoints clamped: source pinned to full clip length.",
    },
    "export.align_warn.source_too_short": {
        "zh_CN": "素材过短，无法满足要求的 {requested} 帧；降为 {feasible} 帧（最大可用 {actual}）。",
        "en_US": "Source too short for {requested} frames; downgraded to {feasible} (max possible {actual}).",
    },
    "export.align_warn.invalid_clip": {
        "zh_CN": "片段范围无效：{reason}",
        "en_US": "Clip range invalid: {reason}",
    },
    "export.align_warn.constraint_unsatisfied": {
        "zh_CN": "无法满足对齐约束：要求 {requested}，实际输出 {actual}。",
        "en_US": "Constraint cannot be satisfied: requested {requested}, output {actual}.",
    },
    "export.align_warn.floor_underflow_to_ceil": {
        "zh_CN": "向下取整溢出，已使用最小合法值 {smallest}。",
        "en_US": "Floor mode underflowed; using smallest valid value {smallest}.",
    },
    "export.align_warn.single_frame": {
        "zh_CN": "输出为单帧。",
        "en_US": "Output is a single frame.",
    },
    "status.analyzing_keyframes": {
        "zh_CN": "正在分析关键帧…",
        "en_US": "Analyzing keyframes…",
    },
    "status.keyframes_deferred": {
        "zh_CN": "已延后关键帧分析（长视频）。首次快进/快退时将开始索引以加速定位。",
        "en_US": "Keyframe scan deferred (long video). Indexing will start on first seek to speed up navigation.",
    },
    "thumbs.duration_placeholder": {
        "zh_CN": "时长：—",
        "en_US": "Duration: —",
    },
    "thumbs.duration": {
        "zh_CN": "时长：{duration}",
        "en_US": "Duration: {duration}",
    },
    "source_path.toggle_tooltip": {
        "zh_CN": "点击切换显示文件名 / 完整路径",
        "en_US": "Click to toggle filename / full path",
    },
    "subtitle.load_dialog_title": {"zh_CN": "加载字幕", "en_US": "Load subtitle"},
    "subtitle.parse_error": {
        "zh_CN": "无法解析字幕文件：{error}",
        "en_US": "Failed to parse subtitle file: {error}",
    },
    "subtitle.status_loaded": {
        "zh_CN": "字幕：{filename}",
        "en_US": "Subtitle: {filename}",
    },
    "subtitle.status_unloaded": {
        "zh_CN": "字幕已卸载",
        "en_US": "Subtitle unloaded",
    },
    "transport.tip.quick_slice": {
        "zh_CN": "快捷切片 {digit}（向前 {before}，向后 {after}）（快捷键：{digit}）",
        "en_US": "Quick slice {digit} (extend {before}f forward, {after}f back) (Shortcut: {digit})",
    },
    "settings.ui_align_fps_match_source": {
        "zh_CN": "以源视频为基准",
        "en_US": "Match source video FPS",
    },
    "settings.quick_slice_group": {
        "zh_CN": "快捷切片设置 (1-5)",
        "en_US": "Quick slice settings (1-5)",
    },
    "settings.quick_slice_key_label": {
        "zh_CN": "按键 {num} - 向前扩展:",
        "en_US": "Key {num} - Extend forward:",
    },
    "settings.quick_slice_mid_label": {
        "zh_CN": "帧，向后扩展:",
        "en_US": "frames, extend backward:",
    },
    "settings.quick_slice_suffix": {
        "zh_CN": "帧",
        "en_US": "frames",
    },
    "settings.tab.snap": {
        "zh_CN": "吸附/快捷切片",
        "en_US": "Snap / Quick Slice",
    },
    "settings.title": {"zh_CN": "设置", "en_US": "Settings"},
    "settings.tab.general": {"zh_CN": "常规", "en_US": "General"},
    "settings.tab.export_defaults": {"zh_CN": "导出参数预设", "en_US": "Export Parameter Presets"},
    "settings.lang": {"zh_CN": "语言", "en_US": "Language"},
    "settings.lang.zh_CN": {"zh_CN": "简体中文", "en_US": "简体中文"},
    "settings.lang.en_US": {"zh_CN": "English", "en_US": "English"},
    "settings.theme": {"zh_CN": "主题", "en_US": "Theme"},
    "settings.theme.system": {"zh_CN": "跟随系统", "en_US": "Follow system"},
    "settings.theme.light": {"zh_CN": "浅色", "en_US": "Light"},
    "settings.theme.dark": {"zh_CN": "深色", "en_US": "Dark"},
    "settings.project_dir": {"zh_CN": "项目目录", "en_US": "Project Directory"},
    "settings.mode.home": {"zh_CN": "用户目录 ~/.easyclip/projects", "en_US": "User home ~/.easyclip/projects"},
    "settings.mode.source": {"zh_CN": "源文件旁", "en_US": "Next to source file"},
    "settings.mode.exe": {"zh_CN": "可执行文件旁", "en_US": "Next to executable"},
    "settings.mode.custom": {"zh_CN": "自定义…", "en_US": "Custom…"},
    "settings.middle_before": {"zh_CN": "中点模式向前帧数", "en_US": "Middle mode frames before"},
    "settings.middle_after": {"zh_CN": "中点模式向后帧数", "en_US": "Middle mode frames after"},
    "settings.playback_seek_seconds": {"zh_CN": "快进 / 快退秒数", "en_US": "Fast-forward / rewind duration (seconds)"},
    "settings.playback_seek_seconds_suffix": {"zh_CN": " 秒", "en_US": " s"},
    "settings.export_fps": {"zh_CN": "导出帧率（恒定 CFR）", "en_US": "Export FPS (constant CFR)"},
    "settings.startup_behavior": {"zh_CN": "启动时加载", "en_US": "On Startup"},
    "settings.startup.ask": {"zh_CN": "每次询问", "en_US": "Ask every time"},
    "settings.startup.do_nothing": {"zh_CN": "无", "en_US": "Do nothing"},
    "settings.startup.auto_load": {
        "zh_CN": "上次项目",
        "en_US": "Auto-load last project",
    },
    "settings.export_preset": {"zh_CN": "预设", "en_US": "Preset"},
    "settings.export_preset.new": {"zh_CN": "新预设", "en_US": "New preset"},
    "settings.export_preset.save": {"zh_CN": "保存预设", "en_US": "Save preset"},
    "settings.export_preset.saved": {"zh_CN": "已保存", "en_US": "Saved"},
    "settings.export_preset.name_placeholder": {"zh_CN": "请输入预设名称", "en_US": "Enter preset name"},
    "settings.export_preset.name_required.title": {"zh_CN": "预设名称不能为空", "en_US": "Preset name required"},
    "settings.export_preset.name_required.body": {
        "zh_CN": "请输入预设名称后再保存。",
        "en_US": "Please enter a preset name before saving.",
    },
    "settings.resolution_align_mode": {"zh_CN": "分辨率预计算", "en_US": "Resolution Pre-calculation"},
    "settings.resolution_align_mode.inherit": {"zh_CN": "继承分辨率", "en_US": "Inherit resolution"},
    "settings.resolution_align_mode.align_width": {"zh_CN": "按宽对齐", "en_US": "Align by width"},
    "settings.resolution_align_mode.align_height": {"zh_CN": "按高对齐", "en_US": "Align by height"},
    "settings.export_preset.duplicate.title": {"zh_CN": "预设名称已存在", "en_US": "Preset name already exists"},
    "settings.export_preset.duplicate.body": {
        "zh_CN": "预设“{name}”已存在。是否覆盖该预设？",
        "en_US": "Preset \"{name}\" already exists. Overwrite it?",
    },
    "settings.export_preset.unsaved.title": {"zh_CN": "预设未保存", "en_US": "Preset not saved"},
    "settings.export_preset.unsaved.body": {
        "zh_CN": "当前预设修改尚未保存，是否先保存？",
        "en_US": "Current preset changes are not saved. Save before closing?",
    },
    "export.preset.default_name": {"zh_CN": "默认预设", "en_US": "Default preset"},
    "export.warn_predicted_pattern": {
        "zh_CN": "在 {fps} fps 下预测输出为 {length} 帧，不满足当前导出参中的 x={x}, y={y} 的 x·n+y 模式",
        "en_US": "Predicted output is {length} frames at {fps} fps (does not match x·n+y for x={x}, y={y})",
    },
    "settings.warn_align_8n1": {
        "zh_CN": "闭合片段时若预测长度不符合 x·n+y 则提示对齐",
        "en_US": "When closing a clip, prompt if predicted length is not x·n+y",
    },
    "settings.experimental": {"zh_CN": "启用实验性管线（转写/VLM 等）", "en_US": "Enable experimental pipeline"},
    "settings.clear_proxies": {"zh_CN": "清除所有代理文件", "en_US": "Clear all proxy files"},
    "settings.clear_proxies.title": {"zh_CN": "清除代理文件", "en_US": "Clear proxy files"},
    "settings.clear_proxies.confirm": {
        "zh_CN": "将删除当前项目目录模式下找到的全部 proxy.mp4 文件。此操作不可撤销，是否继续？",
        "en_US": "This will delete all proxy.mp4 files found under the current project directory mode. Continue?",
    },
    "settings.clear_proxies.no_root": {
        "zh_CN": "当前模式下没有可扫描的项目目录。",
        "en_US": "No project root is available for this mode.",
    },
    "settings.clear_home_cache": {
        "zh_CN": "清理用户目录项目文件",
        "en_US": "Clear Home Project Files",
    },
    "settings.clear_home_cache.confirm": {
        "zh_CN": "将删除 {path} 目录下的所有项目数据。\n\n此操作不可撤销，是否继续？",
        "en_US": "This will delete all project data under {path}.\n\nThis action cannot be undone. Continue?",
    },
    "settings.clear_home_cache.nothing": {
        "zh_CN": "用户目录下没有 EasyClip 缓存数据。",
        "en_US": "No EasyClip cache data found in your home directory.",
    },
    "settings.clear_home_cache.current_project_warn": {
        "zh_CN": "⚠ 当前打开的项目位于此目录中，删除后将无法恢复。",
        "en_US": "⚠ The currently open project is located in this directory and will be lost.",
    },
    "settings.clear_home_cache.progress": {
        "zh_CN": "正在清理缓存...",
        "en_US": "Clearing cache...",
    },
    "settings.clear_home_cache.error": {
        "zh_CN": "清理过程中发生错误：{error}",
        "en_US": "Error during cleanup: {error}",
    },
    "settings.clear_home_cache.done": {
        "zh_CN": "已清理缓存，共删除 {count} 个文件/目录。",
        "en_US": "Cache cleared: {count} files/directories deleted.",
    },
    "settings.clear_home_cache.failed": {
        "zh_CN": "清理过程中出现错误，仅删除了 {count} 个项目，剩余内容可能被其他程序占用。",
        "en_US": "Some items could not be deleted ({count} removed). Remaining items may be in use.",
    },
    "settings.clear_proxies.done": {
        "zh_CN": "已删除 {deleted} 个代理文件，失败 {failed} 个。",
        "en_US": "Deleted {deleted} proxy files, failed {failed}.",
    },
    "export.done": {"zh_CN": "导出完成", "en_US": "Export finished"},
    "export.warn": {"zh_CN": "警告", "en_US": "Warnings"},
    "export.no_clips": {"zh_CN": "没有已闭合的片段可导出。", "en_US": "No closed clips to export."},
    "export.busy": {"zh_CN": "正在导出，请稍候。", "en_US": "Export already in progress."},
    "export.cancel": {"zh_CN": "取消", "en_US": "Cancel"},
    "export.progress_detail": {
        "zh_CN": "正在导出片段 {current} / {total}…",
        "en_US": "Exporting clip {current} / {total}…",
    },
    "export.progress_detail_frames": {
        "zh_CN": "片段 {current}/{total} · 输出帧 {frame}/{frame_total}",
        "en_US": "Clip {current}/{total} · output frame {frame}/{frame_total}",
    },
    "export.progress_detail_seeking": {
        "zh_CN": "片段 {current}/{total} · 转码前解码中…",
        "en_US": "Clip {current}/{total} · Decoding before transcoding…",
    },
    "export.cancelled": {"zh_CN": "导出已取消。", "en_US": "Export cancelled."},
    "export.strong.title": {"zh_CN": "实验性功能", "en_US": "Experimental feature"},
    "export.strong.confirm": {
        "zh_CN": "“导出全部片段（强纠偏）”属于实验性功能，可能显著改变音频节奏或带来兼容性差异。此外，强纠偏模式由于采用精准寻址导致速度较慢，可能会有进度长时间不涨的情况，请耐心等待。是否继续？",
        "en_US": "Export all clips (strong correction) is experimental and may noticeably alter audio pacing or compatibility. Also, strong correction uses accurate output seek which is slower, and progress may appear stuck for a long time. Please be patient. Continue?",
    },
    "export.options.title": {"zh_CN": "导出参数", "en_US": "Export options"},
    "export.options.quick_preset": {"zh_CN": "快速加载预设", "en_US": "Quick Preset"},
    "export.options.quick_preset.placeholder": {"zh_CN": "选择预设…", "en_US": "Select preset…"},
    "export.options.inherit_fps": {"zh_CN": "继承帧率", "en_US": "Inherit FPS"},
    "export.options.inherit_fps_value": {"zh_CN": "将继承：{fps}", "en_US": "Will inherit: {fps}"},
    "export.options.inherit_fps_inline": {"zh_CN": "继承帧率（{fps}）", "en_US": "Inherit FPS ({fps})"},
    "export.filename_template.label": {"zh_CN": "导出文件名模板", "en_US": "Export Filename Template"},
    "export.filename_template.placeholder": {
        "zh_CN": "{source_name}_{clip_index:03d}",
        "en_US": "{source_name}_{clip_index:03d}",
    },
    "export.filename_template.help.tip": {
        "zh_CN": "点击查看模板变量说明",
        "en_US": "Click to view template variables",
    },
    "export.filename_template.help.title": {
        "zh_CN": "导出文件名模板说明",
        "en_US": "Export Filename Template Help",
    },
    "export.filename_template.help.body": {
        "zh_CN": "变量清单（含含义）\n"
        "{source_name}：源文件名（不含扩展名）\n"
        "{source_ext}：源文件扩展名（不含点）\n"
        "{clip_index}：片段编号（从 1 开始）\n"
        "{start_frame}：片段起点帧号\n"
        "{end_frame}：片段终点帧号\n"
        "{start_time_ms}：片段起点时间（毫秒整数）\n"
        "{end_time_ms}：片段终点时间（毫秒整数）\n"
        "{start_time_s}：片段起点时间（秒，浮点）\n"
        "{end_time_s}：片段终点时间（秒，浮点）\n"
        "{start_tc}：片段起点时间码（HH-MM-SS.mmm）\n"
        "{end_tc}：片段终点时间码（HH-MM-SS.mmm）\n"
        "{duration_frames}：片段时长（帧）\n"
        "{duration_ms}：片段时长（毫秒）\n"
        "{duration_s}：片段时长（秒，浮点）\n"
        "{job_ts}：导出任务启动时的 Unix 时间戳（秒）\n"
        "{job_tc}：导出任务启动时的任务时间码（YYYY-MM-DD_HH-MM-SS）",
        "en_US": "Variables\n"
        "{source_name}: source filename without extension\n"
        "{source_ext}: source extension without dot\n"
        "{clip_index}: clip index starting from 1\n"
        "{start_frame}: clip start frame\n"
        "{end_frame}: clip end frame\n"
        "{start_time_ms}: clip start time in milliseconds\n"
        "{end_time_ms}: clip end time in milliseconds\n"
        "{start_time_s}: clip start time in seconds\n"
        "{end_time_s}: clip end time in seconds\n"
        "{start_tc}: clip start timecode (HH-MM-SS.mmm)\n"
        "{end_tc}: clip end timecode (HH-MM-SS.mmm)\n"
        "{duration_frames}: clip duration in frames\n"
        "{duration_ms}: clip duration in milliseconds\n"
        "{duration_s}: clip duration in seconds\n"
        "{job_ts}: export job Unix timestamp (seconds)\n"
        "{job_tc}: export job timecode (YYYY-MM-DD_HH-MM-SS)",
    },
    "export.options.encoder": {"zh_CN": "视频编码器", "en_US": "Video Encoder"},
    "export.options.rate_mode": {"zh_CN": "码率控制", "en_US": "Rate Control"},
    "export.options.rate_mode.bitrate": {"zh_CN": "按码率", "en_US": "Bitrate"},
    "export.options.bitrate_match_source": {
        "zh_CN": "与源视频码率一致",
        "en_US": "Match source bitrate",
    },
    "export.options.bitrate_match_source_disabled_tip": {
        "zh_CN": "已启用「与源视频码率一致」，此处数值不生效",
        "en_US": "Match source bitrate is enabled; this value is ignored",
    },
    "export.options.rate_mode.quality": {"zh_CN": "按量化器（推荐）", "en_US": "Quantizer (recommended)"},
    "export.options.rate_mode.quality_inline": {
        "zh_CN": "量化器（{qname}）",
        "en_US": "Quantizer ({qname})",
    },
    "export.options.quantizer.crf": {"zh_CN": "CRF", "en_US": "CRF"},
    "export.options.quantizer.qv": {"zh_CN": "q:v", "en_US": "q:v"},
    "export.options.quantizer.cq": {"zh_CN": "cq", "en_US": "cq"},
    "export.options.quantizer.global_quality": {"zh_CN": "global_quality", "en_US": "global_quality"},
    "export.options.quantizer.quality": {"zh_CN": "quality", "en_US": "quality"},
    "export.options.video_quality_tip": {
        "zh_CN": "{qname} 数值越小，画质通常越高，文件体积通常越大。",
        "en_US": "Lower {qname} usually means better quality and larger file size.",
    },
    "export.options.rate_mode.label_bitrate": {"zh_CN": "码率", "en_US": "Bitrate"},
    "export.options.rate_mode.label_quality": {"zh_CN": "量化器", "en_US": "Quantizer"},
    "export.options.video_bitrate": {"zh_CN": "视频码率", "en_US": "Video bitrate"},
    "export.options.video_quality": {"zh_CN": "量化器", "en_US": "Quantizer"},
    "export.options.encoder.auto": {"zh_CN": "自动（{resolved}）", "en_US": "Auto ({resolved})"},
    "export.options.encoder.libx264": {"zh_CN": "x264（CPU）", "en_US": "x264 (CPU)"},
    "export.options.encoder.libopenh264": {"zh_CN": "OpenH264（CPU）", "en_US": "OpenH264 (CPU)"},
    "export.options.encoder.mpeg4": {"zh_CN": "MPEG-4（CPU）", "en_US": "MPEG-4 (CPU)"},
    "export.options.encoder.h264_nvenc": {"zh_CN": "H.264 NVENC（NVIDIA）", "en_US": "H.264 NVENC (NVIDIA)"},
    "export.options.encoder.hevc_nvenc": {"zh_CN": "HEVC NVENC（NVIDIA）", "en_US": "HEVC NVENC (NVIDIA)"},
    "export.options.encoder.h264_amf": {"zh_CN": "H.264 AMF（AMD）", "en_US": "H.264 AMF (AMD)"},
    "export.options.encoder.hevc_amf": {"zh_CN": "HEVC AMF（AMD）", "en_US": "HEVC AMF (AMD)"},
    "export.options.encoder.h264_qsv": {"zh_CN": "H.264 QSV（Intel）", "en_US": "H.264 QSV (Intel)"},
    "export.options.encoder.hevc_qsv": {"zh_CN": "HEVC QSV（Intel）", "en_US": "HEVC QSV (Intel)"},
    "export.options.encoder.unavailable_tag": {"zh_CN": "（不可用）", "en_US": "(unavailable)"},
    "export.options.codec_display_resolved": {
        "zh_CN": "{requested} -> 实际 {resolved}",
        "en_US": "{requested} -> actual {resolved}",
    },
    "export.options.codec_fallback_note": {
        "zh_CN": "编码器不可用，已回退：{requested} -> {resolved}",
        "en_US": "Requested codec unavailable; fallback: {requested} -> {resolved}",
    },
    "export.options.resolution": {"zh_CN": "分辨率", "en_US": "Resolution"},
    "export.options.content_resolution": {"zh_CN": "内容分辨率", "en_US": "Content Resolution"},
    "export.options.content_resolution_tip": {
        "zh_CN": "这里的分辨率数值已按 SAR 校正为 PAR 1:1，在此处设置的分辨率会进行拉伸处理。",
        "en_US": "These resolution values are SAR-corrected to PAR 1:1. Resolution changes here are applied by scaling (stretch/resize).",
    },
    "export.options.width_label": {"zh_CN": "宽", "en_US": "W"},
    "export.options.height_label": {"zh_CN": "高", "en_US": "H"},
    "export.options.preset_width": {"zh_CN": "预设宽", "en_US": "Preset W"},
    "export.options.preset_height": {"zh_CN": "预设高", "en_US": "Preset H"},
    "export.options.inherit_resolution": {"zh_CN": "继承分辨率", "en_US": "Inherit resolution"},
    "export.options.inherit_resolution_value": {
        "zh_CN": "将继承：{width}x{height}",
        "en_US": "Will inherit: {width}x{height}",
    },
    "export.options.inherit_resolution_inline": {
        "zh_CN": "继承分辨率（{width}x{height}）",
        "en_US": "Inherit resolution ({width}x{height})",
    },
    "export.options.keep_aspect": {"zh_CN": "保持比例", "en_US": "Keep aspect ratio"},
    "export.options.keep_aspect_on": {"zh_CN": "保持比例（已锁定）", "en_US": "Keep aspect ratio (locked)"},
    "export.options.keep_aspect_off": {"zh_CN": "保持比例（已解锁）", "en_US": "Keep aspect ratio (unlocked)"},
    "export.options.multiple_enable": {"zh_CN": "宽高限定为", "en_US": "Constrain width/height to"},
    "export.options.multiple_suffix": {"zh_CN": "倍数", "en_US": "multiple"},
    "export.options.multiple_tip": {
        "zh_CN": "此处设置的分辨率以上方设置的「内容分辨率」为基准，进行裁切/黑边，不会进行拉伸。",
        "en_US": "Resolution constraints here are based on the Content Resolution set above, applied via cropping/padding, not scaling.",
    },
    "export.options.edge_adjust.title": {"zh_CN": "倍数冲突处理", "en_US": "Multiple Conflict Handling"},
    "export.options.edge_adjust.hint": {
        "zh_CN": "正数表示增加黑边，负数表示裁切像素。",
        "en_US": "Positive values add black padding; negative values crop pixels.",
    },
    "export.options.edge_adjust.mode": {"zh_CN": "处理方式", "en_US": "Method"},
    "export.options.edge_adjust.pad": {"zh_CN": "增加黑边", "en_US": "Add black borders"},
    "export.options.edge_adjust.min_crop": {"zh_CN": "一键最小裁切", "en_US": "Minimal Crop"},
    "export.options.edge_adjust.min_pad": {"zh_CN": "一键最小黑边", "en_US": "Minimal Pad"},
    "export.options.edge_adjust.crop": {"zh_CN": "抛弃边缘像素", "en_US": "Crop edge pixels"},
    "export.options.edge_adjust.horizontal": {"zh_CN": "水平", "en_US": "Horizontal"},
    "export.options.edge_adjust.vertical": {"zh_CN": "垂直", "en_US": "Vertical"},
    "export.options.left": {"zh_CN": "左：", "en_US": "Left:"},
    "export.options.right": {"zh_CN": "右：", "en_US": "Right:"},
    "export.options.top": {"zh_CN": "上：", "en_US": "Top:"},
    "export.options.bottom": {"zh_CN": "下：", "en_US": "Bottom:"},
    "export.options.delta_suffix_pad": {"zh_CN": "（黑边）", "en_US": " (pad)"},
    "export.options.delta_suffix_crop": {"zh_CN": "（裁切）", "en_US": " (crop)"},
    "export.options.delta_suffix_none": {"zh_CN": "（不变）", "en_US": " (none)"},
    "export.options.crop_left": {"zh_CN": "左：丢弃", "en_US": "Left crop"},
    "export.options.crop_right": {"zh_CN": "右：丢弃", "en_US": "Right crop"},
    "export.options.crop_top": {"zh_CN": "上：丢弃", "en_US": "Top crop"},
    "export.options.crop_bottom": {"zh_CN": "下：丢弃", "en_US": "Bottom crop"},
    "export.options.preview.bitrate": {
        "zh_CN": "本次导出将使用：CFR={fps}，编码器={codec}，控制={mode}，码率={bitrate} kbps，内容分辨率={content_width}x{content_height}，最终输出分辨率={width}x{height}",
        "en_US": "This export uses: CFR={fps}, encoder={codec}, control={mode}, bitrate={bitrate} kbps, content resolution={content_width}x{content_height}, final output resolution={width}x{height}",
    },
    "export.options.preview.quality": {
        "zh_CN": "本次导出将使用：CFR={fps}，编码器={codec}，控制={mode}，{qname}={quality}，内容分辨率={content_width}x{content_height}，最终输出分辨率={width}x{height}",
        "en_US": "This export uses: CFR={fps}, encoder={codec}, control={mode}, {qname}={quality}, content resolution={content_width}x{content_height}, final output resolution={width}x{height}",
    },
    "export.options.save_defaults": {
        "zh_CN": "将本次 CFR 保存为新的默认值",
        "en_US": "Save this CFR as new default",
    },
    "export.options.save_as_preset": {
        "zh_CN": "将本次导出参数保存为新的预设",
        "en_US": "Save this export options as a new preset",
    },
    "export.options.save_to_loaded_preset": {
        "zh_CN": "将本次导出参数保存到预设",
        "en_US": "Save this export options to loaded preset",
    },
    "export.options.save_as_preset_placeholder": {
        "zh_CN": "输入预设名称",
        "en_US": "Preset name",
    },
    "export.options.cmd_drawer": {"zh_CN": "FFmpeg 命令预览", "en_US": "FFmpeg command preview"},
    "export.options.cmd_title": {"zh_CN": "Terminal", "en_US": "Terminal"},
    "export.options.copy": {"zh_CN": "复制命令", "en_US": "Copy command"},
    "export.options.copied": {"zh_CN": "已复制", "en_US": "Copied"},
    "export.options.ffmpeg_preview": {
        "zh_CN": "FFmpeg 预览：-c:v {codec} -b:v {bitrate}k | -fps_mode {fps_mode}\n-vf {vf}\n-af {af}",
        "en_US": "FFmpeg preview: -c:v {codec} -b:v {bitrate}k | -fps_mode {fps_mode}\n-vf {vf}\n-af {af}",
    },
    "export.failed": {"zh_CN": "导出失败", "en_US": "Export failed"},
    "export.failed_detail": {
        "zh_CN": "FFmpeg 返回了以下错误信息（可选中复制）：",
        "en_US": "FFmpeg returned the following error (selectable, copyable):",
    },
    "export.copy_error": {"zh_CN": "复制错误信息", "en_US": "Copy error"},
    "dialog.ok": {"zh_CN": "确定", "en_US": "OK"},
    "ffmpeg.bootstrap.title": {"zh_CN": "FFmpeg", "en_US": "FFmpeg"},
    "ffmpeg.bootstrap.downloading": {
        "zh_CN": "正在下载 FFmpeg（预编译包）…",
        "en_US": "Downloading FFmpeg (prebuilt package)…",
    },
    "ffmpeg.bootstrap.mb": {"zh_CN": "已传输约 {mb} MB", "en_US": "Downloaded ~{mb} MB"},
    "ffmpeg.bootstrap.failed_title": {"zh_CN": "无法获取 FFmpeg", "en_US": "Could not obtain FFmpeg"},
    "ffmpeg.bootstrap.failed_body": {
        "zh_CN": "原因：{detail}\n\n请检查网络后重试，或按下一步说明手动安装。",
        "en_US": "Reason: {detail}\n\nCheck your network and retry, or follow the next dialog to install manually.",
    },
    "ffmpeg.bootstrap.verify_failed": {
        "zh_CN": "下载已完成，但在程序目录下的 ffmpeg 文件夹未找到 ffmpeg/ffprobe，请手动放置或检查权限。",
        "en_US": "Download finished, but ffmpeg/ffprobe were not found in the app's ffmpeg folder. Check permissions or install manually.",
    },
    "ffmpeg.bootstrap.not_found_title": {
        "zh_CN": "未找到 FFmpeg",
        "en_US": "FFmpeg not found",
    },
    "ffmpeg.bootstrap.not_found_body": {
        "zh_CN": "需要 FFmpeg 才能打开视频和导出片段。是否自动下载到程序目录？\n\n（不会修改系统文件。）",
        "en_US": "FFmpeg is required to open videos and export clips. Download it to the app directory?\n\n(Will not modify system files.)",
    },
    "ffmpeg.bootstrap.arch_mismatch_app_body": {
        "zh_CN": "程序目录中的 FFmpeg 与当前 CPU 架构不匹配，无法运行。是否下载正确版本？",
        "en_US": "The FFmpeg in the app directory does not match your CPU architecture. Download a compatible version?",
    },
    "ffmpeg.bootstrap.arch_mismatch_system_title": {
        "zh_CN": "系统 FFmpeg 架构不匹配",
        "en_US": "System FFmpeg architecture mismatch",
    },
    "ffmpeg.bootstrap.arch_mismatch_system_body": {
        "zh_CN": "系统 PATH 中的 FFmpeg（{path}）与当前 CPU 架构不匹配。\n\n不会修改系统文件。是否下载正确版本到程序目录？",
        "en_US": "The FFmpeg on your system PATH ({path}) does not match your CPU architecture.\n\nSystem files will not be modified. Download a compatible version to the app directory?",
    },
    "ffmpeg.bootstrap.network_error_title": {
        "zh_CN": "网络不可用",
        "en_US": "Network unavailable",
    },
    "ffmpeg.bootstrap.network_error_body": {
        "zh_CN": "无法下载 FFmpeg：{detail}\n\n请检查网络连接后重试，或手动将 ffmpeg / ffprobe 放置到程序目录下的 ffmpeg 文件夹。",
        "en_US": "Cannot download FFmpeg: {detail}\n\nCheck your network and try again, or manually place ffmpeg / ffprobe into the app's ffmpeg folder.",
    },
    "ffmpeg.bootstrap.download_confirm": {
        "zh_CN": "下载",
        "en_US": "Download",
    },
    "ffmpeg.bootstrap.download_skip": {
        "zh_CN": "跳过",
        "en_US": "Skip",
    },
    "ffmpeg.bootstrap.macos_downloading_ffmpeg": {
        "zh_CN": "正在下载 ffmpeg…",
        "en_US": "Downloading ffmpeg…",
    },
    "ffmpeg.bootstrap.macos_downloading_ffprobe": {
        "zh_CN": "正在下载 ffprobe…",
        "en_US": "Downloading ffprobe…",
    },
    "ffmpeg.bootstrap.manual_title": {"zh_CN": "手动安装 FFmpeg", "en_US": "Install FFmpeg manually"},
    "ffmpeg.bootstrap.manual_body": {
        "zh_CN": "请将 ffmpeg 与 ffprobe 可执行文件放到本程序目录下的 ffmpeg 文件夹，或安装到系统 PATH。\n\n"
        "建议来源（LGPL）：https://github.com/BtbN/FFmpeg-Builds/releases\n\n"
        "许可说明见仓库内 FFMPEG_NOTES.txt。",
        "en_US": "Place ffmpeg and ffprobe in this application's ffmpeg folder, or install them on your PATH.\n\n"
        "Suggested source (LGPL): https://github.com/BtbN/FFmpeg-Builds/releases\n\n"
        "See FFMPEG_NOTES.txt in the repository for compliance notes.",
    },
    "ffmpeg.bootstrap.brew_title": {
        "zh_CN": "通过 Homebrew 安装",
        "en_US": "Install via Homebrew",
    },
    "ffmpeg.bootstrap.brew_available_body": {
        "zh_CN": "是否通过 Homebrew 安装 FFmpeg？\n\n执行命令：brew install ffmpeg\n\n安装后请重启程序。",
        "en_US": "Install FFmpeg via Homebrew?\n\nCommand: brew install ffmpeg\n\nRestart the application after installation.",
    },
    "ffmpeg.bootstrap.brew_not_installed_body": {
        "zh_CN": "未检测到 Homebrew。\n\n是否打开终端安装 Homebrew？\n\n安装命令：\n/bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"\n\n安装 Homebrew 后，可执行 brew install ffmpeg 安装 FFmpeg。",
        "en_US": "Homebrew is not installed.\n\nOpen Terminal to install Homebrew?\n\nCommand:\n/bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"\n\nAfter installing Homebrew, run: brew install ffmpeg",
    },
    "ffmpeg.bootstrap.open_terminal": {
        "zh_CN": "打开终端",
        "en_US": "Open Terminal",
    },
    "ffmpeg.bootstrap.install_restart": {
        "zh_CN": "FFmpeg 安装完成。请重启程序以识别新安装的 FFmpeg。",
        "en_US": "FFmpeg installed. Please restart the application to use it.",
    },
    "ffmpeg.bootstrap.limited_title": {
        "zh_CN": "FFmpeg 未就绪",
        "en_US": "FFmpeg unavailable",
    },
    "ffmpeg.bootstrap.limited_body": {
        "zh_CN": "应用已启动，但 FFmpeg 尚不可用。打开视频、波形分析和导出等功能可能受限。\n\n"
        "请按提示完成 FFmpeg 安装后重试。",
        "en_US": "The app has started, but FFmpeg is currently unavailable. Features like opening videos, "
        "waveform analysis, and export may be limited.\n\nInstall FFmpeg as instructed and try again.",
    },
    "ffmpeg.bootstrap.limited_status": {
        "zh_CN": "FFmpeg 未就绪：部分功能受限",
        "en_US": "FFmpeg unavailable: some features are limited",
    },
    "startup.restore.title": {"zh_CN": "加载上次项目", "en_US": "Restore last project"},
    "startup.restore.body": {
        "zh_CN": "检测到上次打开的项目「{filename}」，是否立即加载？",
        "en_US": "Detected last opened project \"{filename}\". Load it now?",
    },
    "startup.restore.load": {"zh_CN": "加载", "en_US": "Load"},
    "startup.restore.skip": {"zh_CN": "暂不加载", "en_US": "Not now"},
    "startup.restore.remember": {
        "zh_CN": "记住我的选择并不再询问",
        "en_US": "Remember my choice and stop asking",
    },
    "startup.restore.failed": {
        "zh_CN": "加载上次项目失败：{detail}",
        "en_US": "Failed to restore last project: {detail}",
    },
    "timeline.clip_hover_tooltip": {
        "zh_CN": "片段#{clip_no}\n右键单击：选中片段；右键拖拽：平移片段",
        "en_US": "Clip #{clip_no}\nRight-click: select clip; right-drag: move clip",
    },
    "hint.shortcuts": {
        "zh_CN": "拖入视频可打开 | 空格 播放/暂停 | S 起点 E 终点 | 1–5 定长 | M 中点 | C/F 按设置 x·n+y 吸附 | A/D 选中片段 ±1 帧平移 | Z/X 选中边界 ±1 帧 | 快捷键：播放时←/→（<< >>及秒数见常规）；快捷键：暂停时←/→（单帧步进 |</>|）；L / 选中片段内循环播放 | Delete/Backspace 删选中片段 | 双击首尾帧缩略图跳转 | 时间轴/波形悬停十字线同步 | 单击波形跳转 | ↑/↓ 预览音量 ±5 | Ctrl+滚轮 时间轴/波形缩放 | Shift+滚轮 平移 | 双击时间轴复位缩放 | ,/. 平移时间轴 | -/=+/0 缩放与复位 | Ctrl+Z 撤销 | Ctrl+Y/Ctrl+Shift+Z 重做",
        "en_US": "Drop | Space play/pause | S/E | 1–5 | M | C/F | A/D ±1 slip | Z/X ±1 boundary | arrows when playing (<< >> jump; secs in Settings → General); arrows when paused (one frame) | L / loop clip | Delete/Backspace clip | Double-click thumbs | Timeline/wave scrub | Click waveform | ↑/↓ volume ±5 | Ctrl+wheel zoom | Shift wheel pan | Dbl-click timeline reset | ,/. pan | -/=+/0 zoom | Ctrl+Z undo | Ctrl+Y/Ctrl+Shift+Z redo",
    },
    "menu.edit": {"zh_CN": "编辑", "en_US": "Edit"},
    "menu.undo": {"zh_CN": "撤销", "en_US": "Undo"},
    "menu.redo": {"zh_CN": "重做", "en_US": "Redo"},
    "menu.undo_action": {"zh_CN": "撤销: {action}", "en_US": "Undo: {action}"},
    "menu.redo_action": {"zh_CN": "重做: {action}", "en_US": "Redo: {action}"},
    "undo.status.undo": {"zh_CN": "已撤销: {action}", "en_US": "Undone: {action}"},
    "undo.status.redo": {"zh_CN": "已重做: {action}", "en_US": "Redone: {action}"},
    "undo.status.nothing_to_undo": {"zh_CN": "没有可撤销的操作", "en_US": "Nothing to undo"},
    "undo.status.nothing_to_redo": {"zh_CN": "没有可重做的操作", "en_US": "Nothing to redo"},
    "undo.action.set_start": {"zh_CN": "设置起点", "en_US": "Set start"},
    "undo.action.set_end": {"zh_CN": "设置终点", "en_US": "Set end"},
    "undo.action.delete_clip": {"zh_CN": "删除片段", "en_US": "Delete clip"},
    "undo.action.delete_boundary": {"zh_CN": "删除边界", "en_US": "Delete boundary"},
    "undo.action.snap_align": {"zh_CN": "对齐吸附", "en_US": "Snap align"},
    "undo.action.quick_slice": {"zh_CN": "快捷切片", "en_US": "Quick slice"},
    "undo.action.nudge_clip": {"zh_CN": "微调片段", "en_US": "Nudge clip"},
    "undo.action.drag_clip": {"zh_CN": "拖拽片段", "en_US": "Drag clip"},
    "undo.action.nudge_boundary": {"zh_CN": "微调边界", "en_US": "Nudge boundary"},
    "settings.undo_max_steps": {"zh_CN": "最大撤销步数", "en_US": "Max Undo Steps"},

    # ── Annotation ──────────────────────────────────────────────────
    "annotation.menu.file": {"zh_CN": "文件", "en_US": "File"},
    "annotation.open_folder": {"zh_CN": "打开文件夹…", "en_US": "Open Folder…"},
    "annotation.save": {"zh_CN": "保存", "en_US": "Save"},
    "annotation.export": {"zh_CN": "导出标注…", "en_US": "Export Annotations…"},
    "annotation.clip_list": {"zh_CN": "Clip 列表", "en_US": "Clip List"},
    "annotation.system_prompt": {"zh_CN": "System Prompt", "en_US": "System Prompt"},
    "annotation.system_prompt_placeholder": {
        "zh_CN": "输入项目级别的 LLM system prompt…",
        "en_US": "Enter project-level LLM system prompt…",
    },
    "annotation.error": {"zh_CN": "错误", "en_US": "Error"},
    "annotation.project_loaded": {
        "zh_CN": "已加载项目: {name}",
        "en_US": "Project loaded: {name}",
    },
    "annotation.startup.restore_body": {
        "zh_CN": "是否加载上次的标注项目 [{name}]？",
        "en_US": "Load the last annotation project [{name}]?",
    },
    "annotation.missing_clip_title": {"zh_CN": "Clip 已丢失", "en_US": "Clip Missing"},
    "annotation.missing_clip_body": {
        "zh_CN": "上次选中的 clip [{path}] 已不存在，可能是被外部删除。",
        "en_US": "The previously selected clip [{path}] no longer exists. It may have been deleted externally.",
    },
    "annotation.saved": {"zh_CN": "已保存", "en_US": "Saved"},
    "annotation.exported": {"zh_CN": "已导出到: {path}", "en_US": "Exported to: {path}"},
    "annotation.video_not_found": {
        "zh_CN": "视频文件未找到: {path}",
        "en_US": "Video file not found: {path}",
    },
    "annotation.no_clip_selected": {
        "zh_CN": "没有选中的 clip",
        "en_US": "No clip selected",
    },
    "annotation.no_preset": {
        "zh_CN": "没有 LLM 预设配置",
        "en_US": "No LLM preset configured",
    },
    "annotation.llm_done": {
        "zh_CN": "LLM 标注完成",
        "en_US": "LLM annotation completed",
    },
    "annotation.llm_error": {
        "zh_CN": "LLM 调用出错: {error}",
        "en_US": "LLM call error: {error}",
    },
    "annotation.llm_waiting": {"zh_CN": "等待回复中", "en_US": "Waiting for reply"},
    "annotation.llm_error_title": {"zh_CN": "LLM 调用失败", "en_US": "LLM Call Failed"},

    # Annotation editor
    "annotation.prompt_label": {"zh_CN": "提示词 (Prompt)", "en_US": "Prompt"},
    "annotation.prompt_placeholder": {
        "zh_CN": "最终文生视频提示词…",
        "en_US": "Final text-to-video prompt…",
    },
    "annotation.draft_label": {"zh_CN": "草稿 (Draft)", "en_US": "Draft"},
    "annotation.draft_placeholder": {
        "zh_CN": "手动标注草稿，或由 LLM 润色…",
        "en_US": "Manual draft, or refined by LLM…",
    },

    # Draft editor (dynamic per-frame + transition)
    "annotation.draft.global_context": {"zh_CN": "全局描述", "en_US": "Global Context"},
    "annotation.draft.global_context_optional": {"zh_CN": "全局描述（可选）", "en_US": "Global Context (Optional)"},
    "annotation.draft.global_context_placeholder": {
        "zh_CN": "可选的 clip 级别全局描述，留空则不会发送给 LLM…",
        "en_US": "Optional clip-level context, skipped in LLM prompt if empty…",
    },
    "annotation.draft.frame_label": {"zh_CN": "Frame {frame} ({sec:.1f}s)", "en_US": "Frame {frame} ({sec:.1f}s)"},
    "annotation.draft.frame_placeholder": {
        "zh_CN": "描述这一帧的画面内容…",
        "en_US": "Describe what is happening at this frame…",
    },
    "annotation.draft.transition_label": {
        "zh_CN": "→ Transition {f0} → {f1} ({s0:.1f}s → {s1:.1f}s)",
        "en_US": "→ Transition {f0} → {f1} ({s0:.1f}s → {s1:.1f}s)",
    },
    "annotation.draft.transition_placeholder": {
        "zh_CN": "描述从当前帧到下一帧之间的变化…",
        "en_US": "Describe what happens between this frame and the next…",
    },

    # Manual annotation frame selector
    "annotation.frames_label": {"zh_CN": "手动标注帧", "en_US": "Manual Annotation Frames"},
    "annotation.add_frame": {"zh_CN": "添加当前帧为手动标注帧", "en_US": "Add Current Frame as Annotation"},
    "annotation.remove_frame": {"zh_CN": "删除选中手动标注帧", "en_US": "Remove Selected Annotation Frame"},
    "annotation.add_frame_btn_tip": {
        "zh_CN": "添加当前帧为手动标注帧 (M)",
        "en_US": "Add current frame as manual annotation frame (M)",
    },
    "annotation.annotation_num": {
        "zh_CN": "标注帧 #{num}",
        "en_US": "Annotation #{num}",
    },
    "annotation.marker_tooltip": {
        "zh_CN": "手动标注帧 #{num} (帧{frame})",
        "en_US": "Manual Annotation #{num} (frame {frame})",
    },
    "annotation.frame_item": {
        "zh_CN": "帧{frame} ({sec}s)",
        "en_US": "Frame {frame} ({sec}s)",
    },
    "annotation.duplicate_frame": {
        "zh_CN": "帧 {frame} 已存在手动标注帧",
        "en_US": "Frame {frame} already has an annotation",
    },

    # Annotation nudge
    "annotation.btn.nudge_left": {"zh_CN": "◁（A）", "en_US": "◁ (A)"},
    "annotation.btn.nudge_right": {"zh_CN": "▷（D）", "en_US": "▷ (D)"},
    "annotation.tip.nudge_left": {
        "zh_CN": "将选中手动标注帧向左移动 1 帧 (A)",
        "en_US": "Nudge selected annotation frame left by 1 frame (A)",
    },
    "annotation.tip.nudge_right": {
        "zh_CN": "将选中手动标注帧向右移动 1 帧 (D)",
        "en_US": "Nudge selected annotation frame right by 1 frame (D)",
    },
    "annotation.tip.nudge_disabled": {
        "zh_CN": "请先选中一个手动标注帧",
        "en_US": "Select a manual annotation frame first",
    },

    # LLM panel
    "annotation.llm_preset": {"zh_CN": "LLM 预设", "en_US": "LLM Preset"},
    "annotation.manage_presets": {"zh_CN": "管理预设…", "en_US": "Manage Presets…"},
    "annotation.generate": {"zh_CN": "生成标注", "en_US": "Generate"},
    "annotation.preview_draft": {"zh_CN": "预览草稿", "en_US": "Preview Draft"},
    "annotation.preview_draft_empty": {
        "zh_CN": "（草稿为空，请先在 Draft 区域填写内容或添加手动标注帧）",
        "en_US": "(Draft is empty. Add content in the Draft area or add annotation frames first.)"
    },

    # LLM preset management (shared between preferences tab and gear-button dialog)
    "settings.tab.llm_presets": {"zh_CN": "LLM API预设", "en_US": "LLM API Presets"},
    "settings.llm_preset": {"zh_CN": "LLM API预设", "en_US": "LLM API Preset"},
    "settings.llm_preset.new": {"zh_CN": "新预设", "en_US": "New preset"},
    "settings.llm_preset.save": {"zh_CN": "保存预设", "en_US": "Save preset"},
    "settings.llm_preset.saved": {"zh_CN": "已保存", "en_US": "Saved"},
    "settings.llm_preset.name_placeholder": {"zh_CN": "请输入预设名称", "en_US": "Enter preset name"},
    "settings.llm_preset.name_required.title": {"zh_CN": "预设名称不能为空", "en_US": "Preset name required"},
    "settings.llm_preset.name_required.body": {
        "zh_CN": "请输入预设名称。",
        "en_US": "Please enter a preset name.",
    },
    "settings.llm_preset.duplicate.title": {"zh_CN": "预设名称已存在", "en_US": "Preset name already exists"},
    "settings.llm_preset.duplicate.body": {
        "zh_CN": "预设「{name}」已存在。是否覆盖该预设？",
        "en_US": "Preset \"{name}\" already exists. Overwrite it?",
    },
    "settings.llm_preset.unsaved.title": {"zh_CN": "预设未保存", "en_US": "Preset not saved"},
    "settings.llm_preset.unsaved.body": {
        "zh_CN": "当前预设修改尚未保存，是否先保存？",
        "en_US": "Current preset changes are not saved. Save before closing?",
    },
    "annotation.preset_name": {"zh_CN": "名称", "en_US": "Name"},
    "annotation.preset_url": {"zh_CN": "Base URL", "en_US": "Base URL"},
    "annotation.preset_key": {"zh_CN": "API Key", "en_US": "API Key"},
    "annotation.preset_model": {"zh_CN": "模型", "en_US": "Model"},
    "annotation.preset_format": {"zh_CN": "API 格式", "en_US": "API Format"},
    "annotation.preset_format_openai": {"zh_CN": "OpenAI", "en_US": "OpenAI"},
    "annotation.preset_fetch_models": {"zh_CN": "获取模型", "en_US": "Fetch"},
    "annotation.preset_fetch_models_tip": {
        "zh_CN": "从服务器获取可用模型列表",
        "en_US": "Fetch available model list from server",
    },
    "annotation.preset_fetch_error_title": {
        "zh_CN": "获取模型列表失败",
        "en_US": "Failed to fetch model list",
    },
    "annotation.preset_fetch_error_body": {
        "zh_CN": "服务器返回错误：\n\n{error}",
        "en_US": "Server returned an error:\n\n{error}",
    },
    "annotation.preset_fill_base_url": {
        "zh_CN": "请先填写 Base URL 并选择 API 格式",
        "en_US": "Please fill in Base URL and select API Format first",
    },
    "annotation.preset_fetching": {"zh_CN": "获取中", "en_US": "Fetching"},
    "annotation.preset_streaming": {"zh_CN": "流式传输", "en_US": "Streaming"},
    "annotation.preset_thinking": {"zh_CN": "思考模式", "en_US": "Thinking Mode"},
    "annotation.preset_is_omni": {"zh_CN": "Omni 模型 (支持音视频)", "en_US": "Omni Model (audio/video)"},
    "annotation.preset_omni_media_format": {"zh_CN": "音视频格式", "en_US": "Media Format"},
    "annotation.preset_omni_media_qwen": {"zh_CN": "Qwen Omni", "en_US": "Qwen Omni"},

    # Quick-access toggles above prompt editor
    "annotation.quick.streaming": {"zh_CN": "流式", "en_US": "Stream"},
    "annotation.quick.streaming_tip": {
        "zh_CN": "启用流式传输，实时显示生成内容",
        "en_US": "Enable streaming to see content as it is generated",
    },
    "annotation.quick.thinking": {"zh_CN": "思考", "en_US": "Think"},
    "annotation.quick.thinking_tip": {
        "zh_CN": "启用思考模式，模型会输出推理过程",
        "en_US": "Enable thinking mode to show model reasoning",
    },
    "annotation.quick.omni": {"zh_CN": "Omni", "en_US": "Omni"},
    "annotation.quick.omni_tip": {
        "zh_CN": "启用 Omni 模式，发送完整视频文件（含音频）而非帧截图",
        "en_US": "Enable Omni mode to send full video (with audio) instead of frame screenshots",
    },

    # Omni video transcode toggles
    "annotation.omni.reduce_resolution": {"zh_CN": "限制分辨率", "en_US": "Limit Resolution"},
    "annotation.omni.reduce_resolution_tip": {
        "zh_CN": "将视频分辨率限制在 480p 以内（高于此值则缩放，低于则保持原样）",
        "en_US": "Clamp video resolution to 480p max (scale down if taller, leave untouched if already below)",
    },
    "annotation.omni.reduce_bitrate": {"zh_CN": "限制码率", "en_US": "Limit Bitrate"},
    "annotation.omni.reduce_bitrate_tip": {
        "zh_CN": "将视频码率限制在 1.5 Mbps 以内（高于此值则重新编码，低于则保持原样）",
        "en_US": "Cap video bitrate at 1.5 Mbps (re-encode if above, leave untouched if already below)",
    },

    # External modification dialog
    "annotation.external_change_title": {"zh_CN": "检测到外部修改", "en_US": "External Change Detected"},
    "annotation.external_change_body": {
        "zh_CN": "{filename} 已被外部程序修改。",
        "en_US": "{filename} has been modified externally.",
    },
    "annotation.external_change_info": {
        "zh_CN": "加载外部版本会将当前版本暂存为一次撤销操作。",
        "en_US": "Loading the external version will save the current version as an undo step.",
    },
    "annotation.external_change_load": {"zh_CN": "加载外部版本", "en_US": "Load External Version"},
    "annotation.external_change_keep": {"zh_CN": "保留软件版本", "en_US": "Keep Software Version"},
    "annotation.external_loaded": {"zh_CN": "已加载外部版本。Ctrl+Z 可撤销。", "en_US": "External version loaded. Ctrl+Z to undo."},

    # Inline version navigation
    "annotation.version.prev_tip": {"zh_CN": "上一个版本", "en_US": "Previous version"},
    "annotation.version.next_tip": {"zh_CN": "下一个版本", "en_US": "Next version"},
    "annotation.version.label_tip": {
        "zh_CN": "当前版本 / 总版本数",
        "en_US": "Current version / total versions",
    },
    "annotation.version.delete_tip": {"zh_CN": "删除当前版本", "en_US": "Delete current version"},
    "annotation.version.deleted": {"zh_CN": "版本已删除", "en_US": "Version deleted"},
    "annotation.version.cant_delete_last": {
        "zh_CN": "无法删除最后一个版本",
        "en_US": "Cannot delete the last version",
    },
}


def current_lang() -> str:
    s = QSettings("Innovaspire", "EasyClip")
    v = str(s.value("language", "zh_CN"))
    return v if v in ("zh_CN", "en_US") else "zh_CN"


def tr(key: str, **kwargs: str | int) -> str:
    lang = current_lang()
    row = MESSAGES.get(key, {})
    text = row.get(lang) or row.get("zh_CN") or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
