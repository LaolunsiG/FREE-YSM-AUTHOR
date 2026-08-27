# -*- coding: utf-8 -*-
"""
parse2.resolve_name3 无作品匹配「整体全称优先」（阶段零）测试。

背景：Unknown_爱丽丝·玛格特洛依德_LA 此前被误配为 AIC（Alice in Cradle 的
角色全称恰好是"爱丽丝"两字），因为旧实现先做段级全称匹配、短全称抢在
Touhou 长全称之前。阶段零在逐段匹配前先尝试"去符号整体串精确命中角色首项
全称"，整体覆盖优先于逐段短名。

覆盖：
  1) 整体命中优先：未知前缀 +「爱丽丝·玛格特洛依德」-> 归 Touhou（不归 AIC）
  2) 短全称回归：单独「爱丽丝」两字仍是 AIC 角色 -> 归 AIC
  3) 英文整体命中：Unknown_Alice-Margatroid -> 归 Touhou 并补中文名
  4) 分隔符无关：`_` 与 `·` 切出的整体串一致
  5) 正常首项全称：Unknown_大妖精 -> Touhou（无更长名时整体即全称）
  6) 防短名抢占设计保留：Unknown_魔理沙（短名别名非首项）不匹配、保持 Unknown
  7) 完全未知：Unknown_琪斯美 保持原样（不重复加前缀，无前缀也不再添加）
  8) 有作品前缀路径回归：Touhou_古明地恋 -> 补全英文名
  9) 撇号英文名空格转连字符：Ch'ang Feng -> Ch'ang-Feng

运行：python .github/test/test_parse2_fullname_priority.py（退出码 0=全过）
"""
import pathlib
import sys

sys.stdout.reconfigure(encoding='utf-8')

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / ".github" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lib.kb.cmds import build_indexes  # noqa: E402
from lib.kb.parse2 import resolve_name3  # noqa: E402
from lib.kb.sync import build_work_index  # noqa: E402

WORKS = {
    "AIC": {"abbr": "AIC",
            "name": {"zh": ["爱丽丝的摇篮"], "en": ["Alice in Cradle", "AIC"]},
            "category": "Game"},
    "Touhou": {"abbr": "Touhou",
               "name": {"zh": ["东方 Project"], "en": ["Touhou-Project", "TH", "TOUHOU"]},
               "category": "Other"},
    "AL": {"abbr": "AL",
           "name": {"zh": ["碧蓝航线"], "en": ["Azur Lane", "AL"]},
           "category": "Game"},
    "AK": {"abbr": "AK",
           "name": {"zh": ["明日方舟"], "en": ["Arknights", "AK"]},
           "category": "Game"},
}

ROLES = [
    # AIC 角色全称恰为"爱丽丝"两字：是误配的源头（旧实现短全称抢先）
    {"work": "AIC", "zh": ["爱丽丝"], "en": ["alice"], "source": "manual"},
    # Touhou 全称在首项：阶段零整体命中后规范名即全名
    {"work": "Touhou", "zh": ["爱丽丝·玛格特洛依德", "爱丽丝"],
     "en": ["alice-margatroid", "alice"], "source": "manual"},
    # 无更长名的两字全称（整体==全称的正常案例）
    {"work": "Touhou", "zh": ["大妖精"], "en": ["daiyousei"], "source": "manual"},
    # 短名别名非首项（阶段二"剔除是更长角色名子串"设计的回归用例）
    {"work": "Touhou", "zh": ["雾雨魔理沙", "魔理沙"], "en": ["marisa-kirisame"], "source": "manual"},
    # 有作品前缀路径的补全回归用例
    {"work": "Touhou", "zh": ["古明地恋"], "en": ["komeiji-koishi"], "source": "manual"},
    # 撇号英文名（ch'ang feng）：body 去符号后才能匹配
    {"work": "AL", "zh": ["长风"], "en": ["ch'ang feng"], "source": "manual"},
    # 短英文键陷阱：ines 是 Chinese 的子串（ch[ines]e），覆盖率阈值应阻止无作品误配
    {"work": "AK", "zh": ["伊内丝"], "en": ["ines"], "source": "manual"},
]

build_work_index({"works": WORKS, "roles": []})
_cn_idx, _en_idx, EN_TO_CN, CN_TO_EN = build_indexes(ROLES)


def resolve(name: str) -> dict:
    """便捷包装：resolve_name3 只取需要字段。"""
    return resolve_name3(name, ROLES, EN_TO_CN, CN_TO_EN)


def main() -> int:
    checks: list[tuple[bool, str]] = []

    def ck(cond: bool, msg: str) -> None:
        checks.append((cond, msg))

    # 1. 主 bug 场景：整体全称优先，归 Touhou 而非 AIC
    r = resolve("Unknown_爱丽丝·玛格特洛依德_LA")
    ck(r["work"] == "Touhou" and r["work_source"] == "kb",
       f"1 work={r['work']!r}/{r['work_source']!r} (期望 Touhou/kb，非 AIC)")
    ck(r["zh"] == "爱丽丝·玛格特洛依德",
       f"1 zh={r['zh']!r} (期望 爱丽丝·玛格特洛依德)")
    ck(r["en"] == "Alice-Margatroid",
       f"1 en={r['en']!r} (期望 Alice-Margatroid)")
    ck(r["new"] == "Touhou_Alice-Margatroid_爱丽丝·玛格特洛依德_LA",
       f"1 new={r['new']!r} (期望 Touhou_Alice-Margatroid_爱丽丝·玛格特洛依德_LA)")
    ck(not r.get("problems"), f"1 problems={r.get('problems')!r} (期望无遗留问题)")

    # 2. 跨作品同名冲突检测：单独"爱丽丝"同时属于 AIC 首项与 Touhou 别名
    #    -> 冲突（不误归首个作品），列出命中的作品
    r = resolve("Unknown_爱丽丝_LA")
    ck(r["work"] == "Unknown" and r.get("conflict") is True,
       f"2 work={r['work']!r} conflict={r.get('conflict')!r} (期望 Unknown/True，不误归 AIC)")
    ck(sorted(r.get("conflict_works") or []) == ["AIC", "Touhou"],
       f"2 conflict_works={sorted(r.get('conflict_works') or [])!r} (期望 ['AIC','Touhou'])")
    ck(r["new"] == r["original"],
       f"2 new={r['new']!r} (期望原样，冲突不改名)")

    # 3. 英文整体命中：Alice-Margatroid -> Touhou 并补中文名
    r = resolve("Unknown_Alice-Margatroid_LA")
    ck(r["work"] == "Touhou",
       f"3 work={r['work']!r} (期望 Touhou)")
    ck(r["en"] == "Alice-Margatroid" and r["zh"] == "爱丽丝·玛格特洛依德",
       f"3 en={r['en']!r} zh={r['zh']!r}")
    ck(r["new"] == "Touhou_Alice-Margatroid_爱丽丝·玛格特洛依德_LA",
       f"3 new={r['new']!r}")

    # 4. 分隔符无关：_ 与 · 切出的整体串一致 -> 同样整体命中
    r = resolve("Unknown_爱丽丝_玛格特洛依德_LA")
    ck(r["new"] == "Touhou_Alice-Margatroid_爱丽丝·玛格特洛依德_LA",
       f"4 new={r['new']!r} (期望与用例 1 相同)")

    # 5. 无更长名的两字全称：整体即全称，正常归 Touhou
    r = resolve("Unknown_大妖精_LA")
    ck(r["work"] == "Touhou" and r["en"] == "Daiyousei",
       f"5 work={r['work']!r} en={r['en']!r} (期望 Touhou/Daiyousei)")
    ck(r["new"] == "Touhou_Daiyousei_大妖精_LA",
       f"5 new={r['new']!r}")

    # 6. 无作品别名识别：短名"魔理沙"是 Touhou 雾雨魔理沙的别名 -> 识别出作品并规范化
    r = resolve("Unknown_魔理沙_LA")
    ck(r["work"] == "Touhou" and r["new"] == "Touhou_Marisa-Kirisame_雾雨魔理沙_LA",
       f"6 work={r['work']!r} new={r['new']!r} (期望 Touhou_Marisa-Kirisame_雾雨魔理沙_LA)")

    # 7. 完全未知：保持原样（已有 Unknown 前缀不重复添加，无前缀也不再添加）
    r = resolve("Unknown_琪斯美_LA")
    ck(r["work"] == "Unknown" and r["new"] == r["original"],
       f"7 work={r['work']!r} new={r['new']!r} (期望 Unknown 且原样)")
    ck(sorted(r.get("problems") or []) == ["cn-name", "en-name"],
       f"7 problems={r.get('problems')!r} (期望 缺中文名+缺英文名)")

    # 7b. 无 Unknown 前缀 + 带评级：保持原样，不再添加 Unknown_ 前缀
    r = resolve("琪斯美_LA")
    ck(r["work"] == "Unknown" and r["new"] == "琪斯美_LA",
       f"7b work={r['work']!r} new={r['new']!r} (期望 琪斯美_LA，不再添加 Unknown 前缀)")

    # 8. 有作品前缀路径回归：补全英文名不受影响
    r = resolve("Touhou_古明地恋_LA")
    ck(r["work"] == "Touhou" and r["en"] == "Komeiji-Koishi",
       f"8 work={r['work']!r} en={r['en']!r} (期望 Touhou/Komeiji-Koishi)")
    ck(r["new"] == "Touhou_Komeiji-Koishi_古明地恋_LA",
       f"8 new={r['new']!r}")

    # 9. 撇号英文名：ch'ang feng 应在 body 去符号后命中（不再 auto-filled 补全）
    #    输出时空格转连字符：Ch'ang-Feng（ch'ang feng → Ch'ang-Feng）
    r = resolve("AL_长风_Ch'ang Feng_LA")
    ck(r["work"] == "AL" and r["en"] == "Ch'ang-Feng",
       f"9 work={r['work']!r} en={r['en']!r} (期望 AL/Ch'ang-Feng)")
    ck(r["zh"] == "长风" and r["new"] == "AL_Ch'ang-Feng_长风_LA",
       f"9 zh={r['zh']!r} new={r['new']!r} (期望 AL_Ch'ang-Feng_长风_LA)")
    ck(not r.get("filled"), f"9 filled={r.get('filled')!r} (期望空，非 auto-filled)")

    # 10. 短英文键覆盖率阈值：Chinese 含 ines 子串，但只占 body 13%，应阻止误配 AK
    r = resolve("Unknown_Chinese-Math-Textbook-RuiRui_LA")
    ck(r["work"] == "Unknown" and r["new"] == r["original"],
       f"10 work={r['work']!r} new={r['new']!r} (期望 Unknown 且原样，不误配 AK 伊内丝)")

    print("=" * 50)
    all_ok = all(ok for ok, _ in checks)
    for i, (ok, msg) in enumerate(checks, 1):
        print(f"检查 {i}: {'PASS' if ok else 'FAIL'}  {msg}")
    print("parse2 整体优先(阶段零) 测试:", "全部通过" if all_ok else "存在失败")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())