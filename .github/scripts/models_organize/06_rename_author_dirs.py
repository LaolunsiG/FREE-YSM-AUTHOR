#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
作者文件夹重命名工具——目录名与 authors.json 保持一致。

作者目录在仓库根，规范命名 '<编号>-<作者名[,别名]>'（别名逗号连接、最多 5 个、
去 # 前缀与 Windows 非法字符）。本脚本以 authors.json 的 name 数组为权威，
生成规范目录名并重命名（git mv 保留历史），保持目录与数据同步。

用法:
  python .github/scripts/models_organize/06_rename_author_dirs.py                  # dry-run：全量对齐目录名
  python .github/scripts/models_organize/06_rename_author_dirs.py --apply          # 全量对齐并执行
  python .github/scripts/models_organize/06_rename_author_dirs.py --apply 0001 蓝玫瑰 02Bunny
      # 更新 authors.json 中 0001 的 name 为 [蓝玫瑰, 02Bunny]（写盘），并重命名目录
  python .github/scripts/models_organize/06_rename_author_dirs.py --apply 0001  # 按 authors.json 现有 name 对齐 0001 目录名
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import paths as lib_paths

REPO_ROOT = lib_paths.WORKSPACE_ROOT
MAX_ALIASES = 5          # 目录名最多展示的别名数（与作者名对齐规则一致）
ILLEGAL_RE = re.compile(r'[\\/:*?"<>|]')


def clean_name(name: str) -> str | None:
    """清洗单个作者名：去 # 前缀、去首尾空白、过滤 Windows 非法字符；空返回 None。"""
    n = name.lstrip('#＃').strip()
    if not n or ILLEGAL_RE.search(n):
        return None
    return n


def build_dir_name(aid: str, names: list[str]) -> str | None:
    """由编号 + 名称数组生成规范目录名 '<编号>-<别名1,别名2,…>'（最多 MAX_ALIASES 个别名）。

    别名去重、保留顺序；无有效名称返回 None（保持现状/提示）。
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for n in names or []:
        c = clean_name(n)
        if c and c not in seen:
            cleaned.append(c)
            seen.add(c)
        if len(cleaned) >= MAX_ALIASES:
            break
    if not cleaned:
        return None
    return f'{aid}-{",".join(cleaned)}'


def author_dir_for(root: Path, aid: str) -> Path | None:
    """按编号查找作者目录：优先 '<编号>-…' 前缀，其次裸 '<编号>'；不存在返回 None。"""
    prefix = f'{aid}-'
    dirs = sorted(root.iterdir())
    for d in dirs:
        if d.is_dir() and d.name.startswith(prefix):
            return d
    for d in dirs:
        if d.is_dir() and d.name == aid:
            return d
    return None


def is_author_dir_name(name: str) -> bool:
    """判断目录名是否为作者目录：'<编号>-<作者名[,别名]>' 或裸 '<编号>'（未收录新作者）。"""
    return (len(name) >= 4 and name[:4].isdigit()
            and (len(name) == 4 or name[4] == '-'))


def iter_author_dirs(root: Path):
    """遍历根下作者目录（'<编号>-…' 或裸 '<编号>' 格式）。"""
    for d in sorted(root.iterdir()):
        if d.is_dir() and is_author_dir_name(d.name):
            yield d


def git_mv(root: Path, src: Path, dst: Path, apply: bool) -> bool:
    """重命名目录；dry-run 只打印。优先 git mv（保留跟踪历史），失败回退物理移动。"""
    if not apply:
        print(f'  [计划] {src.name}/  ->  {dst.name}/')
        return True
    r = subprocess.run(['git', 'mv', str(src), str(dst)], cwd=root,
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode == 0:
        print(f'  [执行] {src.name}/  ->  {dst.name}/')
        return True
    # git mv 失败：源目录未被 git 跟踪（如用户手动放置的新模型目录）→ 物理移动
    #（git status 会显示删除 + 新增，提交时 git 按内容自动识别为 rename）
    import shutil
    try:
        shutil.move(str(src), str(dst))
        print(f'  [执行·物理] {src.name}/  ->  {dst.name}/（未跟踪目录）')
        return True
    except Exception as e:
        print(f'  [失败] {src.name} -> {dst.name}: {e}')
        return False


def load_authors() -> dict:
    """读取 authors.json 的 authors 字典。"""
    data = lib_paths.load_json(lib_paths.data_path('author-info', 'authors.json'), {})
    return data.get('authors') if isinstance(data, dict) else {}


def save_authors(authors: dict) -> None:
    """写回 authors.json（保留 version/generated 等顶层字段）。"""
    path = lib_paths.data_path('author-info', 'authors.json')
    data = lib_paths.load_json(path, {})
    data['authors'] = authors
    lib_paths.save_json(path, data)


def load_models_refs() -> dict:
    """读取 models.json 的 models 字典。"""
    data = lib_paths.load_json(lib_paths.data_path('model-info', 'models.json'), {})
    return data.get('models') if isinstance(data, dict) else {}


def save_models(models: dict) -> None:
    """写回 models.json（保留顶层字段）。"""
    path = lib_paths.data_path('model-info', 'models.json')
    data = lib_paths.load_json(path, {})
    data['models'] = models
    lib_paths.save_json(path, data)


def cmd_renumber(apply: bool) -> int:
    """重新编号：按文件夹编号顺序分配连续编号（0000 起），全量同步。

    同步范围（以磁盘上的作者文件夹为作者清单）：
      1. 文件夹：git mv '<旧编号>-<名称>' -> '<新编号>-<名称>'
      2. authors.json：key 重映射，readme 路径更新；无文件夹的幽灵条目保留原编号并提示
      3. models.json：authors 数组中的编号按映射替换
      4. co_creators.json：key 前缀 '旧编号/' -> '新编号/'
    """
    dirs = sorted(iter_author_dirs(REPO_ROOT), key=lambda d: int(d.name[:4]))
    if not dirs:
        print('[错误] 仓库根未找到作者目录')
        return 2

    # ── 前置步骤：先清理幽灵作者（无文件夹），再重编号 ──
    # 顺序很重要：幽灵不先删会占着旧编号；重编号把新条目映射到该编号时会覆盖幽灵条目
    #（本次先重编号后删幽灵，0193 幽灵曾被 0195→0193 覆盖——恰好重复无损失，但顺序应固定为先清理）
    authors = load_authors()
    dir_aids = {d.name[:4] for d in dirs}
    ghosts = sorted(a for a in authors if a not in dir_aids)
    if ghosts:
        print(f'[前置] 检测到幽灵作者 {len(ghosts)} 位（无文件夹）'
              f'，重编号前{"删除" if apply else "将删除"}: {ghosts}')
        if apply:
            authors = {aid: entry for aid, entry in authors.items()
                       if aid not in ghosts}
            save_authors(authors)
            print(f'  已删除 {len(ghosts)} 位幽灵作者并写回 authors.json')
    elif not apply:
        print('[前置] 无幽灵作者，直接进入重编号（作者与文件夹已对齐）')

    # 1. 按顺序分配连续编号
    mapping: dict[str, str] = {}   # 旧编号 -> 新编号
    for i, d in enumerate(dirs):
        mapping[d.name[:4]] = f'{i:04d}'

    print(f'重新编号（{"执行" if apply else "dry-run"}）: {len(dirs)} 个文件夹 -> 0000~{len(dirs)-1:04d}')
    print(f'\n=== 编号映射 ===')
    for i, d in enumerate(dirs):
        old, new = d.name[:4], f'{i:04d}'
        if old != new:
            print(f'  {old}-{d.name[5:]}  ->  {new}-{d.name[5:]}')

    # 2. models.json 引用但无文件夹的编号检查（幽灵已前置清理，此处只警告引用缺口）
    models = load_models_refs()
    ref_aids = {aid for v in models.values() for aid in v.get('authors', [])}
    orphan = sorted(a for a in ref_aids if a not in mapping and a not in dir_aids)
    if orphan:
        print(f'[警告] models.json 引用了无文件夹的编号（无法重编号，保持原值）: {orphan}')

    if not apply:
        print('\n(dry-run) 加 --apply 执行重编号')
        return 0

    # 3. 执行：重命名文件夹
    ok = fail = 0
    for i, d in enumerate(dirs):
        old, new = d.name[:4], f'{i:04d}'
        if old == new:
            continue
        new_name = f'{new}-{d.name[5:]}'
        if git_mv(REPO_ROOT, d, REPO_ROOT / new_name, apply):
            ok += 1
        else:
            fail += 1
    print(f'\n文件夹重命名: 成功 {ok}, 失败 {fail}')

    # 4. authors.json：key 重映射 + readme 路径更新
    new_authors = {}
    for aid, entry in authors.items():
        if aid in mapping:
            nid = mapping[aid]
            entry = dict(entry)
            nm = entry.get('name') or []
            # 采用新目录名（编号-别名），readme 路径随之更新
            new_dir = next((d.name for d in dirs if d.name[:4] == aid), None)
            entry['readme'] = f'{nid}-{new_dir[5:]}/README.md' if new_dir else entry.get('readme')
            new_authors[nid] = entry
        else:
            # 兜底：幽灵作者已在重编号前置步骤删除，正常不会走到这里
            new_authors[aid] = entry
    # 为有文件夹但 authors.json 无记录的作者补条目（name 从文件夹名推导）
    for i, d in enumerate(dirs):
        aid = d.name[:4]
        if aid in mapping and mapping[aid] not in new_authors:
            name = d.name[5:]
            if ',' in name:
                name = name.split(',')[0]
            new_authors[mapping[aid]] = {'name': [name], 'platforms': {}}
            print(f'  [新增] authors.json: {mapping[aid]} {name}（原无记录）')
    save_authors(new_authors)
    print(f'  authors.json: {len(new_authors)} 位作者（key 已重映射）')

    # 5. models.json：authors 引用按映射替换
    changed_models = 0
    for v in models.values():
        refs = [mapping.get(a, a) for a in v.get('authors', [])]
        # 去重保序
        seen, dedup = set(), []
        for a in refs:
            if a not in seen:
                seen.add(a)
                dedup.append(a)
        if dedup != v.get('authors'):
            v['authors'] = dedup
            changed_models += 1
    save_models(models)
    print(f'  models.json: {changed_models} 个模型 authors 编号已更新')

    # 6. co_creators.json：key 前缀重映射
    cc_file = lib_paths.data_path('author-info', 'co_creators.json')
    cc = lib_paths.load_json(cc_file, {})
    if cc:
        new_cc = {}
        changed_cc = 0
        for key, val in cc.items():
            kid, _, rest = key.partition('/')
            nk = f'{mapping.get(kid, kid)}/{rest}'
            new_cc[nk] = val
            if nk != key:
                changed_cc += 1
        lib_paths.save_json(cc_file, new_cc)
        print(f'  co_creators.json: {changed_cc} 个 key 已更新')

    print('\n重编号完成')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('args', nargs='*',
                        help='指定作者：<编号> [名称1 名称2 …]（全量对齐时不传）')
    parser.add_argument('--apply', action='store_true',
                        help='真正执行（git mv / 写 authors.json；默认 dry-run）')
    parser.add_argument('--renumber', action='store_true',
                        help='重新编号：按文件夹编号顺序分配连续编号（0000 起），并同步 '
                             'authors.json / models.json / co_creators.json 与文件夹重命名')
    args = parser.parse_args()

    if args.renumber:
        return cmd_renumber(args.apply)

    authors = load_authors()
    renamed = skipped = failed = 0

    if args.args:
        # 指定作者模式：<编号> [名称...]
        aid = args.args[0].zfill(4)
        names = args.args[1:]
        author_dir = author_dir_for(REPO_ROOT, aid)
        if not author_dir:
            print(f'[错误] 未找到作者目录: {aid}')
            return 2

        if names:
            # 有名称参数：更新 authors.json 的 name 数组（写盘）
            if args.apply:
                authors.setdefault(aid, {})['name'] = names
                save_authors(authors)
                print(f'  [执行] authors.json: {aid}.name = {names}')
            else:
                print(f'  [计划] authors.json: {aid}.name = {names}')
            expect = build_dir_name(aid, names)
        else:
            # 无名称参数：按 authors.json 现有 name 对齐目录名
            expect = build_dir_name(aid, authors.get(aid, {}).get('name') or [])

        if not expect:
            print(f'[提示] {aid} 无有效名称，目录保持: {author_dir.name}/')
        elif author_dir.name != expect:
            git_mv(REPO_ROOT, author_dir, REPO_ROOT / expect, args.apply)
            renamed += 1
        else:
            print(f'[一致] {aid} 目录名已是 {author_dir.name}/')
        print('(dry-run) 加 --apply 执行' if not args.apply else '')
        return 0

    # 全量对齐模式
    print(f'全量对齐作者目录（{"执行" if args.apply else "dry-run"}）:')
    for author_dir in iter_author_dirs(REPO_ROOT):
        name = author_dir.name
        aid = name[:4]
        info = authors.get(aid, {})
        names = info.get('name', []) if isinstance(info.get('name'), list) else []
        expect = build_dir_name(aid, names)
        if expect is None:
            skipped += 1
            print(f'  [跳过] {name}/（authors.json 无名称）')
        elif name != expect:
            if git_mv(REPO_ROOT, author_dir, REPO_ROOT / expect, args.apply):
                renamed += 1
            else:
                failed += 1
        else:
            skipped += 1  # 已一致，无需改名

    print(f'\n结果: 重命名 {renamed}, 跳过(一致/无名称) {skipped}, 失败 {failed}'
          f'（{"已执行" if args.apply else "dry-run"}）')
    return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())