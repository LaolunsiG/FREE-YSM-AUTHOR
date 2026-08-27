"""预览图识别与收集（generate_model_readmes / organize_previews 共用同一规则）。"""
import re
from pathlib import Path

IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
PREVIEW_MARKER = re.compile(r'preview', re.I)
PREVIEWS_DIRNAME = 'previews'
# 生成物前缀（build_site 缩略图），不作为预览图识别
THUMB_PREFIX = 'thumb_'


def is_preview_image(path: Path) -> bool:
    """识别规则：文件名含 preview 的图片文件（与 generate_model_readmes 保持一致）；
    排除 build_site 生成的 thumb_ 缩略图（避免生成物被当预览图）。"""
    return (path.is_file() and path.suffix.lower() in IMAGE_EXTS
            and PREVIEW_MARKER.search(path.stem)
            and not path.name.startswith(THUMB_PREFIX))


def is_image_file(path: Path) -> bool:
    """是否图片文件（不要求名字含 preview）。"""
    return path.is_file() and path.suffix.lower() in IMAGE_EXTS


def find_previews_dir(model_dir: Path) -> Path | None:
    """查找已有的 previews 子目录（大小写不敏感），不存在则返回 None。"""
    for sub in model_dir.iterdir():
        if sub.is_dir() and sub.name.lower() == PREVIEWS_DIRNAME:
            return sub
    return None


def collect_preview_images(model_dir: Path) -> list[Path]:
    """收集模型目录下的预览图：根目录 preview* 命名图片 + previews/ 目录内全部图片。"""
    images: list[Path] = []
    for file_path in sorted(model_dir.iterdir()):
        if is_preview_image(file_path):
            images.append(file_path)
    for sub_dir in model_dir.iterdir():
        if sub_dir.is_dir() and sub_dir.name.lower() == PREVIEWS_DIRNAME:
            for file_path in sorted(sub_dir.iterdir()):
                if (is_image_file(file_path)
                        and not file_path.name.startswith(THUMB_PREFIX)
                        and file_path not in images):
                    images.append(file_path)
    return images
