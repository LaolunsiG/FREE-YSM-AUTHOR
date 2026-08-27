# -*- coding: utf-8 -*-
""".ysm 元数据解析、作者分类与平台映射；organize_models（归档）与
generate_model_readmes（模型 README 的 co-creator 识别）共用。

导出：
  extract_metadata(path)    从 .ysm 文本头提取 {name, authors, author_blocks}
  classify_authors(blocks)  作者块分类 -> (primary, model_blocks, co_creators)
  load_platform_map(root)   读平台映射数据文件（支持 --root 跟随）
  map_platforms(contacts)   作者块 contacts -> README 模板字段分组
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 把 .github/scripts 加回 sys.path，保证 lib/ 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import maker_rules as lib_maker_rules  # noqa: E402
from lib import paths as lib_paths  # noqa: E402

# ---------------------------------------------------------------------------
# .ysm 元数据提取正则：兼容多版本
#   旧版(Property): <name> xxx / <authors> xxx（单行标签，值到行尾）
#   新版(Metadata 2.5+): <author> 块结构，作者名在缩进的 <name> 子标签里，可有多个作者块
#   2026 新格式(format 26+): Metadata 段单行 <author> 名字（单数标签，值到行尾）
#   早期无头版: JSON 键 "name"/"authors"
# ---------------------------------------------------------------------------
TOP_NAME_RE = re.compile(r'(?m)^<name>\s*([^\r\n<]+?)\s*$')            # 顶层模型名（行首无缩进）
AUTHORS_LINE_RE = re.compile(r'(?m)^<authors>\s*([^\r\n<]+?)\s*$')     # 旧版单行 authors（复数）
AUTHOR_SINGLE_RE = re.compile(r'(?m)^<author>\s*([^\r\n<]+?)\s*$')     # 2026 单行 author（单数）
AUTHOR_BLOCK_RE = re.compile(r'<author>(.*?)(?=<author>|$)', re.DOTALL)  # 新版作者块
BLOCK_NAME_RE = re.compile(r'(?m)^\s*<name>\s*([^\r\n<]+?)\s*$')       # 块内缩进 <name>
BLOCK_ROLE_RE = re.compile(r'(?m)^\s*<role>\s*([^\r\n<]+?)\s*$')       # 块内缩进 <role>
BLOCK_CONTACT_RE = re.compile(r'(?m)^\s*<contact-\s*([^>]+?)\s*>\s*([^\r\n<]+?)\s*$')  # 块内 <contact-X> 平台
JSON_NAME_RE = re.compile(r'"name"\s*:\s*"([^"]*)"')
JSON_AUTHORS_RE = re.compile(r'"(?:authors|author)"\s*:\s*"([^"]*)"')
EXPORT_SECTION_RE = re.compile(r'\[ ?Export ?\]')


def _meta_path(root: Path | None, fname: str) -> Path:
    """数据路径：优先跟随调用方 root（临时仓库/测试），否则用 lib 语义路径。"""
    if root and root != lib_paths.WORKSPACE_ROOT:
        return root / '.github' / 'data' / 'author-info' / fname
    return lib_paths.data_path('author-info', fname)


# .ysm 元数据只位于 [Export] 段之前的文本头；大文件（数十 MB）的二进制区无需读取。
# 先只读头部，解析不到（无头旧版/极长头部）再回退读全量，兼顾性能与兼容。
_HEAD_BYTES = 256 * 1024


def _parse_metadata_text(text: str) -> dict:
    """从 .ysm 文本（头部或全文）解析 {'name', 'authors', 'author_blocks'}。

    只解析 [Export] 段之前，避免加密二进制区出现巧合标签字节。
    """
    export_m = EXPORT_SECTION_RE.search(text)
    head = text[:export_m.start()] if export_m else text

    fields: dict[str, str] = {}

    m = TOP_NAME_RE.search(head)
    if m:
        fields['name'] = m.group(1).strip()

    author_blocks: list[dict] = []
    m = AUTHORS_LINE_RE.search(head)
    if m:
        # 旧版单行 authors：退化为单个块（无 role/contacts）
        fields['authors'] = m.group(1).strip()
        author_blocks.append({'name': fields['authors'], 'role': '', 'contacts': {}})
    else:
        # 新版块结构：每个 <author> 块内缩进的 <name>/<role>/<contact-X>
        for block in AUTHOR_BLOCK_RE.findall(head):
            m_name = BLOCK_NAME_RE.search(block)
            if not m_name:
                continue
            name = m_name.group(1).strip()
            m_role = BLOCK_ROLE_RE.search(block)
            role = m_role.group(1).strip() if m_role else ''
            contacts: dict[str, str] = {}
            for m_c in BLOCK_CONTACT_RE.finditer(block):
                key = m_c.group(1).strip()
                val = m_c.group(2).strip()
                if key and val:
                    contacts.setdefault(key, val)
            author_blocks.append({'name': name, 'role': role, 'contacts': contacts})
        # 2026 新格式：Metadata 段单行 <author> 名字（Property 风格）。
        # 块解析为空时才启用——块结构的 <author> 行不带值，不会被此正则误匹配。
        if not author_blocks:
            for m_a in AUTHOR_SINGLE_RE.finditer(head):
                name = m_a.group(1).strip()
                if name:
                    author_blocks.append({'name': name, 'role': '', 'contacts': {}})
        if author_blocks:
            fields['authors'] = ' | '.join(b['name'] for b in author_blocks)

    # 早期无头版本（JSON 明文）
    if 'name' not in fields:
        m = JSON_NAME_RE.search(head)
        if m:
            fields['name'] = m.group(1)
    if 'authors' not in fields:
        m = JSON_AUTHORS_RE.search(head)
        if m:
            fields['authors'] = m.group(1)
            author_blocks.append({'name': fields['authors'], 'role': '', 'contacts': {}})

    return {'name': fields.get('name'), 'authors': fields.get('authors'),
            'author_blocks': author_blocks}


def extract_metadata(path: Path, quiet: bool = False) -> dict:
    """返回 {'name', 'authors', 'author_blocks'}；读取失败返回空 dict 并打印警告。

    author_blocks: [{'name', 'role', 'contacts': {平台键: 值}}, ...]（新版块结构，
    按出现顺序；旧版单行 authors 退化为单个块）。

    性能：只读文件头部（前 256KB）解析元数据——.ysm 元数据在 [Export] 段之前，
    全量读取会把数十 MB 的二进制区也读进内存；头部未命中（无头旧版/超长头部）时
    回退读全量。quiet=True 时不打印警告（批量扫描场景，避免无头旧版文件刷屏）。
    """
    try:
        with open(path, 'rb') as f:
            head_bytes = f.read(_HEAD_BYTES)
    except OSError as e:
        if not quiet:
            print(f"  [错误] 无法读取: {e}")
        return {}

    meta = _parse_metadata_text(head_bytes.decode('utf-8', errors='ignore'))
    if meta['name'] or meta['authors']:
        return meta

    # 头部未命中：无头旧版或头部超长，读全量重试
    try:
        raw = path.read_bytes()
    except OSError as e:
        if not quiet:
            print(f"  [错误] 无法读取: {e}")
        return {}
    meta = _parse_metadata_text(raw.decode('utf-8', errors='ignore'))
    if not meta['name'] and not meta['authors'] and not quiet:
        print(f"  [警告] 未能从文件中提取到 <name>/<authors> 元数据（可能是无头旧版或损坏文件）")
    return meta


def model_owner(model_dir: Path) -> tuple[str | None, str]:
    """解析模型目录的主作者名（第一个 .ysm 的 primary 块）；返回 (作者名, 文件名)。

    供库整理（audit 重新分类）、作者推导（kb_tool --sync-authors）、
    模型 README 生成等复用：作者目录下模型的 .ysm 主作者是作者信息的可靠来源。
    """
    for f in sorted(model_dir.glob('*.ysm')) + sorted(model_dir.glob('*.YSM')):
        meta = extract_metadata(f, quiet=True)
        blocks = meta.get('author_blocks') or []
        if not blocks:
            continue
        primary, _, _ = classify_authors(blocks)
        if primary:
            return primary['name'], f.name
    return None, ''


# ---------------------------------------------------------------------------
# 多作者分类（三级制作者信号 + 形象来源排除）
#   - 形象来源/版权/设定类 role（如"模型OC"）即使含"模型"也归 co-creator
#   - primary = 制作者信号最强的第一个块：模型类(P1) > 全包类(P2) > 作者自述(P3)
#   - model_blocks = 所有 P1/P2/P3 块（主作者 move、其他 copy）
#   - 无任何制作者信号时取第一个非形象来源块兜底
# ---------------------------------------------------------------------------
# 判定规则外置到 author-info/maker_rules.json（lib/maker_rules.py 读取，
# 缺失/损坏时回退内置默认），这里仅保留薄封装：is_oc_role / maker_level。
def _is_oc_role(role: str) -> bool:
    """role 是否为形象来源/版权/设定类（含"模型"也不算制作者）。"""
    return lib_maker_rules.is_oc_role(role)


def _maker_level(role: str) -> int:
    """返回制作者信号强度：0=非制作者，1=P1 模型类，2=P2 全包类，3=P3 作者自述。"""
    return lib_maker_rules.role_level(role)


def classify_authors(author_blocks: list[dict]) -> tuple[dict | None, list[dict], list[dict]]:
    """返回 (primary, model_blocks, co_creator_blocks)。

    primary: 制作者信号最强的第一个块（P1 模型类 > P2 全包类 > P3 作者自述），
             同级别取先出现的块；无任何制作者信号时取第一个非形象来源块。
    model_blocks: 所有制作者块（含 primary，可能多个 -> 主作者 move、其他 copy）。
    co_creator_blocks: 其余块（形象来源/动画/物理/服务等，仅记录不归档）。
    """
    if not author_blocks:
        return None, [], []
    makers = [(b, _maker_level(b.get('role') or '')) for b in author_blocks]
    makers = [(b, lv) for b, lv in makers if lv > 0]
    if makers:
        # 信号最强 = level 数值最小（1 最强）；同级别保持作者块原有顺序
        best_level = min(lv for _, lv in makers)
        primary = next(b for b, lv in makers if lv == best_level)
        model_blocks = [b for b, _ in makers]
    else:
        # 无制作者信号：避免把形象来源（如"模型OC"）当主作者归档，取第一个非 OC 块
        primary = next((b for b in author_blocks
                        if not _is_oc_role(b.get('role') or '')), author_blocks[0])
        model_blocks = [primary]
    co_creators = [b for b in author_blocks if b not in model_blocks]
    return primary, model_blocks, co_creators


# ---------------------------------------------------------------------------
# 平台信息：contacts -> README 模板字段（SocialPlatform/SupportPlatform/...）
# 数据文件结构：{分类: [平台键...]}（分类为键、平台键列表为值），脚本反查归类。
# ---------------------------------------------------------------------------
def load_platform_map(root: Path | None = None) -> dict[str, dict[str, list[str]]]:
    """读取平台映射（{分类: {平台规范名: [别名...]}}），别名统一小写，数据文件可手工修改。"""
    data = lib_paths.load_json(_meta_path(root, 'platform_map.json'), {})
    out: dict[str, dict[str, list[str]]] = {}
    for field, platforms in data.items():
        field = str(field)
        out[field] = {}
        for canonical, aliases in platforms.items():
            out[field][str(canonical)] = [str(a).lower() for a in aliases]
    return out


def map_platforms(contacts: dict[str, str],
                  platform_map: dict) -> dict[str, list[str]]:
    """把 ysm 的 <contact-X> 映射为 README 模板字段（SocialPlatform/SupportPlatform/
    OtherPlatform/GroupChat -> ['规范平台名: 值', ...]）。platform_map 为
    {分类: {规范名: [别名...]}}，反查别名归属并输出规范名；未映射的平台归入
    OtherPlatform（保留原始键名，便于人工识别）。"""
    reverse: dict[str, tuple[str, str]] = {}  # 别名 -> (分类, 规范名)
    for field, platforms in platform_map.items():
        for canonical, aliases in platforms.items():
            for alias in aliases:
                reverse.setdefault(alias, (field, canonical))
    mapped: dict[str, list[str]] = {}
    for key, val in contacts.items():
        hit = reverse.get(key.strip().lower())
        field, canonical = hit if hit else ('OtherPlatform', key.strip())
        mapped.setdefault(field, [])
        line = f'{canonical}: {val}' if val else canonical
        if line not in mapped[field]:
            mapped[field].append(line)
    return mapped
