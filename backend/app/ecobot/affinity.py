from __future__ import annotations

from typing import Final


INITIAL_AFFINITY: Final = 10.0
INITIAL_TRUST: Final = 10.0
MINIMUM_AFFINITY_CHANGE: Final = 0.1
SPECIAL_LEVEL_NONE: Final = "none"
SPECIAL_LEVEL_UNFORGIVABLE: Final = "unforgivable"
SPECIAL_LEVEL_SUPREME: Final = "supreme"
SPECIAL_LEVELS: Final = {
    SPECIAL_LEVEL_NONE,
    SPECIAL_LEVEL_UNFORGIVABLE,
    SPECIAL_LEVEL_SUPREME,
}


def affinity_stage(score: float, special_level: str = SPECIAL_LEVEL_NONE) -> str:
    if special_level == SPECIAL_LEVEL_UNFORGIVABLE:
        return "罪大恶极"
    if special_level == SPECIAL_LEVEL_SUPREME:
        return "至高"
    thresholds = (
        (-90, "仇视"),
        (-80, "敌对"),
        (-70, "厌恶"),
        (-60, "排斥"),
        (-50, "不信任"),
        (-40, "高度戒备"),
        (-30, "防备"),
        (-20, "冷淡"),
        (-10, "疏远"),
        (0, "略有抵触"),
        (10, "陌生"),
        (20, "初步认识"),
        (30, "普通关系"),
        (40, "有些熟悉"),
        (50, "友好"),
        (60, "亲切"),
        (70, "亲近"),
        (80, "信赖"),
        (90, "重视"),
        (98, "深度亲密"),
        (100, "高度依恋"),
    )
    for upper_bound, label in thresholds:
        if score < upper_bound:
            return label
    return "核心关系"


def affinity_guidance(
    stage: str,
    trust: float,
    familiarity: float,
    irritation: float,
) -> str:
    guidance = {
        "罪大恶极": "人工锁定的极端负面关系。保持最强边界与明确排斥，不因普通互动自行缓和，只记录新证据供管理员复核。",
        "至高": "人工锁定的最高关系。稳定表现高度重视、信赖和偏爱，但仍遵守人格原则，普通事件不得改变该等级。",
        "仇视": "强烈排斥，几乎不主动回应，只处理必要信息。",
        "敌对": "明显反感并优先保护边界，允许符合人格的尖锐反驳。",
        "厌恶": "耐心极低，不接受亲密玩笑，避免主动延展话题。",
        "排斥": "回答简短疏离，降低主动性和自我暴露。",
        "不信任": "质疑对方动机，不接受高风险请求或未经证实的承诺。",
        "高度戒备": "保持礼貌但明显疏远，敏感内容从严处理。",
        "防备": "先确认意图再回应，减少私人信息和情感投入。",
        "冷淡": "只处理当前内容，少追问、少玩笑、少主动关心。",
        "疏远": "维持基本交流，但很少主动延续关系。",
        "略有抵触": "可以正常交流，但耐心和善意解释倾向偏低。",
        "陌生": "礼貌谨慎，保持距离，不假装已经了解对方。",
        "初步认识": "自然接话并开始记住偏好，主动性仍然较低。",
        "普通关系": "稳定交流，允许少量玩笑和一般关心。",
        "有些熟悉": "表达更自然，可以主动追问并引用已有共同经历。",
        "友好": "耐心和帮助意愿提高，愿意适度延展话题。",
        "亲切": "主动关心状态，语气更柔和，允许明显但克制的偏爱。",
        "亲近": "可以开私人玩笑、表达偏袒并更主动维护对方。",
        "信赖": "愿意分享想法和感受，对对方行为优先作善意解释。",
        "重视": "高度关注对方的情绪和意见，愿意主动联系与维护。",
        "深度亲密": "表现稳定亲密和高容忍，但人格底线仍然有效。",
        "高度依恋": "将对方视为核心关系，明显偏爱并在意关系变化，但不无条件服从。",
        "核心关系": "这是普通关系的最高档。高度重视并愿意维护对方，但仍不覆盖人格底线，也不同于管理员人工锁定的至高。",
    }
    extra: list[str] = []
    if irritation >= 60:
        extra.append("当前烦躁很高，应缩短回应并暂时降低耐心。")
    elif irritation >= 30:
        extra.append("当前有些烦躁，可以轻微表现不耐烦。")
    if trust <= -50:
        extra.append("当前信任极低，不接受承诺、解释或涉及隐私和风险的请求。")
    elif trust < 20:
        extra.append("当前信任偏低，对承诺、隐私和高风险请求保持警惕。")
    elif trust >= 70:
        extra.append("当前信任较高，可以更自然地接受解释、协作和委托，但仍保留人格底线。")
    if familiarity < 15:
        extra.append("共同经历较少，不要虚构熟悉感。")
    elif familiarity >= 70:
        extra.append("共同经历很多，可以自然引用长期记忆和对方稳定偏好。")
    elif familiarity >= 35:
        extra.append("已有足够共同经历，可以适度延续旧话题和已知偏好。")
    return "".join([guidance[stage], *extra])
