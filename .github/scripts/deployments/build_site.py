import json
import sys
from pathlib import Path

# --- 依赖检查 ---
try:
    from jinja2 import Template
except ImportError:
    print("Error: 缺少 'jinja2' 库。请运行: pip install jinja2")
    exit(1)

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    print("Warning: 缺少 'Pillow' 库，将跳过缩略图生成。建议运行: pip install Pillow")
    HAS_PILLOW = False

# 配置
# 脚本按流程阶段分类到 scripts/<类别>/ 子目录：把 .github/scripts 加回 sys.path，
# 保证 lib/ 与跨分类脚本可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import paths as lib_paths
from lib import previews as lib_previews
from lib import readme as lib_readme

# 不再切换工作目录：直接用 WORKSPACE_ROOT 定位，避免 import/运行时的目录副作用
ROOT_DIR = lib_paths.WORKSPACE_ROOT / 'Models'
OUTPUT_FILE = lib_paths.WORKSPACE_ROOT / 'index.html'
TEMPLATE_FILE = lib_paths.data_path('templates', 'website_template.html')
THUMBNAIL_SIZE = (400, 400)

def generate_thumbnail(src_path: Path):
    """生成 WebP 缩略图，返回缩略图的相对路径"""
    if not HAS_PILLOW: return src_path
    
    thumb_path = src_path.with_name(f"thumb_{src_path.stem}.webp")
    
    # 如果缩略图已存在且比原图新，直接返回
    if thumb_path.exists() and thumb_path.stat().st_mtime > src_path.stat().st_mtime:
        return thumb_path

    try:
        with Image.open(src_path) as img:
            # 转换为 RGB (防止 RGBA 保存为 JPEG 报错，虽然我们用 WebP 但保持习惯)
            if img.mode in ('RGBA', 'LA'):
                background = Image.new(img.mode[:-1], img.size, (255, 255, 255))
                background.paste(img, img.split()[-1])
                img = background
            
            img.thumbnail(THUMBNAIL_SIZE)
            img.save(thumb_path, 'WEBP', quality=85)
            print(f"Generated thumbnail: {thumb_path}")
            return thumb_path
    except Exception as e:
        print(f"Failed to generate thumbnail for {src_path}: {e}")
        return src_path

PLATFORMS = ['bilibili', 'youtube', 'afdian', 'patreon', 'ko-fi', 'twitter', 'pixiv', 'sketchfab']


def parse_readme(file_path, default_id):
    """解析作者 README：作者名（Author 段 Name 行）+ 平台账号（复用 lib/readme 统一实现）。

    旧的逐行正则只匹配 'Author: xxx' 格式，在当前 '## Author / - **Name**:' 格式下失效；
    改由 lib/readme.parse_author_name_value / extract_platforms 解析。"""
    info = {"name": default_id, "socials": []}
    if not file_path or not file_path.exists():
        return info
    content = file_path.read_text(encoding='utf-8', errors='ignore')
    name = lib_readme.parse_author_name_value(content)
    if name:
        info['name'] = name
    for key, value in lib_readme.extract_platforms(content).items():
        platform = next((p for p in PLATFORMS if p in key.lower()), key.lower())
        info['socials'].append({"platform": platform, "url": value})
    return info

def build_models_data() -> tuple[list[dict], list[dict]]:
    """扫描 Models/ 构建 (models_data, sorted_authors)。

    只处理 4 位数字作者编号目录（跳过 previews 等杂项目录）；
    作者信息优先 info.json，回退解析 README（复用 lib/readme 统一实现）。
    """
    models_data: list[dict] = []
    authors_set: dict[str, str] = {}

    if not ROOT_DIR.exists():
        return [], []

    for author_dir in sorted(ROOT_DIR.iterdir()):
        if not author_dir.is_dir():
            continue
        author_id = author_dir.name
        if not author_id.isdigit() or len(author_id) != 4:
            continue

        # 1. 尝试读取 info.json (元数据增强)
        info_json_path = author_dir / 'info.json'
        readme_path = None

        if info_json_path.exists():
            try:
                with open(info_json_path, 'r', encoding='utf-8') as f:
                    author_meta = json.load(f)
                    # 确保有基本字段
                    if 'name' not in author_meta:
                        author_meta['name'] = author_id
                    if 'socials' not in author_meta:
                        author_meta['socials'] = []
            except Exception:
                author_meta = parse_readme(None, author_id)
        else:
            # 2. 回退到 README 解析
            for name in ['README.txt', 'readme.txt', 'README.md', 'readme.md']:
                p = author_dir / name
                if p.exists():
                    readme_path = p
                    break
            author_meta = parse_readme(readme_path, author_id)

        authors_set[author_id] = author_meta['name']

        for model_dir in author_dir.iterdir():
            if not model_dir.is_dir():
                continue
            if model_dir.name.lower().startswith('readme'):
                continue

            previews, tags, files = [], [], []

            # 查找预览图（复用 lib/previews.py 统一规则；跳过生成的缩略图）
            preview_images = [f for f in lib_previews.collect_preview_images(model_dir)
                              if not f.name.startswith('thumb_')]
            for f in preview_images:
                thumb = generate_thumbnail(f)
                previews.append({
                    "url": f.as_posix(),        # 原图 (用于放大)
                    "thumb": thumb.as_posix(),  # 缩略图 (用于卡片显示)
                })

            # 查找标签
            tag_file = model_dir / 'tags.md'
            if tag_file.exists():
                try:
                    with open(tag_file, 'r', encoding='utf-8') as tf:
                        tags = [t.strip() for t in tf.read().replace('\n', ',').split(',') if t.strip()]
                except Exception:
                    pass

            # 查找文件
            for f in model_dir.iterdir():
                if (not f.suffix.lower() in ['.html', '.py', '.txt', '.md', '.json', '.webp']
                        and not f.name.startswith('.') and not f.name.startswith('preview')):
                    files.append({"name": f.name, "path": f.as_posix()})

            models_data.append({
                "author_id": author_id,
                "author_name": author_meta['name'],
                "socials": author_meta['socials'],
                "name": model_dir.name,
                "previews": previews,
                "tags": tags,
                "files": files,
                "mtime": model_dir.stat().st_mtime,  # 添加修改时间用于排序
            })

    sorted_authors = [{"id": k, "name": v} for k, v in sorted(authors_set.items())]
    return models_data, sorted_authors


def render_site() -> int:
    """渲染静态站 index.html；返回退出码（0=成功）。"""
    if not ROOT_DIR.exists():
        print(f"Error: Directory '{ROOT_DIR}' not found.")
        return 1

    models_data, sorted_authors = build_models_data()

    if not TEMPLATE_FILE.exists():
        print(f"Error: Template file '{TEMPLATE_FILE}' not found.")
        return 1

    with open(TEMPLATE_FILE, 'r', encoding='utf-8') as f:
        template_content = f.read()

    template = Template(template_content)
    final_html = template.render(
        models_json=json.dumps(models_data, ensure_ascii=False),
        authors_json=json.dumps(sorted_authors, ensure_ascii=False),
    )

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(final_html)

    print(f"Build successful. Generated {len(models_data)} models.")
    return 0


def main() -> int:
    return render_site()


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    raise SystemExit(main())
