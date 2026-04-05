import base64
import html
import importlib
import io
import json
import os
import platform
import re
import sys
import threading
import time
from collections import deque
from uuid import uuid4

import streamlit as st
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

# Add the root directory of the project to the system path to allow importing modules from the project
root_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if root_dir not in sys.path:
    sys.path.append(root_dir)
    print("******** sys.path ********")
    print(sys.path)
    print("")

from app.config import config
from app.models import const
from app.models.schema import (
    MaterialInfo,
    VideoAspect,
    VideoConcatMode,
    VideoParams,
    VideoTransitionMode,
)
from app.services import llm, voice, youtube_upload
from app.services import task as tm
from app.services import state as sm
from app.utils import utils

_WEBUI_TASK_THREADS = {}
_WEBUI_TASK_LOCK = threading.Lock()
_WEBUI_TASK_META_FILE = os.path.join(utils.storage_dir(create=True), "webui_active_task.json")

st.set_page_config(
    page_title="0Code AutoGen",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="auto",
    menu_items={
        "Report a bug": "https://github.com/harry0703/0code-autogen/issues",
        "About": "# 0Code AutoGen\nSimply provide a topic or keyword for a video, and it will "
        "automatically generate the video copy, video materials, video subtitles, "
        "and video background music before synthesizing a high-definition short "
        "video.\n\nhttps://github.com/harry0703/0code-autogen",
    },
)


streamlit_style = """
<style>
h1 {
    padding-top: 0 !important;
}
</style>
"""
st.markdown(streamlit_style, unsafe_allow_html=True)

# 定义资源目录
font_dir = os.path.join(root_dir, "resource", "fonts")
song_dir = os.path.join(root_dir, "resource", "songs")
i18n_dir = os.path.join(root_dir, "webui", "i18n")
config_file = os.path.join(root_dir, "webui", ".streamlit", "webui.toml")
system_locale = utils.get_system_locale()


if "video_subject" not in st.session_state:
    st.session_state["video_subject"] = ""
if "video_script" not in st.session_state:
    st.session_state["video_script"] = ""
if "video_terms" not in st.session_state:
    st.session_state["video_terms"] = ""
if "ui_language" not in st.session_state:
    st.session_state["ui_language"] = config.ui.get("language", system_locale)
if "local_video_materials" not in st.session_state:
    # 记住用户最近一次已经落盘的本地素材，避免仅修改文案后二次生成时丢失素材列表。
    st.session_state["local_video_materials"] = []

# 加载语言文件
locales = utils.load_locales(i18n_dir)

# 创建一个顶部栏，包含标题和语言选择
title_col, lang_col = st.columns([3, 1])

with title_col:
    st.title(f"0Code AutoGen v{config.project_version}")

with lang_col:
    display_languages = []
    selected_index = 0
    for i, code in enumerate(locales.keys()):
        display_languages.append(f"{code} - {locales[code].get('Language')}")
        if code == st.session_state.get("ui_language", ""):
            selected_index = i

    selected_language = st.selectbox(
        "Language / 语言",
        options=display_languages,
        index=selected_index,
        key="top_language_selector",
        label_visibility="collapsed",
    )
    if selected_language:
        code = selected_language.split(" - ")[0].strip()
        st.session_state["ui_language"] = code
        config.ui["language"] = code

support_locales = [
    "zh-CN",
    "zh-HK",
    "zh-TW",
    "de-DE",
    "en-US",
    "fr-FR",
    "vi-VN",
    "th-TH",
    "tr-TR",
    "hi-IN",
    "pa-IN",
]


def get_all_fonts():
    fonts = []
    for root, dirs, files in os.walk(font_dir):
        for file in files:
            if file.endswith(".ttf") or file.endswith(".ttc"):
                fonts.append(file)
    fonts.sort()
    return fonts


def render_font_preview(font_filename: str, text: str = "Hello | हैलो | ਸਤ ਸ੍ਰੀ ਅਕਾਲ", width: int = 420, height: int = 52) -> str:
    """Render a font preview image and return as base64 PNG data URI."""
    font_path = os.path.join(font_dir, font_filename)
    img = Image.new("RGBA", (width, height), (30, 30, 30, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(font_path, 22)
    except Exception:
        font = ImageFont.load_default()
    draw.text((10, 10), text, font=font, fill=(255, 255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def get_all_songs():
    songs = []
    for root, dirs, files in os.walk(song_dir):
        for file in files:
            if file.endswith(".mp3"):
                songs.append(file)
    return songs


# Known script-compatibility for bundled fonts.
# Fonts NOT listed here are assumed to work for any language (Latin/universal).
FONT_SCRIPT_COMPAT = {
    # Hindi (Devanagari) — must render ह,ि,ं etc.
    "NotoSansDevanagari-Bold.ttf": ["hi-IN"],
    "Nirmala.ttc": ["hi-IN", "pa-IN"],           # Windows system font; covers both
    # Punjabi (Gurmukhi) — must render ਸ,ਤ,ੀ etc.
    "NotoSansGurmukhi-Bold.ttf": ["pa-IN"],
    # Chinese / CJK only
    "MicrosoftYaHeiBold.ttc": ["zh-CN", "zh-HK", "zh-TW"],
    "MicrosoftYaHeiNormal.ttc": ["zh-CN", "zh-HK", "zh-TW"],
    "STHeitiLight.ttc": ["zh-CN", "zh-HK", "zh-TW"],
    "STHeitiMedium.ttc": ["zh-CN", "zh-HK", "zh-TW"],
}

# Languages that need non-Latin script support → restrict font list
SCRIPT_RESTRICTED_LANGS = {"hi-IN", "pa-IN", "zh-CN", "zh-HK", "zh-TW"}


def get_fonts_for_language(language: str = "") -> list:
    """Return font list filtered to only fonts that can render the given language."""
    all_fonts = get_all_fonts()
    if not language or language not in SCRIPT_RESTRICTED_LANGS:
        return all_fonts  # no restriction for Latin/auto-detect

    compatible = [
        f for f in all_fonts
        if language in FONT_SCRIPT_COMPAT.get(f, [language])  # listed and includes lang
        or f not in FONT_SCRIPT_COMPAT  # not listed = assumed Latin, skip for restricted langs
    ]
    # Filter out fonts that are ONLY for other restricted languages
    filtered = []
    for f in all_fonts:
        compat = FONT_SCRIPT_COMPAT.get(f)
        if compat is None:
            # Unknown font — skip for non-Latin languages (safe default)
            continue
        if language in compat:
            filtered.append(f)

    return filtered if filtered else all_fonts  # fallback to all if nothing matched


def open_task_folder(task_id):
    try:
        sys = platform.system()
        path = os.path.join(root_dir, "storage", "tasks", task_id)
        if os.path.exists(path):
            if sys == "Windows":
                os.system(f'start "" "{path}"')
            if sys == "Darwin":
                os.system(f'open "{path}"')
    except Exception as e:
        logger.error(e)


_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def _first_non_empty_line(text: str) -> str:
    for line in (text or "").splitlines():
        candidate = line.strip()
        if candidate:
            return candidate
    return ""


def _sanitize_task_folder_name(name: str, max_len: int = 80) -> str:
    if not name:
        return ""

    # Remove characters invalid in Windows paths and collapse spaces.
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", " ", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")

    if len(name) > max_len:
        name = name[:max_len].rstrip(". ")

    if name.upper() in _WINDOWS_RESERVED_NAMES:
        name = f"{name}_task"

    return name


def _build_task_folder_name(video_script: str, video_subject: str) -> str:
    first_line = _first_non_empty_line(video_script)
    base = _sanitize_task_folder_name(first_line or (video_subject or "").strip())
    if not base:
        return str(uuid4())

    tasks_root = os.path.join(root_dir, "storage", "tasks")
    os.makedirs(tasks_root, exist_ok=True)

    candidate = base
    suffix = 2
    while os.path.exists(os.path.join(tasks_root, candidate)):
        candidate = f"{base}-{suffix}"
        suffix += 1

    return candidate


def _task_log_file(task_id: str) -> str:
    return os.path.join(utils.task_dir(task_id), "runtime.log")


def _serialize_video_params(params: VideoParams) -> dict:
    try:
        if hasattr(params, "model_dump_json"):
            return json.loads(params.model_dump_json())
        return params.dict()
    except Exception:
        return {}


def _save_active_task_meta(task_id: str, params: VideoParams):
    payload = {
        "task_id": task_id,
        "params": _serialize_video_params(params),
        "updated_at": int(time.time()),
    }
    try:
        with open(_WEBUI_TASK_META_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"failed to persist active task metadata: {e}")


def _load_active_task_meta() -> dict:
    if not os.path.exists(_WEBUI_TASK_META_FILE):
        return {}
    try:
        with open(_WEBUI_TASK_META_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _clear_active_task_meta(task_id: str = ""):
    meta = _load_active_task_meta()
    meta_task_id = str(meta.get("task_id", "")).strip()
    if task_id and meta_task_id and meta_task_id != task_id:
        return
    try:
        if os.path.exists(_WEBUI_TASK_META_FILE):
            os.remove(_WEBUI_TASK_META_FILE)
    except Exception as e:
        logger.warning(f"failed to clear active task metadata: {e}")


def _is_task_thread_alive(task_id: str) -> bool:
    with _WEBUI_TASK_LOCK:
        thread = _WEBUI_TASK_THREADS.get(task_id)
        return bool(thread and thread.is_alive())


def _read_log_tail(log_file: str, max_lines: int = 400) -> list[str]:
    if not os.path.exists(log_file):
        return []
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            return [line.rstrip("\n") for line in deque(f, maxlen=max_lines)]
    except Exception:
        return []


def _start_task_in_background(task_id: str, params: VideoParams):
    if _is_task_thread_alive(task_id):
        return

    def _runner():
        log_file = _task_log_file(task_id)
        log_sink_id = logger.add(
            log_file,
            level="DEBUG",
            enqueue=True,
            colorize=False,
            format=(
                "{time:%Y-%m-%d %H:%M:%S} | {level} | "
                + '"{file.path}:{line}": {function} - {message}'
                + "\n"
            ),
        )
        try:
            tm.start(task_id=task_id, params=params)
        except Exception as e:
            logger.exception(f"background task crashed: {e}")
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED, error=str(e))
        finally:
            try:
                logger.remove(log_sink_id)
            except Exception:
                pass

            task_info = sm.state.get_task(task_id) or {}
            state = task_info.get("state")
            if state in (const.TASK_STATE_COMPLETE, const.TASK_STATE_FAILED):
                _clear_active_task_meta(task_id)

    thread = threading.Thread(target=_runner, name=f"webui-task-{task_id}", daemon=False)
    with _WEBUI_TASK_LOCK:
        _WEBUI_TASK_THREADS[task_id] = thread
    thread.start()


def scroll_to_bottom():
    js = """
    <script>
        console.log("scroll_to_bottom");
        function scroll(dummy_var_to_force_repeat_execution){
            var sections = parent.document.querySelectorAll('section.main');
            console.log(sections);
            for(let index = 0; index<sections.length; index++) {
                sections[index].scrollTop = sections[index].scrollHeight;
            }
        }
        scroll(1);
    </script>
    """
    st.components.v1.html(js, height=0, width=0)


def _infer_runtime_status_and_progress(log_line: str):
    text = (log_line or "").lower()
    status = "Working"
    progress = None

    if "start generating video" in text:
        status, progress = "Starting task", 2
    elif "## generating video script" in text:
        status, progress = "Generating script", 8
    elif "## generating video terms" in text:
        status, progress = "Generating search terms", 15
    elif "## generating audio" in text:
        status, progress = "Generating audio", 25
    elif "## generating subtitle" in text:
        status, progress = "Generating subtitles", 35
    elif "## correcting subtitle" in text:
        status, progress = "Correcting subtitles", 42
    elif "## downloading videos from pexels" in text:
        status, progress = "Downloading videos from Pexels", 52
    elif "## downloading videos from pixabay" in text:
        status, progress = "Downloading videos from Pixabay", 52
    elif "downloading video:" in text:
        status, progress = "Downloading video clips", 58
    elif "## combining video" in text:
        status, progress = "Combining clips", 76
    elif "## generating video:" in text:
        status, progress = "Rendering final video", 88
    elif "video generation completed" in text or "task" in text and "finished" in text:
        status, progress = "Completed", 100
    elif "video generation failed" in text:
        status = "Failed"

    return status, progress


def _render_log_box(log_placeholder, lines, box_id: str, max_lines: int = 400):
    visible_lines = lines[-max_lines:]

    def _styled_line(raw_line: str) -> str:
        line = str(raw_line or "")
        level_match = re.search(
            r"\|\s*(DEBUG|INFO|WARNING|ERROR|CRITICAL|SUCCESS)\s*\|",
            line,
            flags=re.IGNORECASE,
        )

        line_color = "#d7dbe8"
        level_badge_color = "#9fb3d8"
        if level_match:
            level = level_match.group(1).upper()
            level_styles = {
                "DEBUG": ("#b3bac8", "#9aa3b5"),
                "INFO": ("#d7dbe8", "#74c0fc"),
                "WARNING": ("#ffe8b3", "#f7b731"),
                "ERROR": ("#ffd6d6", "#ff6b6b"),
                "CRITICAL": ("#ffd6d6", "#ff3b30"),
                "SUCCESS": ("#d9fbe1", "#2ecc71"),
            }
            line_color, level_badge_color = level_styles.get(
                level, (line_color, level_badge_color)
            )

        escaped_line = html.escape(line)
        if level_match:
            raw_level_token = level_match.group(0)
            escaped_level_token = html.escape(raw_level_token)
            level_name = level_match.group(1).upper()
            colored_token = (
                f"| <span style='color:{level_badge_color};font-weight:800;'>{level_name}</span> |"
            )
            escaped_line = escaped_line.replace(escaped_level_token, colored_token, 1)

        return f"<span style='color:{line_color};'>{escaped_line}</span>"

    rendered_lines = [_styled_line(line) for line in visible_lines] if visible_lines else [
        "<span style='color:#8b93a7;'>Waiting for logs...</span>"
    ]
    escaped_logs = "<br>".join(rendered_lines)
    log_html = f"""
    <div style=\"border:1px solid #2a2a2a;border-radius:10px;background:#0f1115;padding:10px;\">
      <div style=\"color:#e6e6e6;font-size:14px;font-weight:700;margin-bottom:8px;\">Live Logs</div>
      <div id=\"{box_id}\" style=\"height:320px;overflow-y:auto;background:#090b10;border:1px solid #20242f;border-radius:8px;padding:12px;color:#d7dbe8;font-family:Consolas,'Courier New',monospace;font-size:14px;line-height:1.55;white-space:pre-wrap;\">{escaped_logs}</div>
    </div>
    <script>
      const box = document.getElementById('{box_id}');
      if (box) {{ box.scrollTop = box.scrollHeight; }}
    </script>
    """
    with log_placeholder:
        st.components.v1.html(log_html, height=370)


def _update_runtime_monitor(task_id: str, status_container, progress_container, log_container, log_box_id: str):
    task_info = sm.state.get_task(task_id) or {}
    state = task_info.get("state", const.TASK_STATE_PROCESSING)

    progress = task_info.get("progress", 0)
    try:
        progress = int(progress)
    except Exception:
        progress = 0

    log_lines = _read_log_tail(_task_log_file(task_id), max_lines=400)
    inferred_status = "Working"
    inferred_progress = None
    if log_lines:
        inferred_status, inferred_progress = _infer_runtime_status_and_progress(log_lines[-1])

    if inferred_progress is not None:
        progress = max(progress, inferred_progress)
    progress = max(0, min(100, progress))

    if state == const.TASK_STATE_COMPLETE:
        status_text = "Completed"
        status_container.success(f"Status: {status_text}")
        progress = 100
    elif state == const.TASK_STATE_FAILED:
        status_text = "Failed"
        status_container.error(f"Status: {status_text}")
        progress = max(progress, 1)
    else:
        status_text = inferred_status if inferred_status else "Processing"
        status_container.info(f"Status: {status_text}")

    progress_container.progress(progress)

    if not config.ui.get("hide_log", False):
        _render_log_box(log_container, log_lines, log_box_id)

    return task_info


def init_log():
    logger.remove()
    _lvl = "DEBUG"

    def format_record(record):
        # 获取日志记录中的文件全路径
        file_path = record["file"].path
        # 将绝对路径转换为相对于项目根目录的路径
        relative_path = os.path.relpath(file_path, root_dir)
        # 更新记录中的文件路径
        record["file"].path = f"./{relative_path}"
        # 返回修改后的格式字符串
        # 您可以根据需要调整这里的格式
        record["message"] = record["message"].replace(root_dir, ".")

        _format = (
            "<green>{time:%Y-%m-%d %H:%M:%S}</> | "
            + "<level>{level}</> | "
            + '"{file.path}:{line}":<blue> {function}</> '
            + "- <level>{message}</>"
            + "\n"
        )
        return _format

    logger.add(
        sys.stdout,
        level=_lvl,
        format=format_record,
        colorize=True,
    )


init_log()

locales = utils.load_locales(i18n_dir)


def tr(key):
    loc = locales.get(st.session_state["ui_language"], {})
    return loc.get("Translation", {}).get(key, key)


# 创建基础设置折叠框
if not config.app.get("hide_config", False):
    with st.expander(tr("Basic Settings"), expanded=False):
        config_panels = st.columns(3)
        left_config_panel = config_panels[0]
        middle_config_panel = config_panels[1]
        right_config_panel = config_panels[2]

        # 左侧面板 - 日志设置
        with left_config_panel:
            # 是否隐藏配置面板
            hide_config = st.checkbox(
                tr("Hide Basic Settings"), value=config.app.get("hide_config", False)
            )
            config.app["hide_config"] = hide_config

            # 是否禁用日志显示
            hide_log = st.checkbox(
                tr("Hide Log"), value=config.ui.get("hide_log", False)
            )
            config.ui["hide_log"] = hide_log

        # 中间面板 - LLM 设置

        with middle_config_panel:
            st.write(tr("LLM Settings"))
            llm_providers = [
                "OpenAI",
                "Moonshot",
                "Azure",
                "Qwen",
                "DeepSeek",
                "ModelScope",
                "Gemini",
                "Ollama",
                "G4f",
                "OneAPI",
                "Cloudflare",
                "ERNIE",
                "Pollinations",
            ]
            saved_llm_provider = config.app.get("llm_provider", "OpenAI").lower()
            saved_llm_provider_index = 0
            for i, provider in enumerate(llm_providers):
                if provider.lower() == saved_llm_provider:
                    saved_llm_provider_index = i
                    break

            llm_provider = st.selectbox(
                tr("LLM Provider"),
                options=llm_providers,
                index=saved_llm_provider_index,
            )
            llm_helper = st.container()
            llm_provider = llm_provider.lower()
            config.app["llm_provider"] = llm_provider

            llm_api_key = config.app.get(f"{llm_provider}_api_key", "")
            llm_secret_key = config.app.get(
                f"{llm_provider}_secret_key", ""
            )  # only for baidu ernie
            llm_base_url = config.app.get(f"{llm_provider}_base_url", "")
            llm_model_name = config.app.get(f"{llm_provider}_model_name", "")
            llm_account_id = config.app.get(f"{llm_provider}_account_id", "")

            tips = ""
            if llm_provider == "ollama":
                if not llm_model_name:
                    llm_model_name = "qwen:7b"
                if not llm_base_url:
                    llm_base_url = "http://localhost:11434/v1"

                with llm_helper:
                    tips = """
                            ##### Ollama配置说明
                            - **API Key**: 随便填写，比如 123
                            - **Base Url**: 一般为 http://localhost:11434/v1
                                - 如果 `0Code AutoGen` 和 `Ollama` **不在同一台机器上**，需要填写 `Ollama` 机器的IP地址
                                - 如果 `0Code AutoGen` 是 `Docker` 部署，建议填写 `http://host.docker.internal:11434/v1`
                            - **Model Name**: 使用 `ollama list` 查看，比如 `qwen:7b`
                            """

            if llm_provider == "openai":
                if not llm_model_name:
                    llm_model_name = "gpt-3.5-turbo"
                with llm_helper:
                    tips = """
                            ##### OpenAI 配置说明
                            > 需要VPN开启全局流量模式
                            - **API Key**: [点击到官网申请](https://platform.openai.com/api-keys)
                            - **Base Url**: 可以留空
                            - **Model Name**: 填写**有权限**的模型，[点击查看模型列表](https://platform.openai.com/settings/organization/limits)
                            """

            if llm_provider == "moonshot":
                if not llm_model_name:
                    llm_model_name = "moonshot-v1-8k"
                with llm_helper:
                    tips = """
                            ##### Moonshot 配置说明
                            - **API Key**: [点击到官网申请](https://platform.moonshot.cn/console/api-keys)
                            - **Base Url**: 固定为 https://api.moonshot.cn/v1
                            - **Model Name**: 比如 moonshot-v1-8k，[点击查看模型列表](https://platform.moonshot.cn/docs/intro#%E6%A8%A1%E5%9E%8B%E5%88%97%E8%A1%A8)
                            """
            if llm_provider == "oneapi":
                if not llm_model_name:
                    llm_model_name = (
                        "claude-3-5-sonnet-20240620"  # 默认模型，可以根据需要调整
                    )
                with llm_helper:
                    tips = """
                        ##### OneAPI 配置说明
                        - **API Key**: 填写您的 OneAPI 密钥
                        - **Base Url**: 填写 OneAPI 的基础 URL
                        - **Model Name**: 填写您要使用的模型名称，例如 claude-3-5-sonnet-20240620
                        """

            if llm_provider == "qwen":
                if not llm_model_name:
                    llm_model_name = "qwen-max"
                with llm_helper:
                    tips = """
                            ##### 通义千问Qwen 配置说明
                            - **API Key**: [点击到官网申请](https://dashscope.console.aliyun.com/apiKey)
                            - **Base Url**: 留空
                            - **Model Name**: 比如 qwen-max，[点击查看模型列表](https://help.aliyun.com/zh/dashscope/developer-reference/model-introduction#3ef6d0bcf91wy)
                            """

            if llm_provider == "g4f":
                if not llm_model_name:
                    llm_model_name = "gpt-3.5-turbo"
                with llm_helper:
                    tips = """
                            ##### gpt4free 配置说明
                            > [GitHub开源项目](https://github.com/xtekky/gpt4free)，可以免费使用GPT模型，但是**稳定性较差**
                            - **API Key**: 随便填写，比如 123
                            - **Base Url**: 留空
                            - **Model Name**: 比如 gpt-3.5-turbo，[点击查看模型列表](https://github.com/xtekky/gpt4free/blob/main/g4f/models.py#L308)
                            """
            if llm_provider == "azure":
                with llm_helper:
                    tips = """
                            ##### Azure 配置说明
                            > [点击查看如何部署模型](https://learn.microsoft.com/zh-cn/azure/ai-services/openai/how-to/create-resource)
                            - **API Key**: [点击到Azure后台创建](https://portal.azure.com/#view/Microsoft_Azure_ProjectOxford/CognitiveServicesHub/~/OpenAI)
                            - **Base Url**: 留空
                            - **Model Name**: 填写你实际的部署名
                            """

            if llm_provider == "gemini":
                if not llm_model_name:
                    llm_model_name = "gemini-1.0-pro"

                with llm_helper:
                    tips = """
                            ##### Gemini 配置说明
                            > 需要VPN开启全局流量模式
                            - **API Key**: [点击到官网申请](https://ai.google.dev/)
                            - **Base Url**: 留空
                            - **Model Name**: 比如 gemini-1.0-pro
                            """

            if llm_provider == "deepseek":
                if not llm_model_name:
                    llm_model_name = "deepseek-chat"
                if not llm_base_url:
                    llm_base_url = "https://api.deepseek.com"
                with llm_helper:
                    tips = """
                            ##### DeepSeek 配置说明
                            - **API Key**: [点击到官网申请](https://platform.deepseek.com/api_keys)
                            - **Base Url**: 固定为 https://api.deepseek.com
                            - **Model Name**: 固定为 deepseek-chat
                            """

            if llm_provider == "modelscope":
                if not llm_model_name:
                    llm_model_name = "Qwen/Qwen3-32B"
                if not llm_base_url:
                    llm_base_url = "https://api-inference.modelscope.cn/v1/"
                with llm_helper:
                    tips = """
                            ##### ModelScope 配置说明
                            - **API Key**: [点击到官网申请](https://modelscope.cn/docs/model-service/API-Inference/intro)
                            - **Base Url**: 固定为 https://api-inference.modelscope.cn/v1/
                            - **Model Name**: 比如 Qwen/Qwen3-32B，[点击查看模型列表](https://modelscope.cn/models?filter=inference_type&page=1)
                            """

            if llm_provider == "ernie":
                with llm_helper:
                    tips = """
                            ##### 百度文心一言 配置说明
                            - **API Key**: [点击到官网申请](https://console.bce.baidu.com/qianfan/ais/console/applicationConsole/application)
                            - **Secret Key**: [点击到官网申请](https://console.bce.baidu.com/qianfan/ais/console/applicationConsole/application)
                            - **Base Url**: 填写 **请求地址** [点击查看文档](https://cloud.baidu.com/doc/WENXINWORKSHOP/s/jlil56u11#%E8%AF%B7%E6%B1%82%E8%AF%B4%E6%98%8E)
                            """

            if llm_provider == "pollinations":
                if not llm_model_name:
                    llm_model_name = "default"
                with llm_helper:
                    tips = """
                            ##### Pollinations AI Configuration
                            - **API Key**: Optional - Leave empty for public access
                            - **Base Url**: Default is https://text.pollinations.ai/openai
                            - **Model Name**: Use 'openai-fast' or specify a model name
                            """

            if tips and config.ui["language"] == "zh":
                st.warning(
                    "中国用户建议使用 **DeepSeek** 或 **Moonshot** 作为大模型提供商\n- 国内可直接访问，不需要VPN \n- 注册就送额度，基本够用"
                )
                st.info(tips)

            st_llm_api_key = st.text_input(
                tr("API Key"), value=llm_api_key, type="password"
            )
            st_llm_base_url = st.text_input(tr("Base Url"), value=llm_base_url)
            st_llm_model_name = ""
            if llm_provider != "ernie":
                st_llm_model_name = st.text_input(
                    tr("Model Name"),
                    value=llm_model_name,
                    key=f"{llm_provider}_model_name_input",
                )
                if st_llm_model_name:
                    config.app[f"{llm_provider}_model_name"] = st_llm_model_name
            else:
                st_llm_model_name = None

            if st_llm_api_key:
                config.app[f"{llm_provider}_api_key"] = st_llm_api_key
            if st_llm_base_url:
                config.app[f"{llm_provider}_base_url"] = st_llm_base_url
            if st_llm_model_name:
                config.app[f"{llm_provider}_model_name"] = st_llm_model_name
            if llm_provider == "ernie":
                st_llm_secret_key = st.text_input(
                    tr("Secret Key"), value=llm_secret_key, type="password"
                )
                config.app[f"{llm_provider}_secret_key"] = st_llm_secret_key

            if llm_provider == "cloudflare":
                st_llm_account_id = st.text_input(
                    tr("Account ID"), value=llm_account_id
                )
                if st_llm_account_id:
                    config.app[f"{llm_provider}_account_id"] = st_llm_account_id

        # 右侧面板 - API 密钥设置
        with right_config_panel:

            def get_keys_from_config(cfg_key):
                api_keys = config.app.get(cfg_key, [])
                if isinstance(api_keys, str):
                    api_keys = [api_keys]
                api_key = ", ".join(api_keys)
                return api_key

            def save_keys_to_config(cfg_key, value):
                value = value.replace(" ", "")
                if value:
                    config.app[cfg_key] = value.split(",")

            st.write(tr("Video Source Settings"))

            pexels_api_key = get_keys_from_config("pexels_api_keys")
            pexels_api_key = st.text_input(
                tr("Pexels API Key"), value=pexels_api_key, type="password"
            )
            save_keys_to_config("pexels_api_keys", pexels_api_key)

            pixabay_api_key = get_keys_from_config("pixabay_api_keys")
            pixabay_api_key = st.text_input(
                tr("Pixabay API Key"), value=pixabay_api_key, type="password"
            )
            save_keys_to_config("pixabay_api_keys", pixabay_api_key)

# ══════════════════════════════════════════════════════════════════
# ⚡ PERFORMANCE SETTINGS TAB
# ══════════════════════════════════════════════════════════════════
with st.expander("⚡ Performance Settings", expanded=False):
    st.markdown("### 🎬 FFmpeg Video Encoding")
    st.caption("Controls how video clips are encoded. GPU mode is much faster if you have an NVIDIA GPU.")

    perf_cols = st.columns(2)

    with perf_cols[0]:
        ffmpeg_modes = [
            ("🖥️ CPU  — libx264 (safe, compatible)", "cpu"),
            ("🚀 GPU  — NVENC (fastest, NVIDIA only)", "gpu"),
            ("🔀 Hybrid — GPU → CPU fallback (recommended)", "hybrid"),
        ]
        saved_ffmpeg_mode = config.app.get("ffmpeg_mode", "cpu")
        saved_ffmpeg_idx = next(
            (i for i, (_, v) in enumerate(ffmpeg_modes) if v == saved_ffmpeg_mode), 0
        )
        selected_ffmpeg_idx = st.selectbox(
            "FFmpeg Encoding Mode",
            options=range(len(ffmpeg_modes)),
            format_func=lambda x: ffmpeg_modes[x][0],
            index=saved_ffmpeg_idx,
            key="ffmpeg_mode_select",
            help="GPU (NVENC) encodes video using your NVIDIA GPU — much faster than CPU. Hybrid tries GPU first, falls back to CPU automatically.",
        )
        config.app["ffmpeg_mode"] = ffmpeg_modes[selected_ffmpeg_idx][1]

        if config.app["ffmpeg_mode"] in ("gpu", "hybrid"):
            st.success("✅ NVENC GPU encoding active — video render will be faster!")
        else:
            st.info("ℹ️ CPU encoding active (libx264). Safe for all systems.")

    with perf_cols[1]:
        saved_threads = config.app.get("n_threads", 4)
        n_threads = st.slider(
            "🔧 FFmpeg Threads (CPU encoding)",
            min_value=1,
            max_value=16,
            value=int(saved_threads),
            step=1,
            key="n_threads_slider",
            help="Number of CPU threads for FFmpeg. More threads = faster CPU encoding. Ignored in pure GPU mode.",
        )
        config.app["n_threads"] = n_threads

    st.divider()
    st.markdown("### 🎤 Whisper Subtitle Transcription")
    st.caption("Controls how Whisper generates subtitles when edge-TTS matching fails.")

    whisper_cols = st.columns(3)

    with whisper_cols[0]:
        whisper_devices = [
            ("🖥️ CPU (always works)", "cpu"),
            ("⚡ CUDA GPU (requires CUDA 12)", "cuda"),
        ]
        saved_whisper_device = config.whisper.get("device", "cpu")
        saved_whisper_device_idx = next(
            (i for i, (_, v) in enumerate(whisper_devices) if v == saved_whisper_device), 0
        )
        selected_whisper_device_idx = st.selectbox(
            "Whisper Device",
            options=range(len(whisper_devices)),
            format_func=lambda x: whisper_devices[x][0],
            index=saved_whisper_device_idx,
            key="whisper_device_select",
        )
        config.whisper["device"] = whisper_devices[selected_whisper_device_idx][1]

    with whisper_cols[1]:
        whisper_compute_types = [
            ("int8 — CPU / low VRAM", "int8"),
            ("float16 — GPU (recommended)", "float16"),
            ("float32 — max precision", "float32"),
        ]
        saved_compute = config.whisper.get("compute_type", "int8")
        saved_compute_idx = next(
            (i for i, (_, v) in enumerate(whisper_compute_types) if v == saved_compute), 0
        )
        selected_compute_idx = st.selectbox(
            "Compute Type",
            options=range(len(whisper_compute_types)),
            format_func=lambda x: whisper_compute_types[x][0],
            index=saved_compute_idx,
            key="whisper_compute_select",
            help="float16 is recommended for CUDA GPU. Use int8 for CPU.",
        )
        config.whisper["compute_type"] = whisper_compute_types[selected_compute_idx][1]

    with whisper_cols[2]:
        whisper_models = ["tiny", "base", "small", "medium", "large-v2", "large-v3"]
        saved_model = config.whisper.get("model_size", "large-v3")
        saved_model_idx = whisper_models.index(saved_model) if saved_model in whisper_models else 5
        selected_model = st.selectbox(
            "Whisper Model",
            options=whisper_models,
            index=saved_model_idx,
            key="whisper_model_select",
            help="Larger models are more accurate but slower.\nlarge-v3 = best quality\ntiny/base = fastest",
        )
        config.whisper["model_size"] = selected_model

    if config.whisper.get("device") == "cuda" and config.whisper.get("compute_type") == "float16":
        st.success("✅ GPU Whisper active — subtitle generation will be fast!")
    elif config.whisper.get("device") == "cuda":
        st.warning("⚠️ GPU Whisper with non-float16 — consider float16 for best GPU performance.")
    else:
        st.info("ℹ️ CPU Whisper active. Works everywhere but slower for large-v3.")

    if st.button("💾 Save Performance Settings", key="save_perf_settings"):
        config.save_config()
        st.success("Performance settings saved to config.toml!")


def render_subtitle_settings(params: VideoParams):
    params.subtitle_enabled = st.checkbox(tr("Enable Subtitles"), value=True)

    # Font selector with live preview (language-filtered)
    font_names = get_fonts_for_language(params.video_language)

    if params.video_language in SCRIPT_RESTRICTED_LANGS:
        lang_label = {"hi-IN": "Hindi", "pa-IN": "Punjabi", "zh-CN": "Chinese"}.get(
            params.video_language, params.video_language
        )
        if font_names:
            st.caption(
                f"Showing only **{lang_label}**-compatible fonts ({len(font_names)} available)"
            )
        else:
            st.warning(f"No fonts found for {lang_label}. Showing all fonts.")
            font_names = get_all_fonts()

    saved_font_name = config.ui.get("font_name", "NotoSansDevanagari-Bold.ttf")
    if saved_font_name not in font_names and font_names:
        saved_font_name = font_names[0]
    saved_font_name_index = (
        font_names.index(saved_font_name) if saved_font_name in font_names else 0
    )

    params.font_name = st.selectbox(tr("Font"), font_names, index=saved_font_name_index)
    config.ui["font_name"] = params.font_name

    preview_b64 = render_font_preview(params.font_name)
    st.markdown(
        f'<img src="{preview_b64}" style="width:100%;border-radius:6px;margin-bottom:6px;"/>',
        unsafe_allow_html=True,
    )

    subtitle_positions = [
        (tr("Top"), "top"),
        (tr("Center"), "center"),
        (tr("Bottom"), "bottom"),
        (tr("Custom"), "custom"),
    ]
    saved_subtitle_position = config.ui.get("subtitle_position", "bottom")
    saved_position_index = 2
    for i, (_, pos_value) in enumerate(subtitle_positions):
        if pos_value == saved_subtitle_position:
            saved_position_index = i
            break

    selected_index = st.selectbox(
        tr("Position"),
        index=saved_position_index,
        options=range(len(subtitle_positions)),
        format_func=lambda x: subtitle_positions[x][0],
    )
    params.subtitle_position = subtitle_positions[selected_index][1]
    config.ui["subtitle_position"] = params.subtitle_position

    if params.subtitle_position == "custom":
        saved_custom_position = config.ui.get("custom_position", 70.0)
        custom_position = st.text_input(
            tr("Custom Position (% from top)"),
            value=str(saved_custom_position),
            key="custom_position_input",
        )
        try:
            params.custom_position = float(custom_position)
            if params.custom_position < 0 or params.custom_position > 100:
                st.error(tr("Please enter a value between 0 and 100"))
            else:
                config.ui["custom_position"] = params.custom_position
        except ValueError:
            st.error(tr("Please enter a valid number"))

    font_cols = st.columns([0.3, 0.7])
    with font_cols[0]:
        saved_text_fore_color = config.ui.get("text_fore_color", "#FFFFFF")
        params.text_fore_color = st.color_picker(tr("Font Color"), saved_text_fore_color)
        config.ui["text_fore_color"] = params.text_fore_color

    with font_cols[1]:
        saved_font_size = config.ui.get("font_size", 60)
        params.font_size = st.slider(tr("Font Size"), 30, 100, saved_font_size)
        config.ui["font_size"] = params.font_size

    stroke_cols = st.columns([0.3, 0.7])
    with stroke_cols[0]:
        params.stroke_color = st.color_picker(tr("Stroke Color"), "#000000")
    with stroke_cols[1]:
        params.stroke_width = st.slider(tr("Stroke Width"), 0.0, 10.0, 1.5)

    st.markdown("**Subtitle Background**")
    bg_cols = st.columns([0.4, 0.6])
    with bg_cols[0]:
        saved_bg_color = config.ui.get("subtitle_bg_color", "#000000")
        subtitle_bg_color = st.color_picker(
            "Background Color", saved_bg_color, key="subtitle_bg_color_picker"
        )
        config.ui["subtitle_bg_color"] = subtitle_bg_color
    with bg_cols[1]:
        saved_bg_opacity = config.ui.get("subtitle_bg_opacity", 0)
        subtitle_bg_opacity = st.slider(
            "Opacity (0=transparent)",
            0,
            255,
            int(saved_bg_opacity),
            key="subtitle_bg_opacity_slider",
        )
        config.ui["subtitle_bg_opacity"] = subtitle_bg_opacity

    def hex_to_rgba(hex_color, alpha):
        hex_color = hex_color.lstrip("#")
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        return (r, g, b, alpha)

    params.subtitle_bg_color = hex_to_rgba(subtitle_bg_color, subtitle_bg_opacity)

llm_provider = config.app.get("llm_provider", "").lower()

panel = st.columns(3)
left_panel = panel[0]
middle_panel = panel[1]
right_panel = panel[2]

params = VideoParams(video_subject="")
uploaded_files = []

active_task_meta = _load_active_task_meta()
meta_task_id = str(active_task_meta.get("task_id", "")).strip()

if "active_task_id" not in st.session_state:
    st.session_state["active_task_id"] = meta_task_id
elif not str(st.session_state.get("active_task_id", "")).strip() and meta_task_id:
    # If current session lost task pointer, reattach from persisted metadata.
    st.session_state["active_task_id"] = meta_task_id

with left_panel:
    with st.container(border=True):
        st.write(tr("Video Script Settings"))
        params.video_subject = st.text_input(
            tr("Video Subject"),
            value=st.session_state["video_subject"],
            key="video_subject_input",
        ).strip()

        video_languages = [
            (tr("Auto Detect"), ""),
        ]
        for code in support_locales:
            video_languages.append((code, code))

        selected_index = st.selectbox(
            tr("Script Language"),
            index=0,
            options=range(
                len(video_languages)
            ),  # Use the index as the internal option value
            format_func=lambda x: video_languages[x][
                0
            ],  # The label is displayed to the user
        )
        params.video_language = video_languages[selected_index][1]

        if st.button(
            tr("Generate Video Script and Keywords"), key="auto_generate_script"
        ):
            with st.spinner(tr("Generating Video Script and Keywords")):
                script = llm.generate_script(
                    video_subject=params.video_subject, language=params.video_language
                )
                terms = llm.generate_terms(params.video_subject, script)
                if "Error: " in script:
                    st.error(tr(script))
                elif "Error: " in terms:
                    st.error(tr(terms))
                else:
                    st.session_state["video_script"] = script
                    st.session_state["video_terms"] = ", ".join(terms)
        params.video_script = st.text_area(
            tr("Video Script"), value=st.session_state["video_script"], height=280
        )
        if st.button(tr("Generate Video Keywords"), key="auto_generate_terms"):
            if not params.video_script:
                st.error(tr("Please Enter the Video Subject"))
                st.stop()

            with st.spinner(tr("Generating Video Keywords")):
                terms = llm.generate_terms(params.video_subject, params.video_script)
                if "Error: " in terms:
                    st.error(tr(terms))
                else:
                    st.session_state["video_terms"] = ", ".join(terms)

        params.video_terms = st.text_area(
            tr("Video Keywords"), value=st.session_state["video_terms"]
        )

    with st.expander(tr("Subtitle Settings"), expanded=False):
        with st.container(border=True):
            render_subtitle_settings(params)

with middle_panel:
    with st.container(border=True):
        st.write(tr("Video Settings"))
        video_concat_modes = [
            (tr("Sequential"), "sequential"),
            (tr("Random"), "random"),
        ]
        video_sources = [
            (tr("Pexels"), "pexels"),
            (tr("Pixabay"), "pixabay"),
            (tr("Local file"), "local"),
            (tr("TikTok"), "douyin"),
            (tr("Bilibili"), "bilibili"),
            (tr("Xiaohongshu"), "xiaohongshu"),
        ]

        saved_video_source_name = config.app.get("video_source", "pexels")
        saved_video_source_index = [v[1] for v in video_sources].index(
            saved_video_source_name
        )

        selected_index = st.selectbox(
            tr("Video Source"),
            options=range(len(video_sources)),
            format_func=lambda x: video_sources[x][0],
            index=saved_video_source_index,
        )
        params.video_source = video_sources[selected_index][1]
        config.app["video_source"] = params.video_source

        if params.video_source == "local":
            # Streamlit 的文件类型校验对扩展名大小写敏感，这里同时放行大小写两种形式。
            local_file_types = ["mp4", "mov", "avi", "flv", "mkv", "jpg", "jpeg", "png"]
            uploaded_files = st.file_uploader(
                "Upload Local Files",
                type=local_file_types + [file_type.upper() for file_type in local_file_types],
                accept_multiple_files=True,
            )

        selected_index = st.selectbox(
            tr("Video Concat Mode"),
            index=1,
            options=range(
                len(video_concat_modes)
            ),  # Use the index as the internal option value
            format_func=lambda x: video_concat_modes[x][
                0
            ],  # The label is displayed to the user
        )
        params.video_concat_mode = VideoConcatMode(
            video_concat_modes[selected_index][1]
        )

        # 视频转场模式
        video_transition_modes = [
            (tr("None"), VideoTransitionMode.none.value),
            (tr("Shuffle"), VideoTransitionMode.shuffle.value),
            (tr("FadeIn"), VideoTransitionMode.fade_in.value),
            (tr("FadeOut"), VideoTransitionMode.fade_out.value),
            (tr("SlideIn"), VideoTransitionMode.slide_in.value),
            (tr("SlideOut"), VideoTransitionMode.slide_out.value),
        ]
        selected_index = st.selectbox(
            tr("Video Transition Mode"),
            options=range(len(video_transition_modes)),
            format_func=lambda x: video_transition_modes[x][0],
            index=0,
        )
        params.video_transition_mode = VideoTransitionMode(
            video_transition_modes[selected_index][1]
        )

        video_aspect_ratios = [
            (tr("Portrait"), VideoAspect.portrait.value),
            (tr("Landscape"), VideoAspect.landscape.value),
        ]
        selected_index = st.selectbox(
            tr("Video Ratio"),
            options=range(
                len(video_aspect_ratios)
            ),  # Use the index as the internal option value
            format_func=lambda x: video_aspect_ratios[x][
                0
            ],  # The label is displayed to the user
        )
        params.video_aspect = VideoAspect(video_aspect_ratios[selected_index][1])

        params.video_clip_duration = st.selectbox(
            tr("Clip Duration"), options=[2, 3, 4, 5, 6, 7, 8, 9, 10], index=1
        )
        params.video_count = st.selectbox(
            tr("Number of Videos Generated Simultaneously"),
            options=[1, 2, 3, 4, 5],
            index=0,
        )
    with st.container(border=True):
        st.write(tr("Audio Settings"))

        # 添加TTS服务器选择下拉框
        tts_servers = [
            ("azure-tts-v1", "Azure TTS V1"),
            ("azure-tts-v2", "Azure TTS V2"),
            ("siliconflow", "SiliconFlow TTS"),
            ("gemini-tts", "Google Gemini TTS"),
        ]

        # 获取保存的TTS服务器，默认为v1
        saved_tts_server = config.ui.get("tts_server", "azure-tts-v1")
        saved_tts_server_index = 0
        for i, (server_value, _) in enumerate(tts_servers):
            if server_value == saved_tts_server:
                saved_tts_server_index = i
                break

        selected_tts_server_index = st.selectbox(
            tr("TTS Servers"),
            options=range(len(tts_servers)),
            format_func=lambda x: tts_servers[x][1],
            index=saved_tts_server_index,
        )

        selected_tts_server = tts_servers[selected_tts_server_index][0]
        config.ui["tts_server"] = selected_tts_server

        # 根据选择的TTS服务器获取声音列表
        filtered_voices = []

        if selected_tts_server == "siliconflow":
            # 获取硅基流动的声音列表
            filtered_voices = voice.get_siliconflow_voices()
        elif selected_tts_server == "gemini-tts":
            # 获取Gemini TTS的声音列表
            filtered_voices = voice.get_gemini_voices()
        else:
            # 获取Azure的声音列表
            all_voices = voice.get_all_azure_voices(filter_locals=None)

            # 根据选择的TTS服务器筛选声音
            for v in all_voices:
                if selected_tts_server == "azure-tts-v2":
                    # V2版本的声音名称中包含"v2"
                    if "V2" in v:
                        filtered_voices.append(v)
                else:
                    # V1版本的声音名称中不包含"v2"
                    if "V2" not in v:
                        filtered_voices.append(v)

        friendly_names = {
            v: v.replace("Female", tr("Female"))
            .replace("Male", tr("Male"))
            .replace("Neural", "")
            for v in filtered_voices
        }

        saved_voice_name = config.ui.get("voice_name", "")
        saved_voice_name_index = 0

        # 检查保存的声音是否在当前筛选的声音列表中
        if saved_voice_name in friendly_names:
            saved_voice_name_index = list(friendly_names.keys()).index(saved_voice_name)
        else:
            # 如果不在，则根据当前UI语言选择一个默认声音
            for i, v in enumerate(filtered_voices):
                if v.lower().startswith(st.session_state["ui_language"].lower()):
                    saved_voice_name_index = i
                    break

        # 如果没有找到匹配的声音，使用第一个声音
        if saved_voice_name_index >= len(friendly_names) and friendly_names:
            saved_voice_name_index = 0

        # 确保有声音可选
        if friendly_names:
            selected_friendly_name = st.selectbox(
                tr("Speech Synthesis"),
                options=list(friendly_names.values()),
                index=min(saved_voice_name_index, len(friendly_names) - 1)
                if friendly_names
                else 0,
            )

            voice_name = list(friendly_names.keys())[
                list(friendly_names.values()).index(selected_friendly_name)
            ]
            params.voice_name = voice_name
            config.ui["voice_name"] = voice_name
        else:
            # 如果没有声音可选，显示提示信息
            st.warning(
                tr(
                    "No voices available for the selected TTS server. Please select another server."
                )
            )
            params.voice_name = ""
            config.ui["voice_name"] = ""

        # 只有在有声音可选时才显示试听按钮
        if friendly_names and st.button(tr("Play Voice")):
            play_content = params.video_subject
            if not play_content:
                play_content = params.video_script
            if not play_content:
                play_content = tr("Voice Example")
            with st.spinner(tr("Synthesizing Voice")):
                temp_dir = utils.storage_dir("temp", create=True)
                audio_file = os.path.join(temp_dir, f"tmp-voice-{str(uuid4())}.mp3")
                sub_maker = voice.tts(
                    text=play_content,
                    voice_name=voice_name,
                    voice_rate=params.voice_rate,
                    voice_file=audio_file,
                    voice_volume=params.voice_volume,
                )
                # if the voice file generation failed, try again with a default content.
                if not sub_maker:
                    play_content = "This is a example voice. if you hear this, the voice synthesis failed with the original content."
                    sub_maker = voice.tts(
                        text=play_content,
                        voice_name=voice_name,
                        voice_rate=params.voice_rate,
                        voice_file=audio_file,
                        voice_volume=params.voice_volume,
                    )

                if sub_maker and os.path.exists(audio_file):
                    st.audio(audio_file, format="audio/mp3")
                    if os.path.exists(audio_file):
                        os.remove(audio_file)

        # 当选择V2版本或者声音是V2声音时，显示服务区域和API key输入框
        if selected_tts_server == "azure-tts-v2" or (
            voice_name and voice.is_azure_v2_voice(voice_name)
        ):
            saved_azure_speech_region = config.azure.get("speech_region", "")
            saved_azure_speech_key = config.azure.get("speech_key", "")
            azure_speech_region = st.text_input(
                tr("Speech Region"),
                value=saved_azure_speech_region,
                key="azure_speech_region_input",
            )
            azure_speech_key = st.text_input(
                tr("Speech Key"),
                value=saved_azure_speech_key,
                type="password",
                key="azure_speech_key_input",
            )
            config.azure["speech_region"] = azure_speech_region
            config.azure["speech_key"] = azure_speech_key

        # 当选择硅基流动时，显示API key输入框和说明信息
        if selected_tts_server == "siliconflow" or (
            voice_name and voice.is_siliconflow_voice(voice_name)
        ):
            saved_siliconflow_api_key = config.siliconflow.get("api_key", "")

            siliconflow_api_key = st.text_input(
                tr("SiliconFlow API Key"),
                value=saved_siliconflow_api_key,
                type="password",
                key="siliconflow_api_key_input",
            )

            # 显示硅基流动的说明信息
            st.info(
                tr("SiliconFlow TTS Settings")
                + ":\n"
                + "- "
                + tr("Speed: Range [0.25, 4.0], default is 1.0")
                + "\n"
                + "- "
                + tr("Volume: Uses Speech Volume setting, default 1.0 maps to gain 0")
            )

            config.siliconflow["api_key"] = siliconflow_api_key

        params.voice_volume = st.selectbox(
            tr("Speech Volume"),
            options=[0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0, 4.0, 5.0],
            index=2,
        )

        params.voice_rate = st.selectbox(
            tr("Speech Rate"),
            options=[0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5, 1.8, 2.0],
            index=2,
        )

        # Voice naturalness / expressiveness
        saved_naturalness = config.ui.get("voice_naturalness", 1.0)
        params.voice_naturalness = st.slider(
            "🎭 Voice Naturalness (0=flat, 1=normal, 2=expressive)",
            min_value=0.0,
            max_value=2.0,
            value=float(saved_naturalness),
            step=0.1,
            key="voice_naturalness_slider",
            help="Adjusts how expressive and natural the speech sounds. Higher values add more emotion and variation.",
        )
        config.ui["voice_naturalness"] = params.voice_naturalness

        bgm_options = [
            (tr("No Background Music"), ""),
            (tr("Random Background Music"), "random"),
            (tr("Custom Background Music"), "custom"),
        ]
        selected_index = st.selectbox(
            tr("Background Music"),
            index=1,
            options=range(
                len(bgm_options)
            ),  # Use the index as the internal option value
            format_func=lambda x: bgm_options[x][
                0
            ],  # The label is displayed to the user
        )
        # Get the selected background music type
        params.bgm_type = bgm_options[selected_index][1]

        # Show or hide components based on the selection
        if params.bgm_type == "custom":
            custom_bgm_file = st.text_input(
                tr("Custom Background Music File"), key="custom_bgm_file_input"
            )
            if custom_bgm_file and os.path.exists(custom_bgm_file):
                params.bgm_file = custom_bgm_file
                # st.write(f":red[已选择自定义背景音乐]：**{custom_bgm_file}**")
        params.bgm_volume = st.selectbox(
            tr("Background Music Volume"),
            options=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
            index=2,
        )

with right_panel:
    with st.container(border=True):
        st.write("Runtime Monitor")
        stop_task_button = st.button(
            "Stop Generation",
            key="stop_generation_button",
            type="secondary",
            use_container_width=True,
        )
        monitor_title = st.empty()
        status_container = st.empty()
        progress_container = st.empty()
        log_container = st.empty()
        runtime_log_box_id = "runtime-log-box"

        monitor_title.markdown("### Runtime Monitor")
        status_container.info("Status: Idle")
        progress_container.progress(0)

        if not config.ui.get("hide_log", False):
            _render_log_box(log_container, [], runtime_log_box_id)

    with st.expander(tr("Click to show API Key management"), expanded=False):
        st.subheader(tr("Manage Pexels and Pixabay API Keys"))

        col1, col2 = st.tabs(["Pexels API Keys", "Pixabay API Keys"])

        with col1:
            st.subheader("Pexels API Keys")
            if config.app["pexels_api_keys"]:
                st.write(tr("Current Keys:"))
                for key in config.app["pexels_api_keys"]:
                    st.code(key)
            else:
                st.info(tr("No Pexels API Keys currently"))

            new_key = st.text_input(tr("Add Pexels API Key"), key="pexels_new_key")
            if st.button(tr("Add Pexels API Key")):
                if new_key and new_key not in config.app["pexels_api_keys"]:
                    config.app["pexels_api_keys"].append(new_key)
                    config.save_config()
                    st.success(tr("Pexels API Key added successfully"))
                elif new_key in config.app["pexels_api_keys"]:
                    st.warning(tr("This API Key already exists"))
                else:
                    st.error(tr("Please enter a valid API Key"))

            if config.app["pexels_api_keys"]:
                delete_key = st.selectbox(
                    tr("Select Pexels API Key to delete"), config.app["pexels_api_keys"], key="pexels_delete_key"
                )
                if st.button(tr("Delete Selected Pexels API Key")):
                    config.app["pexels_api_keys"].remove(delete_key)
                    config.save_config()
                    st.success(tr("Pexels API Key deleted successfully"))

        with col2:
            st.subheader("Pixabay API Keys")

            if config.app["pixabay_api_keys"]:
                st.write(tr("Current Keys:"))
                for key in config.app["pixabay_api_keys"]:
                    st.code(key)
            else:
                st.info(tr("No Pixabay API Keys currently"))

            new_key = st.text_input(tr("Add Pixabay API Key"), key="pixabay_new_key")
            if st.button(tr("Add Pixabay API Key")):
                if new_key and new_key not in config.app["pixabay_api_keys"]:
                    config.app["pixabay_api_keys"].append(new_key)
                    config.save_config()
                    st.success(tr("Pixabay API Key added successfully"))
                elif new_key in config.app["pixabay_api_keys"]:
                    st.warning(tr("This API Key already exists"))
                else:
                    st.error(tr("Please enter a valid API Key"))

            if config.app["pixabay_api_keys"]:
                delete_key = st.selectbox(
                    tr("Select Pixabay API Key to delete"), config.app["pixabay_api_keys"], key="pixabay_delete_key"
                )
                if st.button(tr("Delete Selected Pixabay API Key")):
                    config.app["pixabay_api_keys"].remove(delete_key)
                    config.save_config()
                    st.success(tr("Pixabay API Key deleted successfully"))

with st.expander("Social Upload Automation", expanded=False):
    st.caption(
        "Automatically publish generated videos to YouTube and other platforms after each task finishes."
    )

    st.markdown("#### Upload-Post (TikTok / Instagram)")
    upload_post_enabled = st.checkbox(
        "Enable Upload-Post",
        value=bool(config.app.get("upload_post_enabled", False)),
        key="upload_post_enabled_checkbox",
    )
    upload_post_auto_upload = st.checkbox(
        "Auto upload via Upload-Post after generation",
        value=bool(config.app.get("upload_post_auto_upload", False)),
        key="upload_post_auto_upload_checkbox",
    )

    config.app["upload_post_enabled"] = upload_post_enabled
    config.app["upload_post_auto_upload"] = upload_post_auto_upload

    upload_post_username = st.text_input(
        "Upload-Post Username",
        value=str(config.app.get("upload_post_username", "")),
        key="upload_post_username_input",
    ).strip()
    upload_post_api_key = st.text_input(
        "Upload-Post API Key",
        value=str(config.app.get("upload_post_api_key", "")),
        type="password",
        key="upload_post_api_key_input",
    ).strip()

    config.app["upload_post_username"] = upload_post_username
    config.app["upload_post_api_key"] = upload_post_api_key

    upload_post_platform_options = ["tiktok", "instagram"]
    saved_upload_post_platforms = config.app.get("upload_post_platforms", upload_post_platform_options)
    if isinstance(saved_upload_post_platforms, str):
        saved_upload_post_platforms = [x.strip() for x in saved_upload_post_platforms.split(",") if x.strip()]
    if not isinstance(saved_upload_post_platforms, list):
        saved_upload_post_platforms = upload_post_platform_options

    upload_post_platforms = st.multiselect(
        "Upload-Post Platforms",
        options=upload_post_platform_options,
        default=[p for p in saved_upload_post_platforms if p in upload_post_platform_options],
        key="upload_post_platforms_select",
    )
    config.app["upload_post_platforms"] = upload_post_platforms or upload_post_platform_options

    st.divider()
    st.markdown("#### YouTube")

    youtube_upload_enabled = st.checkbox(
        "Enable YouTube Upload",
        value=bool(config.app.get("youtube_upload_enabled", False)),
        key="youtube_upload_enabled_checkbox",
    )
    youtube_auto_upload = st.checkbox(
        "Auto upload to YouTube after generation",
        value=bool(config.app.get("youtube_auto_upload", False)),
        key="youtube_auto_upload_checkbox",
    )

    config.app["youtube_upload_enabled"] = youtube_upload_enabled
    config.app["youtube_auto_upload"] = youtube_auto_upload

    publish_mode_options = ["auto", "shorts", "video"]
    saved_publish_mode = str(config.app.get("youtube_publish_mode", "auto")).strip().lower()
    if saved_publish_mode not in publish_mode_options:
        saved_publish_mode = "auto"
    youtube_publish_mode = st.selectbox(
        "YouTube Publish Mode",
        options=publish_mode_options,
        index=publish_mode_options.index(saved_publish_mode),
        key="youtube_publish_mode_select",
        help="auto: detect Shorts by video shape/duration, shorts: force #Shorts metadata, video: regular upload.",
    )
    config.app["youtube_publish_mode"] = youtube_publish_mode

    privacy_options = ["private", "unlisted", "public"]
    saved_privacy = str(config.app.get("youtube_privacy_status", "private")).strip().lower()
    if saved_privacy not in privacy_options:
        saved_privacy = "private"
    youtube_privacy_status = st.selectbox(
        "YouTube Privacy",
        options=privacy_options,
        index=privacy_options.index(saved_privacy),
        key="youtube_privacy_status_select",
    )
    config.app["youtube_privacy_status"] = youtube_privacy_status

    youtube_category_id = st.text_input(
        "YouTube Category ID",
        value=str(config.app.get("youtube_category_id", "22")),
        key="youtube_category_id_input",
        help="22 = People & Blogs, 24 = Entertainment, 28 = Science & Technology, etc.",
    ).strip()
    config.app["youtube_category_id"] = youtube_category_id or "22"

    st.caption("Provide either a Client Secret File OR Google OAuth Client ID + Client Secret.")

    youtube_client_secrets_file = st.text_input(
        "Google OAuth Client Secret File (optional)",
        value=str(config.app.get("youtube_client_secrets_file", "")),
        key="youtube_client_secrets_file_input",
        help="Optional path to client_secret.json downloaded from Google Cloud OAuth credentials.",
    ).strip()

    youtube_client_id = st.text_input(
        "Google OAuth Client ID (optional)",
        value=str(config.app.get("youtube_client_id", "")),
        key="youtube_client_id_input",
        help="If you do not want to use a file, paste your OAuth client ID here.",
    ).strip()

    youtube_client_secret = st.text_input(
        "Google OAuth Client Secret (optional)",
        value=str(config.app.get("youtube_client_secret", "")),
        type="password",
        key="youtube_client_secret_input",
        help="If you do not want to use a file, paste your OAuth client secret here.",
    ).strip()

    youtube_token_file = st.text_input(
        "YouTube OAuth Token File",
        value=str(config.app.get("youtube_token_file", "storage/oauth/youtube_token.json")),
        key="youtube_token_file_input",
    ).strip()

    config.app["youtube_client_secrets_file"] = youtube_client_secrets_file
    config.app["youtube_client_id"] = youtube_client_id
    config.app["youtube_client_secret"] = youtube_client_secret
    config.app["youtube_token_file"] = youtube_token_file or "storage/oauth/youtube_token.json"

    saved_youtube_tags = config.app.get("youtube_tags", ["0CodeAutoGen", "AIVideo"])
    if isinstance(saved_youtube_tags, list):
        saved_youtube_tags_text = ", ".join([str(x).strip() for x in saved_youtube_tags if str(x).strip()])
    else:
        saved_youtube_tags_text = str(saved_youtube_tags)

    youtube_tags_text = st.text_input(
        "YouTube Tags (comma-separated)",
        value=saved_youtube_tags_text,
        key="youtube_tags_input",
    )
    config.app["youtube_tags"] = [x.strip() for x in youtube_tags_text.split(",") if x.strip()]

    youtube_description = st.text_area(
        "YouTube Description (optional)",
        value=str(config.app.get("youtube_default_description", "")),
        height=100,
        key="youtube_description_input",
        help="If empty, generated script text will be used as the upload description.",
    )
    config.app["youtube_default_description"] = youtube_description

    params.youtube_auto_upload = youtube_auto_upload
    params.youtube_publish_mode = youtube_publish_mode
    params.youtube_description = youtube_description.strip()

    auth_col1, auth_col2 = st.columns(2)
    with auth_col1:
        if st.button("Authorize YouTube Account", key="youtube_authorize_button", use_container_width=True):
            with st.spinner("Opening Google login authorization flow..."):
                yt_module = importlib.reload(youtube_upload)
                auth_result = yt_module.authorize_youtube_account(interactive=True)
            if auth_result.get("success"):
                st.success(auth_result.get("message", "YouTube account authorized."))
            else:
                st.error(auth_result.get("message", "YouTube authorization failed."))
    with auth_col2:
        if st.button("Check YouTube Authorization", key="youtube_check_auth_button", use_container_width=True):
            yt_module = importlib.reload(youtube_upload)
            status = yt_module.youtube_upload_service.auth_status()
            if status.get("success") and status.get("authorized"):
                st.success(status.get("message", "YouTube authorization is ready."))
            else:
                st.warning(status.get("message", "YouTube authorization is not ready."))

start_button = st.button(tr("Generate Video"), use_container_width=True, type="primary")

if stop_task_button:
    active_to_stop = str(st.session_state.get("active_task_id", "")).strip()
    if not active_to_stop:
        active_to_stop = str(_load_active_task_meta().get("task_id", "")).strip()

    if active_to_stop:
        task_snapshot = sm.state.get_task(active_to_stop) or {}
        current_state = task_snapshot.get("state", const.TASK_STATE_PROCESSING)
        current_progress = task_snapshot.get("progress", 0)
        try:
            current_progress = int(current_progress)
        except Exception:
            current_progress = 0

        sm.state.update_task(
            active_to_stop,
            state=current_state,
            progress=current_progress,
            stop_requested=True,
            error="Stop requested by user.",
        )
        st.warning("Stop requested. Current stage will finish safely, then task will terminate.")
    else:
        st.info("No active task to stop.")

active_task_before_start = str(st.session_state.get("active_task_id", "")).strip()
if not active_task_before_start:
    active_task_before_start = str(_load_active_task_meta().get("task_id", "")).strip()

if start_button and active_task_before_start:
    existing_task = sm.state.get_task(active_task_before_start) or {}
    existing_state = existing_task.get("state", const.TASK_STATE_PROCESSING)
    stop_requested = bool(existing_task.get("stop_requested", False))
    if existing_state == const.TASK_STATE_PROCESSING and not stop_requested:
        st.warning("A generation task is already running. Stop it first or wait for completion.")
        start_button = False

if start_button:
    config.save_config()
    if not params.video_subject and not params.video_script:
        st.error(tr("Video Script and Subject Cannot Both Be Empty"))
        scroll_to_bottom()
        st.stop()

    if params.video_source not in ["pexels", "pixabay", "local"]:
        st.error(tr("Please Select a Valid Video Source"))
        scroll_to_bottom()
        st.stop()

    if params.video_source == "pexels" and not config.app.get("pexels_api_keys", ""):
        st.error(tr("Please Enter the Pexels API Key"))
        scroll_to_bottom()
        st.stop()

    if params.video_source == "pixabay" and not config.app.get("pixabay_api_keys", ""):
        st.error(tr("Please Enter the Pixabay API Key"))
        scroll_to_bottom()
        st.stop()

    task_id = _build_task_folder_name(
        video_script=params.video_script,
        video_subject=params.video_subject,
    )

    if uploaded_files:
        local_videos_dir = utils.storage_dir("local_videos", create=True)
        # 每次重新上传时都以本次选择的素材为准，避免旧素材不断重复追加。
        params.video_materials = []
        persisted_local_materials = []
        for file in uploaded_files:
            file_path = os.path.join(local_videos_dir, f"{file.file_id}_{file.name}")
            with open(file_path, "wb") as f:
                f.write(file.getbuffer())
                m = MaterialInfo()
                m.provider = "local"
                m.url = file_path
                params.video_materials.append(m)
                persisted_local_materials.append(
                    {
                        "provider": m.provider,
                        "url": m.url,
                        "duration": m.duration,
                    }
                )
        # 将已上传并保存到本地的视频素材写入会话，供后续只改文案时直接复用。
        st.session_state["local_video_materials"] = persisted_local_materials
    elif params.video_source == "local" and st.session_state["local_video_materials"]:
        # 当用户没有重新上传文件时，复用最近一次已经保存到磁盘的本地素材列表。
        params.video_materials = []
        for material in st.session_state["local_video_materials"]:
            m = MaterialInfo()
            m.provider = material.get("provider", "local")
            m.url = material.get("url", "")
            m.duration = material.get("duration", 0)
            if m.url:
                params.video_materials.append(m)

    st.toast(tr("Generating Video"))
    logger.info(tr("Start Generating Video"))
    logger.info(utils.to_json(params))

    st.session_state["active_task_id"] = task_id
    _save_active_task_meta(task_id, params)
    _start_task_in_background(task_id, params)

    scroll_to_bottom()

active_task_id = str(st.session_state.get("active_task_id", "")).strip()
if active_task_id:
    monitor_title.markdown("### Runtime Monitor")

    task_info = sm.state.get_task(active_task_id) or {}
    if not task_info and not _is_task_thread_alive(active_task_id):
        active_task_meta = _load_active_task_meta()
        if active_task_meta.get("task_id") == active_task_id:
            resume_params_dict = active_task_meta.get("params") or {}
            try:
                resume_params = VideoParams(**resume_params_dict)
                status_container.info("Status: Resuming previous task")
                _start_task_in_background(active_task_id, resume_params)
            except Exception as e:
                status_container.error(f"Status: Failed to resume task ({e})")
                _clear_active_task_meta(active_task_id)
                st.session_state["active_task_id"] = ""
        else:
            meta_task_id = str(active_task_meta.get("task_id", "")).strip()
            if meta_task_id:
                st.session_state["active_task_id"] = meta_task_id
                active_task_id = meta_task_id
                status_container.info("Status: Re-attached to active task")
            else:
                st.session_state["active_task_id"] = ""
                status_container.info("Status: Idle")
                progress_container.progress(0)
                if not config.ui.get("hide_log", False):
                    _render_log_box(log_container, [], runtime_log_box_id)

    if st.session_state.get("active_task_id"):
        task_info = _update_runtime_monitor(
            task_id=active_task_id,
            status_container=status_container,
            progress_container=progress_container,
            log_container=log_container,
            log_box_id=runtime_log_box_id,
        )
    else:
        task_info = {}

    while True:
        state = task_info.get("state")
        if state in (const.TASK_STATE_COMPLETE, const.TASK_STATE_FAILED):
            break
        if not _is_task_thread_alive(active_task_id):
            # If the process restarted and state was in-memory only, avoid infinite wait.
            break

        time.sleep(1)
        task_info = _update_runtime_monitor(
            task_id=active_task_id,
            status_container=status_container,
            progress_container=progress_container,
            log_container=log_container,
            log_box_id=runtime_log_box_id,
        )

    state = task_info.get("state")
    if state == const.TASK_STATE_COMPLETE:
        video_files = task_info.get("videos", []) or []
        st.success(tr("Video Generation Completed"))
        try:
            if video_files:
                player_cols = st.columns(len(video_files) * 2 + 1)
                for i, url in enumerate(video_files):
                    player_cols[i * 2 + 1].video(url)
        except Exception:
            pass

        if st.session_state.get("opened_task_folder_id") != active_task_id:
            open_task_folder(active_task_id)
            st.session_state["opened_task_folder_id"] = active_task_id

    elif state == const.TASK_STATE_FAILED:
        error_detail = str(task_info.get("error", "")).strip()
        if error_detail:
            st.error(f"{tr('Video Generation Failed')}: {error_detail}")
            logger.error(f"{tr('Video Generation Failed')}: {error_detail}")
        else:
            st.error(tr("Video Generation Failed"))
            logger.error(tr("Video Generation Failed"))

config.save_config()


