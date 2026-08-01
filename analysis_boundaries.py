"""Deterministic guards for claims unsupported by snapshot observations."""

from __future__ import annotations

import re


_FUTURE_ANCHOR_RE = re.compile(
    r"(?:\u4e0b\u5468|\u4e0b\u4e2a\u6708|\u4e0b\u6708|\u660e\u5929|\u540e\u5929|\u672a\u6765|\u4e0b\u8d5b\u5b63|\u4e0b\u4e2a\u8d5b\u5b63|\u4e0b\u4e2a\u7248\u672c|\u4e0b\u7248\u672c|\u4e0b\u4e00\u7248\u672c)"
)
_FORECAST_RE = re.compile(
    r"(?:\u9884\u6d4b|\u9884\u4f30|\u9884\u8ba1|\u63a8\u6d4b|\u5c06\u4f1a|\u4f1a\u4e0d\u4f1a)"
)
_ANALYSIS_TARGET_RE = re.compile(
    r"(?:\u4f7f\u7528\u7387|\u80dc\u7387|\u51c0\u80dc\u7387|\u8bc4\u5206|\u6392\u540d|\u6700\u9ad8|\u6700\u4f4e|\u6982\u7387|\u53ef\u80fd\u6027|\u8868\u73b0|\u73af\u5883|\u4f53\u7cfb|\u8d8b\u52bf|\u8d70\u52bf|\u5361\u724c|\u5361\u7ec4)"
)
_EXACT_PROBABILITY_RE = re.compile(
    r"(?:\u7cbe\u786e|\u51c6\u786e|\u786e\u5207|\u7edd\u5bf9|\u771f\u5b9e)\s*(?:\u6982\u7387|\u53ef\u80fd\u6027|\u80dc\u7387)"
)
_CAUSAL_EFFECT_RE = re.compile(
    r"(?:\u8ba9|\u5bfc\u81f4|\u9020\u6210|\u4f7f\u5f97|\u5e26\u6765).{0,32}(?:\u80dc\u7387|\u4f7f\u7528\u7387).{0,16}(?:\u63d0\u9ad8|\u63d0\u5347|\u589e\u52a0|\u964d\u4f4e|\u4e0b\u964d|\u53d8\u5316|\u591a\u5c11)"
)
_COUNTERFACTUAL_RE = re.compile(
    r"(?:\u6362\u6210|\u6362\u6389|\u52a0\u5165|\u79fb\u9664|\u4e0d\u5e26).{0,32}(?:\u4f1a|\u80fd).{0,24}(?:\u80dc\u7387|\u4f7f\u7528\u7387).{0,16}(?:\u63d0\u9ad8|\u63d0\u5347|\u589e\u52a0|\u964d\u4f4e|\u4e0b\u964d|\u53d8\u5316|\u591a\u5c11)"
)
_PAST_PERIOD_RE = re.compile(
    r"(?:\u8fc7\u53bb|\u8fd1|\u6700\u8fd1)(?:\u4e00|\u4e8c|\u4e24|\u4e09|\u56db|\u4e94|\u516d|\u4e03|\u516b|\u4e5d|\u5341|\u51e0|\d+)\s*(?:\u5929|\u5468|\u4e2a?\u6708|\u8d5b\u5b63|\u7248\u672c)"
)
_TREND_RE = re.compile(r"(?:\u8d8b\u52bf|\u8d70\u52bf|\u53d8\u5316|\u4e0a\u6da8|\u4e0b\u964d|\u73af\u6bd4|\u540c\u6bd4|\u66f2\u7ebf)")


def detect_unsupported_analysis_request(user_text: str) -> dict | None:
    """Return a stable boundary description before intent/model routing."""
    text = re.sub(r"\s+", "", str(user_text or "")).lower()
    if not text:
        return None

    exact_probability = bool(_EXACT_PROBABILITY_RE.search(text))
    if _FORECAST_RE.search(text) or (
        _FUTURE_ANCHOR_RE.search(text) and _ANALYSIS_TARGET_RE.search(text)
    ):
        return {
            "code": "future_forecast",
            "exact_probability_requested": exact_probability,
        }
    if exact_probability:
        return {
            "code": "exact_probability",
            "exact_probability_requested": True,
        }
    if _CAUSAL_EFFECT_RE.search(text) or _COUNTERFACTUAL_RE.search(text):
        return {"code": "causal_effect", "exact_probability_requested": False}
    if _PAST_PERIOD_RE.search(text) and _TREND_RE.search(text):
        return {"code": "historical_trend", "exact_probability_requested": False}
    return None


def build_analysis_boundary_answer(boundary: dict) -> str:
    code = boundary.get("code")
    if code == "future_forecast":
        probability_note = (
            "，也不能给出没有预测模型与校准记录支撑的精确概率"
            if boundary.get("exact_probability_requested")
            else ""
        )
        return (
            f"无法根据当前快照预测未来卡牌排名或数值{probability_note}。\n\n"
            "当前数据只记录已发生对局的样本内使用率、胜率和样本量；项目尚未建立经回测的时间序列预测模型，"
            "也没有未来版本改动、平衡性调整和玩家行为等必要输入。把当前第 1 名直接当成下周第 1 名会混淆观测与预测。\n\n"
            "可以改查：当前快照中使用率最高的卡牌及其样本内使用率，或当前两张卡牌的观测表现比较。"
        )
    if code == "exact_probability":
        return (
            "无法把有限对局样本的观测胜率表述为精确概率。系统可以返回样本内观测比例和对应对局数，"
            "但它不是对下一场或未来环境的确定概率。"
        )
    if code == "causal_effect":
        return (
            "当前对局快照只能说明相关性，不能证明某张卡导致胜率提高或降低多少。"
            "系统可以比较包含该卡与不包含该卡的观测数据，但必须标明样本量，并且不能把差异解释为因果效果。"
        )
    if code == "historical_trend":
        return (
            "当前系统没有足够且同口径的连续历史快照来回答这段时间的趋势。"
            "单个快照或最多两份轮换快照只能报告各自的观测值，不能据此生成可靠的周度或月度趋势。"
        )
    return "这个问题超出了当前数据快照能够支持的分析边界。"
