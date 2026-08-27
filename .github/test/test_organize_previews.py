# -*- coding: utf-8 -*-
"""01_organize_previews.py 的配套测试。

覆盖本次优化/修复点：
  1) should_skip_dir：隐藏/暂存/Docs/资源子目录应被跳过，previews 与模型目录不应被跳过
  2) collect_model_dirs：默认根改为仓库根后，只收集作者目录下的模型目录，
     排除 .hidden/_staging/Docs 等非模型目录；MOVE 只收集含 preview* 顶层图片的目录，
     RENAME 收集含 previews/ 或任意图片的目录
  3) plan_move：顶层 preview 图片移入 previews/；目标已存在计入冲突
  4) plan_rename：已规范命名保持原名移入 previews/；未规范命名分配最小未占用编号
  5) apply_plan：成功计数；单个操作失败计入失败列表而不中断整批

运行：python .github/test/test_organize_previews.py（退出码 0=全部通过）
"""
import importlib.util
import pathlib
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8')

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / '.github' / 'scripts'
sys.path.insert(0, str(SCRIPTS))

# 直接加载目标脚本（模块名以数字开头，不能直接 import）；先注册 sys.modules 供 dataclass 使用
_SCRIPT = SCRIPTS / 'models_organize' / '01_organize_previews.py'
_spec = importlib.util.spec_from_file_location('organize_previews', _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.modules['organize_previews'] = _mod
_spec.loader.exec_module(_mod)
P = _mod

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f'  ✓ {label}')
    else:
        FAILURES.append(label)
        print(f'  ✗ {label}')


def touch(path: pathlib.Path) -> None:
    """创建父目录并写入占位内容，确保文件真实存在。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x')


def make_tree(root: pathlib.Path) -> None:
    """构造临时目录树：3 个非模型目录 + 1 个作者目录下的 3 个模型目录。"""
    touch(root / '.hidden' / 'preview01.png')
    touch(root / '_staging' / 'preview01.png')
    touch(root / 'Docs' / 'preview01.png')

    author = root / '0001-TestA'
    m1 = author / 'M1'
    touch(m1 / 'previews' / 'preview01.png')
    touch(m1 / 'preview02.png')
    m2 = author / 'M2'
    touch(m2 / 'previews' / 'preview01.png')
    touch(m2 / 'a.png')
    m3 = author / 'M3'
    touch(m3 / 'previews' / 'preview01.png')
    touch(m3 / 'preview01.png')  # 与 previews/ 内同名 -> 冲突


def test_should_skip_dir() -> None:
    print('[should_skip_dir]')
    for name in ('.hidden', '_staging', 'Docs', 'textures', 'models', 'avatar'):
        check(P.should_skip_dir(name), f'应跳过 {name}')
    for name in ('previews', 'M1', '0001-TestA', 'previews 无关'):
        check(not P.should_skip_dir(name), f'不应跳过 {name}')


def test_collect_model_dirs(tmp: pathlib.Path) -> None:
    print('[collect_model_dirs 排除规则 + 模式差异]')
    roots = [tmp]

    move_dirs = P.collect_model_dirs(roots, rename_mode=False)
    move_names = {d.relative_to(tmp).as_posix() for d in move_dirs}
    check(move_names == {'0001-TestA/M1', '0001-TestA/M3'},
          f'MOVE 只收集含 preview* 顶层图片的模型目录，实际 {sorted(move_names)}')

    rename_dirs = P.collect_model_dirs(roots, rename_mode=True)
    rename_names = {d.relative_to(tmp).as_posix() for d in rename_dirs}
    check(rename_names == {'0001-TestA/M1', '0001-TestA/M2', '0001-TestA/M3'},
          f'RENAME 收集含 previews/ 或任意图片的目录，实际 {sorted(rename_names)}')

    non_model = [d for d in set(move_dirs) | set(rename_dirs)
                 if any(part.startswith(('.', '_')) or part.lower() == 'docs'
                        for part in d.relative_to(tmp).parts)]
    check(not non_model, f'不收集非模型目录（.hidden/_staging/Docs），实际 {non_model}')


def test_plan_move(tmp: pathlib.Path) -> None:
    print('[plan_move]')
    m1 = tmp / '0001-TestA' / 'M1'
    result = P.plan_move(m1)
    check(len(result.plan) == 1 and result.plan[0].src.name == 'preview02.png',
          '顶层 preview02.png 规划移入 previews/')
    check(result.plan[0].dst == m1 / 'previews' / 'preview02.png', '目标路径为 previews/preview02.png')
    check(result.conflicts == [], 'M1 无冲突')

    m3 = tmp / '0001-TestA' / 'M3'
    result3 = P.plan_move(m3)
    check(result3.plan == [] and result3.conflicts == ['preview01.png'],
          '顶层 preview01.png 与 previews/ 同名 -> 冲突')


def test_plan_rename(tmp: pathlib.Path) -> None:
    print('[plan_rename]')
    m1 = tmp / '0001-TestA' / 'M1'
    result = P.plan_rename(m1)
    check(len(result.plan) == 1 and result.plan[0].src.name == 'preview02.png'
          and result.plan[0].dst.name == 'preview02.png',
          '已规范命名 preview02.png 保持原名、移入 previews/')

    m2 = tmp / '0001-TestA' / 'M2'
    result2 = P.plan_rename(m2)
    check(len(result2.plan) == 1 and result2.plan[0].src.name == 'a.png'
          and result2.plan[0].dst.name == 'preview02.png',
          '未规范命名 a.png 分配最小未占用编号 preview02（跳过已占用的 01）')


def test_apply_plan(tmp: pathlib.Path) -> None:
    print('[apply_plan]')
    # 成功场景：把 preview02.png 移入 previews/
    m1 = tmp / '0001-TestA' / 'M1'
    src = m1 / 'preview02.png'
    dst = m1 / 'previews' / 'preview02.png'
    ok, failed = P.apply_plan([P.MovePlan(src, dst)])
    check(ok == 1 and failed == [] and dst.exists() and not src.exists(),
          '正常移动成功计入 1，目标存在、源已移除')

    # 失败场景：源不存在 -> 计入失败列表而不中断
    missing = tmp / '0001-TestA' / 'M2' / 'missing.png'
    ok2, failed2 = P.apply_plan([P.MovePlan(missing, tmp / 'nowhere' / 'x.png')])
    check(ok2 == 0 and len(failed2) == 1, '源缺失的单次失败计入 failed，不抛异常中断')


def main() -> int:
    test_should_skip_dir()
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        make_tree(tmp)
        test_collect_model_dirs(tmp)
        test_plan_move(tmp)
        test_plan_rename(tmp)
        test_apply_plan(tmp)

    print()
    if FAILURES:
        print(f'失败 {len(FAILURES)} 项: {FAILURES}')
        return 1
    print('全部通过 ✓')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
