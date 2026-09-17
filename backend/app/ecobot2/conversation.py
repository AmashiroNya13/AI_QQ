from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class DialogueActType(StrEnum):
    QUESTION = "question"
    REQUEST = "request"
    GREETING = "greeting"
    INSULT = "insult"
    APOLOGY = "apology"
    AGREEMENT = "agreement"
    DISAGREEMENT = "disagreement"
    STATEMENT = "statement"


@dataclass(frozen=True, slots=True)
class ConversationThread:
    thread_id: str
    channel_id: str
    topic: str
    participants: tuple[str, ...] = ()
    addressee_id: str | None = None
    salience: float = 0.0
    status: str = "active"
    version: int = 1
    last_event_at: str = ""


@dataclass(frozen=True, slots=True)
class DialogueAct:
    act_id: str
    event_id: str
    thread_id: str
    actor_id: str
    act_type: DialogueActType
    target_id: str | None
    confidence: float
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SocialObligation:
    obligation_id: str
    thread_id: str
    source_event_id: str
    owner_id: str
    target_id: str | None
    kind: str
    strength: float
    status: str = "pending"
    due_at: str | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AddressingInference:
    event_id: str
    candidates: Mapping[str, float]
    selected_target_id: str | None
    confidence: float
    evidence: tuple[str, ...] = field(default_factory=tuple)


def normalize_topic(content: str) -> str:
    text = re.sub(r"\s+", " ", content.strip())
    text = re.sub(r"[!?！？。！？，,、]+", " ", text)
    return text[:48].strip() or "未命名话题"


def classify_dialogue_act(content: str) -> DialogueActType:
    text = content.strip()
    if not text:
        return DialogueActType.STATEMENT
    if any(token in text for token in ("对不起", "抱歉", "我的错")):
        return DialogueActType.APOLOGY
    if any(token in text for token in ("傻", "蠢", "滚", "垃圾", "脑残")):
        return DialogueActType.INSULT
    if text.endswith(("?", "？")) or any(token in text for token in ("吗", "怎么", "为什么", "谁", "哪里")):
        return DialogueActType.QUESTION
    if any(token in text for token in ("请", "帮我", "能不能", "可以吗", "给我")):
        return DialogueActType.REQUEST
    if any(token in text for token in ("你好", "嗨", "早", "晚安")):
        return DialogueActType.GREETING
    if any(token in text for token in ("同意", "确实", "对的", "没错")):
        return DialogueActType.AGREEMENT
    if any(token in text for token in ("不对", "但是", "不是", "反对")):
        return DialogueActType.DISAGREEMENT
    return DialogueActType.STATEMENT


def infer_addressing(
    event_id: str,
    *,
    actor_id: str,
    content: str,
    metadata: Mapping[str, Any],
    agent_id: str = "ecobot",
) -> AddressingInference:
    is_private = bool(metadata.get("is_private"))
    has_explicit_address = bool(metadata.get("has_at") or metadata.get("has_reply"))
    if is_private:
        return AddressingInference(event_id, {agent_id: 0.98}, agent_id, 0.98, ("私聊频道",))
    if has_explicit_address:
        return AddressingInference(event_id, {agent_id: 0.95}, agent_id, 0.95, ("显式 @ 或回复引用",))
    act = classify_dialogue_act(content)
    if act in {DialogueActType.QUESTION, DialogueActType.REQUEST}:
        return AddressingInference(event_id, {agent_id: 0.42}, None, 0.58, ("群聊中存在问题形式，但没有明确目标",))
    return AddressingInference(event_id, {agent_id: 0.18}, None, 0.82, ("没有直接指向 Ecobot 的证据",))


def new_thread_id(channel_id: str, topic: str) -> str:
    return f"thread:{channel_id}:{uuid.uuid5(uuid.NAMESPACE_URL, topic).hex[:16]}"
