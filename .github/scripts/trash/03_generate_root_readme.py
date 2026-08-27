#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根 README 展示生成工具：根 README 作者表 + 作品分类区块。
（集中作者数据 authors.json 已移至 check&fix/kb_tool.py --authors-data）

由原 build_readme_authors.py（根表渲染）合并，并收纳 02 的 --build-category-map
（作品分类区块），供 cli / pipeline / organize / audit 调用。

用法：
  python 03_generate_root_readme.py                        # 默认：同时构建作者索引 + 作品说明
  python 03_generate_root_readme.py --author               # 只重建根 README 作者索引
  python 03_generate_root_readme.py --build-category-map   # 只更新根 README 作品说明（模型分类区块）
"""
import argparse
import re
import sys
from pathlib import Path

# 脚本按流程阶段分类到 scripts/<类别>/ 子目录：把 .github/scripts 加回 sys.path，
# 保证 lib/ 与跨分类脚本可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import paths as lib_paths  # noqa: E402
from lib import readme as lib_readme  # noqa: E402
from lib.kb.category import (  # noqa: E402
    build_category_map, count_models_by_work, update_readme_works_section,
)
from lib.kb.storage import load_kb_json  # noqa: E402
from lib.author_readme import (  # noqa: E402
    compute_author_marks, load_tag_labels, tag_order_key,
)

WORKSPACE_ROOT = lib_paths.WORKSPACE_ROOT
MODELS_DIR = WORKSPACE_ROOT / 'Models'
FOLDER_RE = re.compile(r'^(\d{4})$')


# ---------------------------------------------------------------------------
# --author：重建根 README 作者表（原 build_readme_authors.py）
# ---------------------------------------------------------------------------
def build_platform_cells(platforms: dict) -> str:
    """作者平台列：多平台用 · 连接；http 值渲染为链接，其余渲染为 平台: 值。"""
    cells = []
    for key, value in (platforms or {}).items():
        if isinstance(value, str) and value.startswith('http'):
            cells.append(f'[{key}]({value})')
        else:
            cells.append(f'{key}: {value}' if value else str(key))
    return ' · '.join(cells)


def build_readme_rows() -> list[tuple[str, str, int, str, str, str]]:
    """收集作者行（编号, 名称, 模型数, 链接, 标记, 平台列）。

    作者名/⭐推荐统一取自 authors.json；🔥高产/R18/团队标记自动判定。
    """
    authors_index = lib_readme.load_authors_index().get('authors') or {}
    rows: list[tuple[str, str, int, str, str, str]] = []
    for folder in sorted(p.name for p in MODELS_DIR.iterdir() if p.is_dir()):
        if not FOLDER_RE.match(folder):
            continue
        author_dir = MODELS_DIR / folder
        link = f'.../../Models/{folder}'

        # 统一使用集中作者数据 authors.json（name 为数组，取规范名）
        entry = authors_index.get(folder) or {}
        names = entry.get('name') or []
        if isinstance(names, str):
            names = lib_readme.split_author_names(names)
        # 列出该作者的全部名称，不同名称用 | 隔开（README 表格内 | 已转义）
        author_name = ' | '.join(names) if names else '暂无'

        model_count = sum(1 for sub in author_dir.iterdir()
                          if sub.is_dir() and not sub.name.startswith('.'))

        # 标记：由 authors.json 的 tags（词表 emoji）驱动；无自动判定
        marks = compute_author_marks(entry)
        labels = load_tag_labels()
        flag_str = ' '.join(labels[m]['emoji'] for m in marks
                            if m in labels and labels[m].get('emoji'))

        rows.append((folder, author_name, model_count, link, flag_str,
                     build_platform_cells(entry.get('platforms') or {})))
    return rows


def build_legend(is_en: bool) -> str:
    """作者表图例：从词表遍历「emoji + 名称」按 order 生成（仅含 emoji 非空标签）。"""
    labels = load_tag_labels()
    parts = []
    for k in sorted(labels, key=tag_order_key):
        meta = labels[k]
        emoji = str(meta.get('emoji') or '')
        if not emoji:
            continue
        name = str(meta.get('en' if is_en else 'zh') or '')
        if name:
            parts.append(f'{emoji} {name}')
    return ('> Marks: ' if is_en else '> 标记：') + ' · '.join(parts)


def build_readme_table(rows: list[tuple[str, str, int, str, str, str]], is_en: bool) -> str:
    """渲染作者表（中/英表头；含标记 + 平台列；空表给占位行）。表上方附图例。"""
    if is_en:
        header, empty_row = ('| ID | Author Name | Platforms | Total Models |',
                             '| - | None |  | 0 |')
    else:
        header, empty_row = ('| 编号 | 作者名称 | 平台 | 收录数量 |',
                             '| - | 暂无 |  | 0 |')
    legend = build_legend(is_en)
    separator = '| --- | --- | --- | ---: |'

    lines = [legend, '', header, separator]
    for folder, author_name, model_count, link, flags, platforms in rows:
        safe = author_name.replace('|', '\\|')
        label = 'None' if (is_en and safe == '暂无') else safe
        name_cell = f'{flags} [{label}]({link})' if flags else f'[{label}]({link})'
        lines.append(f'| {folder} | {name_cell} | {platforms} | {model_count} |')
    return '\n'.join(lines) if rows else f'{header}\n{separator}\n{empty_row}'


def update_root_readme(rows: list[tuple[str, str, int, str, str, str]], path: Path,
                       is_en: bool) -> bool:
    """在 AUTHORS_LIST 标记区间内替换作者表；无变化返回 False。"""
    content = path.read_text(encoding='utf-8')
    start, end = lib_readme.AUTHORS_LIST_START, lib_readme.AUTHORS_LIST_END
    if start not in content or end not in content:
        print(f'Error: Markers not found in {path}')
        return False

    before = content.split(start, 1)[0] + start + '\n'
    after = '\n' + end + content.split(end, 1)[1]
    updated = before + build_readme_table(rows, is_en) + after

    if updated != content:
        path.write_text(updated, encoding='utf-8')
        print(f'Updated {path} with {len(rows)} rows.')
        return True
    print(f'No changes in {path}')
    return False


def write_root_readmes() -> int:
    """重建根 README 与 Docs/README-EN 的作者表。"""
    readme_path = WORKSPACE_ROOT / 'README.md'
    readme_en_path = WORKSPACE_ROOT / 'Docs' / 'README-EN.md'
    if not MODELS_DIR.is_dir():
        print(f'Error: {MODELS_DIR} directory not found.')
        return 2
    if not readme_en_path.is_file():
        print(f'Error: {readme_en_path} not found.')
        return 2
    rows = build_readme_rows()
    update_root_readme(rows, readme_path, False)
    update_root_readme(rows, readme_en_path, True)
    return 0


def build_category_map_cmd() -> int:
    """从 character/*.json 现算分类 + 更新根 README 模型分类区块（不落盘 category_map.json）。

    收纳自 02 脚本：作品分类是根 README 展示区块，与作者表同属"根 README 生成"。
    仅更新 README 区块，不重写知识库数据（数据不变）。
    """
    data = load_kb_json(lib_paths.MODEL_INFO_DIR)
    cat_map = build_category_map(data)
    model_counts = count_models_by_work(
        [WORKSPACE_ROOT / 'Models', WORKSPACE_ROOT / 'Other-YSM-Models'])
    un = [k for k, v in (data.get("works") or {}).items()
          if not (isinstance(v, dict) and v.get("category"))]
    total = sum(len(v) for v in cat_map.values())
    total_models = sum(model_counts.values())
    print(f"分类（从 character/*.json 现算）: {total} 个作品、{total_models} 个模型"
          f"分布在 {len(cat_map)} 个大类")
    changed, action = update_readme_works_section(
        WORKSPACE_ROOT / "README.md", data, model_counts)
    if changed:
        print(f"根 README 模型分类区块已{action}")
    # 英文版：Docs/README-EN.md 模型分类区块（旧版手写块会被替换为 markers 包裹的新内容）
    changed_en, action_en = update_readme_works_section(
        WORKSPACE_ROOT / "Docs" / "README-EN.md", data, model_counts, lang='en')
    if changed_en:
        print(f"README-EN 模型分类区块已{action_en}")
    if un:
        print(f"提示：{len(un)} 个作品未标注 category，已归为 Other："
              f"{', '.join(sorted(un))}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--author', action='store_true', help='只重建根 README 作者索引')
    parser.add_argument('--build-category-map', action='store_true',
                        help='只更新根 README 作品说明（模型分类区块）')
    args = parser.parse_args()

    # 默认同时构建作者索引 + 作品说明；传单一参数则只构建对应区块
    if args.author != args.build_category_map:
        return write_root_readmes() if args.author else build_category_map_cmd()
    code = write_root_readmes()
    build_category_map_cmd()
    return code


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
