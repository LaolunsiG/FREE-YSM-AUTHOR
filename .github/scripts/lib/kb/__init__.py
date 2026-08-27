# -*- coding: utf-8 -*-
"""kb（命名知识库）子包：按职责拆分，也是唯一库入口。

  text.py    字符串/名称工具（无 lib 依赖）
  parse.py   名称解析与知识库构建（resolve_name / build_kb）
  storage.py 知识库多文件存储（load / save / migrate）
  sync.py    works 索引（build_work_index）
  category.py 作品大类：从 character/*.json 现算分类 + 根 README 分类区块渲染
  cmds.py    交互命令 / 检查 / 合并 / 索引 / 扫描
  authors.py 作者维护（合并重复作者 / 从模型推导作者名 / 重建作者数据）

本包 re-export 全部公开 API，外部统一 `from lib.kb import ...`
（原 kb_tool.py 薄壳已并入 rename_model_folders.py 并删除）。
"""
from lib.kb import authors, category, cmds, parse, storage, sync, text  # noqa: F401

from lib.kb.category import (  # noqa: F401,E402
    CATEGORIES, CATEGORY_TITLES, README_END_MARKER, README_START_MARKER,
    build_category_map, get_work_entry, get_work_tags,
    render_readme_works_section, update_readme_works_section,
)
from lib.kb.cmds import (  # noqa: F401,E402
    DEFAULT_ROOTS, REPO_ROOT, add_role_cmd, add_work_interactive, ask,
    build_indexes, del_role_cmd, format_pair_lines, get_target_dirs,
    has_substr_overlap, list_role_cmd, load_merge_skips, pair_skip_key,
    prune_merge_skips, rename_work_cmd, rename_work_interactive, roles_cmd,
    run_check, run_merge, run_suggest, save_merge_skips,
    set_default_role_cmd, work_display_name,
)
from lib.kb.authors import (  # noqa: F401,E402
    add_author_alias, find_merge_candidates, merge_authors, merge_authors_flow,
    sync_authors_from_models, write_authors_data,
)
from lib.kb.parse import (  # noqa: F401,E402
    EXTRA_WORK_ALIASES, get_work_canonical,
    role_key, role_names, set_work_aliases,
)
from lib.kb.storage import (  # noqa: F401,E402
    load_kb_json, migrate_from_sqlite, save_kb_json,
)
from lib.kb.sync import (  # noqa: F401,E402
    build_work_index, parse_readme_works, sync_works_from_readme, work_value_names,
)
from lib.kb.text import (  # noqa: F401,E402
    CJK_RE, CN_SKIN_RE, EN_TAIL_RE, MIXED_SEG_RE, PAREN_RE, TOUHOU_PREFIX_RE,
    has_cjk, init_caps, normalize_en_key, normalize_work_name,
)
