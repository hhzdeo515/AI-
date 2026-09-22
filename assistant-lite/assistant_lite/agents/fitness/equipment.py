"""常见健身器械知识库。

用途：文字问器械名时零 token 直接回答；图片识别出器械名后也复用这里的内容，
再由模型结合健康档案个性化润色。
内容为通用训练常识，不构成医疗建议。
"""

from __future__ import annotations

from typing import Any

EQUIPMENT: dict[str, dict[str, Any]] = {
    "史密斯机": {
        "aliases": ("史密斯", "smith", "轨道杠铃"),
        "targets": "胸、肩、腿、臀（随动作变化）",
        "usage": [
            "先调好安全卡扣高度，卡扣应在最低位略高于胸口/膝下",
            "躺/站到杠铃正下方，确认左右配重对称",
            "起杠后旋转手腕解锁挂钩，做完记得转回去挂上",
        ],
        "mistakes": ["忘记设安全卡扣", "左右配重不等导致杠铃歪斜", "全程锁死肘/膝关节"],
        "dose": "新手每动作 3 组 × 8–12 次，组间休息 60–90 秒",
    },
    "卧推架": {
        "aliases": ("卧推", "平板卧推", "bench"),
        "targets": "胸大肌、三角肌前束、肱三头肌",
        "usage": [
            "眼睛在杠铃正下方，肩胛后缩下沉贴紧凳面",
            "握距略宽于肩，全握（拇指扣住）",
            "下放到胸骨中下段轻触，再推起",
        ],
        "mistakes": ["耸肩、肩胛松开", "杠铃弹胸", "臀离凳面起桥过度"],
        "dose": "3–4 组 × 6–12 次；大重量务必有保护者或使用安全杆",
    },
    "深蹲架": {
        "aliases": ("深蹲", "squat rack", "蹲架"),
        "targets": "股四头肌、臀大肌、核心",
        "usage": [
            "挂钩高度设在锁骨下方一点，出杠只需小半步",
            "杠铃放在斜方肌上部（高杠）或三角肌后束（低杠）",
            "下蹲至大腿与地面平行或略低，膝盖与脚尖同向",
        ],
        "mistakes": ["膝盖内扣", "脚跟离地", "腰椎代偿弓背"],
        "dose": "3–5 组 × 5–10 次；必须设置安全杆",
    },
    "腿举机": {
        "aliases": ("腿举", "倒蹬", "leg press"),
        "targets": "股四头肌、臀大肌、腘绳肌",
        "usage": [
            "臀背完全贴紧靠垫，不要留空隙",
            "脚踩在踏板中上部，与肩同宽",
            "下放至膝角约 90 度即止，不要追求过低",
        ],
        "mistakes": ["下放过深导致腰椎离开靠垫", "膝关节完全锁死", "脚位过高/过低伤膝"],
        "dose": "3–4 组 × 10–15 次",
    },
    "高位下拉": {
        "aliases": ("下拉", "lat pulldown", "背阔肌下拉"),
        "targets": "背阔肌、肱二头肌",
        "usage": [
            "大腿卡在压腿垫下固定身体",
            "正握略宽于肩，先把肩胛下沉再拉",
            "拉至锁骨上方，感受背部收紧",
        ],
        "mistakes": ["用手臂硬拽而不是用背", "身体大幅后仰借力", "拉到颈后（伤肩）"],
        "dose": "3–4 组 × 10–15 次",
    },
    "坐姿划船": {
        "aliases": ("划船机", "rowing", "坐姿划船机"),
        "targets": "背部、肱二头肌、后三角",
        "usage": [
            "先挺胸沉肩，不要含胸驼背",
            "肘部贴近身体向后拉，肩胛主动后缩",
            "回放时控制，不要靠惯性甩",
        ],
        "mistakes": ["弓背借力", "耸肩", "回放过快失去张力"],
        "dose": "3–4 组 × 10–15 次",
    },
    "哑铃": {
        "aliases": ("dumbbell", "小哑铃"),
        "targets": "全身（取决于动作）",
        "usage": [
            "选择能标准完成 8–12 次的重量",
            "动作全程控制离心（放下）阶段 2–3 秒",
            "手腕保持中立，不要翻腕",
        ],
        "mistakes": ["重量过大导致借力", "动作幅度不完整", "忽快忽慢失去控制"],
        "dose": "每动作 3 组 × 8–12 次",
    },
    "杠铃": {
        "aliases": ("barbell", "奥杆"),
        "targets": "全身（取决于动作）",
        "usage": [
            "确认卡扣已扣紧，配重左右对称",
            "先用空杆练习动作模式，再逐级加重",
            "复合动作建议有人保护",
        ],
        "mistakes": ["跳过热身直接上大重量", "配重不对称", "锁死关节"],
        "dose": "复合动作 3–5 组 × 5–10 次",
    },
    "龙门架": {
        "aliases": ("绳索", "cable", "cross over", "绳索机"),
        "targets": "全身（可调角度多）",
        "usage": [
            "先调滑轮高度与配重销，确认销子插到底",
            "站姿稳定，核心收紧，用绳索全程保持张力",
            "动作末端停顿 1 秒强化收缩",
        ],
        "mistakes": ["被回弹的配重拽走", "配重销没插稳", "身体随重量晃动"],
        "dose": "3 组 × 12–15 次（孤立动作）",
    },
    "跑步机": {
        "aliases": ("treadmill", "跑台"),
        "targets": "心肺、下肢",
        "usage": [
            "先慢走 3–5 分钟热身",
            "新手用坡度代替速度来提高强度，对膝更友好",
            "结束前降速慢走 3 分钟再停",
        ],
        "mistakes": ["不系安全扣", "跑步时看手机", "突然停机"],
        "dose": "有氧 20–40 分钟，心率控制在最大心率的 60%–75%",
    },
    "椭圆机": {
        "aliases": ("elliptical", "太空漫步机"),
        "targets": "心肺、下肢（关节压力小）",
        "usage": ["脚掌踩实踏板", "双手推拉把手带动上肢", "保持躯干直立不弯腰"],
        "mistakes": ["只靠腿部不推拉", "阻力过小变成滑行"],
        "dose": "20–40 分钟；适合膝/踝有伤的人做低冲击有氧",
    },
    "壶铃": {
        "aliases": ("kettlebell", "壶铃摆荡"),
        "targets": "臀、后链、核心",
        "usage": [
            "摆荡是髋铰链发力，不是靠手臂抬",
            "顶点时臀腿收紧，壶铃靠惯性到胸高",
            "全程保持背部中立",
        ],
        "mistakes": ["用肩抬起", "弓背下放", "手腕过度后折"],
        "dose": "3–5 组 × 15–20 次",
    },
    "弹力带": {
        "aliases": ("阻力带", "resistance band", "拉力带"),
        "targets": "全身（辅助与激活）",
        "usage": ["固定端要牢靠", "用前检查有无破损", "控制回弹，不要突然松手"],
        "mistakes": ["带子老化断裂", "固定点不牢", "回弹打到脸"],
        "dose": "每动作 2–3 组 × 15–20 次",
    },
    "引体向上架": {
        "aliases": ("引体向上", "pull up", "单杠"),
        "targets": "背阔肌、肱二头肌、核心",
        "usage": [
            "先沉肩再拉，不要耸肩起",
            "做不了可先用弹力带辅助或做离心",
            "下放到手臂接近伸直即可",
        ],
        "mistakes": ["摆荡借力", "半程动作", "耸肩"],
        "dose": "3–5 组 × 力竭前 1–2 次",
    },
    "健腹轮": {
        "aliases": ("ab wheel", "腹肌轮"),
        "targets": "核心、腹直肌",
        "usage": ["从跪姿开始", "骨盆后倾，腰部不要塌", "只推到能保持腰不塌的距离"],
        "mistakes": ["塌腰（极易伤腰）", "推得过远失控"],
        "dose": "3 组 × 8–12 次",
    },
}

#: 反向索引：别名 -> 规范名
_INDEX: dict[str, str] = {}
for _name, _info in EQUIPMENT.items():
    _INDEX[_name] = _name
    for _a in _info.get("aliases", ()):
        _INDEX[_a.lower()] = _name


def find(text: str) -> tuple[str | None, dict[str, Any] | None]:
    """从一段文字里找出器械。返回 (规范名, 条目) 或 (None, None)。"""
    if not text:
        return None, None
    low = text.lower()
    # 长名优先，避免"哑铃"抢先匹配"哑铃卧推"里的部分
    for alias in sorted(_INDEX, key=len, reverse=True):
        if alias in low:
            name = _INDEX[alias]
            return name, EQUIPMENT[name]
    return None, None


def render(name: str, info: dict[str, Any]) -> str:
    """把器械条目渲染成可读文本。"""
    parts = [f"**{name}**", f"主要练：{info.get('targets', '')}"]
    if info.get("usage"):
        parts.append("\n**用法要点**")
        parts += [f"  {i}. {u}" for i, u in enumerate(info["usage"], 1)]
    if info.get("mistakes"):
        parts.append("\n**常见错误**")
        parts += [f"  - {m}" for m in info["mistakes"]]
    if info.get("dose"):
        parts.append(f"\n**建议剂量**\n  {info['dose']}")
    return "\n".join(parts)


def names() -> list[str]:
    return list(EQUIPMENT)
