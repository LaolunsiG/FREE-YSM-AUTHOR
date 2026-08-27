# -*- coding: utf-8 -*-
"""
01_organize_previews.plan_rename 同名自动重编号回归测试。

背景：重命名模式下，顶层 preview01.png 与 previews/preview01.png 同名时，
旧实现直接记为冲突跳过（退出码 3），顶层图片永远无法归入 previews/。
修复后：已规范命名的顶层图片若目标名已被 previews/ 内文件占用，自动
重命名为下一个未占用编号。

覆盖：
  1) 顶层与 previews/ 同名 -> 自动重编号（preview01 -> preview05 等）
  2) 顶层 previewNN 在 previews/ 内无同名 -> 保留原名移入
  3) previews/ 内未规范图片 -> 就地重命名分配编号
  4) 幂等：apply 后再规划无操作
  5) 编号耗尽（超过 99 张）-> 报错
  6) 未规范命名顶层图片分配编号（previews/ 不存在）

运行：python .github/test/test_organize_previews_rename.py（退出码 0=全过）
"""
import importlib.util
import pathlib
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8')

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / ".github" / "scripts" / "models_organize" / "01_organize_previews.py"

_spec = importlib.util.spec_from_file_location("organize_previews", SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
# 必须先在 sys.modules 注册，否则模块内 @dataclass 查 sys.modules 取类命名空间会失败
sys.modules["organize_previews"] = _mod
_spec.loader.exec_module(_mod)

plan_rename = _mod.plan_rename
apply_plan = _mod.apply_plan


def _make(model_dir: pathlib.Path, previews: list[str], top: list[str]) -> None:
    """构造目录：previews 列表进 previews/，top 列表放顶层（均为空占位文件）。"""
    if previews:
        (model_dir / "previews").mkdir(parents=True)
        for name in previews:
            (model_dir / "previews" / name).write_bytes(b"")
    for name in top:
        (model_dir / name).write_bytes(b"")
    (model_dir / "README.md").write_text("", encoding="utf-8")


def _plan_map(result, model_dir: pathlib.Path) -> dict[str, str]:
    """返回 {src.name: 相对 model_dir 的 dst 路径}，便于断言。"""
    return {item.src.name: item.dst.relative_to(model_dir).as_posix()
            for item in result.plan}


def main() -> int:
    checks: list[tuple[bool, str]] = []

    def ck(cond: bool, msg: str) -> None:
        checks.append((cond, msg))

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)

        # 1. 本次问题场景：顶层 preview01/02 与 previews/ 同名，自动重编号
        d = root / "case1"
        d.mkdir()
        _make(d, ["preview01.png", "preview02.png", "preview03.png",
                  "preview04.png", "preview06.png"], ["preview01.png", "preview02.png"])
        r = plan_rename(d)
        m = _plan_map(r, d)
        ck(len(r.plan) == 2, f"1a 应规划 2 项，实际 {len(r.plan)} ({m})")
        ck(m.get("preview01.png") == "previews/preview05.png",
           f"1b 顶层 preview01.png 应重编号为 previews/preview05.png，实际 {m.get('preview01.png')!r}")
        ck(m.get("preview02.png") == "previews/preview07.png",
           f"1c 顶层 preview02.png 应重编号为 previews/preview07.png，实际 {m.get('preview02.png')!r}")
        ck(not r.conflicts, f"1d 不应有冲突跳过，实际 {r.conflicts!r}")
        ck(r.has_work, "1e has_work 应为 True")

        # 2. 顶层 previewNN 在 previews/ 内无同名 -> 保留原名移入；未规范图分配编号
        d = root / "case2"
        d.mkdir()
        _make(d, ["preview01.png"], ["preview02.png", "photo.png"])
        r = plan_rename(d)
        m = _plan_map(r, d)
        ck(m.get("preview02.png") == "previews/preview02.png",
           f"2a 顶层 preview02.png 应保留原名移入，实际 {m.get('preview02.png')!r}")
        ck(m.get("photo.png") == "previews/preview03.png",
           f"2b 未规范 photo.png 应分配 preview03.png，实际 {m.get('photo.png')!r}")
        ck(not r.conflicts, f"2c 不应有冲突，实际 {r.conflicts!r}")

        # 3. previews/ 内未规范图片 -> 就地重命名
        d = root / "case3"
        d.mkdir()
        _make(d, ["foo.png"], [])
        r = plan_rename(d)
        m = _plan_map(r, d)
        ck(m.get("foo.png") == "previews/preview01.png",
           f"3 previews/foo.png 应就地重命名为 previews/preview01.png，实际 {m.get('foo.png')!r}")

        # 4. 幂等：apply 后再规划应无操作
        ok, failed = apply_plan(r.plan)
        ck(ok == 1 and not failed, f"4a apply 应成功 1 项无失败，实际 ok={ok} failed={failed}")
        r2 = plan_rename(d)
        ck(len(r2.plan) == 0 and not r2.has_work,
           f"4b apply 后再次规划应无操作，实际 plan={len(r2.plan)} has_work={r2.has_work}")

        # 5. 编号耗尽：100 张图片 -> 报错（编号上限 99）
        d = root / "case5"
        d.mkdir()
        _make(d, [], [f"img{i:03d}.png" for i in range(100)])
        r = plan_rename(d)
        ck(bool(r.errors) and any("编号耗尽" in e for e in r.errors),
           f"5 编号耗尽应报错，实际 errors={r.errors!r}")

        # 6. 无 previews/ 时未规范命名顶层图片分配编号
        d = root / "case6"
        d.mkdir()
        _make(d, [], ["a.png", "b.jpg"])
        r = plan_rename(d)
        m = _plan_map(r, d)
        ck(m.get("a.png") == "previews/preview01.png" and m.get("b.jpg") == "previews/preview02.jpg",
           f"6 无 previews/ 时未规范图片应分配编号，实际 {m!r}")

    print("=" * 50)
    all_ok = all(ok for ok, _ in checks)
    for i, (ok, msg) in enumerate(checks, 1):
        print(f"检查 {i}: {'PASS' if ok else 'FAIL'}  {msg}")
    print("organize_previews 同名重编号 测试:", "全部通过" if all_ok else "存在失败")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
