#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YSM 模型仓库统一命令行入口（人工使用）。

把 models_organize/ check&fix/ deployments/ 全部脚本与数据校验收纳为子命令，不用记脚本路径：
  python .github/scripts/cli.py <子命令> [转发参数...]

子命令对应脚本一览（--list 查看）：
  organize       归档 .ysm → models_organize/01_organize_models.py
  previews       预览图归位 → models_organize/01_organize_previews.py
  rename-files   重命名模型文件 → models_organize/02_rename_model_files.py
  rename-folders 重命名模型文件夹（纯重命名） → models_organize/02_rename_model_folders.py
  kb             知识库维护(角色/作品增删改查/合并/默认名/重命名键) → check&fix/kb_tool.py
  authors        重建作者数据 → check&fix/kb_tool.py --authors-data
  readmes        生成模型 README → models_organize/03_generate_model_readmes.py
  author-readmes 生成作者 README → models_organize/03_generate_author_readmes.py
  author-dirs    重命名作者文件夹(同步 authors.json) → models_organize/06_rename_author_dirs.py
  readmes-all    生成全部 README(作者+模型+根+Other索引) → 见 PIPELINE_STEPS['readmes-all']
  authors-list   更新根 README 作者表 → models_organize/03_generate_root_readme.py --author
  category-map   更新根 README 模型分类区块 → models_organize/03_generate_root_readme.py --build-category-map
  format         格式化作者 README → check&fix/format_author_readme.py
  translate      翻译 README-EN → models_organize/05_translate_readme.py
  site           生成静态网站 → deployments/build_site.py
  flow           流程编排(inbox/full/rename/...)（内联自原 pipeline.py，见本文件 PIPELINE_STEPS）
  audit          库整理(重新分类/合并作者/空壳/缺失) → check&fix/model_check&fix.py
  check          数据契约校验 → lib/validate.py

子命令后的所有参数原样转发给目标脚本（如 --apply / --check 等），
由目标脚本自行解释，保证与直接运行脚本行为完全一致。

注意：本入口不做参数解析（薄转发层），只识别第一个位置参数为子命令，
其余参数一律透传——避免 argparse REMAINDER 在子命令场景吞不掉选项的问题。
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

# 把 .github/scripts 加回 sys.path（本脚本位于 scripts/ 根，直接加父目录）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import paths as lib_paths  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = lib_paths.WORKSPACE_ROOT

# 子命令 -> (脚本相对 scripts/ 的路径, 默认参数, 一句话说明)；默认参数先于用户转发参数
# 脚本已按流程阶段归入 models_organize/（模型整理）/ check&fix/（库整理）/ deployments/（部署）；
# flow 为特殊子命令（内联流程编排，见 PIPELINE_STEPS / run_flow，无对应独立脚本）。
COMMANDS: dict[str, tuple[str, list[str], str]] = {
    'organize': ('models_organize/01_organize_models.py', [], '归档 .ysm 到 Models/<编号>/'),
    'audit': ('check&fix/model_check&fix.py', [], '库整理:重新分类/合并作者/空壳报告/缺失(无分类无预览图)'),
    'previews': ('models_organize/01_organize_previews.py', [], '预览图归入 previews/ 并规范命名'),
    'rename-files': ('models_organize/02_rename_model_files.py', [], '重命名模型文件（独立脚本）'),
    'rename-folders': ('models_organize/02_rename_model_folders.py', [],
                       '重命名模型文件夹（纯重命名；知识库维护用 kb）'),
    'kb': ('check&fix/kb_tool.py', [], '知识库维护:角色/作品增删改查/合并/默认名/重命名键'),
    'authors': ('check&fix/kb_tool.py', ['--authors-data'], '重建集中作者数据 authors.json'),
    'readmes': ('models_organize/03_generate_model_readmes.py', [], '生成/重写模型 README'),
    'author-readmes': ('models_organize/03_generate_author_readmes.py', ['--apply'], '生成/重写作者 README（从 authors.json）'),
    'author-dirs': ('models_organize/06_rename_author_dirs.py', [], '按 authors.json 重命名作者文件夹并同步数据（dry-run）'),
    'other-index': ('models_organize/03_generate_other_models_index.py', [], '生成 Other-YSM-Models 模型总索引'),
    'authors-list': ('models_organize/03_generate_root_readme.py', ['--author'], '更新根 README 作者表格'),
    'category-map': ('models_organize/03_generate_root_readme.py', ['--build-category-map'], '更新根 README 模型分类区块'),
    'readmes-all': ('', [], '生成全部 README(作者+模型+根+Other索引)，见 PIPELINE_STEPS[readmes-all]'),
    'format': ('check&fix/format_author_readme.py', [], '格式化作者级 README（作者推导已移至 kb --sync-authors）'),
    'translate': ('models_organize/04_translate_readme.py', [], '翻译 README → README-EN'),
    'site': ('deployments/build_site.py', [], '生成静态模型浏览站 index.html'),
    'flow': ('', [], '流程编排(inbox/full/rename/...)，内联自原 pipeline.py'),
    'check': ('lib/validate.py', [], '数据契约校验(schemas/)'),
}

USAGE = (
    '用法: python .github/scripts/cli.py <子命令> [转发参数...]\n'
    '      python .github/scripts/cli.py --list    查看全部子命令\n'
)


def run_script(rel_script: str, default_args: list[str], args: list[str]) -> int:
    """子进程运行目标脚本（默认参数 + 用户转发参数）；返回其退出码。cwd=仓库根。"""
    script = SCRIPT_DIR / rel_script
    if not script.is_file():
        print(f'[错误] 未找到脚本: {script}', file=sys.stderr)
        return 2
    cmd = [sys.executable, str(script), *default_args, *args]
    print(f'$ {os.path.relpath(script, REPO_ROOT)} {" ".join(cmd[2:]) if cmd[2:] else ""}')
    return subprocess.run(cmd, cwd=REPO_ROOT, env=os.environ).returncode


def print_commands() -> None:
    """打印全部子命令清单。"""
    print('可用子命令:')
    for name, (_, _, desc) in COMMANDS.items():
        print(f'  {name:<14} {desc}')
    print()


# ---------------------------------------------------------------------------
# 流程编排（合并自原 pipeline.py）：cli.py flow 子命令的内联实现
# ---------------------------------------------------------------------------
# 每个步骤 = (脚本相对 scripts/ 的路径, 传给该脚本的参数[, 是否致命])；顺序即执行顺序
#   第三元素可省略（默认 True=失败即中止流程）；False=非致命：子脚本失败只警告，
#   继续后续步骤（如预览图归位 previews，冲突文件不阻断核心 README/翻译流程）
PIPELINE_STEPS: dict[str, list[tuple[str, list[str]] | tuple[str, list[str], bool]]] = {
    'inbox': [
        ('models_organize/01_organize_models.py', ['_Model-Inbox', '--apply']),
        # 预览图归位（--rename 统一命名为 preview01..99），必须在 README 生成前
        # （03_generate_model_readmes.py 引用 previews/ 下的预览图）；非致命
        ('models_organize/01_organize_previews.py', ['--apply', '--rename'], False),
        ('check&fix/kb_tool.py', ['--authors-data']),
        ('models_organize/03_generate_model_readmes.py', []),
        # Other-YSM-Models 模型总索引（归档时未命中作者的模型可能归入该目录）
        ('models_organize/03_generate_other_models_index.py', ['--apply']),
        ('check&fix/format_author_readme.py', []),
        ('models_organize/03_generate_root_readme.py', ['--author']),
        ('models_organize/04_translate_readme.py', []),
    ],
    'full': [
        # 预览图归位同样适用于模型/文档变更后的全量刷新；非致命
        ('models_organize/01_organize_previews.py', ['--apply', '--rename'], False),
        ('check&fix/kb_tool.py', ['--authors-data']),
        ('models_organize/03_generate_model_readmes.py', []),
        # Other-YSM-Models 模型总索引（workflow 触发路径含 Other-YSM-Models/**）
        ('models_organize/03_generate_other_models_index.py', ['--apply']),
        ('check&fix/format_author_readme.py', []),
        ('models_organize/03_generate_root_readme.py', ['--author']),
        ('models_organize/04_translate_readme.py', []),
    ],
    'other-index': [
        ('models_organize/03_generate_other_models_index.py', ['--apply']),
    ],
    # 一键生成全部 README：
    #   作者 README（覆盖模式，从 authors.json）→ 模型 README → 根 README 作者表 → Other-YSM 索引
    'readmes-all': [
        ('models_organize/03_generate_author_readmes.py', ['--apply']),
        ('models_organize/03_generate_model_readmes.py', []),
        ('models_organize/03_generate_root_readme.py', ['--author']),
        ('models_organize/03_generate_other_models_index.py', ['--apply']),
    ],
    'rename': [
        ('models_organize/02_rename_model_folders.py', ['--apply']),
    ],
    'authors': [
        ('check&fix/kb_tool.py', ['--authors-data']),
    ],
    'readmes': [
        ('models_organize/03_generate_model_readmes.py', []),
    ],
    'authors-list': [
        ('models_organize/03_generate_root_readme.py', ['--author']),
    ],
    'translate': [
        ('models_organize/05_translate_readme.py', []),
    ],
}

# 每个脚本的一句话说明（flow --list 用）
FLOW_STEP_DESC = {
    'models_organize/01_organize_models.py': '归档 .ysm 到 Models/<编号>/',
    'models_organize/01_organize_previews.py': '预览图归入 previews/ 并规范命名(--rename)',
    'models_organize/03_generate_author_readmes.py': '生成作者 README（合并模式：先反向合并手写信息再生成）',
    'models_organize/03_generate_root_readme.py': '根 README 展示(作者表/分类区块；authors.json 已移至 kb --authors-data)',
    'models_organize/03_generate_model_readmes.py': '生成模型 README',
    'models_organize/03_generate_other_models_index.py': 'Other-YSM-Models 模型总索引(按作品分组)',
    'check&fix/format_author_readme.py': '格式化作者级 README',
    'models_organize/04_translate_readme.py': '翻译 README → README-EN',
    'models_organize/02_rename_model_folders.py': '重命名模型文件夹',
    'check&fix/kb_tool.py': '知识库维护(角色/作品/--authors-data/--sync-authors)',
}


def run_flow(argv: list[str]) -> int:
    """cli.py flow 子命令：按预定义流程顺序调用脚本（合并自原 pipeline.py）。"""
    parser = argparse.ArgumentParser(
        prog='cli.py flow', description='多脚本流程编排（原 pipeline.py 合并而来）',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('flow', nargs='?', default='full',
                        help='流程名（默认 full）; 用 --list 查看全部')
    parser.add_argument('--dry-run', action='store_true',
                        help='只打印将执行的步骤，不真正运行')
    parser.add_argument('--list', action='store_true', help='列出全部流程及其步骤')
    args = parser.parse_args(argv)

    if args.list:
        for name, steps in PIPELINE_STEPS.items():
            print(f'[{name}]')
            for step in steps:
                rel, step_args, *rest = step
                fatal = rest[0] if rest else True
                desc = FLOW_STEP_DESC.get(rel, '')
                suffix = f" {' '.join(step_args)}" if step_args else ''
                tag = '' if fatal else '  (非致命)'
                print(f'    {rel}{suffix}{tag}  {desc}')
        return 0

    if args.flow not in PIPELINE_STEPS:
        print(f"未知流程: {args.flow}（可用: {', '.join(PIPELINE_STEPS)}）", file=sys.stderr)
        return 2

    print(f'== pipeline: {args.flow} ==')
    for step in PIPELINE_STEPS[args.flow]:
        rel, step_args, *rest = step
        fatal = rest[0] if rest else True  # 第三元素可选，默认致命（失败中止）
        script = SCRIPT_DIR / rel
        if not script.is_file():
            print(f"  [错误] 未找到脚本: {script}", file=sys.stderr)
            return 2
        desc = FLOW_STEP_DESC.get(rel, '')
        tag = '' if fatal else '（非致命）'
        print(f"  → {rel} {' '.join(step_args) if step_args else ''}  {desc}{tag}")
        if args.dry_run:
            print('    (dry-run) 未执行')
            continue
        code = subprocess.run([sys.executable, str(script), *step_args],
                              cwd=REPO_ROOT, env=os.environ).returncode
        if code != 0:
            if not fatal:
                # 非致命步骤：只警告，继续后续步骤（如预览图归位冲突不阻断核心流程）
                print(f"  [警告] {rel} 退出码 {code}（非致命，继续后续步骤）", file=sys.stderr)
                continue
            print(f"流程中止: {rel} 退出码 {code}", file=sys.stderr)
            return code
    print('== pipeline 完成 ==')
    return 0


def main() -> int:
    argv = sys.argv[1:]

    # 帮助/清单类参数单独处理，其余一律视为 (子命令, 转发参数...)
    if not argv:
        print(USAGE, end='')
        return 2
    if argv[0] in ('-h', '--help', 'help'):
        print(__doc__)
        print(USAGE)
        return 0
    if argv[0] in ('--list', 'list'):
        print_commands()
        return 0

    command, *forwarded = argv
    if command == 'flow':
        return run_flow(forwarded)
    if command == 'readmes-all':
        # 一键生成全部 README：复用 PIPELINE_STEPS['readmes-all'] 流程编排；
        # 转发参数（如 --dry-run）传给 run_flow
        return run_flow(['readmes-all', *forwarded])
    entry = COMMANDS.get(command)
    if entry is None:
        print(f'[错误] 未知子命令: {command}', file=sys.stderr)
        print_commands()
        return 2

    rel_script, default_args, _ = entry
    return run_script(rel_script, default_args, forwarded)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
