# -*- coding: utf-8 -*-
"""命名解析：整体去符号匹配 + 角色两侧加 _ 切分 + 名称块重组。

规则（2026-08-21 v2，用户逐条确认）：
  1. 格式化：所有符号 -> _、英文小写（format_name）；作品数据同样归一化。
  2. 作品识别（match_work）：首段命中（含中文作品名）/ ASCII 前缀累积 / 长名子串补全。
  3. 有作品（resolve_name3）：
     - 去作品字段后整体去 _ 为连续串；用该作品角色全称（去符号小写）子串匹配，
       命中角色名两侧加 _ 切分（最长命中优先）；
     - 角色名替换为规范名（zh/en 数组首项）；同名角色去重（保留第一个）；
     - 名称块 = 名称 + 其修饰未知字段：
         无前缀未知：修饰 = 紧跟其后的未知（最多 1 个）；
         有前缀未知（触发后置）：修饰 = 紧跟其前的未知；
       游离未知（触发时的尾部多余）放末尾；
     - 名称块按 英文在前、中文在后 重排（修饰随名称移动）；
     - 缺失侧补全：只有 zh 补 en、只有 en 补 zh（英文在前中文在后）。
  4. 无作品：
     - 整体去 _ 后用全库角色中英文全称匹配（阶段零：去符号整体串精确命中角色
       首项全称优先，避免短全称跨作品抢占长全称，如 AIC"爱丽丝" vs
       Touhou"爱丽丝·玛格特洛依德"；再逐段/连续匹配）；
     - 命中 1 个角色：加作品前缀并按 3 重组；
     - 命中多个角色同一作品：只加作品前缀，其余不修改；
     - 命中多个角色不同作品 / 无命中：保持原文件夹名（不再添加 Unknown 前缀）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from lib.kb.text import (  # noqa: E402
    has_cjk, init_caps, normalize_en_key,
    normalize_work_name,
)
from lib.kb.parse import (  # noqa: E402
    EXTRA_WORK_ALIASES, get_work_canonical, role_names,
)

# 第 1 步：名称格式化。符号统一为 `_`、语言切换分段、英文小写、末尾段剥离评级。
_SYM_TO_UNDERSCORE_RE = re.compile(r"[\s\-_·・：:（）()，,。.、]+")
_CJK_EN_GAP_RE = re.compile(r"(?<=[\u4e00-\u9fff\u3040-\u30ff])(?=[A-Za-z0-9])"
                            r"|(?<=[A-Za-z0-9])(?=[\u4e00-\u9fff\u3040-\u30ff])")
_TAIL_GRADE_RE = re.compile(r"(?:^|_)(la|lb|lc|ld)$")


def _segment_spans(name: str) -> list[tuple[str, int, int]]:
    """切段并记录每段在原始名中的 (文本, 原始起始, 原始结束)。"""
    segs: list[tuple[str, int, int]] = []
    cur_start: int | None = None
    cur_kind = ""
    for i, ch in enumerate(name):
        if _SYM_TO_UNDERSCORE_RE.match(ch):
            if cur_start is not None:
                segs.append((name[cur_start:i], cur_start, i))
                cur_start, cur_kind = None, ""
            continue
        kind = "cjk" if has_cjk(ch) else "ascii"
        if (ch == '号' and cur_kind == 'ascii' and cur_start is not None
                and name[cur_start:i].isdigit()):
            cur_kind = 'cjk'
            continue
        if cur_start is None:
            cur_start, cur_kind = i, kind
        elif cur_kind != kind:
            segs.append((name[cur_start:i], cur_start, i))
            cur_start, cur_kind = i, kind
    if cur_start is not None:
        segs.append((name[cur_start:len(name)], cur_start, len(name)))
    return segs


def format_name(name: str) -> tuple[str, str, list]:
    """规则 1：符号统一为 `_`、英文小写；末尾独立段剥离评级。"""
    spans = _segment_spans(name)
    texts = [t.lower() for t, _s, _e in spans]
    grade = ""
    if texts:
        m = _TAIL_GRADE_RE.search(texts[-1])
        if m:
            grade = m.group(1).upper()
            texts = texts[:-1]
            spans = spans[:-1]
    fmt = "_".join(texts)
    return fmt, grade, spans


def match_work(fmt: str, allow_prefix: bool = True) -> tuple[str, str, int]:
    """规则 2：作品识别。返回 (work, source, prefix_end)。

    - 首段直接命中（含中文作品名/别名，如 蔚蓝档案/碧蓝档案 -> BA）；
    - ASCII 前缀累积（magia_record -> Magia Record）；
    - 长全称/中文名归一化子串匹配（补全规则）。
    """
    segs = fmt.split("_")
    best, best_len, source, prefix_end = "", 0, "none", 0
    if allow_prefix:
        if segs and len(segs[0]) >= 2:
            w0 = get_work_canonical(segs[0])
            if w0:
                best, best_len, source = w0, len(segs[0]), "prefix"
                prefix_end = 1
        if not best:
            acc = ""
            for i, seg in enumerate(segs):
                if not seg.isascii():
                    break
                acc = (acc + "_" if acc else "") + seg
                w = get_work_canonical(acc)
                if w and len(acc) > best_len:
                    best, best_len, source = w, len(acc), "prefix"
                    prefix_end = i + 1
    nfmt = normalize_work_name(fmt)
    for nk, w in EXTRA_WORK_ALIASES.items():
        if (len(nk) >= 2 and (len(nk) >= 4 or has_cjk(nk))
                and nk in nfmt and len(nk) > best_len):
            best, best_len, source = w, len(nk), "substr"
    return best, source, prefix_end


# ---------------------------------------------------------------------------
# 规则 3/4：整体去符号匹配 + 角色两侧加 _ 切分 + 名称块重组
# ---------------------------------------------------------------------------
_SYMBOL_RE = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]")


def _norm(s: str) -> str:
    """去所有符号、小写。保留全部字母数字（含 ī/ā 等拉丁扩展与中文）。"""
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _coverage_min_len(body: str) -> int:
    """覆盖率阈值：角色名键长度须 >= body 去符号后长度的 30%（至少 3 字符）。

    防止短英文键（如 Ines=4）在长英文词组（Chinese-Math-...=30）中无意子串命中
    （ch**ines**e 含 ines）。Chinese 角色名不受影响（8 字全称 vs 3 字短名仍达标）。
    """
    bn = len(_norm(body)) * 0.3
    return max(3, int(bn))


def _role_keys(roles: list[dict], work: str | None = None,
               full_only: bool = False) -> list[tuple[str, str, str]]:
    """角色匹配键：[(去符号小写全称, 规范名, 'zh'/'en/'ja')]，长名优先。

    work 非空时只取该作品角色；否则全库。只取 len>=2 的键（避免单字误配）。
    `ja` 键（日文名）的 lang 设为 'zh'（与中文别名同等对待——排序/补全同中文块，
    但数据语义上日文名归 `ja` 键，不混入 `zh`）。
    full_only=True（4-2 无作品）：只用 zh/en 数组**第一个名称**（标准名/全称），
    不做别名子串匹配（配合调用方的"完整覆盖"检查，避免"爱丽丝"简称误配 BA
    而抢占 Touhou 的"爱丽丝·玛格特洛依德"全称命中）。
    """
    items: list[tuple[str, str, str]] = []
    for r in roles:
        w = str(r.get("work", ""))
        if work and w != work:
            continue
        zs = [z for z in (r.get("zh") or []) if z]
        es = [e for e in (r.get("en") or []) if e]
        js = [j for j in (r.get("ja") or []) if j]
        z_c = zs[0] if zs else ""
        e_c = es[0] if es else ""
        if full_only:
            # 只用第一个名称（标准名/全称）
            names = []
            if zs:
                names.append((zs[0], "zh"))
            if es:
                names.append((es[0], "en"))
        else:
            names = [(z, "zh") for z in zs] + [(e, "en") for e in es] + [(j, "zh") for j in js]
        for nm, lang in names:
            n = _norm(nm)
            # 允许单字中文角色名（如 光/渚），英文仍需 >=2 避免单字母误配
            if n and (len(n) >= 2 or has_cjk(n)):
                items.append((n, z_c if lang == "zh" else e_c, lang))
    items.sort(key=lambda x: -len(x[0]))
    return items


def _en_seg_boundaries(segs: list[str], prefix_end: int) -> tuple[set[int], set[int]]:
    """body 中英文段的 [起点, 终点) 位置集合（供英文角色名段级对齐）。

    去前缀后的每段 `_norm(seg)` 拼进 body；非 CJK 段（英文/数字）记录其
    起止累积位置。跨连续英文段（asuma+toki -> asumatoki）的起止各查集合，
    天然允许拼接命中。
    """
    starts: set[int] = set()
    ends: set[int] = set()
    pos = 0
    for seg in segs[prefix_end:]:
        n = _norm(seg)
        if n and not any(has_cjk(ch) for ch in n):
            starts.add(pos)
            ends.add(pos + len(n))
        pos += len(n)
    return starts, ends


def _split_tokens(s: str, keys: list[tuple[str, str, str]],
                 en_boundaries: tuple[set[int], set[int]] | None = None) -> list[tuple[str, bool, str]]:
    """整体串 s 按角色名切分（最长命中优先）。

    返回 [(文本, 是否角色, 规范名 或 ''，lang 或 '')]，角色名两侧自动加 _ 语义。
    en_boundaries=(英文段起点集合, 英文段终点集合)：非空时，英文角色键（lang='en'）
    命中要求 [start, end) **完整覆盖英文段**（start∈起点集 且 end∈终点集，允许跨连续
    英文段如 asuma+toki），防止英文短别名嵌在他人英文名中被截取子串
    （如 lujiang 中的 jian、tokiri 中的 toki）。中文/日文键不受此限制。
    """
    en_starts, en_ends = en_boundaries if en_boundaries else (None, None)
    tokens: list[tuple[str, bool, str, str]] = []
    i, n = 0, len(s)
    while i < n:
        best = None
        for nm, canon, lang in keys:
            if s.startswith(nm, i):
                if lang == "en" and en_starts is not None:
                    # 英文键段级对齐：命中起点/终点必须落在英文段边界上，否则跳过
                    if i not in en_starts or (i + len(nm)) not in en_ends:
                        continue
                if best is None or len(nm) > len(best[0]):
                    best = (nm, canon, lang)
        if best:
            nm, canon, lang = best
            tokens.append((nm, True, canon, lang))
            i += len(nm)
        else:
            j = i
            while j < n and not any(s.startswith(nm, j) for nm, _c, _l in keys):
                j += 1
            if j > i:
                tokens.append((s[i:j], False, "", ""))
                i = j
            else:
                tokens.append((s[i], False, "", ""))
                i += 1
    return tokens


def _reorder(tokens: list[tuple[str, bool, str, str]],
             work: str) -> tuple[str, list[str], list[str]]:
    """名称块重组：去重 -> 修饰归属 -> 英文前中文后 -> 未知后置。

    返回 (重组串, zh 名称列表, en 名称列表)。
    """
    # 1. 去重：canon 相同（同一角色）或名称存在子串包含（渚 ⊂ 桐藤渚）视为重复，
    #    保留更长/标准名（全名优先）；文本完全相同的重复名（酒狐_酒狐）同样合并。
    #    不同角色但名称无包含关系（酒狐/小莫莫 若均为独立角色）各自保留。
    kept: list[tuple[str, bool, str, str]] = []
    for t, is_role, canon, lang in tokens:
        if not is_role:
            kept.append((t, is_role, canon, lang))
            continue
        dup_idx: int | None = None
        for i, (kt, kis, kcanon, klang) in enumerate(kept):
            if not kis or klang != lang:
                continue
            # canon 相同，或文本存在子串包含（如 渚 ⊂ 桐藤渚）
            if canon == kcanon or (t in kt or kt in t):
                dup_idx = i
                break
        if dup_idx is None:
            kept.append((t, is_role, canon, lang))
        elif len(t) > len(kept[dup_idx][0]):
            kept[dup_idx] = (t, is_role, canon, lang)  # 保留更长名称
    dedup = kept

    if not dedup:
        return "", [], []

    # 2. 触发判断：第一个 token 是未知（前缀未知 -> 未知后置模式）
    triggered = not dedup[0][1]

    # 3. 名称块 = 名称 + 修饰未知；游离未知 = 未成为修饰的
    #    不触发：修饰 = 紧跟名称之后的未知（最多 1 个）
    #    触发  ：修饰 = 紧跟名称之前的未知；末尾多余未知为游离
    blocks: list[tuple[str, str]] = []  # (规范名, 修饰文本或 "")
    free_unknowns: list[str] = []
    pending_unknown: str | None = None  # 触发模式下名称前的待归属未知

    for t, is_role, canon, _lang in dedup:
        if is_role:
            if triggered and pending_unknown:
                blocks.append((canon, pending_unknown))
                pending_unknown = None
            else:
                blocks.append((canon, ""))
        else:
            if not triggered:
                # 修饰 = 紧跟名称后的未知：附加到前一个名称块（最多 1 个）
                if blocks and not blocks[-1][1]:
                    blocks[-1] = (blocks[-1][0], t)
                else:
                    free_unknowns.append(t)
            else:
                if pending_unknown is None:
                    pending_unknown = t
                else:
                    free_unknowns.append(t)

    if triggered and pending_unknown:
        free_unknowns.append(pending_unknown)

    # 4. 英文块前、中文块后（各保持原始相对顺序）；未知后置
    #    英文名统一 init_caps + 空格转连字符（shimoe koharu → Shimoe-Koharu）
    blocks_capped = [(init_caps(b[0]).replace(" ", "-") if not has_cjk(b[0]) else b[0], b[1]) for b in blocks]
    zh_names = [b[0] for b in blocks_capped if has_cjk(b[0])]
    en_names = [b[0] for b in blocks_capped if not has_cjk(b[0])]
    ordered = [b for b in blocks_capped if not has_cjk(b[0])] + [b for b in blocks_capped if has_cjk(b[0])]
    parts = [b[0] + (("_" + b[1]) if b[1] else "") for b in ordered]
    parts += free_unknowns
    return "_".join(parts), zh_names, en_names


def _parts_segments(parts: str) -> set[str]:
    """parts（重组串）的独立段（小写、en 连字符按 _ 归一）集合。

    供 _fill_missing 判断补全名是否已是 parts 中的独立段，
    避免"陈_Ch'en"场景中中文名被覆盖率过滤为未知段后补全重复。
    """
    return {s.lower() for s in parts.replace("-", "_").split("_") if s}


def _fill_missing(parts: str, zh_names: list[str], en_names: list[str],
                  work: str, en_to_cn: dict | None, cn_to_en: dict | None,
                  filled: list[str]) -> tuple[str, list[str], list[str]]:
    """缺失侧补全（英文在前中文在后）：只有 zh 补 en、只有 en 补 zh。

    以第一个名称反查该作品规范另一侧名。返回 (重组串, zh列表, en列表)。
    """
    if zh_names and not en_names and cn_to_en:
        cands: set[str] = set()
        for w, e in cn_to_en.get(zh_names[0], []):
            if w == work:
                cands.add(e)
        if len(cands) == 1:
            en_name = init_caps(cands.pop()).replace(" ", "-")
            if en_name.replace("-", "_").lower() not in _parts_segments(parts):
                parts = en_name + ("_" + parts if parts else "")
                en_names = [en_name]
                filled.append("EN auto-filled: " + en_name)
    elif en_names and not zh_names and en_to_cn:
        cands = set()
        for kk in {normalize_en_key(en_names[0]), normalize_en_key(en_names[0]).replace("_", "-")}:
            for w, c in en_to_cn.get(kk, []):
                if w == work:
                    cands.add(c)
        if len(cands) == 1:
            zh_name = cands.pop()
            if zh_name.lower() not in _parts_segments(parts):
                parts = (parts + "_" if parts else "") + zh_name
                zh_names = [zh_name]
                filled.append("CN auto-filled: " + zh_name)
    return parts, zh_names, en_names


def resolve_name3(name: str, roles: list[dict],
                  en_to_cn: dict | None = None, cn_to_en: dict | None = None) -> dict:
    """主解析：作品识别 -> 整体去符号匹配 -> 名称块重组 -> 补全。

    返回 original/new/status/work/zh/en/grade/problems 等字段。
    """
    orig = name
    notes: list[str] = []
    problems: list[str] = []
    filled: list[str] = []

    fmt, grade, spans = format_name(name)
    segs = fmt.split("_") if fmt else []
    unknown_seen = False
    if segs and segs[0].lower() == "unknown":
        unknown_seen = True
        segs, spans = segs[1:], spans[1:]

    work, work_source, prefix_end = match_work("_".join(segs), allow_prefix=True)
    if work and work != "Unknown" and prefix_end == 0 and segs:
        head = segs[0]
        if head.isascii() and normalize_en_key(head).replace("-", "_") == work.lower().replace("-", "_"):
            prefix_end = 1

    # 独立段作品名移除：首段无前缀时（含 match_work 子串命中——作品名藏在中间
    # 独立段，如 泳装花子bunny碧蓝档案 的"碧蓝档案"），定位该段并移除，
    # 避免作品名残留为未知段；子串命中场景限同一作品（w0 == work）
    middle_work_idx: int | None = None
    raw_rest: str | None = None  # 中间作品段移除后的原始剩余（保留原分隔符）
    if not work or work == "Unknown" or prefix_end == 0:
        for i, seg in enumerate(segs):
            # 独立段作品识别仅接受 >=3 字符或含中文的段：2 字母独立段多是常见
            # 英文介词/单词（in/an/as...），照检会把 Singer-In-Last-Phase 的
            # "In" 误判为作品键 in（IN=无限暖暖）；缩写作品（AK/AL/BA）走首段识别。
            if len(seg) < 3 and not has_cjk(seg):
                continue
            w0 = get_work_canonical(seg)
            if w0 and (work in ("", "Unknown") or w0 == work):
                if not work or work == "Unknown":
                    work, work_source = w0, "prefix"
                middle_work_idx = i
                break
    if middle_work_idx is not None:
        s, e = spans[middle_work_idx][1], spans[middle_work_idx][2]
        raw_rest = re.sub(r"_{2,}", "_", name[:s] + name[e:]).strip("_-")
        segs = segs[:middle_work_idx] + segs[middle_work_idx + 1:]
        spans = spans[:middle_work_idx] + spans[middle_work_idx + 1:]
        prefix_end = 0

    # 去作品字段后的整体串（去所有 _，再统一去符号：' 等字符保留在段内会阻塞
    # 子串匹配，如 Ch'ang Feng 的键 changfeng 无法匹配 ch'angfeng）
    body = _norm("".join(segs[prefix_end:]))
    # 英文段边界（供英文角色名段级对齐，防 lujiang 中的 jian 类截取误配）
    en_starts, en_ends = _en_seg_boundaries(segs, prefix_end)

    # substr 作品命中（如"重装战姬爱塔"中的"重装战姬"）：作品名藏在段内**非独立位置**，
    # 独立段移除逻辑（middle_work_idx）覆盖不到。从 body 中移除该作品名子串，
    # 避免残留为未知段挂在角色名后（取与 work 匹配的最长子串，最长优先防误删）；
    # 移除后同步修正英文段边界（被删区间的边界丢弃，其后的前移 gap）
    if work and work_source == "substr" and prefix_end == 0 and body:
        best_sub = ""
        for nk, wk in EXTRA_WORK_ALIASES.items():
            if wk == work and nk and nk in body and len(nk) > len(best_sub):
                best_sub = nk
        if best_sub:
            p = body.find(best_sub)
            gap = len(best_sub)
            body = body[:p] + body[p + gap:]

            def _shift_unchecked(pos: int) -> int:
                if pos < p:
                    return pos
                if pos >= p + gap:
                    return pos - gap
                return -1  # 落在被删区间内：边界作废

            en_starts = {x for x in (_shift_unchecked(s) for s in en_starts) if x >= 0}
            en_ends = {x for x in (_shift_unchecked(s) for s in en_ends) if x >= 0}

    # 匹配角色：有作品 -> 作品库（子串+别名）；无作品 -> 全库宽松匹配（全部名称
    # + 子串，含别名），命中唯一作品即按"有作品"规则重组；多作品冲突由调用方显示。
    # 覆盖率阈值过滤短键在长词组中的无意子串命中（Chinese 中的 ines）。
    full_only = not (work and work != "Unknown")
    if full_only:
        keys = _role_keys(roles, None, full_only=False)
        min_len = _coverage_min_len(body)
        # 中文角色名正常 2 字（千咲/星野/白子），不挡；英文键由覆盖率阈值防误配
        keys = [k for k in keys if len(k[0]) >= min_len or (has_cjk(k[0]) and len(k[0]) >= 2)]
        tokens = _split_tokens(body, keys, (en_starts, en_ends)) if body else []
    else:
        keys = _role_keys(roles, work, full_only=False)
        tokens = _split_tokens(body, keys, (en_starts, en_ends)) if body else []
    role_count = sum(1 for _t, is_role, _c, _l in tokens if is_role)
    hit_works: set[str] = set()
    hit_roles: set[tuple] = set()  # (work, zh首项, en首项)：区分同一角色的中英文多次命中
    for t, is_role, canon, lang in tokens:
        if is_role:
            # 收集 canon 匹配的**所有**角色作品（不 break）：同一 canon/短名可能
            # 同时属于多个作品（如"爱丽丝" = AIC 首项 = Touhou 别名），
            # 只取首个会漏检跨作品冲突。
            for r in roles:
                if ((lang == "zh" and canon in (r.get("zh") or []))
                        or (lang == "en" and any(normalize_en_key(x) == normalize_en_key(canon)
                                                 for x in (r.get("en") or [])))):
                    hit_works.add(str(r.get("work", "")))
                    zh0 = (r.get("zh") or [""])[0]
                    en0 = (r.get("en") or [""])[0]
                    hit_roles.add((str(r.get("work", "")), zh0, en0))

    conflict = False
    conflict_works: list[str] = []
    no_reorder = False  # 4-2：命中多个**不同**角色且同作品 -> 只加作品前缀，不重组
    if work and work != "Unknown":
        pass
    elif not work:
        if role_count >= 1 and len(hit_works) == 1:
            work, work_source = next(iter(hit_works)), "kb"
            # 多 token 命中但同属一个角色（如中文名+英文名都命中，新泽西+New-Jersey）
            # 仍重组合并；只有命中多个不同角色才只加前缀不重组
            no_reorder = len(hit_roles) > 1
        elif len(hit_works) > 1:
            conflict = True
            conflict_works = sorted(hit_works)
            work, work_source = "Unknown", "conflict"
        else:
            work, work_source = "Unknown", "none"
    else:
        work, work_source = "Unknown", "none"

    # 前缀修正：work 由角色反查确定后，若首段小写 == work 键小写（如 DMC 未注册
    # 但角色命中确定 DMC），则首段视为作品前缀，重新切 body。
    if work and work != "Unknown" and prefix_end == 0 and segs:
        head = segs[0]
        if head.isascii() and normalize_en_key(head).replace("-", "_") == work.lower().replace("-", "_"):
            prefix_end = 1
            body = _norm("".join(segs[prefix_end:]))
            en_starts, en_ends = _en_seg_boundaries(segs, prefix_end)
            tokens = _split_tokens(body, keys, (en_starts, en_ends)) if body else []
            role_count = sum(1 for _t, is_role, _c, _l in tokens if is_role)

    # 重组
    zh_names: list[str] = []
    en_names: list[str] = []
    if work and work != "Unknown":
        if role_count == 0 or no_reorder:
            # 作品角色库无匹配 / 多角色同作品：只确认作品前缀，其余保持原始
            # （不重组、不去符号，保留原始分隔符与大小写；
            #   中间作品段场景用 raw_rest（已移除该段），切片止于剥离评级后的 end）
            if middle_work_idx is not None:
                rest = raw_rest or ""
            else:
                rest = (name[spans[prefix_end][1]:spans[-1][2]]
                        if prefix_end < len(spans) else "")
            new = work + ("_" + rest if rest else "")
        else:
            parts, zh_names, en_names = _reorder(tokens, work)
            parts, zh_names, en_names = _fill_missing(parts, zh_names, en_names,
                                                      work, en_to_cn, cn_to_en, filled)
            new = work + ("_" + parts if parts else "")
    elif work == "Unknown" and segs:
        # 无匹配 / 多作品冲突 / 多角色异作品：保持原文件夹名，不再添加 Unknown_ 前缀
        new = orig
        grade = ""  # orig 已含评级，避免下方重复追加
    else:
        new = orig  # 空名等兜底：原样

    if grade:
        new += "_" + grade

    # 问题标记
    zh_str = "_".join(zh_names)
    en_str = "-".join(en_names)
    if not zh_str:
        problems.append("cn-name")
    if not en_str:
        problems.append("en-name")
    if (not conflict) and (not work or work == "Unknown") and (zh_str or en_str):
        problems.append("works")
    problems = list(dict.fromkeys(problems))

    status = "OK" if new == orig else "FIX"
    return {
        "original": orig, "new": new, "status": status, "notes": "; ".join(notes),
        "filled": "; ".join(filled), "work": work, "zh": zh_str, "en": en_str,
        "grade": grade, "cn_skin": "", "en_skin": "", "conflict": conflict,
        "conflict_works": conflict_works, "work_source": work_source,
        "problems": problems, "candidate_skins": [], "en_extra": "",
    }
