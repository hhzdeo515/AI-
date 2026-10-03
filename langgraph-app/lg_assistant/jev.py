"""Native TypeSafe v1 transport. Never treat Jev as a chat completion model."""
from __future__ import annotations

import math
import time
import requests

from . import config, call_metrics, llm


class JevError(llm.LLMError):
    pass


def _probability(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def validate(response, questions):
    if not isinstance(response, dict) or not isinstance(response.get("model"), str):
        raise JevError("JEV 返回缺少模型信息")
    answers = response.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise JevError("JEV 返回的问题集合与请求不一致")
    for name, question in questions.items():
        answer = answers[name]
        if not isinstance(answer, dict) or answer.get("type") != question["type"]:
            raise JevError("JEV 返回类型不匹配")
        if question["type"] == "choice":
            probabilities = answer.get("probabilities")
            criteria = question["criteria"]
            if (not isinstance(probabilities, dict) or set(probabilities) != set(criteria)
                    or answer.get("choice") not in criteria
                    or not _probability(answer.get("confidence"))
                    or not all(_probability(p) for p in probabilities.values())
                    or abs(sum(probabilities.values()) - 1) > .02):
                raise JevError("JEV 候选选项或概率分布无效")
            if probabilities[answer["choice"]] + 1e-6 < max(probabilities.values()):
                raise JevError("JEV 选择不是最高概率选项")
        elif question["type"] == "noul":
            if not _probability(answer.get("noul")):
                raise JevError("JEV yes/no 概率无效")
        else:
            raise JevError("本版本仅使用 choice 与 noul 问题")
    usage = response.get("usage")
    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens")):
        raise JevError("JEV Token 用量无效")
    return response


def evaluate(state, questions):
    if not config.TYPESAFE_API_KEY:
        raise JevError("未配置 TYPESAFE_API_KEY，请填写本版本 .env")
    if not 0.5 < config.JEV_MIN_CONFIDENCE <= 1:
        raise JevError("JEV_MIN_CONFIDENCE 必须大于0.5且不超过1")
    if not questions:
        raise JevError("JEV 问题不能为空")
    payload = {"state": state, "model": config.JEV_MODEL, "questions": questions}
    started = time.perf_counter()
    attempts = 0
    try:
        for attempt in range(config.JEV_MAX_RETRIES + 1):
            attempts += 1
            try:
                response = requests.post(
                    config.JEV_BASE_URL + "/v1/systemone", json=payload,
                    headers={"Authorization": "Bearer " + config.TYPESAFE_API_KEY},
                    timeout=config.JEV_TIMEOUT,
                )
            except requests.RequestException:
                # Transport exceptions can contain URLs, headers or request data.
                if attempt < config.JEV_MAX_RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                raise JevError("JEV 网络连接失败或超时") from None
            if response.status_code in (429, 529) and attempt < config.JEV_MAX_RETRIES:
                try:
                    delay = float(response.headers.get("Retry-After", 2 ** attempt))
                except (ValueError, TypeError):
                    delay = 2 ** attempt
                if not math.isfinite(delay):
                    delay = 2 ** attempt
                time.sleep(min(5, max(.1, delay)))
                continue
            if response.status_code != 200:
                raise JevError(f"JEV 接口返回 HTTP {response.status_code}，请检查密钥、额度或接口配置")
            try:
                result = validate(response.json(), questions)
            except ValueError:
                raise JevError("JEV 接口未返回合法 JSON") from None
            break
    except JevError as exc:
        call_metrics.record(config.JEV_MODEL, time.perf_counter() - started, error=exc,
                            provider="typesafe", attempts=attempts)
        raise
    elapsed = time.perf_counter() - started
    call_metrics.record(result["model"], elapsed, result, provider="typesafe", attempts=attempts)
    return {**result, "seconds": round(elapsed, 3)}
