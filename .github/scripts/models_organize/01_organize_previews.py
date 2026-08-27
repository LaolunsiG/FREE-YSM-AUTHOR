#!/usr/bin/env python3
"""整理模型文件夹中的预览图片,统一归入 previews/ 子目录并规范命名。

设计目标(与 generate_model_readmes.py 保持兼容):
- 移动模式(默认):只处理文件名为 preview* 的图片(与 generate_model_readmes.py
  的 is_preview_image 规则一致),将其移入 previews/
- 重命名模式(--rename):把模型目录顶层与 previews/ 下**所有**图片统一重命名为
  `preview<两位数字>.<扩展名>`,顶层图片一并归入 previews/;已符合规范命名的文件
  保持原名,编号自动跳过已被占用的序号(幂等)
- 移动/重命名后如需更新 README 引用,加 --with-gen-readmes 联动重跑 generate_model_readmes.py
  注意:generate_model_readmes.py 会整体模板化重写模型 README,模板外的手工内容会被覆盖
- 安全:默认 dry-run,加 --apply 才真正执行;目标已存在同名文件时跳过并计数

退出码:0 成功;1 编号耗尽等错误;2 目录不存在;3 存在未处理的冲突文件

用法:
    python ".github/scripts/models_organize/01_organize_previews.py" --rename               # 预览（默认扫描 Models + Other-YSM-Models）
    python ".github/scripts/models_organize/01_organize_previews.py" --apply [--rename]        # 真正移动/重命名（不联动 README）
    python ".github/scripts/models_organize/01_organize_previews.py" --apply [--rename] --with-gen-readmes   # 执行后联动重跑模型 README
    python ".github/scripts/models_organize/01_organize_previews.py" <目录>...                 # 指定根目录，自动检查其下需整理的模型目录（可多个，相对仓库根）

默认（不传 <目录>）扫描仓库根（作者目录平铺在根下）；传任意目录则把该目录作为根，自动递归
检查其下需要整理预览图的模型目录（其他资源根目录也可显式传入）。
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
# 脚本按流程阶段分类到 scripts/<类别>/ 子目录：把 .github/scripts 加回 sys.path，
# 保证 lib/ 与跨分类脚本可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from lib import paths as lib_paths
from lib import previews as lib_previews

WORKSPACE_ROOT = lib_paths.WORKSPACE_ROOT
SCRIPT_DIR = Path(__file__).resolve().parent

# 默认扫描根目录（无路径参数时）：仓库根。仓库已重构为「根目录/<编号>-作者/模型/」平铺结构，
# 作者目录（如 0001-02Bunny,蓝玫瑰）直接位于仓库根，模型目录位于作者目录下。
# 其他资源根目录（若存在）可在路径参数中显式传入。
DEFAULT_ROOTS = [WORKSPACE_ROOT]
# 预览图识别规则统一复用 lib/previews.py
IMAGE_EXTS = lib_previews.IMAGE_EXTS
PREVIEW_MARKER = lib_previews.PREVIEW_MARKER
PREVIEWS_DIRNAME = lib_previews.PREVIEWS_DIRNAME
is_preview_image = lib_previews.is_preview_image
is_image_file = lib_previews.is_image_file
find_previews_dir = lib_previews.find_previews_dir
# 模型资源子目录：其中的图片（纹理/贴图/头像/子模型/声音等）不是模型预览图，
# 递归扫描时必须跳过，避免 textures/previews/、avatar/previews/ 等被误判为模型预览目录。
RESOURCE_DIR_NAMES = {'textures', 'models', 'animations', 'sounds', 'lang', 'scripts',
                      'avatar', 'avatars', 'icons', 'icon'}
# 仓库内与模型无关、不应被当作模型根扫描的目录名（小写比较）：
# Docs 为文档目录（内含示意图）；隐藏目录(. 开头)与暂存区(_ 开头)另由 should_skip_dir 处理。
NON_MODEL_DIR_NAMES = {'docs'}
# 规范命名:preview + 两位数字(01~99),如 preview01.png
PREVIEW_NUMBER_RE = re.compile(r'^preview(\d{2})$', re.IGNORECASE)
MAX_PREVIEW_INDEX = 99
# 生成脚本在 models_organize/ 下(与 lib/ 同级目录体系)
GENERATE_SCRIPT = SCRIPT_DIR.parent / 'models_organize' / '03_generate_model_readmes.py'


@dataclass(frozen=True)
class MovePlan:
    """单个文件操作,等价于 shutil.move(src, dst)"""

    src: Path
    dst: Path

    @property
    def rename_only(self) -> bool:
        return self.src.parent == self.dst.parent

    @property
    def move_only(self) -> bool:
        return self.src.parent != self.dst.parent and self.src.name == self.dst.name

    @property
    def verb(self) -> str:
        if self.src.parent != self.dst.parent and self.src.name != self.dst.name:
            return '移动并重命名'
        if self.move_only:
            return '移动'
        return '重命名'


@dataclass
class DirResult:
    """一个模型目录的处理计划与统计"""

    plan: list[MovePlan] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    other_images: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_work(self) -> bool:
        return bool(self.plan or self.conflicts)


def should_skip_dir(name: str) -> bool:
    """遍历时是否跳过该子目录（不进入其内部）。

    需排除：隐藏目录（. 开头）、暂存区（_ 开头，如 _Model-Inbox/_Unknown-Author）、
    仓库非模型目录（Docs）、模型资源子目录（textures 等）。
    """
    lower = name.lower()
    return (name.startswith('.') or name.startswith('_')
            or lower in NON_MODEL_DIR_NAMES
            or lower in RESOURCE_DIR_NAMES)


def _silence_walk_error(error: OSError) -> None:
    """os.walk 遇到无权限/已被删除的目录时静默跳过，不让整体扫描中断。"""


def collect_model_dirs(roots: list[Path], rename_mode: bool) -> list[Path]:
    """递归收集所有需要处理的模型目录(任意层级)。

    除了标准的「作者目录/模型目录」两层结构,仓库根下还存在「系列包」式的
    嵌套变体目录(如 0058/艺方像素/系列/模型/),它们同样携带预览图片,应一并处理。
    这些目录通常没有 README,generate_model_readmes.py 不会处理它们,本脚本负责为它们归类。

    移动模式:只收集含「文件名含 preview 的顶层图片」的目录。
    重命名模式:顶层或 previews/ 下存在任意图片即收集。

    用 os.walk 遍历并在每层剪枝跳过非模型目录（隐藏/暂存区/Docs/资源子目录），
    避免进入 .git、.venv 等大量无关文件，也防止把文档示意图误当作模型预览图。
    """
    dirs: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for current_dir, subdir_names, file_names in os.walk(root, onerror=_silence_walk_error):
            # 剪枝：不进入被排除的目录（剪枝比事后过滤更省时）
            subdir_names[:] = [d for d in subdir_names if not should_skip_dir(d)]
            current = Path(current_dir)
            for fname in file_names:
                p = current / fname
                if not is_image_file(p):
                    continue
                # 图片位于 previews/ 内：重命名模式下归入其父（模型）目录；移动模式不动它
                if current.name.lower() == PREVIEWS_DIRNAME.lower():
                    if rename_mode:
                        dirs.add(current.parent)
                # 图片位于模型目录顶层：移动模式需文件名含 preview，重命名模式任意图片都算
                elif rename_mode or PREVIEW_MARKER.search(p.stem):
                    dirs.add(current)
    return sorted(dirs)


def plan_move(model_dir: Path) -> DirResult:
    """移动模式:把顶层 preview 命名的图片移入 previews/(原行为)。

    顶层其他图片(文件名不含 preview)仅统计,不移动。
    """
    result = DirResult()
    top_images = sorted(p for p in model_dir.iterdir() if is_preview_image(p))
    result.other_images = sorted(
        p.name
        for p in model_dir.iterdir()
        if is_image_file(p) and not PREVIEW_MARKER.search(p.stem)
    )
    if not top_images:
        return result

    target = find_previews_dir(model_dir) or model_dir / PREVIEWS_DIRNAME
    for src in top_images:
        dst = target / src.name
        if dst.exists():
            result.conflicts.append(src.name)
            continue
        result.plan.append(MovePlan(src, dst))
    return result


def plan_rename(model_dir: Path) -> DirResult:
    """重命名模式:把顶层与 previews/ 下的所有图片统一命名为 previewNN。

    - previews/ 内已规范命名的文件保持原名(占用对应编号)
    - 顶层已规范命名的文件尽量保留原名移入 previews/;若目标名已被
      previews/ 内文件占用,则自动重命名为下一个未占用编号(而非冲突跳过)
    - 其余图片按稳定顺序(先 previews/ 内,后顶层,各自按名称排序)分配
      未占用的最小编号,从 01 开始
    - 编号上限为 MAX_PREVIEW_INDEX,超出视为错误
    """
    result = DirResult()
    previews = find_previews_dir(model_dir)
    target = previews or model_dir / PREVIEWS_DIRNAME

    previews_images: list[Path] = []
    if previews is not None:
        previews_images = sorted(p for p in previews.iterdir() if is_image_file(p))
    top_images = sorted(p for p in model_dir.iterdir() if is_image_file(p))

    # 已占用的编号:previews/ 内与顶层所有 previewNN 文件都占位,
    # 保证顶层同名图片重编号时不会撞到 previews/ 内的同名文件
    used: set[int] = set()
    for c in previews_images + top_images:
        match = PREVIEW_NUMBER_RE.match(c.stem)
        if match:
            used.add(int(match.group(1)))

    # 跟踪本计划内已产生的目标路径,避免两个操作规划到同一目标
    planned_targets: set[Path] = set()
    next_index = 1

    def _next_free() -> int:
        """取下一个最小未占用编号并占用;编号耗尽返回 -1。"""
        nonlocal next_index
        while next_index in used:
            next_index += 1
        if next_index > MAX_PREVIEW_INDEX:
            return -1
        used.add(next_index)
        return next_index

    def _assign(c: Path) -> bool:
        """为图片分配未占用编号并登记操作;返回 False 表示编号耗尽(已记错误)。"""
        idx = _next_free()
        if idx < 0:
            result.errors.append(
                f'{model_dir.name}: 待重命名图片超过 {MAX_PREVIEW_INDEX} 张,编号耗尽,'
                '该目录未处理')
            return False
        dst = target / f'preview{idx:02d}{c.suffix}'
        if dst.exists() or dst in planned_targets:
            result.conflicts.append(c.name)
            return True
        planned_targets.add(dst)
        result.plan.append(MovePlan(c, dst))
        return True

    # 阶段一:previews/ 内的图片。已规范命名保持不动,其余分配编号(就地重命名)
    for c in previews_images:
        if PREVIEW_NUMBER_RE.match(c.stem):
            continue
        if not _assign(c):
            return result

    # 阶段二:顶层图片。已规范命名的尽量保留原名移入;同名被 previews/ 内
    # 文件占用时自动重编号,避免冲突跳过导致图片永远无法归入 previews/
    for c in top_images:
        if PREVIEW_NUMBER_RE.match(c.stem):
            dst = target / c.name
            if dst.exists() or dst in planned_targets:
                if not _assign(c):
                    return result
                continue
            planned_targets.add(dst)
            result.plan.append(MovePlan(c, dst))
            continue
        # 未规范命名:分配最小未占用编号
        if not _assign(c):
            return result
    return result


def apply_plan(plan: list[MovePlan]) -> tuple[int, list[str]]:
    """执行文件操作，返回（成功项数, 失败描述列表）。

    dst 的父目录不存在时自动创建；单个操作失败（权限不足、目标被占用等）时
    记录错误并继续，避免一个文件失败导致整批中断、留下半处理状态。
    """
    succeeded = 0
    failed: list[str] = []
    for item in plan:
        try:
            item.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item.src), str(item.dst))
            succeeded += 1
        except (OSError, shutil.Error) as exc:
            failed.append(f'{item.src.name} -> {item.dst}: {exc}')
    return succeeded, failed


def run_generate_readmes() -> int:
    """重跑 generate_model_readmes.py 更新 README 引用"""
    proc = subprocess.run(
        [sys.executable, str(GENERATE_SCRIPT)],
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',  # Windows 下子进程输出若非 UTF-8 也不至于解码崩溃
    )
    for line in (proc.stdout or '').splitlines():
        print(f'  [generate] {line}')
    if proc.returncode != 0:
        print(f'  [generate] stderr: {proc.stderr}', file=sys.stderr)
    return proc.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--apply', action='store_true',
                        help='真正创建 previews/ 并移动/重命名文件;缺省为 dry-run')
    parser.add_argument('--rename', action='store_true',
                        help='重命名模式:把顶层与 previews/ 下的图片统一命名为 previewNN')
    parser.add_argument('--with-gen-readmes', action='store_true',
                        help='执行后联动重跑 generate_model_readmes.py 更新 README 引用（默认不联动）')
    parser.add_argument('paths', nargs='*', default=None,
                        help='直接引用路径处理（可多个，如 Models/0001/AveMujica_三角初华_LB；'
                             '不传则全量扫描所有根目录）')
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.paths:
        # 指定目录作为根，自动递归检查其下需要整理预览图的模型目录
        model_dirs: list[Path] = []
        for p in args.paths:
            d = WORKSPACE_ROOT / p
            if not d.is_dir():
                print(f'错误:目录不存在: {p}', file=sys.stderr)
                return 2
            model_dirs.extend(collect_model_dirs([d], rename_mode=args.rename))
        model_dirs = sorted(set(model_dirs), key=lambda x: str(x))
    else:
        model_dirs = collect_model_dirs(DEFAULT_ROOTS, rename_mode=args.rename)

    mode_name = 'RENAME(重命名)' if args.rename else 'MOVE(移动)'
    run_mode = 'APPLY(执行)' if args.apply else 'DRY-RUN(预览)'
    print(f'模式: {run_mode} {mode_name}  模型目录数: {len(model_dirs)}')
    print('-' * 60)

    total_ops = 0
    total_applied = 0
    total_failed = 0
    total_conflicts = 0
    total_other = 0
    processed = 0
    unchanged = 0
    error_dirs = 0

    for model_dir in model_dirs:
        rel = model_dir.relative_to(WORKSPACE_ROOT).as_posix()
        result = plan_rename(model_dir) if args.rename else plan_move(model_dir)

        if result.errors:
            error_dirs += 1
            for message in result.errors:
                print(f'错误:{rel}: {message}', file=sys.stderr)
            continue

        if not result.has_work:
            if result.other_images:
                processed += 1
                total_other += len(result.other_images)
                print(rel)
                for name in result.other_images:
                    print(f'    保留(非 preview 命名) {name}')
            else:
                unchanged += 1
            continue

        processed += 1
        total_ops += len(result.plan)
        total_conflicts += len(result.conflicts)
        total_other += len(result.other_images)

        prefix = '' if args.apply else '将'
        print(rel)
        for item in result.plan:
            dst_rel = item.dst.relative_to(model_dir).as_posix()
            print(f'    {prefix}{item.verb} {item.src.name} -> {dst_rel}')
        for name in result.conflicts:
            print(f'    跳过(目标已存在) {name}')
        for name in result.other_images:
            print(f'    保留(非 preview 命名) {name}')

        if args.apply and result.plan:
            applied, failed = apply_plan(result.plan)
            total_applied += applied
            total_failed += len(failed)
            print(f'    已执行 {applied} 个文件操作')
            for message in failed:
                print(f'    失败: {message}', file=sys.stderr)

    print('-' * 60)
    print(f'处理目录: {processed}  未变更: {unchanged}  文件操作: {total_ops}  '
          f'执行成功: {total_applied}  失败: {total_failed}  '
          f'冲突跳过: {total_conflicts}  保留的其他图片: {total_other}')

    # 以「实际执行成功数」为准：若 apply 阶段全部失败，不应联动重跑 README
    if args.apply and total_applied > 0 and args.with_gen_readmes:
        print('正在重跑 generate_model_readmes.py 更新 README 引用 ...')
        rc = run_generate_readmes()
        if rc != 0:
            print('错误:generate_model_readmes.py 返回非零,README 引用可能未更新', file=sys.stderr)
            return rc
    elif args.apply and total_applied > 0:
        print('提示:已跳过 README 重生成(默认不联动)。如需更新引用请加 --with-gen-readmes 或手动运行 '
              'python .github/scripts/models_organize/03_generate_model_readmes.py。')

    # 冲突文件会同时被顶层与 previews/ 收集,导致 README 重复引用,必须提示
    if total_conflicts > 0:
        print('警告:存在冲突文件未处理(见上),请手动处理以免 README 重复引用', file=sys.stderr)
        return 3
    if error_dirs > 0:
        print(f'错误:{error_dirs} 个目录因编号耗尽等原因未处理', file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
