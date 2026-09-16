"""Provider presets — what "add a model by adding its key" fills in for you.

Free tiers change without notice (in July 2026 alone Cerebras became a card-required trial and
GitHub Models was retired), so every number here says where it came from and is only a prior:
the limiter overrides it with the provider's own rate-limit headers, and
`cadre provider models <id>` asks the endpoint what it serves today.

Checked 2026-09-16. `source` values:
  "docs"      — the provider's own rate-limit page on that date
  "reported"  — independent write-ups from Sept 2026; the provider publishes no table
  "guess"     — nothing published; deliberately conservative
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CHECKED = "2026-09-16"


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


_GROQ_LIMITS = dict(rpm=30, rpd=1000, tpm=8000, tpd=200_000)

PRESETS: dict[str, Preset] = {p.id: p for p in [
    Preset(
        "groq", "Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", True,
        signup="https://console.groq.com/keys", source="docs",
        note="Fastest free tier. 8,000 tokens/minute is the ceiling a team hits first.",
        models=(
            ModelPreset("openai/gpt-oss-120b", "strong", "gpt-oss", **_GROQ_LIMITS),
            ModelPreset("openai/gpt-oss-20b", "fast", "gpt-oss", **_GROQ_LIMITS),
        ),
    ),
    Preset(
        "gemini", "Google Gemini (AI Studio)",
        "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", True,
        signup="https://aistudio.google.com/apikey", source="guess",
        note="Limits are per project and shown only in AI Studio; these defaults are conservative. "
             "Free-tier prompts may be used to improve Google's products.",
        models=(ModelPreset("gemini-2.5-flash", "strong", "gemini", rpm=10, rpd=250, tpm=250_000),),
    ),
    Preset(
        "openrouter", "OpenRouter (free models)", "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY", True, signup="https://openrouter.ai/keys", source="docs",
        note="20 requests/minute; 50/day, or 1,000/day once $10 of credit has ever been bought. "
             "The free catalogue changes often, so models are discovered, not listed.",
        default_limits=ModelPreset("", rpm=20, rpd=50),
        discover_filter=r":free$",
    ),
    Preset(
        "mistral", "Mistral (Experiment plan)", "https://api.mistral.ai/v1", "MISTRAL_API_KEY", True,
        signup="https://console.mistral.ai/api-keys", source="reported",
        note="Broad model access; the free plan requires opting in to training on your data.",
        models=(
            ModelPreset("mistral-medium-latest", "strong", "mistral", rpm=2, tpm=500_000),
            ModelPreset("mistral-small-latest", "fast", "mistral", rpm=2, tpm=500_000),
        ),
    ),
    Preset(
        "cohere", "Cohere (trial key)", "https://api.cohere.ai/compatibility/v1", "CO_API_KEY", True,
        signup="https://dashboard.cohere.com/api-keys", source="reported",
        note="1,000 calls a month in total and non-commercial only — use as a last fallback.",
        models=(ModelPreset("command-a-03-2025", "strong", "cohere", rpm=20, rpd=33),),
    ),
    Preset(
        "nvidia", "NVIDIA API Catalog", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", True,
        signup="https://build.nvidia.com", source="reported",
        models=(ModelPreset("meta/llama-3.3-70b-instruct", "strong", "llama", rpm=40),),
    ),
    Preset(
        "cloudflare", "Cloudflare Workers AI",
        "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1", "CLOUDFLARE_API_TOKEN",
        True, signup="https://dash.cloudflare.com/profile/api-tokens", source="reported",
        note="10,000 neurons a day, roughly 50k–290k tokens depending on the model.",
        params=("account_id",),
        models=(
            ModelPreset("@cf/meta/llama-3.3-70b-instruct-fp8-fast", "strong", "llama",
                        rpm=300, tpd=50_000),
            ModelPreset("@cf/openai/gpt-oss-120b", "strong", "gpt-oss", rpm=300, tpd=50_000),
        ),
    ),
    Preset(
        "zai", "Z.ai (GLM flash models)", "https://api.z.ai/api/paas/v4", "ZAI_API_KEY", True,
        signup="https://z.ai/manage-apikey/apikey-list", source="guess",
        models=(ModelPreset("glm-4.5-flash", "fast", "glm", rpm=10),),
    ),
    Preset(
        "huggingface", "Hugging Face Inference Providers", "https://router.huggingface.co/v1",
        "HF_TOKEN", True, signup="https://huggingface.co/settings/tokens", source="reported",
        note="About $0.10 of credit a month — enough for a handful of calls.",
        models=(ModelPreset("openai/gpt-oss-120b", "strong", "gpt-oss", rpd=20),),
    ),
    # --- local: no key, nothing leaves the machine. On an 8 GB single-channel laptop a 1.5B
    # model manages ~3.4 tok/s, so these are fallbacks, not a team's main engine.
    Preset("ollama", "Ollama (local)", "http://127.0.0.1:11434/v1", None, True, local=True,
           source="docs", models=(ModelPreset("qwen2.5:1.5b", "fast", "qwen"),)),
    Preset("llamacpp", "llama.cpp server (local)", "http://127.0.0.1:8080/v1", None, True,
           local=True, source="docs"),
    Preset("lmstudio", "LM Studio (local)", "http://127.0.0.1:1234/v1", None, True, local=True,
           source="docs"),
    # --- paid, for organisations that have keys; same adapter
    Preset("openai", "OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY", False,
           signup="https://platform.openai.com/api-keys",
           models=(ModelPreset("gpt-5-mini", "strong", "openai"),)),
    Preset("anthropic", "Anthropic (OpenAI-compatible endpoint)", "https://api.anthropic.com/v1",
           "ANTHROPIC_API_KEY", False, signup="https://console.anthropic.com/settings/keys",
           models=(ModelPreset("claude-haiku-4-5", "strong", "claude"),)),
    Preset("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", False,
           signup="https://platform.deepseek.com/api_keys",
           models=(ModelPreset("deepseek-chat", "strong", "deepseek"),)),
    Preset("custom", "Any OpenAI-compatible endpoint", "", None, False),
]}

_FAMILIES = [
    ("gpt-oss", "gpt-oss"), ("gemini", "gemini"), ("gemma", "gemma"), ("llama", "llama"),
    ("qwen", "qwen"), ("qwq", "qwen"), ("mixtral", "mistral"), ("mistral", "mistral"),
    ("magistral", "mistral"), ("devstral", "mistral"), ("codestral", "mistral"),
    ("deepseek", "deepseek"), ("glm", "glm"), ("command", "cohere"), ("claude", "claude"),
    ("kimi", "kimi"), ("nemotron", "nemotron"), ("phi", "phi"), ("grok", "grok"),
    ("gpt", "openai"), ("o3", "openai"), ("o4", "openai"),
]

_STRONG_WORDS = re.compile(r"large|medium|pro\b|plus|maverick|reasoning|command-a", re.I)


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
