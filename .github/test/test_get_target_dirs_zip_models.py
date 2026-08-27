# -*- coding: utf-8 -*-
"""
cmds.get_target_dirs 纯 zip 压缩包模型目录收集回归测试。

背景：02_rename_model_folders.py 传作者目录（如 0040-碎de帆）时走
get_target_dirs 的非 Models 分支，原实现用 _collect_model_dirs 递归
（要求含 .ysm 文件或 previews/ 目录），纯 zip 压缩包模型目录（只有
README.md + .zip）被漏收集，导致部分模型文件夹永远不会被重命名。

修复：作者目录（以 4 位编号开头，如 0040 或 0040-碎de帆）直接收集其
全部子目录，与 Models/<作者> 分支一致；非作者目录（如 Other-YSM-Models
下的作品层）仍走 .ysm/previews 递归，避免把作品层误当模型目录。

覆盖：
  1) _is_author_dir 识别：纯编号 / 编号+后缀 / 非作者目录
  2) 根下作者目录：收集全部子目录（含纯 zip 模型目录）
  3) 非作者目录（作品层）：仍递归收集，漏掉纯 zip 目录
  4) 作者目录跳过 previews 子目录

运行：python .github/test/test_get_target_dirs_zip_models.py（退出码 0=全过）
"""
import pathlib
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8')

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / ".github" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lib.kb.cmds import _is_author_dir, get_target_dirs  # noqa: E402


def _mkfile(p: pathlib.Path, name: str) -> None:
    """在目录 p 下建一个占位文件（内容为空）。"""
    (p / name).write_text("", encoding="utf-8")


def _build_tree(root: pathlib.Path) -> None:
    """构造与仓库一致的目录结构（作者目录 + 作品层）。"""
    # 作者目录：编号+中文后缀，含 .ysm / previews / 纯 zip 三种模型目录
    author = root / "0040-碎de帆"
    (author / "Touhou_Remilia-Scarlet_蕾米莉亚").mkdir(parents=True)
    _mkfile(author / "Touhou_Remilia-Scarlet_蕾米莉亚", "a.ysm")
    (author / "VTuber_Minato-Aqua_凑阿库娅" / "previews").mkdir(parents=True)
    _mkfile(author / "VTuber_Minato-Aqua_凑阿库娅", "README.md")
    # 纯 zip 压缩包模型目录：无 .ysm、无 previews，必须按目录收集
    (author / "Touhou_雾雨魔理沙_Marisa-Kirisame").mkdir(parents=True)
    _mkfile(author / "Touhou_雾雨魔理沙_Marisa-Kirisame", "m.zip")
    (author / "Touhou_博丽灵梦_Hakurei-Reimu_LA").mkdir(parents=True)
    _mkfile(author / "Touhou_博丽灵梦_Hakurei-Reimu_LA", "m.zip")

    # 纯编号作者目录（无后缀）
    (root / "0050" / "某模型").mkdir(parents=True)
    _mkfile(root / "0050" / "某模型", "a.ysm")

    # 非作者目录（作品层）：应按 .ysm/previews 递归，纯 zip 目录漏掉
    (root / "Other-YSM-Models" / "AK" / "年").mkdir(parents=True)
    _mkfile(root / "Other-YSM-Models" / "AK" / "年", "nian.ysm")
    (root / "Other-YSM-Models" / "AK" / "德克萨斯" / "previews").mkdir(parents=True)
    (root / "Other-YSM-Models" / "AK" / "纯zip作品").mkdir(parents=True)
    _mkfile(root / "Other-YSM-Models" / "AK" / "纯zip作品", "x.zip")


def main() -> int:
    checks: list[tuple[bool, str]] = []

    def ck(cond: bool, msg: str) -> None:
        checks.append((cond, msg))

    # 1. _is_author_dir 识别
    ck(_is_author_dir("0040"), "1a 纯编号识别 (期望 True)")
    ck(_is_author_dir("0040-碎de帆"), "1b 编号+后缀识别 (期望 True)")
    ck(_is_author_dir("0040_碎de帆"), "1c 编号+下划线后缀识别 (期望 True)")
    ck(not _is_author_dir("AK"), "1d 作品层不误判 (期望 False)")
    ck(not _is_author_dir("Unknown_四季映姬"), "1e 模型层不误判 (期望 False)")
    ck(not _is_author_dir("Other-YSM-Models"), "1f 大类目录不误判 (期望 False)")

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _build_tree(root)

        # 2. 根下作者目录：收集全部子目录（含纯 zip 模型目录）
        dirs = get_target_dirs(str(root / "0040-碎de帆"))
        names = sorted(d.name for d in dirs)
        ck(len(dirs) == 4, f"2a 作者目录应收集 4 个子目录，实际 {len(dirs)} ({names})")
        ck("Touhou_雾雨魔理沙_Marisa-Kirisame" in names,
           "2b 纯 zip 模型目录应被收集")
        ck("Touhou_博丽灵梦_Hakurei-Reimu_LA" in names,
           "2c _LA 纯 zip 模型目录应被收集")
        ck("Touhou_Remilia-Scarlet_蕾米莉亚" in names,
           "2d .ysm 模型目录应被收集")
        ck("VTuber_Minato-Aqua_凑阿库娅" in names,
           "2e previews 模型目录应被收集")

        # 3. 纯编号作者目录（无后缀）同样收集
        dirs = get_target_dirs(str(root / "0050"))
        ck([d.name for d in dirs] == ["某模型"],
           f"3 纯编号作者目录收集 ({[d.name for d in dirs]!r})")

        # 4. 非作者目录（作品层）：仍走 .ysm/previews 递归，纯 zip 目录漏掉
        dirs = get_target_dirs(str(root / "Other-YSM-Models"))
        names = sorted(d.name for d in dirs)
        ck(len(dirs) == 2, f"4a 作品层应只收集 2 个模型目录，实际 {len(dirs)} ({names})")
        ck("年" in names and "德克萨斯" in names,
           "4b .ysm/previews 模型目录应被收集")
        ck("纯zip作品" not in names,
           "4c 作品层下纯 zip 目录不应被收集（保持原语义）")

        # 5. 作者目录的 previews 子目录本身不应被当作模型目录
        author_dirs = get_target_dirs(str(root / "0040-碎de帆"))
        ck(not any(d.name == "previews" for d in dirs + author_dirs),
           "5 previews 目录不应出现在结果中")

    print("=" * 50)
    all_ok = all(ok for ok, _ in checks)
    for i, (ok, msg) in enumerate(checks, 1):
        print(f"检查 {i}: {'PASS' if ok else 'FAIL'}  {msg}")
    print("get_target_dirs 纯 zip 收集 测试:", "全部通过" if all_ok else "存在失败")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
