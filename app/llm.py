"""LLM 客户端（OpenAI 兼容接口）。

三件事在这里解决，都不算新鲜，但缺一个就会在真实使用中崩：

1. **模型会把 JSON 裹进 markdown 代码块**。直接 json.loads 必炸，所以先剥围栏。
2. **模型会漏字段或写错类型**。所以校验失败要把**报错原文**回灌给它自己改，
   而不是我们自己猜怎么补。回灌比重试有效得多——它看见 `KeyError: variants`
   才知道自己漏了什么。
3. **本地中转必须绕开系统代理**。本机有 HTTP_PROXY 指向另一个端口，
   不设 trust_env=False 会得到 502，而且报错信息完全指不到真正原因。
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from openai import OpenAI

from .config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_RETRY,
    LLM_MODEL,
    LLM_TEMPERATURE,
    LLM_TIMEOUT,
    LLM_TRUST_ENV,
)


class LLMError(RuntimeError):
    """LLM 不可用或始终没返回合法结果。调用方应降级而不是把异常抛给用户。"""


_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$")
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def _strip_fence(text: str) -> str:
    """剥掉 markdown 代码围栏。模型十次里有两次会加，不加这步就白重试。"""
    t = text.strip()
    if t.startswith("```"):
        t = _FENCE.sub("", t)
    # 有些模型会在 JSON 前后加一句解释，取第一个 { 到最后一个 }
    left, right = t.find("{"), t.rfind("}")
    if left != -1 and right != -1 and right > left:
        t = t[left : right + 1]
    return _TRAILING_COMMA.sub(r"\1", t).strip()


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str = LLM_BASE_URL,
        model: str = LLM_MODEL,
        api_key: str = LLM_API_KEY,
        timeout: float = LLM_TIMEOUT,
        temperature: float = LLM_TEMPERATURE,
        trust_env: bool = LLM_TRUST_ENV,
    ) -> None:
        self.model = model
        # trust_env=False：不读 HTTP_PROXY/NO_PROXY，本地中转直连。
        # 若改接外网 API 且身处代理环境，在 .env 里设 LLM_TRUST_ENV=1。
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            http_client=httpx.Client(trust_env=trust_env, timeout=timeout),
        )
        self.temperature = temperature

    def ping(self) -> tuple[bool, str]:
        """探活。界面上要能显示「模型可用/不可用」，否则用户不知道该改哪里。"""
        try:
            r = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=8,
                temperature=0,
            )
            return True, (r.choices[0].message.content or "").strip()[:40]
        except Exception as exc:  # noqa: BLE001  探活一律不抛，只回报
            return False, f"{type(exc).__name__}: {exc}"

    def chat(self, system: str, user: str, *, max_tokens: int = 1600) -> str:
        try:
            r = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=self.temperature,
                max_tokens=max_tokens,
            )
            return r.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"调用模型失败：{type(exc).__name__}: {exc}") from exc

    def chat_json(
        self,
        system: str,
        user: str,
        *,
        required_keys: tuple[str, ...] = (),
        max_retry: int = LLM_MAX_RETRY,
    ) -> dict[str, Any]:
        """要结构化输出。失败就把报错原文回灌，最多 max_retry 次。"""
        messages_user = user
        last_err = ""

        for attempt in range(1, max_retry + 1):
            raw = self.chat(system, messages_user)
            cleaned = _strip_fence(raw)
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                last_err = f"JSON 解析失败（第 {attempt} 次）：{exc}"
                messages_user = (
                    f"{user}\n\n---\n你上一次的输出无法被解析为 JSON，错误原文：\n{exc}\n"
                    f"上一次的输出是：\n{raw[:800]}\n请只返回合法 JSON，不要任何解释文字、不要 markdown 代码块。"
                )
                continue

            if not isinstance(data, dict):
                last_err = f"顶层不是对象（第 {attempt} 次）"
                continue

            missing = [k for k in required_keys if k not in data]
            if missing:
                last_err = f"缺少字段 {missing}（第 {attempt} 次）"
                messages_user = (
                    f"{user}\n\n---\n你上一次返回的 JSON 缺少必需字段：{missing}。"
                    f"请补齐后只返回完整 JSON。"
                )
                continue

            return data

        raise LLMError(f"模型连续 {max_retry} 次未返回合法结果。最后一次错误：{last_err}")


_default: LLMClient | None = None


def get_client() -> LLMClient:
    global _default
    if _default is None:
        _default = LLMClient()
    return _default
