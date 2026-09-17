"""Provider presets — what "add a model by adding its key" fills in for you (FR-10).

Free tiers change without notice (in July 2026 alone Cerebras became a card-required trial and
GitHub Models was retired), so every number here says where it came from and is only a prior:
the limiter overrides it with the provider's own rate-limit headers, and
`cadre provider refresh` asks each endpoint what it serves today.

Checked on the date in `CHECKED`. `source` values (per preset, overridable per model):
  "docs"      — the provider's own page states the number
  "reported"  — third-party write-ups; the provider publishes no figure
  "guess"     — nothing published; deliberately conservative

`day_reset` (ADR-018) and `trains_on_free_data` (ADR-020) carry the URL they were read from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CHECKED = "2026-09-17"


@dataclass(frozen=True)
class ModelPreset:
    name: str
    tier: str = "fast"  # strong | fast
    family: str = ""
    rpm: int | None = None
    rpd: int | None = None
    tpm: int | None = None
    tpd: int | None = None
    protocol: str = "native"  # native | json
    source: str = ""  # empty: the preset's source applies


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    base_url: str
    env: str | None  # conventional environment variable for the key
    free: bool
    local: bool = False
    signup: str = ""
    source: str = "guess"
    note: str = ""
    models: tuple[ModelPreset, ...] = field(default_factory=tuple)
    #: when models are discovered rather than listed, apply these limits to each one
    default_limits: ModelPreset | None = None
    #: only offer discovered models whose id matches (OpenRouter: the `:free` variants)
    discover_filter: str | None = None
    #: placeholders in base_url the owner must supply, e.g. Cloudflare's account id
    params: tuple[str, ...] = ()
    #: the provider's own daily clock: UTC, an IANA zone, or rolling (ADR-018)
    day_reset: str = "UTC"
    day_reset_source: str = ""
    #: does the free tier train on prompts? yes / no / unknown (ADR-020)
    trains_on_free_data: str = "unknown"
    policy_source: str = ""


_GROQ = dict(rpm=30, rpd=1000, tpm=8000, tpd=200_000, source="docs")
# Google publishes no per-model free limits (they are shown only in AI Studio). The 3.x Flash RPD
# of 20 and Flash-Lite RPD of 500 are third-party reports from Sept 2026; RPM/TPM are guesses.
_G_FLASH = dict(rpm=5, rpd=20, tpm=250_000, source="reported")
_G_LITE = dict(rpm=10, rpd=500, tpm=250_000, source="reported")
_G_OLD = dict(rpm=5, rpd=20, tpm=250_000, source="guess")

PRESETS: dict[str, Preset] = {p.id: p for p in [
    Preset(
        "groq", "Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", True,
        signup="https://console.groq.com/keys", source="docs",
        note="Fastest free tier. 8,000 tokens/minute per model is the ceiling a team hits first. "
             "Two model families on one key, so reviews can be independent.",
        models=(
            ModelPreset("openai/gpt-oss-120b", "strong", "gpt-oss", **_GROQ),
            ModelPreset("openai/gpt-oss-20b", "fast", "gpt-oss", **_GROQ),
            ModelPreset("qwen/qwen3.8-27b", "fast", "qwen", **_GROQ),
        ),
        # "Rate limits apply at the organization level"; no reset clock is published, and the
        # x-ratelimit-reset-requests header "always refers to Requests Per Day"
        day_reset="rolling", day_reset_source="https://console.groq.com/docs/rate-limits",
        trains_on_free_data="no",
        policy_source="https://console.groq.com/docs/legal/services-agreement",
    ),
    Preset(
        "gemini", "Google AI Studio (Gemini)",
        "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", True,
        signup="https://aistudio.google.com/apikey", source="reported",
        note="One key reaches every free Gemini chat model, each with its own quota. Limits apply "
             "per project and are shown only in AI Studio. The free tier is used to improve "
             "Google's products — keep private runs away from it.",
        models=(
            ModelPreset("gemini-3.8-flash", "strong", "gemini", **_G_FLASH),
            ModelPreset("gemini-3.7-flash", "strong", "gemini", **_G_FLASH),
            ModelPreset("gemini-3.6-flash", "strong", "gemini", **_G_FLASH),
            ModelPreset("gemini-3.5-flash", "strong", "gemini", **_G_FLASH),
            ModelPreset("gemini-3-flash-preview", "strong", "gemini", **{**_G_FLASH, "source": "guess"}),
            ModelPreset("gemini-2.5-flash", "strong", "gemini", **_G_OLD),
            ModelPreset("gemini-2.5-pro", "strong", "gemini", **{**_G_OLD, "rpm": 2}),
            ModelPreset("gemini-3.5-flash-lite", "fast", "gemini", **_G_LITE),
            ModelPreset("gemini-3.1-flash-lite", "fast", "gemini", **_G_LITE),
            ModelPreset("gemini-2.5-flash-lite", "fast", "gemini", **{**_G_LITE, "rpd": 100, "source": "guess"}),
        ),
        day_reset="America/Los_Angeles",
        day_reset_source="https://ai.google.dev/gemini-api/docs/rate-limits",
        trains_on_free_data="yes", policy_source="https://ai.google.dev/gemini-api/docs/pricing",
    ),
    Preset(
        "openrouter", "OpenRouter (free models)", "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY", True, signup="https://openrouter.ai/keys", source="docs",
        note="20 requests/minute; 50/day, or 1,000/day once 10 credits have ever been bought. "
             "The free catalogue changes often, so models are discovered, not listed. Whether a "
             "free model's host trains on prompts depends on that host.",
        default_limits=ModelPreset("", rpm=20, rpd=50),
        discover_filter=r":free$",
        day_reset="UTC", day_reset_source="https://openrouter.ai/docs/api-reference/limits",
        trains_on_free_data="unknown",
        policy_source="https://openrouter.ai/docs/guides/privacy/provider-logging",
    ),
    Preset(
        "mistral", "Mistral (free mode)", "https://api.mistral.ai/v1", "MISTRAL_API_KEY", True,
        signup="https://console.mistral.ai/api-keys", source="reported",
        note="Limits are shown only in the console. Free mode may train on your data unless you "
             "opt out under Admin → Privacy.",
        models=(
            ModelPreset("mistral-medium-latest", "strong", "mistral", rpm=2, tpm=500_000),
            ModelPreset("mistral-small-latest", "fast", "mistral", rpm=2, tpm=500_000),
        ),
        trains_on_free_data="yes", policy_source="https://help.mistral.ai/en/articles/347617",
    ),
    Preset(
        "cohere", "Cohere (trial key)", "https://api.cohere.ai/compatibility/v1", "CO_API_KEY", True,
        signup="https://dashboard.cohere.com/api-keys", source="docs",
        note="1,000 calls a month in total and non-commercial only — use as a last fallback. "
             "Prompts may train Cohere's models unless you opt out in the dashboard.",
        # 20/min is stated; 33/day is the monthly 1,000 spread evenly (derived, not stated)
        models=(ModelPreset("command-a-03-2025", "strong", "cohere", rpm=20, rpd=33),),
        trains_on_free_data="yes", policy_source="https://cohere.com/enterprise-data-commitments",
    ),
    Preset(
        "nvidia", "NVIDIA API Catalog (trial)", "https://integrate.api.nvidia.com/v1",
        "NVIDIA_API_KEY", True, signup="https://build.nvidia.com", source="reported",
        note="Trial credits for evaluation only. The trial terms let NVIDIA use prompts and "
             "outputs to improve its models.",
        models=(ModelPreset("meta/llama-3.3-70b-instruct", "strong", "llama", rpm=40),),
        trains_on_free_data="yes",
        policy_source="https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf",
    ),
    Preset(
        "cloudflare", "Cloudflare Workers AI",
        "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1", "CLOUDFLARE_API_TOKEN",
        True, signup="https://dash.cloudflare.com/profile/api-tokens", source="docs",
        note="10,000 neurons a day (roughly 50k–290k tokens depending on the model); 300 text "
             "requests a minute. Cloudflare does not train on your prompts.",
        params=("account_id",),
        models=(
            ModelPreset("@cf/meta/llama-3.3-70b-instruct-fp8-fast", "strong", "llama",
                        rpm=300, tpd=50_000, source="reported"),
            ModelPreset("@cf/openai/gpt-oss-120b", "strong", "gpt-oss", rpm=300, tpd=50_000,
                        source="reported"),
        ),
        day_reset="UTC", day_reset_source="https://developers.cloudflare.com/workers-ai/platform/limits/",
        trains_on_free_data="no",
        policy_source="https://developers.cloudflare.com/workers-ai/platform/data-usage/",
    ),
    Preset(
        "zai", "Z.ai (GLM Flash models)", "https://api.z.ai/api/paas/v4", "ZAI_API_KEY", True,
        signup="https://z.ai/manage-apikey/apikey-list", source="guess",
        note="GLM-4.7-Flash and GLM-4.5-Flash are free; about one request at a time (reported).",
        models=(
            ModelPreset("glm-4.7-flash", "fast", "glm", rpm=10),
            ModelPreset("glm-4.5-flash", "fast", "glm", rpm=10),
        ),
        trains_on_free_data="unknown",
        policy_source="https://docs.z.ai/legal-agreement/privacy-policy",
    ),
    Preset(
        "huggingface", "Hugging Face Inference Providers", "https://router.huggingface.co/v1",
        "HF_TOKEN", True, signup="https://huggingface.co/settings/tokens", source="docs",
        note="About $0.10 of credit a month — enough for a handful of calls. Hugging Face does not "
             "train on requests; the upstream provider's policy applies.",
        models=(ModelPreset("openai/gpt-oss-120b", "strong", "gpt-oss", rpd=20, source="guess"),),
        trains_on_free_data="unknown",
        policy_source="https://huggingface.co/docs/inference-providers/security",
    ),
    # --- local: no key, nothing leaves the machine. On an 8 GB single-channel laptop a 1.5B
    # model manages ~3.4 tok/s, so these are fallbacks, not a team's main engine.
    Preset("ollama", "Ollama (local)", "http://127.0.0.1:11434/v1", None, True, local=True,
           source="docs", models=(ModelPreset("qwen2.5:1.5b", "fast", "qwen"),),
           trains_on_free_data="no"),
    Preset("llamacpp", "llama.cpp server (local)", "http://127.0.0.1:8080/v1", None, True,
           local=True, source="docs", trains_on_free_data="no"),
    Preset("lmstudio", "LM Studio (local)", "http://127.0.0.1:1234/v1", None, True, local=True,
           source="docs", trains_on_free_data="no"),
    # --- paid, opt-in, labelled; same adapter
    Preset("deepseek", "DeepSeek (paid API)", "https://api.deepseek.com", "DEEPSEEK_API_KEY", False,
           signup="https://platform.deepseek.com/api_keys", source="docs",
           note="Paid per token; no free tier. Free DeepSeek models appear only when OpenRouter "
                "offers a DeepSeek `:free` model (none on 2026-09-17).",
           models=(ModelPreset("deepseek-flash", "strong", "deepseek"),
                   ModelPreset("deepseek-v4-pro", "strong", "deepseek")),
           trains_on_free_data="unknown",
           policy_source="https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html"),
    Preset("openai", "OpenAI (paid)", "https://api.openai.com/v1", "OPENAI_API_KEY", False,
           signup="https://platform.openai.com/api-keys",
           models=(ModelPreset("gpt-5-mini", "strong", "openai"),)),
    Preset("anthropic", "Anthropic (paid, OpenAI-compatible endpoint)", "https://api.anthropic.com/v1",
           "ANTHROPIC_API_KEY", False, signup="https://console.anthropic.com/settings/keys",
           models=(ModelPreset("claude-haiku-4-5", "strong", "claude"),)),
    Preset("custom", "Any OpenAI-compatible endpoint", "", None, False),
]}

_FAMILIES = [
    ("gpt-oss", "gpt-oss"), ("gemini", "gemini"), ("gemma", "gemma"), ("llama", "llama"),
    ("qwen", "qwen"), ("qwq", "qwen"), ("mixtral", "mistral"), ("mistral", "mistral"),
    ("magistral", "mistral"), ("devstral", "mistral"), ("codestral", "mistral"),
    ("deepseek", "deepseek"), ("glm", "glm"), ("command", "cohere"), ("north", "cohere"),
    ("claude", "claude"), ("kimi", "kimi"), ("nemotron", "nemotron"), ("phi", "phi"),
    ("grok", "grok"), ("gpt", "openai"), ("o3", "openai"), ("o4", "openai"),
]

_STRONG_WORDS = re.compile(r"large|medium|pro\b|plus|maverick|reasoning|command-a|ultra|super", re.I)

#: model ids that are not chat models an agent can use
NOT_CHAT = re.compile(
    r"embed|tts|audio|speech|live|transcri|whisper|image|imagen|veo|lyria|guard|robotics|"
    r"computer-use|aqa|moderation|rerank|orpheus|vision-exp|native-audio", re.I)


def guess_family(model: str) -> str:
    """Model family for independence routing. Unknown names fall back to their first token."""
    low = model.lower().split("/")[-1]
    for needle, fam in _FAMILIES:
        if needle in low:
            return fam
    return re.split(r"[-_:.@]", low)[0] or low


def guess_tier(model: str) -> str:
    """`strong` for ~60B+ parameters or a vendor's large/pro line; everything else `fast`.

    Only a default for discovered models — the owner can change any model's tier.
    """
    size = re.search(r"(\d+(?:\.\d+)?)b\b", model.lower())
    if size:
        return "strong" if float(size.group(1)) >= 60 else "fast"
    return "strong" if _STRONG_WORDS.search(model) else "fast"


def is_chat_model(model: str) -> bool:
    return not NOT_CHAT.search(model)
