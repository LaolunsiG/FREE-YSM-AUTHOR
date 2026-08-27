#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Other-YSM-Models 顶层 README 模型总索引生成工具。

在 Other-YSM-Models/README.md 生成按作品分组的模型总索引（## Models 段，
每组 <details> 折叠，作品标题用 character/*.json 的中英文全称，Unknown 最后）。
索引段用 marker 包裹，可幂等更新；marker 外的手工说明内容（如"都是没有作者信息
的模型"）保留不动。

收集范围：Other-YSM-Models/<作品>/<模型>/ 两层（兼容更深嵌套），
含 .ysm 文件或 previews/ 子目录的目录视为模型目录（与 03_generate_model_readmes
的规则一致）。

用法:
  python .github/scripts/models_organize/03_generate_other_models_index.py            # dry-run 预览
  python .github/scripts/models_organize/03_generate_other_models_index.py --apply    # 真正写入
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 把 .github/scripts 加回 sys.path，保证 lib/ 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import paths as lib_paths
from lib.author_readme import render_models_section, load_work_names

REPO_ROOT = lib_paths.WORKSPACE_ROOT

# 顶层索引段的包裹标记（marker 替换，保留 marker 外的手工内容）
START_MARKER = '<!-- GENERATED OTHER MODELS INDEX START -->'
END_MARKER = '<!-- GENERATED OTHER MODELS INDEX END -->'


def collect_model_dirs(root: Path) -> list[Path]:
    """递归收集 root 下所有模型目录（含 .ysm 文件或 previews/ 子目录即视为模型）。

    适配 Other-YSM-Models 的 <作品>/<模型> 两层（或更深）组织；作品层（无 .ysm）
    继续向下找，避免把 AK/、BA/ 等作品目录误当模型目录。
    """
    def walk(d: Path) -> list[Path]:
        try:
            entries = list(d.iterdir())
        except OSError:
            return []
        has_ysm = any(e.is_file() and e.suffix.lower() == '.ysm' for e in entries)
        has_previews = any(e.is_dir() and e.name == 'previews' for e in entries)
        if has_ysm or has_previews:
            return [d]
        out: list[Path] = []
        for e in entries:
            if e.is_dir() and e.name != 'previews' and not e.name.startswith('.'):
                out.extend(walk(e))
        return out

    dirs: list[Path] = []
    if root.is_dir():
        for sub in sorted(root.iterdir()):
            if sub.is_dir() and not sub.name.startswith('.'):
                dirs.extend(walk(sub))
    return sorted(set(dirs), key=lambda d: str(d))


def render_index_block(root: Path, models: list[Path]) -> str:
    """渲染带 marker 的顶层索引段（## Models 按作品分组折叠）。

    分组与显示用模型文件夹名（basename），链接用相对 root 的路径
    （如 AK/AK_阿米娅_Amiya），保证 README 与作品目录同级的链接正确。
    """
    names = [p.name for p in models]
    links = [p.relative_to(root).as_posix() for p in models]
    section = render_models_section(names, load_work_names(), links=links)
    return f"{START_MARKER}\n{section.rstrip()}\n{END_MARKER}"


def update_readme(root: Path, block: str) -> tuple[bool, str]:
    """把索引段写入 <root>/README.md：已有 marker 则替换，否则追加到末尾。

    返回 (是否变更, 动作)。marker 外的手工内容（如目录说明）保留不动。
    """
    readme_path = root / 'README.md'
    content = (readme_path.read_text(encoding='utf-8', errors='ignore')
               if readme_path.exists() else '')
    if START_MARKER in content and END_MARKER in content:
        new = re.sub(re.escape(START_MARKER) + r'.*?' + re.escape(END_MARKER),
                     block, content, flags=re.DOTALL)
        if new == content:
            return False, ''
        readme_path.write_text(new, encoding='utf-8')
        return True, 'updated'
    sep = '\n\n' if content and not content.endswith('\n') else ('\n' if content else '')
    readme_path.write_text(content + sep + block + '\n', encoding='utf-8')
    return True, 'appended'


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', metavar='PATH', default=None,
                        help='仓库根目录（默认自动检测）')
    parser.add_argument('--apply', action='store_true',
                        help='真正写入（默认 dry-run 只预览）')
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else REPO_ROOT
    other_dir = root / 'Other-YSM-Models'
    if not other_dir.is_dir():
        print(f'错误: {other_dir} 目录不存在。')
        return 2

    models = collect_model_dirs(other_dir)
    if not models:
        print('未找到任何模型目录。')
        return 0

    # 分组摘要（分组键即模型名前缀，与 render_models_section 一致；Unknown 最后）
    groups: dict[str, int] = {}
    for p in models:
        prefix = p.name.split('_', 1)[0].strip() if '_' in p.name else 'Unknown'
        if not prefix or prefix.lower() == 'unknown':
            prefix = 'Unknown'
        groups[prefix] = groups.get(prefix, 0) + 1
    ordered = sorted(groups, key=lambda k: (k.lower() == 'unknown', k.lower()))
    print(f'Other-YSM-Models 下共 {len(models)} 个模型目录：')
    for prefix in ordered:
        print(f'  {prefix}: {groups[prefix]}')

    block = render_index_block(other_dir, models)
    if args.apply:
        changed, action = update_readme(other_dir, block)
        if changed:
            print(f'已写入 Other-YSM-Models/README.md（{action}）')
        else:
            print('Other-YSM-Models/README.md 已是最新，无需更新')
    else:
        print('dry-run 预览（未写入）：加 --apply 写入 Other-YSM-Models/README.md。')
    return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
