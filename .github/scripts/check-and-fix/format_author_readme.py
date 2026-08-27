import os
import re
import sys
from pathlib import Path
# 脚本按流程阶段分类到 scripts/<类别>/ 子目录：把 .github/scripts 加回 sys.path，
# 保证 lib/ 与跨分类脚本可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from lib import paths as lib_paths
from lib import readme as lib_readme

WORKSPACE_ROOT = lib_paths.WORKSPACE_ROOT
MODELS_DIR = WORKSPACE_ROOT / 'Models'
get_safe_relpath = lib_paths.get_safe_relpath

# 平台容器键：其下有子项列表(如 - **Bilibili**: ...)，无有效子项时容器行应被删除
CONTAINER_KEYS = {'socialplatform', 'supportplatform', 'otherplatform', 'groupchat'}


def get_item_key(stripped: str) -> str:
    """提取条目行的键名(小写)，如 - **Bilibili**: xxx -> bilibili"""
    m = re.match(r'^-\s*\*\*([^*]+)\*\*', stripped)
    return m.group(1).strip().lower() if m else ''


def normalize_name_value(name: str) -> str:
    name = name.strip()
    name = re.sub(r'\s*\|\s*', ' | ', name)
    name = re.sub(r'\s+', ' ', name)
    return name


# 作者名/别名 -> README 路径 索引（交叉链接用；复用 lib/readme 统一实现）。
# 惰性构建 + 模块级缓存：避免仅 import 本模块时触发全库扫描的 import 副作用。
_NAME_LINKS_CACHE: dict | None = None


def get_name_links() -> dict:
    """构建作者名/别名 -> README 路径 索引（首次调用时全库扫描一次，之后缓存）。"""
    global _NAME_LINKS_CACHE
    if _NAME_LINKS_CACHE is None:
        _NAME_LINKS_CACHE = lib_readme.build_author_readme_index(MODELS_DIR)
    return _NAME_LINKS_CACHE


def normalize_heading(heading: str) -> str:
    """标题规范化：Author/Co-creator 变体统一，其余标题保持原样"""
    text = heading.strip()
    if text.lower() == 'author':
        return 'Author'
    normalized = text.lower().replace('_', ' ').replace('-', ' ').strip()
    if normalized in {'co creator', 'cocreator', 'co author', 'coauthor'}:
        return 'Co-creator'
    return text


def extract_heading_and_inline_body(line: str) -> tuple[str, str]:
    match = re.search(r'(\s+-\s*\*\*(?:Name|作者名称)\*\*.*)$', line, re.IGNORECASE)
    if not match:
        return line, ''
    heading = line[:match.start(1)].rstrip()
    inline_body = line[match.start(1):].lstrip()
    return heading, inline_body


def section_has_name_items(body: str) -> bool:
    return bool(re.search(r'(?m)^\s*-\s*\*\*(?:Name|作者名称)\*\*', body))


def name_has_link(value: str) -> bool:
    return bool(re.search(r'\[.+?\]\(.+?\)', value))


def find_first_relative_link(lines: list[str]) -> str | None:
    for line in lines:
        match = re.search(r'\]\((\.\.?/[^\)]+)\)', line)
        if match:
            return match.group(1)
    return None


def render_indent(line: str, stripped: str) -> str:
    """按缩进层级规范化子项缩进：Tab 展开为 4 空格，孙项 4 空格，子项 2 空格"""
    raw_indent = line[:len(line) - len(line.lstrip())]
    indent_len = len(raw_indent.expandtabs(4))
    if indent_len >= 4:
        return '    ' + stripped
    return '  ' + stripped


def normalize_item_lines(item_lines: list[str], source_path: Path) -> list[str]:
    first_line = item_lines[0].strip()
    match = re.match(r'-\s*\*\*(?:Name|作者名称)\*\*\s*[:：]\s*(.*)', first_line, re.IGNORECASE)
    name_value = match.group(1).strip() if match else ''

    if not name_has_link(name_value):
        target = find_first_relative_link(item_lines[1:])
        if target and name_value:
            name_value = f'[{name_value}]({target})'
        elif name_value:
            normalized = normalize_name_value(name_value)
            name_links = get_name_links()
            if normalized in name_links:
                target_path = name_links[normalized]
                if target_path != source_path:
                    relative = os.path.relpath(target_path, start=source_path.parent).replace('\\', '/')
                    name_value = f'[{name_value}]({relative})'

    if name_has_link(name_value):
        name_value = re.sub(r'\)\)+$', ')', name_value)

    rendered = [f'- **Name**: {name_value}']
    lines = [line for line in item_lines[1:] if line.strip()]
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        role_match = re.match(r'^-\s*\*\*Role\*\*\s*[:：]\s*(.*)$', stripped, re.IGNORECASE)
        if role_match:
            # 作者级 Role 已废弃（作者在不同模型里负责功能不一致）：
            # 格式化时直接删除 Role 行，不再补全默认值。
            i += 1
            continue

        if re.match(r'^#{2,}\s', stripped):
            # 块内的子标题(如 ###)保持原样，不做缩进规范化
            rendered.append(stripped)
            i += 1
            continue

        key = get_item_key(stripped)
        if key in CONTAINER_KEYS:
            # 容器行(如 - **SupportPlatform**: #Afdian)：
            # 其后确有有效子项列表才保留，子项渲染为 4 空格孙项；否则删除容器行
            has_sub = False
            if i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                nxt_key = get_item_key(nxt)
                has_sub = bool(nxt_key) and nxt_key not in CONTAINER_KEYS \
                    and nxt_key != 'role' and not re.match(r'^#{2,}\s', nxt)
            if has_sub:
                rendered.append(render_indent(line, stripped))
                rendered.append('    ' + lines[i + 1].strip())
                i += 2
            else:
                i += 1  # 无有效子项：容器行删除
            continue

        rendered.append(render_indent(line, stripped))
        i += 1

    return rendered




def parse_section_blocks(section_body: str) -> list[list[str]]:
    """按 Name 条目分组；Name 之前的说明文本不构成条目块，直接跳过"""
    section_lines = section_body.splitlines()
    item_blocks: list[list[str]] = []
    current_block: list[str] = []
    for line in section_lines:
        if re.match(r'^\s*-\s*\*\*(?:Name|作者名称)\*\*', line):
            if current_block:
                item_blocks.append(current_block)
            current_block = [line]
        elif current_block:
            current_block.append(line)
    if current_block:
        item_blocks.append(current_block)
    return item_blocks


def render_blocks_text(heading: str, blocks: list[list[str]],
                       source_path: Path, has_next: bool) -> str:
    """渲染段落文本：标题 + 条目列表，段间保留空行"""
    rendered_lines = [f'## {heading}', '']
    for block in blocks:
        rendered_lines.extend(normalize_item_lines(block, source_path))
        rendered_lines.append('')
    if rendered_lines and rendered_lines[-1] == '':
        rendered_lines.pop()
    text = '\n'.join(rendered_lines)
    if has_next:
        text = text.rstrip('\n') + '\n\n'
    return text


def reformat_author_section(content: str, source_path: Path) -> tuple[str, bool]:
    original_content = content
    content = re.sub(r'(?<![#\n])(##(?!#)\s*(?:Author|Co-creator|Co creator))', r'\n\1', content)
    matches = list(re.finditer(r'(?m)^(##(?!#).+)$', content))
    if not matches:
        return content, content != original_content

    # 第一遍：解析所有段落，归类条目
    # kind: 'raw' 原样保留 | 'author' 首个 Author 段 | 'cocreator' Co-creator 段(含合并条目)
    parsed: list[tuple[str, dict]] = []
    author_rendered = False
    cocreator_planned = False
    cocreator_blocks: list[list[str]] = []  # 所有 Co-creator 条目(含后续 Author 段合并)

    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        section_text = content[start:end]
        heading_line = match.group(1)
        heading, inline_body = extract_heading_and_inline_body(heading_line)
        section_body = content[start + len(match.group(1)):end]
        if inline_body:
            section_body = inline_body + '\n' + section_body.lstrip('\n')

        sec = {'start': start, 'end': end, 'text': section_text,
               'heading': heading, 'body': section_body,
               'has_next': idx + 1 < len(matches)}

        if not section_has_name_items(section_body):
            parsed.append(('raw', sec))
            continue

        normalized_heading = normalize_heading(heading[2:].strip())
        sec['norm_heading'] = normalized_heading
        blocks = parse_section_blocks(section_body)

        if normalized_heading == 'Author':
            if not author_rendered:
                author_rendered = True
                parsed.append(('author', sec))
            elif not cocreator_planned:
                # 非首个 Author 段且尚无 Co-creator 段：以 Co-creator 标题输出
                cocreator_planned = True
                cocreator_blocks.extend(blocks)
                parsed.append(('cocreator', sec))
            else:
                # 已有 Co-creator 段：条目合并进去，本段不再输出
                cocreator_blocks.extend(blocks)
                parsed.append(('skip', sec))
            continue
        elif normalized_heading == 'Co-creator':
            cocreator_blocks.extend(blocks)
            if not cocreator_planned:
                cocreator_planned = True
                parsed.append(('cocreator', sec))
            else:
                # 第二个 Co-creator 段：条目已并入第一个段，本段不再输出
                parsed.append(('skip', sec))
            continue
        else:
            # 自定义标题段(如 ## 其他作者)：标题保持原样，条目正常渲染，不参与合并
            parsed.append(('custom', sec))

    # 第二遍：按顺序输出
    output_parts: list[str] = []
    last_index = 0
    for kind, sec in parsed:
        if kind == 'skip':
            # 已合并/吸收的段：不输出，仅推进指针
            last_index = sec['end']
            continue
        if kind == 'raw':
            output_parts.append(content[last_index:sec['start']])
            output_parts.append(sec['text'])
        elif kind == 'author':
            output_parts.append(content[last_index:sec['start']])
            output_parts.append(render_blocks_text(
                'Author', parse_section_blocks(sec['body']), source_path, sec['has_next']))
        elif kind == 'cocreator':
            output_parts.append(content[last_index:sec['start']])
            output_parts.append(render_blocks_text(
                'Co-creator', cocreator_blocks, source_path, sec['has_next']))
        elif kind == 'custom':
            output_parts.append(content[last_index:sec['start']])
            output_parts.append(render_blocks_text(
                sec['norm_heading'], parse_section_blocks(sec['body']), source_path, sec['has_next']))
        last_index = sec['end']

    output_parts.append(content[last_index:])
    updated_content = ''.join(output_parts)
    return updated_content, updated_content.rstrip('\n') != original_content.rstrip('\n')


def read_text_with_newline(path: Path) -> tuple[str, str]:
    """读取文本并检测主换行符；内容统一转为 \n 处理，写回时再还原"""
    raw = path.read_bytes()
    newline = '\r\n' if b'\r\n' in raw else '\n'
    text = raw.decode('utf-8', errors='ignore').replace('\r\n', '\n')
    return text, newline


def write_text_with_newline(path: Path, text: str, newline: str):
    """写回文本，保留原换行风格（write_bytes 避免 Windows 下 write_text 的二次换行翻译）"""
    if newline == '\r\n':
        text = text.replace('\n', '\r\n')
    path.write_bytes(text.encode('utf-8'))


def process_single_file(readme_path: Path, check_only: bool = False) -> int:
    """处理单个 README；返回 1 表示需要/已经修改，0 表示无变化"""
    if not readme_path.is_file():
        print(f"Error: File not found: {readme_path}")
        return 0

    content, newline = read_text_with_newline(readme_path)
    updated_content, is_modified = reformat_author_section(content, readme_path)

    display_path = get_safe_relpath(readme_path)
    if is_modified:
        if check_only:
            print(f"Would reformat: {display_path}")
        else:
            write_text_with_newline(readme_path, updated_content, newline)
            print(f"Reformatted: {display_path}")
        return 1
    else:
        print(f"Skipped (No change needed): {display_path}")
        return 0


def process_all_authors(check_only: bool = False) -> int:
    """批量处理所有作者 README；返回需要/已经修改的文件数"""
    if not MODELS_DIR.is_dir():
        print(f"Error: {MODELS_DIR} directory does not exist.")
        return 0

    updated_count = 0
    for author_dir in sorted(MODELS_DIR.iterdir()):
        if author_dir.is_dir() and author_dir.name.isdigit() and len(author_dir.name) == 4:
            for fname in ['README.md', 'readme.md']:
                readme_path = author_dir / fname
                if readme_path.is_file():
                    content, newline = read_text_with_newline(readme_path)
                    updated_content, is_modified = reformat_author_section(content, readme_path.resolve())
                    if is_modified:
                        if check_only:
                            print(f"Would reformat: {get_safe_relpath(readme_path)}")
                        else:
                            write_text_with_newline(readme_path, updated_content, newline)
                            print(f"Reformatted: {get_safe_relpath(readme_path)}")
                        updated_count += 1
                    break

    verb = 'would reformat' if check_only else 'reformatted'
    print(f"\nBatch process completed. Total {verb}: {updated_count}")
    return updated_count


if __name__ == '__main__':
    check_only = any(a in ('--check', '--dry-run') for a in sys.argv[1:])
    args = [a for a in sys.argv[1:] if not a.startswith('--')]

    if args:
        raw_arg = args[0].strip('"\'')
        arg_path = Path(raw_arg).resolve()

        if arg_path.is_file():
            target_readme = arg_path
        elif arg_path.is_dir():
            target_readme = arg_path / 'README.md'
        elif raw_arg.isdigit():
            folder_id = raw_arg.zfill(4)
            target_readme = MODELS_DIR / folder_id / 'README.md'
        else:
            print(f"Error: Invalid path or author ID: {raw_arg}")
            sys.exit(2)

        print(f"Target file: {target_readme}")
        changed = process_single_file(target_readme, check_only)
        if check_only and changed:
            sys.exit(1)
    else:
        print("No input specified. Running batch reformat for all authors...")
        changed = process_all_authors(check_only)
        if check_only and changed:
            sys.exit(1)
