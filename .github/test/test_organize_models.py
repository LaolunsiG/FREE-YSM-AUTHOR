# -*- coding: utf-8 -*-
"""01_organize_models.py 修复的配套测试。

覆盖本次修复的 3 个 bug：
  1) --with-readme-table：底层脚本已废弃（README 作者索引功能移除），该选项应从
     argparse 移除，再传入应被拒绝
  2) register_author：已存在 '<编号>-<名称>' 目录时应复用，不再错误创建裸编号目录
  3) archive_model_bundle：同名不同内容时衍生版本号递增（_v2/_v3...），不覆盖历史版本

运行：python .github/test/test_organize_models.py（退出码 0=全部通过）
"""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8')

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / '.github' / 'scripts'
sys.path.insert(0, str(SCRIPTS))

# 直接加载目标脚本（模块名以数字开头，不能直接 import）；先注册 sys.modules
_SCRIPT = SCRIPTS / 'models_organize' / '01_organize_models.py'
_spec = importlib.util.spec_from_file_location('organize_models', _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.modules['organize_models'] = _mod
_spec.loader.exec_module(_mod)
P = _mod

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f'  ✓ {label}')
    else:
        FAILURES.append(label)
        print(f'  ✗ {label}')


def test_readme_table_option_removed() -> None:
    print('[Bug1: --with-readme-table 已移除]')
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), '--with-readme-table', '.'],
        cwd=str(REPO), capture_output=True, text=True,
        encoding='utf-8', errors='replace',
    )
    check(proc.returncode != 0, '传入 --with-readme-table 被 argparse 拒绝（退出码非 0）')


def test_register_author_reuses_named_dir(tmp: pathlib.Path) -> None:
    print('[Bug2: register_author 复用已有 <编号>-<名称> 目录]')
    author_dir = tmp / '0207-已有作者'
    author_dir.mkdir(parents=True)

    fake_authors = tmp / 'fake_authors.json'
    saved: list[tuple] = []

    # 拦截数据读写，避免触碰真实 authors.json
    orig_data_path = P.lib_paths.data_path
    orig_save_json = P.lib_paths.save_json
    P.lib_paths.data_path = lambda category, *parts: fake_authors
    P.lib_paths.save_json = lambda path, data: saved.append((path, data))
    try:
        P.register_author(tmp, '0207', '新作者名')
    finally:
        P.lib_paths.data_path = orig_data_path
        P.lib_paths.save_json = orig_save_json

    check(author_dir.is_dir(), '已有 <编号>-<名称> 目录仍存在')
    check(not (tmp / '0207').exists(), '不再误创建裸编号目录 0207/')
    check(bool(saved), 'authors.json 被写入（登记作者数据）')


def test_archive_version_increments(tmp: pathlib.Path) -> None:
    print('[Bug3: 衍生版本号递增，不覆盖历史版本]')
    src_dir = tmp / 'src'
    src_dir.mkdir()
    src = src_dir / 'model.ysm'
    src.write_bytes(b'src-content')

    model_dir = tmp / 'author' / 'model'
    model_dir.mkdir(parents=True)
    (model_dir / 'model.ysm').write_bytes(b'dest-v1')
    (model_dir / 'model_v2.ysm').write_bytes(b'dest-v2')

    P.archive_model_bundle(
        src, tmp / 'author', 'model', apply=True, root=tmp, verbose=False)

    v3 = model_dir / 'model_v3.ysm'
    check(v3.exists() and v3.read_bytes() == b'src-content', '新内容写入 model_v3.ysm')
    check((model_dir / 'model.ysm').read_bytes() == b'dest-v1', 'model.ysm 未被覆盖')
    check((model_dir / 'model_v2.ysm').read_bytes() == b'dest-v2', 'model_v2.ysm 未被覆盖')


def main() -> int:
    test_readme_table_option_removed()
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        test_register_author_reuses_named_dir(tmp)
        test_archive_version_increments(tmp)

    print()
    if FAILURES:
        print(f'失败 {len(FAILURES)} 项: {FAILURES}')
        return 1
    print('全部通过 ✓')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
