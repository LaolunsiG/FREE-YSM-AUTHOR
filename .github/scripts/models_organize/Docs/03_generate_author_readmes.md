# 03_generate_author_readmes.py 完整流程

YSM 作者 README 生成工具——按 `authors.json` 数据生成作者 README，也支持从 README 反向覆盖/合并到 `authors.json`。

## 脚本职责

- 按 `authors.json` 的作者条目生成作者 README（编号 + Name + 平台分类段，无 Role）
- **合并模式（默认）**：先反向合并作者 README 的手写信息（team / platform / badges）进 `authors.json`，再生成 README（双向同步，只补缺失不覆盖）
- **覆盖模式（`--overwrite`）**：只从 `authors.json` 单向生成，忽略 README 手写内容
- **从 README 覆盖（`--from-readme`）**：以 README 为权威反向覆盖/新增 `team/platforms/badges` 到 `authors.json`（不生成 README）
- 自动判定徽章（`high-output` / `nsfw`）：不最先执行，改为各模式读取 badges 后合并进既有 badges

## 目录与数据

| 路径 | 作用 |
| --- | --- |
| `Models/<4位编号>/` | 作者目录，必须存在才生成 |
| `Models/<4位编号>/README.md` | 作者 README（本脚本生成/覆盖） |
| `author-info/authors.json` | 集中作者数据（`load_authors_index()` 读取） |
| `author-info/tag_labels.json` | badges 词表（中英文对照，`load_tag_labels()` 读取） |

## 命令行参数

```
python .github/scripts/models_organize/03_generate_author_readmes.py [选项] [编号...]
```

| 参数 | 说明 |
| --- | --- |
| `authors`（位置参数） | 作者编号（可多个），不给则按 `authors.json` 全量生成；自动补零到 4 位 |
| `--root PATH` | 仓库根目录（默认自动检测） |
| `--apply` | 真正写入文件（默认 dry-run 只预览） |
| `--overwrite-readme` | 覆盖生成：从 authors.json 生成作者 README（**默认行为**，可省略） |
| `--overwrite-author` | 反向覆盖：从作者 README 覆盖到 authors.json（以 README 为权威，加 `--apply` 写盘，不生成 README） |
| `--merge` | 合并模式：先反向合并 README 手写信息进 authors.json，再生成 README（默认是覆盖生成，此参数显式启用合并） |

## 执行流程

### 1. 初始化
1. 解析参数 → 确定 `models_dir`（`<root>/Models`）
2. 加载 `authors.json` → 取 `authors` 字典
3. **过滤有效作者**（`author_entries`）：
   - 有 `name` 字段（非空数组）
   - 对应 `Models/<编号>/` 目录存在
   - `only` 参数指定编号时仅处理指定作者
4. 输出：`entries = [(编号, entry), ...]`

### 2. 自动徽章（各模式读取 badges 后合并，非最先执行）
**不在一开始计算；而是在各模式读取来源 badges（README 或 author.json）之后、执行合并/覆盖之前，把自动判定徽章合并进既有 badges（追加去重）：**
- `high-output`：模型数 ≥ 20（`HIGH_OUTPUT_THRESHOLD`）
- `nsfw`：目录下文件夹名含 `nsfw` / `r18` / `r-18` / `18+`
- 合并模式 / `--overwrite-author`：在 `merge_readmes_to_authors` 内，README badges + 自动徽章 → 合并/覆盖到 author.json
- 覆盖生成（默认 / `--overwrite-readme`）：author.json badges + 自动徽章 → 覆盖到 README（`_merge_auto_badges`）

### 3. 模式选择

```
┌─ --overwrite-author? ──→ README 覆盖到 authors.json（不生成 README）→ return
├─ --merge?            ──→ 合并模式（README ↔ JSON 双向）
└─ 默认 / --overwrite-readme ──→ 覆盖生成（JSON → README）
```

### 4. `--from-readme` 分支（新增独立参数）
1. 调用 `merge_readmes_to_authors(..., overwrite=True)`：
   - 解析每个作者 README 的 `team` / `platforms` / `badges`
   - **全量覆盖**：platforms 完全替换 JSON（来源无则删除）；team 覆盖或删除；badges 仅当 README 有时替换（无 badges 行时保留自动徽章）
   - `--apply` 时写回 `authors.json`
2. 不生成 README，直接返回

### 5. 合并模式（默认，无 `--overwrite`）
1. **反向合并**（`merge_readmes_to_authors`，`overwrite=False`）：
   - 解析每个作者 README 的 `team` / `platforms` / `badges`
   - 调用 `merge_author_updates` 幂等合并：
     - platforms：只补缺失的 http(s) 键（已有键不覆盖）
     - team：非空才写入
     - badges：追加去重（自动徽章 + README 徽章）
   - `--apply` 时写回 `authors.json`；否则打印待合并数量
2. **重新读取**：`entries = author_entries(...)`（用合并后的最新数据）

### 6. 覆盖模式（`--overwrite`）
- 跳过反向合并步骤
- 直接使用 entries 生成 README（忽略 README 手写）

### 7. 遍历生成（apply 时）
对每个 `(aid, entry)`：
1. 打印 `编号 name1 | name2 | ...`
2. 扫描模型目录下模型文件夹 → `models` 列表
3. 调用 `render_author_readme(aid, entry, models, model_dir)` 渲染 → 写入 `Models/<aid>/README.md`
4. 累加 `generated`

### 8. 徽章落盘（apply 且 `badges_updated`）
- 重新读 `authors.json`
- 把每个 `entries[i]` 写回 `authors[aid]`
- 写回 `authors.json`

### 9. 输出统计
- 合并/覆盖模式：打印合并/覆盖/未匹配的作者数
- 生成统计：`已生成 <N> 个作者 README`（apply）或 `dry-run 预览：未写入`

## 三种模式对比

| 模式 | 触发 | 方向 | 语义 |
| --- | --- | --- | --- |
| **覆盖生成（默认）** | 无参数 / `--overwrite-readme` | authors.json → README（正向） | 以 authors.json 为权威生成 README |
| **反向覆盖** | `--overwrite-author` | README → authors.json（反向覆盖） | 以 README 为权威覆盖 JSON（全量替换 platforms/team） |
| **合并模式** | `--merge` | README ↔ authors.json（双向） | 先反向合并（只补缺失不覆盖），再生成 README |

## 关键函数

### author_entries(models_dir, only)
返回 `[(编号, entry), ...]`。过滤条件：有 name + 目录存在 + 满足 only 限定。

### parse_readme_author_info(text)
从作者 README 提取可反向合并的信息 → `{team, platforms, badges}`（缺项则无键）。
- team：`- **team**: <值>` 行
- badges：`- **badges**: <显示文本>` 行 → 反查词表得键列表
- platforms：缩进子行 `- **Bilibili**: url`（`[label](url)` 还原为 url）

### merge_readmes_to_authors(models_dir, entries, apply, overwrite=False)
合并/覆盖模式的主逻辑。
- `overwrite=False`：幂等合并（只补缺失，不覆盖）
- `overwrite=True`：全量覆盖（platforms 完全替换、team 覆盖或删除、badges 仅当 README 有时替换）
- `--apply` 时写回 `authors.json`，返回合并的作者数

### auto_author_marks(model_count, author_dir)
返回 `[mark_key, ...]`。规则见上方"自动标签"。

## 数据流

```
authors.json  ──读──► entries（过滤有效作者）
    │
    ├─ 自动徽章（high-output/nsfw）─追加─► entry.badges
    │
    ├─ [合并模式] ◄── README 手写信息（反向合并 team/platform/badges）
    │                   写回 authors.json（幂等，只补缺失）
    ├─ [覆盖模式] ──→ 直接生成 README（忽略 README 手写）
    ├─ [--from-readme] ──→ 全量覆盖 authors.json（以 README 为权威）
    │
    ▼
render_author_readme(aid, entry, models, model_dir)
    │
    └─ 写入 Models/<aid>/README.md
```

## 正则表达式

| 名称 | 匹配内容 |
| --- | --- |
| `TEAM_LINE_RE` | `^\s*-\s*\*\*team\*\*\s*[:：]\s*(?P<val>.+)$`（多行，忽略大小写） |
| `TAGS_LINE_RE` | `^\s*-\s*\*\*(?:badges\|标签)\*\*\s*[:：]\s*(?P<val>.+)$` |
| `PLATFORM_SUB_RE` | `^\s{2,}-\s*\*\*(?P<key>[^*]+)\*\*\s*[:：]\s*(?P<val>.*)$` |

## 常量

| 名称 | 值 | 说明 |
| --- | --- | --- |
| `HIGH_OUTPUT_THRESHOLD` | `20` | 模型数 ≥ 此值 → `high-output` 标签 |
| `R18_KEYWORDS` | `('nsfw', 'r18', 'r-18', '18+')` | 模型文件夹名含即触发 `nsfw` 标签 |

## 依赖

- `lib.paths`：`data_path`、`load_json`、`save_json`、`WORKSPACE_ROOT`
- `lib.readme`：`load_authors_index`
- `lib.author_readme`：`render_author_readme`、`load_tag_labels`
- `lib.kb.authors`：`merge_author_updates`（支持 `overwrite` 参数）

## 示例

```bash
# 覆盖生成（默认：JSON -> README，dry-run）
python .github/scripts/models_organize/03_generate_author_readmes.py

# 覆盖生成 + 写入
python .github/scripts/models_organize/03_generate_author_readmes.py --apply

# 显式覆盖生成（与默认等价）
python .github/scripts/models_organize/03_generate_author_readmes.py --overwrite-readme --apply

# 合并模式（先反向合并 README 手写进 authors.json，再生成 README）
python .github/scripts/models_organize/03_generate_author_readmes.py --merge --apply

# 反向覆盖（README -> authors.json）
python .github/scripts/models_organize/03_generate_author_readmes.py --overwrite-author --apply

# 指定编号
python .github/scripts/models_organize/03_generate_author_readmes.py 0058 0093

# 指定编号 + 反向覆盖
python .github/scripts/models_organize/03_generate_author_readmes.py 0000 0015 --overwrite-author --apply
```

## 后续扩展方向

- 新增手写字段（如 `bio` / `introduction`）：改 `parse_readme_author_info` 的正则 + 合并逻辑
- 新增自动标签规则：加 `auto_author_marks` 判定 + 词表 `tag_labels.json`
- 新增模式：在 `main()` 里加参数分支（如"增量模式"只生成缺失的 README）
- 修改覆盖行为：调 `merge_author_updates` 的 overwrite 分支（`lib/kb/authors.py`）