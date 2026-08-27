# -*- coding: utf-8 -*-
"""
03_generate_model_readmes.py 的 Download 区块渲染测试（2026 改版后）。

覆盖：
  1) _human_size：B/KB/MB 人性化显示
  2) build_download_lines：URL percent-encoding（中文/空格/间隔号）、相对路径、
     大小显示、排序；.ysm 与压缩包（zip/7z）混合收录；非白名单（txt/md）排除；
     仅压缩包无 .ysm 也输出；无任何可下载文件返回空
  3) 集成 [c1]：区块题为 Model Info -> Download -> Author Info，顺序正确
  4) 集成 [c2]：Model Info 内为预览图 + Name/Category/Game 字段；
     Author Info 只含 Author/Co-creator（不含 Category/Game 字段）
  5) 集成 [c3]：所有收缩块默认展开（`<details open>`，无裸 `<details>`）
  6) 集成 [c4]：Game 标签 = 简写 + 中英文第一个标准名（不含 ja/别名重复）
  7) 无可下载文件时不输出 Download 区块

测试数据放 Models/_test_rename/（.gitignore 排除，不污染仓库）。
运行：python .github/test/test_model_readme_download.py（退出码 0=全过）
"""
import importlib.util
import pathlib
import sys
import tempfile
from urllib.parse import quote

sys.stdout.reconfigure(encoding='utf-8')

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / ".github" / "scripts"

# 03 脚本是顶层脚本（顶层代码只定义函数/常量，main 受 __name__ 保护），可安全 import
_spec = importlib.util.spec_from_file_location(
    "gen_model_readmes", SCRIPTS / "models_organize" / "03_generate_model_readmes.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def main() -> int:
    checks: list[tuple[bool, str]] = []

    def ck(cond: bool, msg: str) -> None:
        checks.append((cond, msg))

    # 1. 大小显示
    ck(gen._human_size(512) == "512 B", f"_human_size(512)={gen._human_size(512)!r} (期望 '512 B')")
    ck(gen._human_size(1024) == "1.0 KB", f"_human_size(1024)={gen._human_size(1024)!r}")
    ck(gen._human_size(500 * 1024) == "500.0 KB", f"_human_size(500KB)={gen._human_size(500 * 1024)!r}")
    ck(gen._human_size(3 * 1024 * 1024) == "3.0 MB", f"_human_size(3MB)={gen._human_size(3 * 1024 * 1024)!r}")

    with tempfile.TemporaryDirectory(dir=(REPO / "Models" / "_test_rename")) as tmp:
        tdir = pathlib.Path(tmp) / "AIC_Alice_爱丽丝·玛格特洛依德_LA"
        tdir.mkdir(parents=True)
        # 一个 .ysm、一个 zip 压缩包、一个非白名单 txt（不应收录）
        (tdir / "model_v1.ysm").write_bytes(b"x" * 500 * 1024)     # 500.0 KB
        (tdir / "bundle.7z").write_bytes(b"y" * (3 * 1024 * 1024 + 512 * 1024))  # 3.5 MB
        (tdir / "README.txt").write_text("ignore me")

        # 2. build_download_lines：.ysm + 压缩包混合、txt 排除、URL/大小/顺序
        lines = gen.build_download_lines(tdir, gen.RAW_BASE)
        ck(len(lines) == 2, f"download 行数={len(lines)} (期望 2：ysm+7z，txt 排除)")
        rel = tdir.relative_to(gen.WORKSPACE_ROOT).as_posix()
        ck(rel.startswith("Models/_test_rename/") and "/" in rel and "\\" not in rel,
           f"相对路径={rel!r} (期望 / 分隔、Models/ 开头)")
        enc_dir = quote(rel, safe="/")
        v1_url = f"{gen.RAW_BASE}/{enc_dir}/model_v1.ysm"
        ck(f"- [model_v1.ysm (500.0 KB)]({v1_url})" in lines,
           f"ysm 行={[l for l in lines if 'model_v1' in l]!r}")
        ck(f"- [bundle.7z (3.5 MB)]({gen.RAW_BASE}/{enc_dir}/bundle.7z)" in lines,
           f"7z 行={[l for l in lines if 'bundle' in l]!r} (压缩包应收录)")
        ck(not any("README.txt" in l for l in lines),
           f"txt 不应收录: {lines!r}")
        # 排序：bundle < model_v1
        ck(lines[0].startswith("- [bundle.7z"), f"排序={[l[2:14] for l in lines]!r}")

        # 2b. 仅压缩包、无 .ysm 也输出
        zip_only = pathlib.Path(tmp) / "zip_only"
        zip_only.mkdir()
        (zip_only / "pack.zip").write_bytes(b"z" * 2048)  # 2.0 KB
        zl = gen.build_download_lines(zip_only, gen.RAW_BASE)
        ck(len(zl) == 1 and zl[0].startswith("- [pack.zip (2.0 KB)]"),
           f"仅压缩包行={zl!r}")

        # 2c. 无任何可下载文件 -> 空
        empty_dir = pathlib.Path(tmp) / "empty"
        empty_dir.mkdir()
        (empty_dir / "note.txt").write_text("no downloadables")
        ck(gen.build_download_lines(empty_dir, gen.RAW_BASE) == [],
           f"空目录={gen.build_download_lines(empty_dir, gen.RAW_BASE)!r} (期望 [])")

        # 3-6. 集成渲染
        model_authors = [{'name': ['测试'], 'platforms': {},
                          'role': '', 'author_id': '0000'}]
        content = gen.build_meta_and_preview_content(
            tdir, [], "#Game", "#AIC #Alice in Cradle #爱丽丝的摇篮",
            [], model_authors,
            "#爱丽丝·玛格特洛依德 | #Alice-Margatroid")
        # 3. 区块顺序与标题 [c1]
        idx_info = content.find("## Model Info")
        idx_dl = content.find("## Download")
        idx_author = content.find("## Author Info")
        ck(idx_info != -1 and idx_dl != -1 and idx_author != -1,
           f"三区块都应存在 (info={idx_info}, dl={idx_dl}, author={idx_author})")
        ck(idx_info < idx_dl < idx_author,
           f"区块顺序 Model Info={idx_info} < Download={idx_dl} < Author Info={idx_author}")
        ck("## Preview Images" not in content and "## Model Details" not in content,
           "旧标题 Preview Images / Model Details 不应再出现")
        # 4. Name/Category/Game 在 Model Info 段内；Author Info 段不含 Category/Game [c2]
        info_seg = content[idx_info:idx_dl]
        author_seg = content[idx_author:]
        ck("- **Name**: #爱丽丝·玛格特洛依德 | #Alice-Margatroid" in info_seg,
           "Name 字段应在 Model Info 内")
        ck("- **Category**: #Game" in info_seg and "- **Game**:" in info_seg,
           "Category/Game 字段应在 Model Info 内")
        ck("- **Category**:" not in author_seg and "- **Game**:" not in author_seg,
           "Author Info 不应含 Category/Game 字段")
        # 5. 所有收缩块默认展开 [c3]
        ck('<details open>' in content and '<details>' not in content,
           f"全部 <details open>（裸 details 检查: '<details>' in content = {'<details>' in content}）")
        ck(content.count('<details open>') >= 3,
           f"至少 3 个 <details open>（实际 {content.count('<details open>')}）")
        # 6. Game 标签格式（简写 + 中英文首名，无 ja）[c4]
        works = gen.load_works()
        ck(gen.get_work_tags(works, "MGWT")
           == "#MGWT #Magical Girl Witch Trial #魔法少女的魔女审判",
           f"get_work_tags(MGWT)={gen.get_work_tags(works, 'MGWT')!r}")
        ck("魔法少女ノ魔女裁判" not in gen.get_work_tags(works, "MGWT"),
           "Game 标签不应含日语名")
        ck(gen.get_work_tags(works, "Touhou")
           == "#Touhou #Touhou-Project #东方 Project",
           f"get_work_tags(Touhou)={gen.get_work_tags(works, 'Touhou')!r} (en 取首项，别名不重复)")
        ck(gen.get_work_tags(works, "NOPE") == "#Unknown",
           "未匹配作品前缀 -> #Unknown")

        # 7. 无可下载文件时不输出 Download 区块
        model_authors2 = [{'name': ['测试'], 'platforms': {},
                           'role': '', 'author_id': '0000'}]
        content_empty = gen.build_meta_and_preview_content(
            empty_dir, [], "#Unknown", "",
            [], model_authors2, "")
        ck("## Download" not in content_empty,
           "无 .ysm/压缩包时不输出 Download 区块")

    print("=" * 50)
    all_ok = all(ok for ok, _ in checks)
    for i, (ok, msg) in enumerate(checks, 1):
        print(f"检查 {i}: {'PASS' if ok else 'FAIL'}  {msg}")
    print("模型 README 改版渲染测试:", "全部通过" if all_ok else "存在失败")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())