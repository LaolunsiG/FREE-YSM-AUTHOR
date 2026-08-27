"""作者 README 解析：作者名提取、作者索引构建（消除各脚本重复实现）。"""
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import paths

# 作者 README 的 Name 行、Author 段标记
NAME_LINE_RE = re.compile(r'\s*-\s*\*\*(?:Name|作者名称)\*\*\s*[:：]\s*(.*)')
AUTHOR_SECTION_RE = re.compile(r'##\s*Author\b(.*?)(?=\n##|\Z)', re.DOTALL | re.IGNORECASE)
# 根 README 作者索引行：| 0095 | [#name](.../../Models/0095) | 19 |
INDEX_ROW_RE = re.compile(r'^\|\s*(\d{4})\s*\|\s*\[([^\]]+)\]\(([^)]*)\)\s*\|')
# 作者字符串分隔符（全/半角）与 "中文(English)" 拆分
AUTHOR_SPLIT_RE = re.compile(r'[\s|｜,，、;/；]+')
PAREN_PAIR_RE = re.compile(r'^([^()（）]*)[(（]([^)）]*)[)）]$')
# 作者 README 中 2 空格缩进的平台账号子行（如 "  - **Bilibili**: [name](url)"）
PLATFORM_LINE_RE = re.compile(r'^  - \*\*([^*]+)\*\*\s*[:：]\s*(.+)$')
# 宽松版平台行（0-2 空格缩进、冒号可选）：audit_models 合并平台行时使用，
# 兼容容器行（- **SocialPlatform**: #Bilibili）与子行（- **Bilibili**: [..](..)）
PLATFORM_ANY_LINE_RE = re.compile(r'^\s{0,2}-\s*\*\*([^*]+)\*\*\s*[:：]?\s*(.*)$')
# 根 README 作者表自动生成区域标记（03_generate_root_readme.py --author 写入、translate_readme 保护）
AUTHORS_LIST_START = '<!-- AUTHORS_LIST_START -->'
AUTHORS_LIST_END = '<!-- AUTHORS_LIST_END -->'


def parse_author_name_value(content: str) -> str:
    """提取 ## Author 段内第一个 Name 行（避开 Co-creator）；无 Author 段时退回全文首行匹配。"""
    m = AUTHOR_SECTION_RE.search(content)
    scope = m.group(1) if m else content
    for line in scope.splitlines():
        m2 = NAME_LINE_RE.match(line)
        if m2:
            return m2.group(1).strip()
    return ''


def extract_primary_author_name(content: str) -> str:
    """提取主作者名称（避开 Co-creator 区块）；无结果返回 '暂无'。"""
    return parse_author_name_value(content) or '暂无'


def normalize_alias(s: str) -> str:
    """作者别名归一化：NFKC、去空白（含全角）、去 #、去首尾标点、小写。"""
    s = unicodedata.normalize('NFKC', s)
    s = re.sub(r'[\s\u00a0\u200b]', '', s)
    s = s.lstrip('#＃')
    s = s.strip(' 　._-·•\\')
    return s.lower()


def split_authors(authors_str: str) -> list[str]:
    """把 authors 原始串拆成候选别名。'鸡姬(raw_chicken)' -> ['鸡姬', 'raw_chicken']；
    'A | B' -> ['A', 'B']。"""
    candidates: list[str] = []
    for seg in AUTHOR_SPLIT_RE.split(authors_str):
        seg = seg.strip()
        if not seg:
            continue
        m = PAREN_PAIR_RE.match(seg)
        if m:
            outer, inner = m.group(1).strip(), m.group(2).strip()
            if outer and inner:
                candidates.extend([outer, inner])
            else:
                candidates.append(outer or inner)
        else:
            candidates.append(seg)
    return candidates


def _can_substr_match(key: str) -> bool:
    """子串匹配门槛：>=4 字符，或含中文且 >=2 字符（中文作者名常见 2-3 字）。"""
    if len(key) >= 4:
        return True
    return len(key) >= 2 and re.search(r'[\u4e00-\u9fff]', key) is not None


def find_author(authors_str: str, alias_to_id: dict[str, str],
                verbose: bool = False) -> tuple[str | None, str]:
    """返回 (作者编号 或 None, 说明)。

    按候选顺序逐个匹配（主作者在前）：先看完全匹配，再看子串匹配；
    第一个有匹配的候选即返回，避免多作者时被次要作者"抢走"归属。
    """
    candidates = split_authors(authors_str)
    if verbose:
        print(f"  候选别名: {candidates}")

    for cand in candidates:
        key = normalize_alias(cand)
        if not key:
            continue
        if key in alias_to_id:
            return alias_to_id[key], f"完全匹配: {cand}"
        if _can_substr_match(key):
            best: tuple[int, str, str] | None = None  # (匹配别名长度, 编号, 别名)
            for alias, author_id in alias_to_id.items():
                if not _can_substr_match(alias):
                    continue
                if key in alias or alias in key:
                    if best is None or len(alias) > best[0]:
                        best = (len(alias), author_id, alias)
            if best:
                return best[1], f"子串匹配: {best[2]}"

    return None, "未命中任何已收录作者"


def match_author_id(name: str, alias_to_id: dict[str, str],
                    runtime_index: dict[str, str] | None = None,
                    verbose: bool = False) -> tuple[str | None, str]:
    """匹配作者编号（纯查询，无副作用）；归档与重新分类共用的唯一入口。

    与 organize_models 归档时的判定完全一致（同一函数）：
    alias_to_id 为静态别名→编号索引，runtime_index 为本次运行新增的作者（归档时
    逐条并入，重新分类场景为空）；返回 (编号 或 None, 匹配说明)。
    """
    idx = {**alias_to_id, **(runtime_index or {})}
    return find_author(name, idx, verbose)


def extract_platforms(content: str) -> dict[str, str]:
    """从作者 README 的 Author 段提取平台账号（孙项行）。
    值为 Markdown 链接时取 URL，否则取文本（如 QQ 号）。"""
    m = AUTHOR_SECTION_RE.search(content)
    scope = m.group(1) if m else content
    platforms: dict[str, str] = {}
    for line in scope.splitlines():
        pm = PLATFORM_LINE_RE.match(line)
        if not pm:
            continue
        key = pm.group(1).strip()
        value = pm.group(2).strip()
        url_m = re.search(r'\]\(([^)]+)\)', value)
        platforms[key] = url_m.group(1) if url_m else value
    return platforms


def split_author_names(name_value: str) -> list[str]:
    """把 Name 值拆成名称数组：先去 Markdown 链接语法，再按 | 拆分去空去重。

    兼容部分作者 README 里 Name 写成链接的情况（如 0058/0156），
    避免把链接 URL 污染进作者数据；去重防止合并/手改产生的重复别名。"""
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', name_value)
    out: list[str] = []
    for p in text.split('|'):
        p = p.strip()
        if p and p not in out:
            out.append(p)
    return out


def build_authors_data(models_dir: Path, root_readme: Path) -> dict:
    """构建集中作者数据（authors.json 的结构）。Models README 优先，根 README 索引补缺。"""
    authors: dict[str, dict] = {}

    if models_dir.is_dir():
        for author_dir in sorted(models_dir.iterdir()):
            if not (author_dir.is_dir() and re.fullmatch(r'\d{4}', author_dir.name)):
                continue
            readme = author_dir / 'README.md'
            if not readme.is_file():
                continue
            content = paths.read_text_utf8(readme)
            name_value = parse_author_name_value(content)
            if not name_value:
                continue
            rel_readme = ''
            try:
                rel_readme = str(readme.relative_to(models_dir.parent)).replace('\\', '/')
            except ValueError:
                rel_readme = str(readme)
            authors[author_dir.name] = {
                'name': split_author_names(name_value),
                'readme': rel_readme,
                'platforms': extract_platforms(content),
            }

    # 根 README 索引补缺（仅补"编号目录存在但无 README"的作者；
    # 目录已删除的作者不补，避免根表过期行补回幽灵作者）
    if root_readme.is_file():
        for line in paths.read_text_utf8(root_readme).splitlines():
            m = INDEX_ROW_RE.match(line.strip())
            if not m:
                continue
            author_id = m.group(1)
            if author_id in authors or not (models_dir / author_id).is_dir():
                continue
            authors[author_id] = {
                'name': split_author_names(m.group(2).strip()),
                'readme': '',
                'platforms': {},
            }

    return {
        'version': 1,
        'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'authors': authors,
    }


def load_authors_index() -> dict:
    """读取集中作者数据 author-info/authors.json；缺失/损坏返回空 dict。"""
    return paths.load_json(paths.data_path('author-info', 'authors.json'), {})


def build_author_index(models_dir: Path, root_readme: Path) -> tuple[dict[str, str], dict[str, str]]:
    """返回 (别名->编号, 编号->显示名)。优先用集中数据 authors.json，缺失时回退扫描。"""
    data = load_authors_index()
    authors = data.get('authors') if isinstance(data, dict) else None
    if authors:
        alias_to_id: dict[str, str] = {}
        id_to_name: dict[str, str] = {}
        for author_id, entry in authors.items():
            names = entry.get('name') or []
            if isinstance(names, str):
                # 兼容旧结构（name 为字符串）：拆成数组再索引
                names = split_author_names(names)
            for i, name in enumerate(names):
                if not name:
                    continue
                if i == 0:
                    id_to_name.setdefault(author_id, name)
                key = normalize_alias(name)
                if key:
                    alias_to_id.setdefault(key, author_id)
        return alias_to_id, id_to_name
    return build_author_index_scan(models_dir, root_readme)


def build_author_index_scan(models_dir: Path, root_readme: Path) -> tuple[dict[str, str], dict[str, str]]:
    """扫描版回退：直接解析 Models README 与根 README 索引（authors.json 缺失时使用）。"""
    alias_to_id: dict[str, str] = {}
    id_to_name: dict[str, str] = {}

    def register(name_value: str, author_id: str) -> None:
        if not author_id or not name_value:
            return
        id_to_name.setdefault(author_id, name_value)
        for alias in [a for a in (x.strip() for x in name_value.split('|')) if a]:
            key = normalize_alias(alias)
            if key:
                alias_to_id.setdefault(key, author_id)

    if models_dir.is_dir():
        for author_dir in sorted(models_dir.iterdir()):
            if not (author_dir.is_dir() and re.fullmatch(r'\d{4}', author_dir.name)):
                continue
            readme = author_dir / 'README.md'
            if not readme.is_file():
                continue
            register(parse_author_name_value(paths.read_text_utf8(readme)), author_dir.name)

    if root_readme.is_file():
        for line in paths.read_text_utf8(root_readme).splitlines():
            m = INDEX_ROW_RE.match(line.strip())
            if not m:
                continue
            author_id, name_value = m.group(1), m.group(2).strip()
            # 已在 Models README 注册过的不覆盖；仅补缺（如编号目录存在但无 README）
            if author_id not in id_to_name:
                register(name_value, author_id)

    return alias_to_id, id_to_name


def build_author_readme_index(models_dir: Path) -> dict[str, Path]:
    """作者名/别名 -> README 路径 索引（作者 README 交叉链接用）。

    authors.json 优先（readme 字段定位），缺失时回退扫描 Models 作者 README；
    与 build_author_index 共享同一数据源，消除 format_author_readme 的重复扫描实现。
    """
    data = load_authors_index()
    authors = data.get('authors') if isinstance(data, dict) else None
    if authors:
        index: dict[str, Path] = {}
        for entry in authors.values():
            rel = entry.get('readme') or ''
            if not rel:
                continue
            readme_path = models_dir.parent / rel
            if not readme_path.is_file():
                continue
            names = entry.get('name') or []
            if isinstance(names, str):
                names = split_author_names(names)
            for name in names:
                if name and name not in index:
                    index[name] = readme_path
        return index
    # 回退扫描 Models 作者 README（authors.json 缺失时）
    index = {}
    if models_dir.is_dir():
        for author_dir in sorted(models_dir.iterdir()):
            if not (author_dir.is_dir() and re.fullmatch(r'\d{4}', author_dir.name)):
                continue
            readme = author_dir / 'README.md'
            if not readme.is_file():
                continue
            name_value = parse_author_name_value(paths.read_text_utf8(readme))
            for name in split_author_names(name_value):
                if name and name not in index:
                    index[name] = readme
    return index
