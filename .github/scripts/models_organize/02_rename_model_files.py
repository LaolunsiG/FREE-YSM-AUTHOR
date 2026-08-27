#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YSM 模型文件批量重命名工具（本仓库专用）。

从 02_rename_model_folders.py 拆出：02_rename_model_folders.py 只负责模型文件夹重命名，
本脚本负责模型文件（.ysm 及附属 .zip/.7z/.rar/.bbmodel 等）重命名。

命名规则:
    <文件夹名(去评级)>[_保留描述词][_v版本][_副本序号]<后缀>
    例: AL_标枪_Javelin_非公开_v2.6.12.ysm -> AL_Javelin_标枪_非公开_v2.6.12.ysm

  1. 保留描述词（免费/付费/公开/非公开/兔女郎等，数据见
     .github/data/model-info/skin_tags.json，可经 --keep-word 追加）可能出现在文件名的
     任意位置，命中即提取到文件夹名之后，用于区分同一模型的不同服装/版本。
  2. 版本号（v2 / ver2 / 1.1 / v1.1 等）统一为 _v<版本>。
  3. 副本序号（(1)、（1）、_1、-1）统一为 _<数字>。

默认 dry-run 只预览；加 --apply 才真正重命名。

用法:
  python '.github/scripts/models_organize/02_rename_model_files.py'                       # 预览（默认仓库根）
  python '.github/scripts/models_organize/02_rename_model_files.py' --apply               # 执行
  python '.github/scripts/models_organize/02_rename_model_files.py' 0001-02Bunny,蓝玫瑰 --apply   # 指定作者目录
  python '.github/scripts/models_organize/02_rename_model_files.py' 某.ysm                # 指定单个文件
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
# 脚本按流程阶段分类到 scripts/<类别>/ 子目录：把 .github/scripts 加回 sys.path，
# 保证 lib/ 与跨分类脚本可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from lib import models as lib_models
from lib import paths as lib_paths


# 允许重命名的模型文件扩展名（模型本体 + 常见附属压缩包/源文件）
ALLOWED_FILE_EXTS = {'.ysm', '.zip', '.7z', '.rar', '.tar', '.gz', '.bbmodel'}

# 保留词数据统一托管在 .github/data/model-info/skin_tags.json：
# 每个标签的 name.en / name.zh / aliases 都会作为需保留的描述词
# （免费/付费/公开/非公开/兔女郎/泳装等）。扩充词汇只需改数据文件，
# 可用 --keep-word 追加运行期自定义词。词可带可选「版」后缀（如 非公开版）。
_KEEP_WORDS_CACHE: set[str] | None = None


def keep_words() -> set[str]:
    """保留词全集：从 skin_tags.json 加载全部标签的 en/zh/aliases。

    数据集中在 .github/data/model-info/skin_tags.json，扩充词汇只需改数据文件；
    本进程内缓存一次，--keep-word 可再追加。文件缺失/损坏时退化为空集。
    """
    global _KEEP_WORDS_CACHE
    if _KEEP_WORDS_CACHE is None:
        words: set[str] = set()
        tags = lib_paths.load_json(
            lib_paths.data_path('model-info', 'skin_tags.json'), {})
        for tag in tags.values():
            name = tag.get('name') or {}
            for key in ('en', 'zh'):
                val = name.get(key)
                if val:
                    words.add(str(val))
            for alias in tag.get('aliases') or []:
                words.add(str(alias))
        _KEEP_WORDS_CACHE = words
    return _KEEP_WORDS_CACHE


def _word_pattern(word: str) -> str:
    """构建保留词匹配正则。

    英文/带连字符词用自定义词边界 `(?<![A-Za-z0-9])...(?![A-Za-z0-9])`：
    `_`、`-` 也被当作分隔符（内置 \\b 把 `_` 当词字符，导致 `_bunny_` 中的
    bunny 匹配不到），同时避免 old 误匹配 Mold、new 误匹配 NewModel；
    中文词直接匹配；词可带可选「版」后缀。
    """
    if re.fullmatch(r'[A-Za-z-]+', word):
        return rf'(?<![A-Za-z0-9]){re.escape(word)}(?:版)?(?![A-Za-z0-9])'
    return re.escape(word) + r'(?:版)?'


def extract_keep_words(file_stem: str, folder_name: str) -> list[str]:
    """从文件名提取需保留的描述词（免费/付费/公开/非公开/服装词等，数据见 skin_tags.json）。

    这些词可能出现在文件名任意位置；按词长降序匹配，避免短词命中长词子串
    （如 公开 ⊂ 非公开）；文件夹名已含的词不再重复提取；英文词按词边界匹配。
    """
    folder_keywords = set(re.split(r'[-_\s]+', folder_name))
    # 单字母词（如皮肤表的 'l'）无区分意义且易误匹配，排除
    candidates = [w for w in keep_words() if len(w) >= 2 and w not in folder_keywords]
    found: list[str] = []
    for word in sorted(candidates, key=len, reverse=True):
        # 短词若已是已找到长词的子串则跳过（如 公开 已被 非公开 覆盖）
        if any(word in f for f in found):
            continue
        if re.search(_word_pattern(word), file_stem, re.IGNORECASE):
            found.append(word)
    return found


def parse_file_stem(file_stem: str) -> tuple[str, str]:
    """智能解析文件名，分离为：版本号、副本序号（如 兔子洞Ver1.1 -> ("_v1.1", "")）。"""
    stem = file_stem
    # 1. 提取末尾的副本序号 (如 (1)、（1）、_1、-1)
    copy_tag = ""
    copy_match = re.search(r'[\s_-]*[\(（](\d+)[\)）]$|[\s_-]+(\d+)$', stem)
    if copy_match:
        num = copy_match.group(1) or copy_match.group(2)
        copy_tag = f"_{num}"
        stem = stem[:copy_match.start()].strip('-_ ')
    # 2. 提取版本号。双分支：优先"版本前缀+纯整数"（v2/ver2），其次"带小数点版本"
    #    （1.1/v2.1/Ver1.1，前缀 v/ver/version/r 可选）。
    #    纯整数分支要求数字前有 v/ver/version 前缀、且前缀前是分隔符/非词字符，
    #    避免误伤 RABBIT1、Fox1 这类编号尾数（数字紧贴字母、无版本前缀）。
    version_tag = ""
    version_match = re.search(
        r'(?:[\s_.-]+|(?<=[^\w]))(?:ver(?:sion)?|v)[\s_.-]*(\d+)(?![.\w])'
        r'|(?:(?:[\s_-]|(?<=[^\w]))*)(?:ver(?:sion)?|v|r)?[\s._-]*(\d+(?:\.\d+)+)',
        stem, re.IGNORECASE)
    if version_match:
        version_num = version_match.group(1) or version_match.group(2)
        version_tag = f"_v{version_num}"
        stem = (stem[:version_match.start()] + stem[version_match.end():]).strip('-_ ')
    return version_tag, copy_tag


def rename_files_cmd(target_path: Path, apply_changes: bool = False) -> int:
    """重命名模型文件：<文件夹名(去评级)>[_保留描述词][_v版本][_副本序号]<后缀>。

    dry-run 默认预览，--apply 才真正改名。
    """
    if target_path.is_file():
        files = [target_path]
    elif target_path.is_dir():
        # 跳过隐藏目录（.git/.venv 等），避免默认扫描仓库根时进入大量无关目录
        files = [
            p for p in target_path.rglob('*')
            if p.is_file()
            and not any(part.startswith('.') for part in p.relative_to(target_path).parts)
        ]
    else:
        print(f"错误: 路径不存在 -> {target_path}", file=sys.stderr)
        return 2

    renamed_count = 0
    skipped_count = 0
    print(f"{'='*20} "
          f"{'执行模式: 真实修改 (--apply)' if apply_changes else '执行模式: 预览模式 (Dry-Run)'}"
          f" {'='*20}\n")
    for file_path in sorted(files):
        if file_path.suffix.lower() not in ALLOWED_FILE_EXTS:
            continue
        folder_name = file_path.parent.name
        base_folder_name = lib_models.clean_folder_name(folder_name)
        original_name = file_path.name
        ext = file_path.suffix
        version_tag, copy_tag = parse_file_stem(file_path.stem)
        # 保留描述词：从文件名任意位置提取（免费/付费/公开/非公开/服装词等），
        # 用于区分同一模型的不同服装/版本
        keep_words = extract_keep_words(file_path.stem, folder_name)
        keep_tag = '_' + '_'.join(keep_words) if keep_words else ''
        new_stem = f"{base_folder_name}{keep_tag}{version_tag}{copy_tag}"
        new_stem = re.sub(r'_+', '_', new_stem).strip('_')
        new_name = f"{new_stem}{ext}"
        if original_name == new_name:
            skipped_count += 1
            continue
        new_file_path = file_path.parent / new_name
        # 同名冲突处理：追加 .数字 后缀（如 ..._v1.0.ysm -> ..._v1.0.1.ysm -> .2...）
        if apply_changes and new_file_path.exists():
            counter = 1
            while new_file_path.exists():
                new_file_path = file_path.parent / f"{new_stem}.{counter}{ext}"
                counter += 1
            new_name = new_file_path.name
        print(f"[匹配] 目录: {folder_name}/")
        print(f"  原名: {original_name}")
        print(f"  新名: {new_name}\n")
        if apply_changes:
            file_path.rename(new_file_path)
        renamed_count += 1
    print(f"{'='*50}")
    print(f"统计完成: 待修改/已修改 = {renamed_count}, 无需修改 = {skipped_count}")
    if not apply_changes and renamed_count > 0:
        print("\n提示: 当前为预览模式，磁盘文件未修改。如确认无误，请在命令末尾加上 --apply 执行！")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('paths', nargs='*',
                        help='文件或目录（默认仓库根；目录递归收集模型文件）')
    parser.add_argument('--apply', action='store_true',
                        help='真正执行重命名（默认 dry-run 预览）')
    parser.add_argument('--root', metavar='PATH', default=None,
                        help='仓库根目录（默认自动检测）')
    parser.add_argument('--keep-word', action='append', default=None,
                        metavar='WORD',
                        help='追加需保留的描述词（可多次；默认数据见 skin_tags.json）')
    args = parser.parse_args()

    if args.keep_word:
        keep_words().update(w.strip() for w in args.keep_word if w.strip())

    root = Path(args.root).resolve() if args.root else lib_paths.WORKSPACE_ROOT
    # 仓库已平铺：作者目录在根下，默认扫描仓库根（不再指向已不存在的 Models/）
    target = Path(args.paths[0]) if args.paths else root
    return rename_files_cmd(target, apply_changes=args.apply)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
