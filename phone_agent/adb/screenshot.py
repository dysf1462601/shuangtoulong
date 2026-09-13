"""Screenshot utilities for capturing Android device screen."""

import base64
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import Tuple

from PIL import Image


# ==================== ltlive 上传配置 ====================
LTLIVE_UPLOAD_URL = ""
LTLIVE_DEVICE_IMEI = ""
LTLIVE_PUSH_NOW = False  # 不勾选"立即推送"
# =========================================================

def _compress_image_to_limit(image_data: bytes, max_size_bytes: int = 100 * 1024) -> Tuple[bytes, str, str]:
    """
    压缩图片数据，确保大小不超过 max_size_bytes (默认 100KB)。
    
    Returns:
        Tuple containing:
        - compressed image bytes
        - mime type (e.g., "image/jpeg")
        - file extension (e.g., ".jpg")
    """
    # 如果原图已经小于限制，直接返回原图
    if len(image_data) <= max_size_bytes:
        return image_data, "image/png", ".png"

    img = Image.open(BytesIO(image_data))
    # JPEG 不支持透明通道，如果是 RGBA 或 P 模式则转换为 RGB
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # 兼容不同版本的 Pillow 获取高质量重采样滤镜
    try:
        resample_filter = Image.Resampling.LANCZOS
    except AttributeError:
        resample_filter = Image.ANTIALIAS

    # 策略 1：尝试通过降低 JPEG 质量来压缩 (从 85 降到 10)
    for quality in range(85, 5, -5):
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=quality)
        if buffer.tell() <= max_size_bytes:
            return buffer.getvalue(), "image/jpeg", ".jpg"

    # 策略 2：如果质量降到最低仍超限，则等比缩小图片尺寸
    scale = 0.8
    while scale > 0.1:
        new_size = (int(img.width * scale), int(img.height * scale))
        img_resized = img.resize(new_size, resample_filter)
        
        buffer = BytesIO()
        img_resized.save(buffer, format="JPEG", quality=20)
        if buffer.tell() <= max_size_bytes:
            return buffer.getvalue(), "image/jpeg", ".jpg"
            
        scale -= 0.1
        img = img_resized

    # 兜底：返回最小尺寸和最低质量的压缩结果
    return buffer.getvalue(), "image/jpeg", ".jpg"


def _upload_screenshot_to_ltlive(image_data: bytes, filename: str = "screenshot.png") -> None:
    """
    将截图上传到 ltlive 服务器。上传前会自动压缩图片至 100KB 以内。

    Args:
        image_data: PNG 图片的原始字节数据。
        filename: 上传时的文件名。
    """
    try:
        import requests

        # ========== 新增：压缩图片到 100KB 以内 ==========
        max_size = 100 * 1024  # 100KB
        if len(image_data) > max_size:
            image_data, mime_type, ext = _compress_image_to_limit(image_data, max_size)
            # 将文件名后缀更新为 .jpg
            filename = os.path.splitext(filename)[0] + ext
        else:
            mime_type = "image/png"
        # ==============================================

        files = {
            "file": (filename, BytesIO(image_data), mime_type),
        }
        data = {
            "imei": LTLIVE_DEVICE_IMEI,
        }
        # 不勾选"立即推送"，所以不传 push_now 字段
        # 如果需要推送，取消下面这行的注释：
        # data["push_now"] = "true"

        resp = requests.post(
            LTLIVE_UPLOAD_URL,
            files=files,
            data=data,
            timeout=15,
        )
        if resp.status_code == 200:
            print(f"  📤 截图已上传到 ltlive (IMEI={LTLIVE_DEVICE_IMEI})")
        else:
            print(f"  ⚠️ 截图上传失败, HTTP {resp.status_code}: {resp.text[:200]}")
    except ImportError:
        print("  ⚠️ requests 库未安装，跳过截图上传。安装: pip install requests")
    except Exception as e:
        # 上传失败不影响主流程，仅打印警告
        print(f"  ⚠️ 截图上传异常(不影响主流程): {e}")


@dataclass
class Screenshot:
    """Represents a captured screenshot."""

    base64_data: str
    width: int
    height: int
    is_sensitive: bool = False


def get_screenshot(device_id: str | None = None, timeout: int = 10) -> Screenshot:
    """
    Capture a screenshot from the connected Android device.

    Args:
        device_id: Optional ADB device ID for multi-device setups.
        timeout: Timeout in seconds for screenshot operations.

    Returns:
        Screenshot object containing base64 data and dimensions.

    Note:
        If the screenshot fails (e.g., on sensitive screens like payment pages),
        a black fallback image is returned with is_sensitive=True.
    """
    temp_path = os.path.join(tempfile.gettempdir(), f"screenshot_{uuid.uuid4()}.png")
    adb_prefix = _get_adb_prefix(device_id)

    try:
        # Execute screenshot command
        result = subprocess.run(
            adb_prefix + ["shell", "screencap", "-p", "/sdcard/tmp.png"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        # Check for screenshot failure (sensitive screen)
        output = result.stdout + result.stderr
        if "Status: -1" in output or "Failed" in output:
            return _create_fallback_screenshot(is_sensitive=True)

        # Pull screenshot to local temp path
        subprocess.run(
            adb_prefix + ["pull", "/sdcard/tmp.png", temp_path],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if not os.path.exists(temp_path):
            return _create_fallback_screenshot(is_sensitive=False)

        # Read and encode image
        img = Image.open(temp_path)
        width, height = img.size

        buffered = BytesIO()
        img.save(buffered, format="PNG")
        png_bytes = buffered.getvalue()
        base64_data = base64.b64encode(buffered.getvalue()).decode("utf-8")

        # Cleanup
        os.remove(temp_path)

        # ========== 新增：上传截图到 ltlive ==========
        # _upload_screenshot_to_ltlive(png_bytes, filename=f"screenshot_{uuid.uuid4().hex[:8]}.png")
        # =============================================

        return Screenshot(
            base64_data=base64_data, width=width, height=height, is_sensitive=False
        )

    except Exception as e:
        print(f"Screenshot error: {e}")
        return _create_fallback_screenshot(is_sensitive=False)


def _get_adb_prefix(device_id: str | None) -> list:
    """Get ADB command prefix with optional device specifier."""
    if device_id:
        return ["adb", "-s", device_id]
    return ["adb"]


def _create_fallback_screenshot(is_sensitive: bool) -> Screenshot:
    """Create a black fallback image when screenshot fails."""
    default_width, default_height = 1080, 2400

    black_img = Image.new("RGB", (default_width, default_height), color="black")
    buffered = BytesIO()
    black_img.save(buffered, format="PNG")
    base64_data = base64.b64encode(buffered.getvalue()).decode("utf-8")

    return Screenshot(
        base64_data=base64_data,
        width=default_width,
        height=default_height,
        is_sensitive=is_sensitive,
    )
