"""
Hard rules for 「我的版本」 scripts that the AI does not always follow.
Applied in code so they hold every time (rewrite_posts.py before saving,
generate_dashboard.py before showing older scripts):

1. No emoji at all (口播 scripts are read aloud).
2. AiPlug is only mentioned in scripts that are about AI; anywhere else
   the AiPlug line becomes the normal Syntax Technologies CTA.
"""
import re

_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # pictographs, flags (regional indicators), symbols
    "☀-➿"           # misc symbols, dingbats (✅ ❌ ⚠ ✨ …)
    "⬀-⯿"           # arrows/stars (⭐ ⬆ …)
    "←-⇿"           # arrows
    "⌀-⏿"           # ⏰ ⌛ …
    "︎️‍⃣"  # variation selectors, ZWJ, keycap
    "]"
)
# AI topic words, ignoring the product name itself
_AI_TOPIC = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])|人工智能|自动化|ChatGPT|Claude|Gemini|[Aa]utomat")

CTA_ZH = "有问题欢迎留言，或者联络 Syntax Technologies，012-663 9318。"
CTA_EN = "Leave a comment, or contact Syntax Technologies at 012-663 9318."


def _is_english(text):
    return not re.search(r"[一-鿿]", text)


def strip_emoji(text):
    out = _EMOJI.sub("", text)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return "\n".join(line.strip() for line in out.splitlines()).strip()


def fix_aiplug_cta(text):
    if "AiPlug" not in text:
        return text
    without = "\n".join(l for l in text.splitlines() if "AiPlug" not in l)
    if _AI_TOPIC.search(without):
        return text  # the script really is about AI: keep the AiPlug CTA
    cta = CTA_EN if _is_english(text) else CTA_ZH
    lines, done = [], False
    for line in text.splitlines():
        if "AiPlug" in line:
            if not done:
                lines.append(cta)
                done = True
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def clean_script(text):
    if not text:
        return text
    return fix_aiplug_cta(strip_emoji(text))
