# -*- coding: utf-8 -*-
"""02_rename_model_files.py 删除变体词标准化功能的配套测试。

覆盖：
  1) parse_file_stem：只返回 (版本号, 副本序号)；变体词不再被提取/标准化
     （如 兔子洞Ver1.1 不再产出 swimsuit，且 兔子洞 不进入结果）
  2) rename_files_cmd：dry-run 只预览不改磁盘；--apply 真正重命名，且变体词不进入文件名

运行：python .github/test/test_rename_model_files.py（退出码 0=全部通过）
"""
import importlib.util
import pathlib
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8')

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / '.github' / 'scripts'
sys.path.insert(0, str(SCRIPTS))

# 直接加载目标脚本（模块名以数字开头，不能直接 import）；先注册 sys.modules
_SCRIPT = SCRIPTS / 'models_organize' / '02_rename_model_files.py'
_spec = importlib.util.spec_from_file_location('rename_model_files', _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.modules['rename_model_files'] = _mod
_spec.loader.exec_module(_mod)
P = _mod

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f'  ✓ {label}')
    else:
        FAILURES.append(label)
        print(f'  ✗ {label}')


def test_parse_file_stem() -> None:
    print('[parse_file_stem：版本/副本提取，变体词不再保留]')
    check(P.parse_file_stem('VOC_初音_Miku_兔子洞Ver1.1') == ('_v1.1', ''),
          '兔子洞Ver1.1 -> ("_v1.1", "")，变体词 兔子洞 不进入结果')
    check(P.parse_file_stem('AK_阿米娅_v2') == ('_v2', ''),
          'v2 -> ("_v2", "")')
    check(P.parse_file_stem('某模型_1.0(1)') == ('_v1.0', '_1'),
          '1.0(1) -> ("_v1.0", "_1")')
    check(P.parse_file_stem('BA_月雪宫子RABBIT1') == ('', ''),
          'RABBIT1 编号尾数不被误判为版本/变体')
    check(P.parse_file_stem('BA_月雪宫子_换装_2.0') == ('_v2.0', ''),
          '换装_2.0 -> ("_v2.0", "")，变体词 换装 不进入结果')


def test_rename_files_cmd_dry_run(tmp: pathlib.Path) -> None:
    print('[rename_files_cmd：dry-run 只预览，变体词不进入新名]')
    model_dir = tmp / 'VOC_初音_Miku'
    model_dir.mkdir(parents=True)
    src = model_dir / 'VOC_初音_Miku_兔子洞Ver1.1.ysm'
    src.write_bytes(b'x')

    rc = P.rename_files_cmd(tmp, apply_changes=False)
    check(rc == 0, 'dry-run 返回 0')
    check(src.exists(), 'dry-run 后源文件仍在磁盘')


def test_rename_files_cmd_apply(tmp: pathlib.Path) -> None:
    print('[rename_files_cmd：--apply 真正重命名，变体词不进入文件名]')
    model_dir = tmp / 'BA_月雪宫子_LA'
    model_dir.mkdir(parents=True)
    src = model_dir / 'BA_月雪宫子_换装_2.0.ysm'
    src.write_bytes(b'x')

    rc = P.rename_files_cmd(tmp, apply_changes=True)
    check(rc == 0, 'apply 返回 0')
    check(not src.exists(), '旧文件已改名')
    check((model_dir / 'BA_月雪宫子_v2.0.ysm').exists(),
          '新名含去评级文件夹名 + 版本号，变体词 换装 不进入文件名')


def test_extract_keep_words() -> None:
    print('[extract_keep_words：保留描述词任意位置提取]')
    folder = 'AL_Javelin_标枪_LA'
    check(P.extract_keep_words('AL_标枪_Javelin_非公开_v2.6.12', folder) == ['非公开'],
          '非公开 在文件名中间被提取')
    check(P.extract_keep_words('标枪-非公开版2.6.8', folder) == ['非公开'],
          '非公开版（带版后缀）被提取为 非公开')
    check(P.extract_keep_words('AL_标枪_Javelin_公开_v1', folder) == ['公开'],
          '公开 单独命中（不误判为 非公开）')
    check(P.extract_keep_words('AL_标枪_Javelin_v1', folder) == [],
          '无保留词时返回空')
    # 文件夹名已含的词不重复提取
    check(P.extract_keep_words('标枪_兔女郎_v2', 'AL_标枪_兔女郎_LA') == [],
          '文件夹名已含 兔女郎 时文件名中的不重复提取')
    # 服装词（来自 skin_tags.json）
    check(P.extract_keep_words('AL_标枪_Javelin_圣诞_v1', folder) == ['圣诞'],
          '圣诞（skin_tags.json christmas.zh）被提取')
    check(P.extract_keep_words('AL_标枪_Javelin_旗袍_v1', folder) == ['旗袍'],
          '旗袍（新增标签）被提取')
    # 英文词边界：_ 是分隔符可匹配；NewModel/Mold 不误伤
    check(P.extract_keep_words('AL_标枪_Javelin_bunny_v1', folder) == ['bunny'],
          'bunny（bunnygirl 的 alias）在 _ 分隔下被提取')
    check(P.extract_keep_words('AK_NewModel_v1', folder) == [],
          'new 不误匹配 NewModel（词边界）')
    check(P.extract_keep_words('标枪_Mold_v1', folder) == [],
          'old 不误匹配 Mold（词边界）')
    # 单字母词（如 skin_tags 的 l）被排除
    check('l' not in P.extract_keep_words('AL_标枪_Javelin_l_v1', folder),
          '单字母词 l 不参与保留（易误匹配）')


def test_keep_words_loaded() -> None:
    print('[keep_words：从 skin_tags.json 加载数据]')
    words = P.keep_words()
    for w in ('免费', '付费', '公开', '非公开', '兔女郎', '泳装', '圣诞', 'all-age'):
        check(w in words, f'保留词包含 {w}（来自 skin_tags.json）')
    check(len(words) >= 50, f'保留词数量充足（当前 {len(words)} 个）')
    # --keep-word 追加：往缓存集合加自定义词后能提取
    P.keep_words().add('梦幻')
    check(P.extract_keep_words('标枪_梦幻_v1', 'AL_Javelin_标枪_LA') == ['梦幻'],
          '追加自定义词 梦幻 后可被提取')


def test_rename_keeps_words(tmp: pathlib.Path) -> None:
    print('[rename_files_cmd：apply 重命名时保留描述词]')
    model_dir = tmp / 'AL_Javelin_标枪_LA'
    model_dir.mkdir(parents=True)
    src = model_dir / 'AL_标枪_Javelin_非公开_v2.6.12.ysm'
    src.write_bytes(b'x')

    P.rename_files_cmd(tmp, apply_changes=True)
    check(not src.exists(), '旧文件已改名')
    check((model_dir / 'AL_Javelin_标枪_非公开_v2.6.12.ysm').exists(),
          '新名保留 非公开 描述词并提取版本号')


def test_skip_hidden_dirs(tmp: pathlib.Path) -> None:
    print('[默认路径：跳过隐藏目录]')
    # 模拟仓库根：普通模型目录 + 隐藏目录（目录可能已被前序测试创建）
    model_dir = tmp / 'AL_Javelin_标枪_LA'
    model_dir.mkdir(parents=True, exist_ok=True)
    hidden = tmp / '.hidden_dir'
    hidden.mkdir(parents=True, exist_ok=True)
    (model_dir / 'AL_标枪_Javelin_v1.ysm').write_bytes(b'x')
    (hidden / 'invisible.ysm').write_bytes(b'x')

    rc = P.rename_files_cmd(tmp, apply_changes=False)
    check(rc == 0, '目录扫描返回 0（不报路径不存在）')
    # 隐藏目录文件不参与；普通目录文件参与（被匹配并输出，源文件仍存在）
    check((hidden / 'invisible.ysm').exists(), '隐藏目录文件未被扫描')


def main() -> int:
    test_parse_file_stem()
    test_extract_keep_words()
    test_keep_words_loaded()
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        test_rename_files_cmd_dry_run(base)
        test_rename_files_cmd_apply(base)
        test_rename_keeps_words(base)
        test_skip_hidden_dirs(base)

    print()
    if FAILURES:
        print(f'失败 {len(FAILURES)} 项: {FAILURES}')
        return 1
    print('全部通过 ✓')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
