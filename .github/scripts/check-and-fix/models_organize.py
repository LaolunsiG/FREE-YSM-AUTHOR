#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YSM 模型库整理工具（本仓库专用）——处理"已有库"的整理维护。

职责分工：organize_models.py 管入库（_Model-Inbox → Models/），本脚本管库内整理：
  0. 重复模型删除（--dedupe）：按 sha256 检测 Models/<作者>/ 下内容相同的 .ysm，
     保留一个（文件名与文件夹同名优先），--apply 删除。
  1. 重新分类（--reclassify）：扫描 Models/<编号>/<模型>/ 下 .ysm 的主作者，
     与目录编号比对；归属错误时报告，--apply 移动到正确作者。
  2. 合并重复作者（--merge-authors）：手动输入保留编号(keep) 与并入编号(drop)，
     先改 authors.json（名字/平台/标签并入 keep 并删 drop 条目），再整体移动模型、
     迁移 co_creators 键、重建索引。
  3. 空壳报告（--report-empty）：无 .ysm 的模型文件夹（空壳）与无模型作者目录。
  4. 缺失报告（--report-missing / --report-no-category / --report-no-preview）：
     统计无分类（作品前缀不在 character/*.json，含 Unknown_ 前缀）与无预览图的模型，显示路径；可分开查看；
     无分类报告对 Unknown_ 前缀的模型单独标注（已合并原 --report-unknown）。
  5. 指定检测目录（位置参数，可多个）：限定 dedupe 与报告类的扫描范围；
     默认 dedupe 按各模型文件夹独立检测（只检测同一文件夹下的文件，避免跨文件夹误删），
     --all-files 改为在整根内检测所有文件；报告类默认 Models + Blockbench-Models + Other-YSM-Models。
  6. 全部功能默认 dry-run（只读报告），--apply 才写盘；合并作者必须逐对确认。

用法:
  python .github/scripts/cli.py audit                    # 全量审计报告
  python .github/scripts/cli.py audit --dedupe          # 重复模型检测（默认同文件夹内；--apply 删除）
  python .github/scripts/cli.py audit --dedupe Models/0005 --apply   # 只检测某作者目录（同文件夹内）
  python .github/scripts/cli.py audit --dedupe --all-files Models/0055 --apply  # 该目录内所有文件一起检测
  python .github/scripts/cli.py audit --reclassify --apply   # 应用重新分类
  python .github/scripts/cli.py audit --merge-authors --apply # 合并作者（手动输入 keep/drop 编号）
  python .github/scripts/cli.py audit --report-empty     # 空壳报告
  python .github/scripts/cli.py audit --report-missing   # 缺失汇总（无分类+无预览图+完整）
  python .github/scripts/cli.py audit --report-no-category  # 无分类（前缀不在 character/*.json，含 Unknown 标注）
  python .github/scripts/cli.py audit --report-no-preview   # 只看无预览图
  python .github/scripts/cli.py audit Other-YSM-Models --report-no-preview  # 只检测指定目录
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 把 .github/scripts 加回 sys.path，保证 lib/ 与跨分类脚本可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import console as lib_console
from lib import models as lib_models
from lib import paths as lib_paths
from lib import previews as lib_previews
from lib import readme as lib_readme
from lib import ysm as lib_ysm
from lib.author_readme import format_author_name

WORKSPACE_ROOT = lib_paths.WORKSPACE_ROOT
MODELS_DIR = WORKSPACE_ROOT / 'Models'
# 报告类默认扫描根（--dir 未指定时）
DEFAULT_REPORT_ROOTS = [
    MODELS_DIR,
    WORKSPACE_ROOT / 'Blockbench-Models',
    WORKSPACE_ROOT / 'Other-YSM-Models',
]


def resolve_roots(dirs_arg: list[str] | None, default: list[Path] | None) -> list[Path] | None:
    """解析位置参数指定的检测根（可多个）；未指定返回 default（可为 None 表示不限定）。"""
    if not dirs_arg:
        return default
    roots: list[Path] = []
    for d in dirs_arg:
        p = Path(d)
        if not p.is_absolute():
            p = WORKSPACE_ROOT / p
        if p.is_dir():
            roots.append(p)
        else:
            print(f'[警告] 目录不存在，跳过: {p}')
    return roots or default


def is_author_dir(path: Path) -> bool:
    """4 位数字编号的作者目录。"""
    return path.is_dir() and re.fullmatch(r'\d{4}', path.name) is not None


def iter_author_dirs() -> list[Path]:
    return [d for d in sorted(MODELS_DIR.iterdir()) if is_author_dir(d)]


def iter_model_dirs(author_dir: Path):
    for d in sorted(author_dir.iterdir()):
        if d.is_dir() and not d.name.startswith('.') and d.name.lower() != 'previews':
            yield d


def model_owner(model_dir: Path) -> tuple[str | None, str]:
    """解析模型目录主作者名（复用 lib/ysm.py 统一实现）。"""
    return lib_ysm.model_owner(model_dir)


def model_author_blocks(model_dir: Path) -> list[dict]:
    """解析模型目录下用于归属判定的作者块。

    优先采用**含 role 信息**的 .ysm 的作者块（新版块结构），
    避免旧版单行无 role 的 authors（如 "作者A、作者B"）整体被当主作者导致误判
    （典型：AL_平海_20.ysm 把「星语TAT、雾雨波波沙」整串当主作者）；
    全部 .ysm 均无 role 时回退取第一个有块的 .ysm（保持旧行为）。
    """
    ysms = sorted(model_dir.glob('*.ysm')) + sorted(model_dir.glob('*.YSM'))
    # 第一遍：优先取第一个含非空 role 的块
    for f in ysms:
        blocks = (lib_ysm.extract_metadata(f, quiet=True).get('author_blocks') or [])
        if any(b.get('role') or '' for b in blocks):
            return blocks
    # 回退：全部无 role（旧版单行 authors），取第一个有块的 .ysm
    for f in ysms:
        blocks = (lib_ysm.extract_metadata(f, quiet=True).get('author_blocks') or [])
        if blocks:
            return blocks
    return []


def count_ysm(directory: Path) -> int:
    """目录（含子目录）下的 .ysm 文件数。"""
    return sum(1 for f in directory.rglob('*')
               if f.is_file() and f.suffix.lower() == '.ysm')


def file_sha256(path: Path) -> str:
    """文件 sha256（读取失败返回空串）。"""
    h = hashlib.sha256()
    try:
        with path.open('rb') as f:
            for chunk in iter(lambda: f.read(1 << 16), b''):
                h.update(chunk)
    except OSError:
        return ''
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 重复模型删除（--dedupe）
# ---------------------------------------------------------------------------
def collect_ysm_dirs(root: Path) -> list[Path]:
    """递归收集 root 下所有含 .ysm 的目录（模型文件夹 = dedupe 默认检测单元）。

    与 03_generate_other_models_index 的 walk 一致：含 .ysm 即视为模型文件夹（不再下钻）。
    """
    out: list[Path] = []

    def walk(d: Path) -> None:
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        if any(e.is_file() and e.suffix.lower() == '.ysm' for e in entries):
            out.append(d)
            return
        for e in entries:
            if e.is_dir() and not e.name.startswith('.') and e.name.lower() != 'previews':
                walk(e)

    walk(root)
    return out


def cmd_dedupe(apply: bool, roots: list[Path] | None = None, all_files: bool = False) -> int:
    """检测并删除内容重复的 .ysm（sha256 相同，保留一个）。

    检测单元：
      - 默认（all_files=False）：模型文件夹（最后一层含 .ysm 的目录）——只检测同一
        文件夹下的重复文件（如 0118/欧根亲王 下的 v1 与 v2），避免跨文件夹误删；
      - --all-files：整个根内所有文件一起检测（跨文件夹），roots 未指定时按各作者目录。
    保留规则：文件名与所属文件夹同名（规范命名）优先，否则路径排序第一个。
    默认 dry-run 预览；--apply 执行删除。
    """
    if all_files:
        # 整根内所有文件一起检测（跨子文件夹/跨作者）；roots 指定则用指定根，否则整个 Models
        units = roots if roots else [MODELS_DIR]
    else:
        units = []
        for root in (roots or [MODELS_DIR]):
            units.extend(collect_ysm_dirs(root))
    groups: list[tuple[str, list[Path]]] = []
    for unit in units:
        by_hash: dict[str, list[Path]] = {}
        for f in sorted(unit.rglob('*.ysm')):
            if f.is_file():
                by_hash.setdefault(file_sha256(f), []).append(f)
        # 每组只在当前单元内判定；跨单元的同内容文件不算重复
        for h, fs in by_hash.items():
            if len(fs) > 1:
                groups.append((h, fs))
    if not groups:
        print('未发现重复模型文件（sha256 均唯一）。')
        return 0
    n_files = sum(len(fs) for _h, fs in groups)
    print(f'发现 {len(groups)} 组内容重复（共 {n_files} 个文件，将保留 {len(groups)} 个）：')
    removed = 0
    for h, fs in sorted(groups, key=lambda kv: kv[1][0].as_posix()):
        keep = next((f for f in fs if f.stem == f.parent.name), fs[0])
        print(f'\n  组: sha256={h[:12]}…（{len(fs)} 个相同）')
        for f in fs:
            if f == keep:
                print(f'    [保留] {f.relative_to(WORKSPACE_ROOT)}（{f.stat().st_size} 字节）')
            else:
                print(f'    [删除] {f.relative_to(WORKSPACE_ROOT)}（{f.stat().st_size} 字节）')
        if apply:
            for f in fs:
                if f != keep:
                    f.unlink()
                    removed += 1
    if apply:
        print(f'\n已删除 {removed} 个重复文件。')
    else:
        print('\n预览模式（dry-run），未删除。加 --apply 执行删除。')
    return removed


# ---------------------------------------------------------------------------
# 重新分类（--reclassify）
# ---------------------------------------------------------------------------
def _model_author_candidates(model_blocks: list[dict], alias_to_id: dict) -> list[dict]:
    """把模型的每个作者块名字作为一个作者候选，逐个匹配编号。

    返回 [{name, block, aid}]；aid 为匹配到的作者编号或 None。
    旧版单行 authors（如 '木宁苒 & 星屑海螺'）整体作为一个作者名，不做拆分
    （无 role 信息不可靠，归属差异由用户手动处理）。
    """
    out: list[dict] = []
    for b in model_blocks:
        name = (b['name'] or '').strip()
        if not name:
            continue
        aid, _ = lib_readme.match_author_id(name, alias_to_id)
        out.append({'name': name, 'block': b, 'aid': aid})
    return out


def scan_reclassify() -> list[dict]:
    """按作者目录检测归属差异（以作者文件夹为单位）。

    对每个作者目录 cur 下的每个模型，解析其模型作者候选集合：
      - 当前作者 cur 在模型作者集合中（模型属于 cur）：
          · 多作者模型 -> 复制到其它作者目录（当前保留）
          · 单作者模型 -> 归属正确，不处理
      - 当前作者 cur 不在模型作者集合中（模型不属于 cur，放错目录）：
          · 多作者模型 -> 复制到所有匹配作者目录（不只第一个），并清理当前（移动）
          · 单作者模型 -> 报告，不自动处理（由用户手动）
    返回每条含 model_dir/cur/owners/cands/matched_ids/unmatched/multi/current_in/kind。
    """
    alias_to_id, _ = lib_readme.build_author_index(MODELS_DIR, WORKSPACE_ROOT / 'README.md')
    issues: list[dict] = []
    for author_dir in iter_author_dirs():
        for model_dir in iter_model_dirs(author_dir):
            blocks = model_author_blocks(model_dir)
            if not blocks:
                continue
            primary, model_blocks, _ = lib_ysm.classify_authors(blocks)
            if not primary:
                continue
            cands = _model_author_candidates(model_blocks, alias_to_id)
            if not cands:
                continue
            names = {c['name'] for c in cands}
            matched_ids = sorted({c['aid'] for c in cands if c['aid']})
            unmatched = [c for c in cands if not c['aid']]
            cur = author_dir.name
            multi = len(names) > 1            # 候选作者数 >1 = 多作者（含 '&' 复合）
            current_in = cur in matched_ids   # 当前作者是否是模型作者之一
            # 判定并归类
            if multi and (matched_ids or unmatched):
                # 多作者：cur 在其中 -> 复制其它；cur 不在 -> 移动（复制全部 + 清理当前）
                kind = 'copy' if current_in else 'move'
            elif not multi and not current_in:
                # 单作者且当前作者不在模型中：报告，不自动处理
                kind = 'report'
            else:
                continue  # 单作者且归属正确：不处理
            # 显示说明：目标作者（copy/move 排除当前目录；report 显示主作者归属）
            others = [a for a in matched_ids if a != cur]
            if kind == 'copy':
                dest = ', '.join(others) if others else '(无其它作者)'
            elif kind == 'move':
                dest = ', '.join(matched_ids) if matched_ids else '(无匹配)'
            else:
                dest = (f'主作者「{primary["name"]}」匹配 {", ".join(matched_ids)}'
                        if matched_ids else f'主作者「{primary["name"]}」未匹配')
            note = f"作者: {' / '.join(sorted(names))}  →  {dest}"
            if unmatched:
                note += f"   [未匹配作者: {' / '.join(c['name'] for c in unmatched)}]"
            issues.append({
                'model_dir': model_dir,
                'cur': cur,
                'owners': sorted(names),
                'owner': primary['name'],
                'cands': cands,
                'matched_ids': matched_ids,
                'unmatched': unmatched,
                'multi': multi,
                'current_in': current_in,
                'kind': kind,
                'note': note,
            })
    return issues


# ---------------------------------------------------------------------------
# 移动/复制辅助（需求 1/2/3：内容重复检查 / 未匹配作者新建 / 整文件夹 vs 单本体）
# ---------------------------------------------------------------------------
def next_free_author_id() -> str:
    """Models 下第一个空缺的 4 位作者编号（参照 01_organize_models）。"""
    existing = {int(d.name) for d in MODELS_DIR.iterdir()
                if d.is_dir() and re.fullmatch(r'\d{4}', d.name)}
    i = 0
    while i in existing:
        i += 1
    return f'{i:04d}'


def _first_ysm(model_dir: Path) -> Path | None:
    """模型文件夹内第一个 .ysm（reclassify 归属判定的对象，需求 3 的「模型本体」）。"""
    ysms = sorted(model_dir.glob('*.ysm')) + sorted(model_dir.glob('*.YSM'))
    return ysms[0] if ysms else None


def _folder_author_uniform(model_dir: Path, alias_to_id: dict) -> bool:
    """需求 3 判定：是否整文件夹移动/复制。

    True=单模型，或所有有作者信息的 .ysm 作者编号相同（可整文件夹搬）；
    False=存在作者不同，或都无作者信息（只搬单个模型本体）。
    """
    ysms = [f for f in model_dir.iterdir()
            if f.is_file() and f.suffix.lower() in ('.ysm', '.YSM')]
    if len(ysms) <= 1:
        return True
    ids: set[str] = set()
    for f in ysms:
        blocks = (lib_ysm.extract_metadata(f, quiet=True).get('author_blocks') or [])
        primary, model_blocks, _ = lib_ysm.classify_authors(blocks)
        if not primary:
            continue
        for b in model_blocks:
            aid, _ = lib_readme.match_author_id(b['name'], alias_to_id)
            if aid:
                ids.add(aid)
    if not ids:
        return False  # 都无作者信息 → 只本体
    return len(ids) == 1  # 作者全同 → 整文件夹；否则只本体


def _target_duplicate_note(target_author: str, model_dir: Path,
                           only_ysm: Path | None) -> str | None:
    """需求 1：目标作者下是否已有与待搬文件内容重复的 .ysm（sha256/大小判定，对齐 01）。

    返回冲突说明；None=无冲突。only_ysm=None 检查目录内全部 .ysm，否则只检查该文件。
    """
    dest_author = MODELS_DIR / target_author
    if not dest_author.is_dir():
        return None
    if only_ysm is None:
        files = [f for f in model_dir.rglob('*.ysm') if f.is_file()]
        files += [f for f in model_dir.rglob('*.YSM') if f.is_file()]
    else:
        files = [only_ysm]
    for src in files:
        try:
            src_size = src.stat().st_size
        except OSError:
            continue
        src_sha = file_sha256(src)
        for sub in dest_author.iterdir():
            if not sub.is_dir() or sub.name.startswith('.'):
                continue
            for ysm in sub.rglob('*.ysm'):
                try:
                    if ysm.stat().st_size != src_size:
                        continue
                except OSError:
                    continue
                if file_sha256(ysm) == src_sha:
                    return f'目标作者已有相同内容: {ysm.relative_to(WORKSPACE_ROOT)}'
    return None


def _merge_author_entry(entry: dict, block: dict | None, names: list[str]) -> bool:
    """补缺合并：别名去重追加末尾；平台只补缺失的 http 键（对齐 01_organize_models）。

    返回是否有变化。复用已有作者编号时调用，避免信息丢失。
    """
    changed = False
    known = {lib_readme.normalize_alias(n) for n in entry.get('name', [])}
    for alias in names:
        if alias and lib_readme.normalize_alias(alias) not in known:
            entry.setdefault('name', []).append(alias)
            known.add(lib_readme.normalize_alias(alias))
            changed = True
    platforms = entry.setdefault('platforms', {})
    for key, value in ((block or {}).get('contacts') or {}).items():
        if key not in platforms and isinstance(value, str) and value.startswith('http'):
            platforms[key] = value
            changed = True
    return changed


def _ensure_author(name: str, block: dict | None,
                   cache: dict[str, str] | None = None) -> str:
    """需求 2：未匹配作者参照 01 新建编号 + 登记 authors.json + 建目录（--apply 才调用）。

    防重复建目录：同一作者在多个模型里都被判为未匹配时，本次运行只建一次——
    先查 cache（本次运行已建），再查 authors.json 已有同名（归一化匹配）；
    命中则复用编号（补缺合并），未命中才新建并登记 cache。
    """
    cache = cache if cache is not None else {}
    names = [t.strip() for t in format_author_name(name).split('|') if t.strip()]
    keys = {lib_readme.normalize_alias(n) for n in names}
    keys.discard('')

    # 1) 本次运行已新建：复用编号，避免同一作者重复建目录
    for k in keys:
        if k in cache:
            return cache[k]

    path = lib_paths.data_path('author-info', 'authors.json')
    data = lib_paths.load_json(path, {})
    authors = data.setdefault('authors', {})

    # 2) authors.json 已有同名（归一化匹配）：复用编号 + 补缺合并
    for aid, entry in authors.items():
        entry_keys = {lib_readme.normalize_alias(n) for n in (entry.get('name') or [])}
        if entry_keys & keys:
            _merge_author_entry(entry, block, names)
            lib_paths.save_json(path, data)
            for k in keys:
                cache.setdefault(k, aid)
            print(f"  复用作者 {aid}（{name}，authors.json 已存在）")
            return aid

    # 3) 真新作者：分配编号 + 登记 authors.json + 建目录
    new_id = next_free_author_id()
    authors[new_id] = {
        'name': names,
        'readme': f'Models/{new_id}/README.md',
        'platforms': dict((block or {}).get('contacts') or {}),
    }
    authors = dict(sorted(authors.items(), key=lambda kv: int(kv[0])))
    data['authors'] = authors
    lib_paths.save_json(path, data)
    (MODELS_DIR / new_id).mkdir(parents=True, exist_ok=True)
    for k in keys:
        cache.setdefault(k, new_id)
    print(f"  新建作者目录 {new_id}（{name}）")
    return new_id


def move_model_dir(model_dir: Path, target_author: str,
                   only_ysm: Path | None = None) -> str:
    """把模型目录（或仅单个 .ysm 本体）移到 Models/<target_author>/ 下。

    移动前做内容重复检查（需求 1，对齐 01 的 hash/大小判定）；处理目标同名/同模型冲突。
    only_ysm=None 移动整个目录；否则只移动该 .ysm（需求 3：作者不同时只搬本体）。
    """
    dup = _target_duplicate_note(target_author, model_dir, only_ysm)
    if dup:
        return f'[冲突] {dup}（跳过，避免重复）'
    dest_author = MODELS_DIR / target_author
    if only_ysm is not None:
        # 单本体：目标作者下已存在同名/同模型目录时，空壳（无 .ysm）→ 填充；否则冲突
        if dest_author.is_dir():
            for sub in dest_author.iterdir():
                if sub.is_dir() and lib_models.same_model(model_dir.name, sub.name):
                    has_ysm = any(f.is_file() and f.suffix.lower() in ('.ysm', '.YSM')
                                  for f in sub.rglob('*'))
                    if not has_ysm:
                        # 空壳：把模型本体填充进已有目录（避免误删真实数据，与整目录分支一致）
                        target = sub / only_ysm.name
                        if target.exists():
                            return f'[冲突] 目标已存在: {target.relative_to(WORKSPACE_ROOT)}'
                        try:
                            shutil.move(str(only_ysm), str(target))
                        except OSError as e:
                            return f'[错误] 移动失败: {only_ysm.name}: {e}'
                        return (f'[填充] {only_ysm.relative_to(WORKSPACE_ROOT)} -> '
                                f'{target.relative_to(WORKSPACE_ROOT)}（目标为空壳，合并 .ysm）')
                    return f'[冲突] 目标作者下已有同模型: {sub.relative_to(WORKSPACE_ROOT)}'
        dest_dir = dest_author / model_dir.name
        try:
            dest_author.mkdir(parents=True, exist_ok=True)
            dest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return f'[错误] 创建目录失败: {e}'
        target = dest_dir / only_ysm.name
        if target.exists():
            return f'[冲突] 目标已存在: {target.relative_to(WORKSPACE_ROOT)}'
        try:
            shutil.move(str(only_ysm), str(target))
        except OSError as e:
            return f'[错误] 移动失败: {only_ysm.name}: {e}'
        return f'[移动] {only_ysm.relative_to(WORKSPACE_ROOT)} -> {target.relative_to(WORKSPACE_ROOT)}'
    dest = dest_author / model_dir.name
    if dest.exists():
        # 目标同名目录为空壳（无 .ysm）：填充而非跳过（与 01 find_duplicate 一致）
        has_ysm = any(f.is_file() and f.suffix.lower() in ('.ysm', '.YSM')
                      for f in dest.rglob('*'))
        if not has_ysm:
            try:
                for item in sorted(model_dir.iterdir()):
                    if item.is_dir() and item.name == 'previews':
                        # previews 并入目标 previews（跳过已存在同名文件）
                        for p in item.rglob('*'):
                            if p.is_file():
                                dp = dest / 'previews' / p.relative_to(item)
                                if not dp.exists():
                                    dp.parent.mkdir(parents=True, exist_ok=True)
                                    shutil.move(str(p), str(dp))
                    elif item.is_file() and item.name.lower() == 'readme.md':
                        continue  # 保留目标 README
                    else:
                        dp = dest / item.name
                        if not dp.exists():
                            shutil.move(str(item), str(dp))
                # 源目录已搬空（仅剩 README/空 previews）：整体清理
                leftover = [x for x in model_dir.iterdir()]
                if all(x.name.lower() in ('readme.md', 'previews') for x in leftover):
                    shutil.rmtree(str(model_dir))
            except OSError as e:
                return f'[错误] 空壳填充失败: {model_dir.name}: {e}'
            return (f'[填充] {model_dir.relative_to(WORKSPACE_ROOT)} -> '
                    f'{dest.relative_to(WORKSPACE_ROOT)}（目标为空壳，合并 .ysm）')
        return f'[冲突] 目标已存在: {dest.relative_to(WORKSPACE_ROOT)}'
    # 目标作者下已有同模型（same_model）目录：提示不自动合并（避免误并不同版本）
    if dest_author.is_dir():
        for sub in dest_author.iterdir():
            if sub.is_dir() and sub.name != model_dir.name \
                    and lib_models.same_model(model_dir.name, sub.name):
                return f'[冲突] 目标作者下已有同模型: {sub.relative_to(WORKSPACE_ROOT)}'
    try:
        dest_author.mkdir(parents=True, exist_ok=True)
        shutil.move(str(model_dir), str(dest))
    except OSError as e:
        return f'[错误] 移动失败: {model_dir.name}: {e}'
    return f'[移动] {model_dir.relative_to(WORKSPACE_ROOT)} -> {dest.relative_to(WORKSPACE_ROOT)}'


def reclassify(apply: bool) -> int:
    """重新分类：以作者目录为单位检测模型归属（用户规则）。

    对每个作者目录 cur 下的每个模型：
      - 无作者信息：跳过
      - 多作者模型：
          · cur 在模型作者中 → 复制到其它匹配作者目录（当前保留）
          · cur 不在模型作者中 → 移动到所有匹配作者目录（不只第一个，当前不留）
      - 单作者模型：
          · 主作者匹配 cur → 不处理
          · 主作者不匹配 → 报告，由用户手动处理
    """
    issues = scan_reclassify()
    if not issues:
        print('重新分类: 未发现归属差异（所有模型归属正确且多作者副本完整）。')
        return 0

    copies = [it for it in issues if it['kind'] == 'copy']
    moves = [it for it in issues if it['kind'] == 'move']
    reports = [it for it in issues if it['kind'] == 'report']
    print(f'重新分类: 发现 {len(issues)} 个待处理（复制 {len(copies)} / 移动 {len(moves)} / 报告 {len(reports)}）:')
    for it in issues:
        rel = it['model_dir'].relative_to(WORKSPACE_ROOT)
        verb = {'copy': '复制', 'move': '移动', 'report': '报告'}[it['kind']]
        print(f"  [{verb}] {rel}  {it['note']}")
    if not apply:
        print('dry-run: 未执行;加 --apply 逐项确认后执行')
        return len(issues)

    # 作者索引（归属/新建判定用）
    alias_to_id, _ = lib_readme.build_author_index(MODELS_DIR, WORKSPACE_ROOT / 'README.md')
    # 本次运行新建/复用的作者缓存：同一作者在多个模型里共享编号，避免重复建目录
    new_author_cache: dict[str, str] = {}
    copied = moved = 0

    def _resolve_targets(it: dict, exclude_cur: bool) -> list[str]:
        """解析复制/移动目标作者编号：匹配到的（可排除当前）+ 未匹配作者新建。"""
        targets = [a for a in it['matched_ids'] if not (exclude_cur and a == it['cur'])]
        for c in it['unmatched']:
            targets.append(_ensure_author(c['name'], c['block'], new_author_cache))
        return targets

    # 1) 复制：多作者模型，cur 在模型作者中 → 复制到其它匹配作者目录（当前保留）
    for i, it in enumerate(copies, 1):
        rel = it['model_dir'].relative_to(WORKSPACE_ROOT)
        targets = _resolve_targets(it, exclude_cur=True)
        if not targets:
            continue
        ans = _ask(f"[复制{i}/{len(copies)}] 复制 {rel} 到作者 {', '.join(targets)}？"
                   f"（多作者模型，当前保留在 {it['cur']}；未匹配作者将新建编号） (y/n/q): ").lower()
        if ans in ('q', 'quit'):
            break
        if ans not in ('y', 'yes'):
            continue
        # 需求 3：文件夹内多模型作者不同 → 只复制单个模型本体
        uniform = _folder_author_uniform(it['model_dir'], alias_to_id)
        only = None if uniform else _first_ysm(it['model_dir'])
        if only is not None:
            print(f"  （文件夹内多模型作者不同，只复制模型本体 {only.name}）")
        for aid in targets:
            # 需求 1：目标作者已有相同内容 → 跳过
            dup = _target_duplicate_note(aid, it['model_dir'], only)
            if dup:
                print(f"  [跳过] {dup}")
                continue
            if only is not None:
                dest_dir = MODELS_DIR / aid / it['model_dir'].name
                try:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    target = dest_dir / only.name
                    if target.exists():
                        print(f"  [跳过] 目标已存在: {target.relative_to(WORKSPACE_ROOT)}")
                        continue
                    shutil.copy2(str(only), str(target))
                    copied += 1
                    print(f"  [复制] {only.relative_to(WORKSPACE_ROOT)} -> {target.relative_to(WORKSPACE_ROOT)}")
                except OSError as e:
                    print(f"  [跳过] 复制失败: {e}")
                continue
            dest = MODELS_DIR / aid / it['model_dir'].name
            try:
                shutil.copytree(str(it['model_dir']), str(dest))
                copied += 1
                print(f"  [复制] {rel} -> Models/{aid}/")
            except FileExistsError:
                print(f"  [跳过] 目标已存在: Models/{aid}/{it['model_dir'].name}")
            except OSError as e:
                print(f"  [错误] 复制失败: {rel} -> Models/{aid}/: {e}")

    # 2) 移动：多作者模型，cur 不在模型作者中 → 复制到所有匹配作者目录，并从当前移除
    for i, it in enumerate(moves, 1):
        rel = it['model_dir'].relative_to(WORKSPACE_ROOT)
        targets = _resolve_targets(it, exclude_cur=False)
        if not targets:
            print(f"  [跳过] {rel}（无匹配作者可移动）")
            continue
        ans = _ask(f"[移动{i}/{len(moves)}] 移动 {rel} 到作者 {', '.join(targets)}？"
                   f"（模型不属于作者 {it['cur']}，将复制到上述作者并从当前移除） (y/n/q): ").lower()
        if ans in ('q', 'quit'):
            break
        if ans not in ('y', 'yes'):
            continue
        # 需求 3：文件夹内多模型作者不同 → 只搬单个模型本体
        uniform = _folder_author_uniform(it['model_dir'], alias_to_id)
        only = None if uniform else _first_ysm(it['model_dir'])
        if only is not None:
            print(f"  （文件夹内多模型作者不同，只移动模型本体 {only.name}）")
        ok = 0
        for aid in targets:
            dup = _target_duplicate_note(aid, it['model_dir'], only)
            if dup:
                print(f"  [跳过] {dup}")
                continue
            if only is not None:
                dest_dir = MODELS_DIR / aid / it['model_dir'].name
                try:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    target = dest_dir / only.name
                    if target.exists():
                        print(f"  [跳过] 目标已存在: {target.relative_to(WORKSPACE_ROOT)}")
                        continue
                    shutil.copy2(str(only), str(target))
                    ok += 1
                    print(f"  [复制] {only.relative_to(WORKSPACE_ROOT)} -> {target.relative_to(WORKSPACE_ROOT)}")
                except OSError as e:
                    print(f"  [跳过] 复制失败: {e}")
                continue
            dest = MODELS_DIR / aid / it['model_dir'].name
            try:
                shutil.copytree(str(it['model_dir']), str(dest))
                ok += 1
                print(f"  [复制] {rel} -> Models/{aid}/")
            except FileExistsError:
                print(f"  [跳过] 目标已存在: Models/{aid}/{it['model_dir'].name}")
            except OSError as e:
                print(f"  [错误] 复制失败: {rel} -> Models/{aid}/: {e}")
        # 全部目标复制成功后，从当前作者目录移除（移动语义：当前不留）
        if ok == len(targets) and ok > 0:
            try:
                if only is not None:
                    only.unlink()
                    moved += 1
                    print(f"  [移动] 已从 {it['cur']} 移除 {only.relative_to(WORKSPACE_ROOT)}")
                else:
                    shutil.rmtree(str(it['model_dir']))
                    moved += 1
                    print(f"  [移动] 已从 {it['cur']} 移除整个模型目录")
            except OSError as e:
                print(f"  [错误] 移除失败: {e}")

    # 3) 报告：单作者模型，主作者不匹配当前目录 → 报告，不自动处理
    for it in reports:
        rel = it['model_dir'].relative_to(WORKSPACE_ROOT)
        print(f"  [报告] {rel} 不属于作者 {it['cur']}（主作者「{it['owner']}」），需手动处理")

    print(f'重新分类完成: 复制 {copied} 个，移动 {moved} 个，报告 {len(reports)} 个')
    return copied + moved


# ---------------------------------------------------------------------------
# 合并重复作者（--merge-authors）
# ---------------------------------------------------------------------------
def merge_authors_ids(keep: str, drop: str, dry_run: bool = False) -> str:
    """把 drop 作者并入 keep：先改 authors.json（名字/平台/标签并入 keep 去重 + 删 drop 条目），
    再整体移动 drop 目录下所有模型，迁移 co_creators，删除 drop 目录。

    dry_run=True 只预览将写入的 authors.json 变更与移动计划（不动盘）。
    """
    results: list[str] = []
    path = lib_paths.data_path('author-info', 'authors.json')
    data = lib_paths.load_json(path, {})
    authors = data.setdefault('authors', {})
    if keep not in authors or drop not in authors:
        return f'[错误] 编号不存在: keep={keep}, drop={drop}'
    keep_entry = authors[keep]
    drop_entry = authors[drop]

    # 1. authors.json：名字/平台/标签并入 keep，删 drop 条目
    keep_norms = {lib_readme.normalize_alias(x) for x in keep_entry.get('name', [])}
    name_added = [n for n in drop_entry.get('name', [])
                  if lib_readme.normalize_alias(n) not in keep_norms]
    plat_added = [k for k in (drop_entry.get('platforms') or {})
                  if k not in keep_entry.get('platforms', {})]
    tag_added = [t for t in (drop_entry.get('tags') or [])
                 if t not in keep_entry.get('tags', [])]
    if dry_run:
        results.append(f'[计划·authors.json] {drop} 并入 {keep}:')
        results.append(f'    追加名字: {", ".join(name_added) if name_added else "(无)"}')
        results.append(f'    补平台键: {", ".join(plat_added) if plat_added else "(无)"}')
        results.append(f'    补标签: {", ".join(tag_added) if tag_added else "(无)"}')
        results.append(f'    删除 {drop} 条目')
    else:
        keep_entry.setdefault('name', []).extend(name_added)
        keep_entry.setdefault('platforms', {}).update(
            {k: drop_entry['platforms'][k] for k in plat_added})
        keep_entry.setdefault('tags', []).extend(tag_added)
        authors.pop(drop)
        authors = dict(sorted(authors.items(), key=lambda kv: int(kv[0])))
        data['authors'] = authors
        lib_paths.save_json(path, data)
        results.append(f'[authors.json] {drop} 并入 {keep}（名字/平台/标签已合并，删除 {drop} 条目）')

    # 2. 整体移动 drop 目录下模型到 keep
    keep_dir, drop_dir = MODELS_DIR / keep, MODELS_DIR / drop
    if drop_dir.is_dir():
        for model_dir in sorted(drop_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            if dry_run:
                results.append(f'  [计划·移动] {model_dir.name} -> Models/{keep}/')
            else:
                results.append(move_model_dir(model_dir, keep))

    # 3. 迁移 co_creators 键 drop/xxx -> keep/xxx
    meta_path = lib_paths.data_path('author-info', 'co_creators.json')
    meta = lib_paths.load_json(meta_path, {})
    to_migrate = [k for k in meta if k.startswith(f'{drop}/')]
    if to_migrate:
        if dry_run:
            results.append(f'  [计划·co_creators] 迁移 {len(to_migrate)} 条键')
        else:
            for key in to_migrate:
                meta[f'{keep}/{key.split("/", 1)[1]}'] = meta.pop(key)
            lib_paths.save_json(meta_path, meta)
            results.append(f'[co_creators] 迁移 {len(to_migrate)} 条键')

    # 4. 删除只剩 README（或空）的 drop 目录
    if drop_dir.is_dir():
        if dry_run:
            # dry-run 时模型子目录尚未移动：删除判定只看剩余的非模型文件
            moved_dirs = [d for d in drop_dir.iterdir() if d.is_dir()]
            remaining = [p for p in drop_dir.rglob('*') if p.is_file()
                         and not any(d in p.parents for d in moved_dirs)]
        else:
            remaining = [p for p in drop_dir.rglob('*') if p.is_file()]
        if not remaining or (len(remaining) == 1 and remaining[0].name.lower() == 'readme.md'):
            if dry_run:
                results.append(f'  [计划·删除] 作者目录 Models/{drop}')
            else:
                try:
                    shutil.rmtree(str(drop_dir))
                    results.append(f'[删除] 作者目录 Models/{drop}')
                except OSError as e:
                    results.append(f'[错误] 删除失败 Models/{drop}: {e}')
        else:
            results.append(f'[保留] Models/{drop} 仍有文件（未删除，需人工处理）')
    return '\n  '.join(results)


def merge_authors_flow(apply: bool) -> int:
    """手动输入 keep/drop 编号合并作者：先改 authors.json，再整体移动（交互循环）。

    dry-run 预览将写入的 authors.json 变更与移动计划；--apply 才执行。
    """
    print('合并作者（手动）：输入 保留编号(keep) 与 并入编号(drop)；q 退出。')
    merged = 0
    while True:
        keep = _ask('  保留作者编号（keep，如 0011）或 q 退出: ').strip()
        if keep.lower() in ('q', 'quit', ''):
            break
        if not re.fullmatch(r'\d{4}', keep):
            print('  编号需为 4 位数字（如 0011）')
            continue
        drop = _ask(f'  要并入 {keep} 的作者编号（drop）或 q 退出: ').strip()
        if drop.lower() in ('q', 'quit', ''):
            break
        if not re.fullmatch(r'\d{4}', drop):
            print('  编号需为 4 位数字')
            continue
        if keep == drop:
            print('  keep 与 drop 不能相同')
            continue
        print('  ' + merge_authors_ids(keep, drop, dry_run=not apply).replace('\n', '\n  '))
        if apply:
            merged += 1
    if merged and apply:
        _rebuild_indexes()
    print(f'合并作者: 共 {merged} 对已合并' if apply else 'dry-run: 未执行')
    return merged


def _rebuild_indexes() -> None:
    """合并后重建集中作者数据与根 README 作者表（drop 作者目录已删，索引需同步）。"""
    for script, args, label in [('models_organize/03_generate_root_readme.py', ['--data'], '作者数据 authors.json'),
                                ('models_organize/03_generate_root_readme.py', ['--author'], '根 README 作者表')]:
        p = WORKSPACE_ROOT / '.github' / 'scripts' / script
        if not p.is_file():
            print(f'  [警告] 未找到 {p}，跳过{label}重建')
            continue
        print(f'  重建{label}...')
        subprocess.run([sys.executable, str(p), *args], cwd=WORKSPACE_ROOT, check=False)


def _ask(prompt: str) -> str:
    """安全交互输入（复用 lib/console.py 统一实现，与 lib/kb 一致）。"""
    return lib_console.ask(prompt)


# ---------------------------------------------------------------------------
# 空壳报告（--report-empty）
# ---------------------------------------------------------------------------
def report_empty() -> int:
    """报告无 .ysm 的模型文件夹（空壳）与无模型作者目录。返回空壳数。"""
    empty_models: list[Path] = []
    empty_authors: list[Path] = []
    for author_dir in iter_author_dirs():
        models = list(iter_model_dirs(author_dir))
        if not models:
            empty_authors.append(author_dir)
            continue
        for model_dir in models:
            if count_ysm(model_dir) == 0:
                empty_models.append(model_dir)
    print(f'空壳报告:')
    print(f'  无模型作者目录 {len(empty_authors)} 个:')
    for d in empty_authors:
        print(f'    {d.relative_to(WORKSPACE_ROOT)}')
    print(f'  无 .ysm 的模型文件夹 {len(empty_models)} 个:')
    for d in empty_models:
        print(f'    {d.relative_to(WORKSPACE_ROOT)}')
    return len(empty_models) + len(empty_authors)


# ---------------------------------------------------------------------------
# 缺失报告：无分类 / 无预览图（可分开查看）
# ---------------------------------------------------------------------------
def iter_all_model_dirs(roots: list[Path] | None = None) -> list[Path]:
    """所有模型目录：Models/<作者>/<模型>（两层）+ 其他根（一层）。

    roots 默认 [Models, Blockbench-Models, Other-YSM-Models]；--dir 指定后仅用指定根。
    """
    roots = roots or DEFAULT_REPORT_ROOTS
    dirs: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        if root == MODELS_DIR:
            for author in iter_author_dirs():
                dirs.extend(iter_model_dirs(author))
        else:
            for d in sorted(root.iterdir()):
                if d.is_dir() and not d.name.startswith('.') and d.name.lower() != 'previews':
                    dirs.append(d)
    return dirs


def scan_missing(roots: list[Path] | None = None) -> tuple[list[Path], list[Path], int]:
    """一次扫描所有模型目录：返回 (无分类列表, 无预览图列表, 总数)。

    作品集合直接取自 character/*.json 的顶层键（单一数据源，不再依赖
    category_map.json；合并后不再有独立的 works.json）。
    """
    cat_keys: set[str] = set()
    rdir = lib_paths.data_path('model-info', 'character')
    if rdir.is_dir():
        for f in rdir.glob('*.json'):
            content = lib_paths.load_json(f, {})
            # 新格式：作品键由 work.abbr 决定（读取不依赖文件名）
            work = content.get('work') if isinstance(content, dict) else None
            if isinstance(work, dict):
                abbr = work.get('abbr')
                if not abbr:
                    # 旧格式兜底：name 若是字符串即作品键（新格式 name 是 dict，忽略）
                    nm = work.get('name')
                    abbr = nm if isinstance(nm, str) else ''
                if abbr:
                    cat_keys.add(str(abbr).lower())
    no_cat: list[Path] = []
    no_preview: list[Path] = []
    total = 0
    for model_dir in iter_all_model_dirs(roots):
        total += 1
        prefix = model_dir.name.split('_')[0].strip().lower()
        if prefix and prefix not in cat_keys:
            no_cat.append(model_dir)
        if not lib_previews.collect_preview_images(model_dir):
            no_preview.append(model_dir)
    return no_cat, no_preview, total


def report_no_category(roots: list[Path] | None = None) -> int:
    """报告"待确认归属"的模型：真无分类（前缀不在 character/*.json）+
    Unknown_ 前缀（合并原 --report-unknown，即使 Unknown 被收录为分类也列出）。

    Unknown 前缀模型单独标注 [Unknown]。
    """
    no_cat, _, total = scan_missing(roots)
    # Unknown 前缀模型：即使 Unknown 被收录为分类，仍属"待确认归属"，并入本报告
    unknown_all = [d for d in iter_all_model_dirs(roots) if 'unknown' in d.name.lower()]
    unknown_only = [d for d in unknown_all if d not in no_cat]
    combined = sorted(set(no_cat) | set(unknown_only), key=lambda p: str(p))
    print(f'待确认归属报告（共 {total} 个模型目录; 真无分类 {len(no_cat)} + Unknown 前缀 {len(unknown_only)}）:')
    print(f'  共 {len(combined)} 个:')
    for d in combined:
        tag = '[Unknown] ' if 'unknown' in d.name.lower() else ''
        print(f'    {tag}{d.relative_to(WORKSPACE_ROOT)}')
    return len(combined)


def report_no_preview(roots: list[Path] | None = None) -> int:
    """报告无预览图的模型，显示路径。返回数。"""
    _, no_preview, total = scan_missing(roots)
    print(f'无预览图报告（共 {total} 个模型目录）:')
    print(f'  无预览图模型 {len(no_preview)} 个:')
    for d in no_preview:
        print(f'    {d.relative_to(WORKSPACE_ROOT)}')
    return len(no_preview)


def report_missing(roots: list[Path] | None = None) -> int:
    """汇总报告：无分类 + 无预览图 + 完整模型。"""
    no_cat, no_preview, total = scan_missing(roots)
    no_cat_set = set(no_cat)
    no_preview_set = set(no_preview)
    ok = total - len(no_cat_set | no_preview_set)
    print(f'缺失报告（共 {total} 个模型目录）:')
    print(f'  无分类模型 {len(no_cat)} 个（作品前缀不在 character/*.json）:')
    for d in no_cat:
        print(f'    {d.relative_to(WORKSPACE_ROOT)}')
    print(f'  无预览图模型 {len(no_preview)} 个:')
    for d in no_preview:
        print(f'    {d.relative_to(WORKSPACE_ROOT)}')
    print(f'  既有分类又有预览图: {ok} 个')
    return len(no_cat) + len(no_preview)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--reclassify', action='store_true', help='重新分类（扫 Models 校验作者归属）')
    parser.add_argument('--dedupe', action='store_true',
                        help='检测并删除内容重复的模型（sha256 相同；默认同文件夹内，--all-files 整根；--apply 执行删除）')
    parser.add_argument('--all-files', action='store_true',
                        help='dedupe：在整根内检测所有文件（跨文件夹）；默认只检测同文件夹下的文件')
    parser.add_argument('--merge-authors', action='store_true', help='合并重复作者（逐对确认）')
    parser.add_argument('--report-empty', action='store_true', help='空壳报告（无 .ysm 的文件夹）')
    parser.add_argument('--report-missing', action='store_true',
                        help='缺失汇总（无分类 + 无预览图 + 完整，显示路径）')
    parser.add_argument('--report-no-category', action='store_true',
                        help='无分类报告（作品前缀不在 character/*.json，显示路径）')
    parser.add_argument('--report-no-preview', action='store_true',
                        help='无预览图报告（显示路径）')
    parser.add_argument('paths', nargs='*', default=None,
                        help='指定检测根目录（可多个，相对仓库根或绝对路径）；不传则默认')
    parser.add_argument('--apply', action='store_true', help='真正执行（默认 dry-run 只报告）')
    args = parser.parse_args()

    if not MODELS_DIR.is_dir():
        print(f'错误: {MODELS_DIR} 目录不存在。')
        return 2

    if args.dedupe:
        # 默认按模型文件夹内检测；--all-files 改为整根检测（跨文件夹）
        return cmd_dedupe(args.apply, resolve_roots(args.paths, None), args.all_files)
    if args.reclassify:
        return reclassify(args.apply)
    if args.merge_authors:
        return merge_authors_flow(args.apply)
    if args.report_empty:
        return report_empty()
    if args.report_no_category:
        return report_no_category(resolve_roots(args.paths, DEFAULT_REPORT_ROOTS))
    if args.report_no_preview:
        return report_no_preview(resolve_roots(args.paths, DEFAULT_REPORT_ROOTS))
    if args.report_missing:
        return report_missing(resolve_roots(args.paths, DEFAULT_REPORT_ROOTS))

    # 默认：全量审计报告（只读）
    print('== 全量审计（只读）==')
    issues = scan_reclassify()
    print(f'重新分类差异: {len(issues)} 个')
    for it in issues[:20]:
        rel = it['model_dir'].relative_to(WORKSPACE_ROOT)
        print(f"  {rel}  主作者「{it['owner']}」-> {it['matched']}（当前 {it['cur']}）")
    if len(issues) > 20:
        print(f'  ...（其余 {len(issues) - 20} 条略）')
    print('\n用法提示: --reclassify / --merge-authors / --report-empty 配合 --apply 执行')
    return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
