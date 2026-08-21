from bot.ai import MissingKeyError, pack_messages, require_llm
from bot.config import GPT, GROK
import pytest


def test_pack_messages_order() -> None:
    packed = pack_messages(
        "sys",
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
        "next",
    )
    assert packed[0] == {"role": "system", "content": "sys"}
    assert packed[-1] == {"role": "user", "content": "next"}
    assert len(packed) == 4


def test_require_llm_raises_without_key() -> None:
    with pytest.raises(MissingKeyError) as gpt_err:
        require_llm({}, GPT)
    assert "OPENAI_API_KEY" in str(gpt_err.value)

    with pytest.raises(MissingKeyError) as grok_err:
        require_llm({}, GROK)
    assert "XAI_API_KEY" in str(grok_err.value)
