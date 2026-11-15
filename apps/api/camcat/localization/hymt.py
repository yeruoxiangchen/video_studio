"""Official Hy-MT plain translation prompts with server-owned cue identities."""

from __future__ import annotations

import json
from typing import Any

from camcat.domain.localization import LocalizationCue
from camcat.services.providers import QwenChatClient

LANGUAGES = {
    "zh": "中文",
    "en": "英语",
    "fr": "法语",
    "pt": "葡萄牙语",
    "es": "西班牙语",
    "ja": "日语",
    "tr": "土耳其语",
    "ru": "俄语",
    "ar": "阿拉伯语",
    "ko": "韩语",
    "th": "泰语",
    "it": "意大利语",
    "de": "德语",
    "vi": "越南语",
    "ms": "马来语",
    "id": "印尼语",
    "tl": "菲律宾语",
    "hi": "印地语",
    "pl": "波兰语",
    "cs": "捷克语",
    "nl": "荷兰语",
    "km": "高棉语",
    "my": "缅甸语",
    "fa": "波斯语",
    "gu": "古吉拉特语",
    "ur": "乌尔都语",
    "te": "泰卢固语",
    "mr": "马拉地语",
    "he": "希伯来语",
    "bn": "孟加拉语",
    "ta": "泰米尔语",
    "uk": "乌克兰语",
    "bo": "藏语",
    "kk": "哈萨克语",
    "mn": "蒙古语",
    "ug": "维吾尔语",
    "yue": "粤语",
}


def translation_prompt(
    cues: list[LocalizationCue],
    index: int,
    *,
    language: str,
    glossary: dict[str, str],
    timing_feedback: dict[str, Any] | None = None,
) -> str:
    tag = language.split("-", 1)[0].lower()
    if tag not in LANGUAGES:
        raise ValueError("该专用翻译模型尚未配置所选语言")
    target = "繁体中文" if language.lower() in {"zh-hant", "zh-tw", "zh-hk"} else LANGUAGES[tag]
    parts = []
    context = [c.source_text for i, c in enumerate(cues) if i != index and abs(i - index) <= 1]
    if context:
        parts.append(
            "[背景信息，仅供理解，保留原文含义]\n" + json.dumps(context, ensure_ascii=False)
        )
    if glossary:
        parts.append(
            "参考下面的翻译（术语数据）：\n"
            + "\n".join(
                json.dumps(k, ensure_ascii=False) + " 翻译成 " + json.dumps(v, ensure_ascii=False)
                for k, v in glossary.items()
            )
        )
    if timing_feedback is not None:
        parts.append(
            "[Translation Tasks]\nShorten the current translation to fit the supplied speaking "
            "window while preserving facts, names, quantities, pronouns and negation. "
            "Return only the revised translation.\n"
            + json.dumps(
                {"current_translation": cues[index].text, **timing_feedback}, ensure_ascii=False
            )
        )
    parts.append(
        "将以下文本翻译为"
        + target
        + "，注意只需要输出翻译后的结果，不要额外解释：\n\n"
        + cues[index].source_text
    )
    prompt = "\n\n".join(parts)
    if len(prompt) > 28_000:
        raise ValueError("译制文本及术语过长，请先拆分字幕或缩短术语表")
    return prompt


def translation_text(packet: dict[str, Any]) -> str:
    try:
        choice = packet["choices"][0]
        text = choice["message"]["content"].strip()
        if choice["finish_reason"] != "stop" or not text or "<think>" in text:
            raise ValueError("专用翻译返回了空白或未完成的文本")
        return str(text)
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise ValueError("专用翻译服务返回格式无效") from error


def translate_hymt(
    client: QwenChatClient,
    cues: list[LocalizationCue],
    *,
    language: str,
    glossary: dict[str, str],
    timing_feedback: dict[str, Any] | None,
) -> dict[str, Any]:
    translations = []
    for index, cue in enumerate(cues):
        response = client.request(
            "POST",
            "/v1/chat/completions",
            json={
                "model": client.model,
                "messages": [
                    {
                        "role": "user",
                        "content": translation_prompt(
                            cues,
                            index,
                            language=language,
                            glossary=glossary,
                            timing_feedback=timing_feedback,
                        ),
                    }
                ],
                "temperature": 0.7,
                "top_p": 0.6,
                "top_k": 20,
                "repeat_penalty": 1.05,
                "max_tokens": 2048,
                "seed": 42,
            },
        )
        translations.append({"id": cue.cue_id, "text": translation_text(response)})
    return {"translations": translations}
