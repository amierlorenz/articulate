# Free and cheap model options (November 2026)

Ticket: amierlorenz/articulate#6. Researched 2026-11-17 against official pricing, docs and terms pages.
Each fact links to its source. Where a fact could not be confirmed on a public primary page, it says so.

## Workload being sized

| Job | Volume (estimate) | Tokens (estimate) |
|---|---|---|
| Pocket transcripts (lectures + personal), tagging, task extraction | 1-3 h/day; speech runs about 150 words/min, so about 12k tokens per hour | 12-36k in/day, a few k out |
| Nightly class intake (slide decks, 3-5 courses) | a few decks/week | 10-20k in per deck |
| Daily plan | 1 call/day | a few k |
| Quiz generation and grading | a few generation calls + 10-30 grading calls/day (fewer if graded in batches) | small |
| Embeddings for matching | tens to low hundreds of chunks/day | small |

Rough total: **20-60 LLM calls/day, about 1.5M input and 0.3M output tokens/month.** That is small; every option
below except the tightest free quotas can carry it. The real constraints are **privacy of personal transcripts**
and **daily request caps**, not cost.

## Summary table

| Option | Free limits | Cost beyond free | May train on / read inputs? |
|---|---|---|---|
| Gemini API free tier | Per-model RPM/RPD shown only in AI Studio (not on the public page) | 3.5 Flash-Lite $0.30/$2.50; 3.8 Flash $0.75/$3.75 (doubles Jan 1, 2027) | **Yes** on free tier (human review, product improvement); No on paid |
| Gemma 4 via Gemini API | Free of charge on free tier | n/a | **Yes** (free tier) |
| GitHub Models | **Retired July 30, 2026** | n/a | n/a |
| Groq | Free plan exists; e.g. gpt-oss-120b / qwen3.8-27b at 30 RPM, 1K RPD, 200K TPD (table labelled Developer plan; account page is authoritative) | Pay per token | Does not retain by default; no training statement; ZDR available |
| OpenRouter `:free` models | 20 RPM; 50 RPD, or 1,000 RPD after buying $10 of credits once | Per model | Depends on provider; account setting can block providers that train |
| Cloudflare Workers AI | 10,000 Neurons/day (about 2k/day needed for this workload on Llama 3.1 8B) | $0.011 per 1k Neurons (Workers Paid) | **No** ("does not use Customer Content ... to train") |
| Local Ollama / MLX (this Mac) | Unlimited; 7-9B comfortable, 12-14B tight | $0 (electricity) | **No**, nothing leaves the Mac |
| Apple Foundation Models (on-device, `fm` CLI) | Unlimited; 8K context on macOS 27 | $0 | **No**, on-device |
| Apple Foundation Model on PCC | Per-user daily limit (number unpublished); 32K context; officially for apps under 2M downloads | $0 to developer | Apple says data is never stored |
| `claude -p` on Claude Pro | Shares the 5-hour session limit and weekly limit with chat and Claude Code; no published numbers | Pro is $20/month (already paid) | Only if the account's training setting is on (5-year retention); off means 30 days |
| Claude API, Haiku 5.5 | none | $0.10 / $0.50 per MTok (Batch: $0.05 / $0.25); about **$0.30/month** for this workload | **No** (Commercial Terms) |

## Gemini API

**Models (current).** The models page lists `gemini-4-carbon`, `gemini-4-argon`, `gemini-4-hydrogen`,
`gemini-3.8-flash` (stable), `gemini-3.6-flash` (stable), `gemini-3.5-flash-lite` (stable), `gemini-3.1-flash-lite`,
`gemini-3-flash-preview`, and embeddings `gemini-embedding-2` (multimodal) and `gemini-embedding-001`
([models](https://aistudio.google.com/docs/models)).
- **`gemini-3.7-flash` is not on the models page any more.** MieracleStudy's bake-off script still falls back to it
  (`run-gemini.py` lists 3.8, 3.7, 3.6). Expect it to fail or be removed; drop it from fallback lists.
- `gemini-2.5-pro` and `gemini-2.5-flash` are deprecated and route to `gemini-3.8-flash` and `gemini-3.5-flash-lite`
  on Dec 1, 2026 ([models](https://aistudio.google.com/docs/models), [pricing](https://aistudio.google.com/docs/pricing)).

**Free tier availability and paid prices** ([pricing](https://aistudio.google.com/docs/pricing), USD per 1M tokens, input/output):

| Model | Free tier | Paid |
|---|---|---|
| gemini-4-carbon / argon | No | $2 / $10 (rising to $4 / $20 on Dec 1, 2026) |
| gemini-4-hydrogen | **Yes** | $1.50 / $7.50 |
| gemini-3.8-flash, gemini-3.6-flash | Yes | $0.75 / $3.75 (rising to $1.50 / $7.50 on Jan 1, 2027) |
| gemini-3.5-flash-lite | Yes | $0.30 / $2.50 |
| gemini-3.1-flash-lite | Yes | $0.25 / $1.50 |
| gemini-2.5-flash-lite | Yes | $0.10 / $0.40 |
| gemini-embedding-2 | Yes | $0.20 (text) |
| gemini-embedding-001 | Yes | $0.15 |
| Gemma 4 | Free of charge on free tier | n/a |

**Rate limits.** The public rate-limits page no longer has a per-model free-tier table. It says limits "can be viewed
in Google AI Studio", are per project, and RPD resets at midnight Pacific. The only free-tier row it prints is for
Gemini Decision 1 (200 RPM / 10,000 RPD) ([rate limits](https://aistudio.google.com/docs/rate-limits)).
- The prior MieracleStudy facts (3.8/3.6 Flash at 5 RPM / 20 RPD each, separate quotas; 3.5 Flash-Lite at 500 RPD)
  **could not be confirmed on a Google page.** One third-party guide repeats the same numbers (20 RPD for 3.8 and
  3.6 Flash, 500 RPD for 3.5 and 3.1 Flash-Lite), which is consistent but not authoritative. Check the AI Studio
  rate-limit page for the project's real numbers. `gemini-4-hydrogen` being on the free tier is new and its
  free RPD is unknown.
- Embeddings: no free-tier limit is printed anywhere public.

**Data use** ([Gemini API terms](https://aistudio.google.com/docs/terms), effective March 23, 2026):
- Unpaid Services: "human reviewers may read, annotate, and process your API input and output", and Google uses
  submitted content to develop products and ML technologies.
- Paid Services: prompts and responses are not used to improve products; logged for a limited time for abuse
  detection only.
- Must be 18+. Users in the EEA, UK and Switzerland get paid-service terms even on free quota.
- The pricing table's "Used to improve our products" row is Yes on every free tier and No on every paid tier.

**Consequence for Articulate:** free Gemini is fine for class slides and public course material, but sending
personal Pocket transcripts to it means Google staff may read them. That contradicts the PLAN.md rule that no
transcript leaves the Mac unless Amier enables that service. A billing-linked (Tier 1) project flips the data terms
to "not used" at a cost of a couple of dollars a month at this volume.

## GitHub Models

Retired: "As of July 30, 2026, GitHub Models has been fully retired." Playground, catalog, inference API and BYOK
are all gone; GitHub points to Azure AI Foundry and Copilot instead
([GitHub docs](https://docs.github.com/en/github-models/use-github-models/prototyping-with-ai-models)). Not an option.

## Groq

- The rate-limits page has Free and Developer tabs but renders one table and says it shows Developer base limits.
  Examples: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b` at 30 RPM, 1K RPD, 8K TPM, 200K TPD;
  `whisper-large-v3(-turbo)` at 20 RPM, 2K RPD, 7.2K audio-seconds/hour. The account's `/settings/limits` page is
  authoritative ([Groq rate limits](https://console.groq.com/docs/rate-limits)). Free-plan numbers may be lower.
- 200K tokens/day is enough for this workload (about 50k/day).
- Data: "By default, Groq does not retain customer data for inference requests"; may log up to 30 days for
  reliability or abuse; ZDR can be enabled by all customers. No statement about training
  ([Groq your data](https://console.groq.com/docs/your-data)).

## OpenRouter free models

- `:free` variants: 20 requests/minute; 50 requests/day if under $10 of credits ever purchased, 1,000/day at $10+
  ([limits](https://openrouter.ai/docs/api/reference/limits)).
- Free text models listed by the public models API on 2026-11-17 include `google/gemma-4-31b-it:free`,
  `google/gemma-4-26b-a4b-it:free`, `nvidia/nemotron-3-ultra-550b-a55b:free`, `nvidia/nemotron-3.5-super-vl:free`,
  `nvidia/nemotron-3.5-lightning:free`, `inclusionai/ling-3.2-flash:free`, `baidu/ernie-5.5-flash-preview:free`
  and a few others (14 total) (`GET https://openrouter.ai/api/v1/models`). The list churns; free variants come and go.
- Data: each provider has its own policy; an account setting stops routing to providers that may train, with
  "separate settings for paid and free models"
  ([provider logging](https://openrouter.ai/docs/guides/privacy/provider-logging)). Many free endpoints exist
  because the provider logs or trains on prompts, so turning that off may leave few or no free models. Not
  confirmed which ones.

## Cloudflare Workers AI

- 10,000 Neurons/day free, on the Workers Free plan too; resets 00:00 UTC; beyond that $0.011 per 1,000 Neurons on
  Workers Paid, and Free-plan requests past the limit fail
  ([pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/)).
- Examples per 1M tokens: Llama 3.1 8B 25,608 in / 75,147 out Neurons; Llama 3.3 70B fp8-fast 26,668 / 204,805;
  bge-small embeddings 1,841; bge-base 6,058.
- Sizing: this workload on Llama 3.1 8B is about 60k Neurons/month (about 2k/day), well inside the free 10k/day.
  On Llama 3.3 70B it is about 100k/month (about 3.3k/day), still free.
- Data: "Cloudflare does not use Customer Content processed by Workers AI to train AI models"
  ([data usage](https://developers.cloudflare.com/workers-ai/platform/data-usage/)).
- Model quality: the per-model catalog was not reviewed here. The listed examples are older open models.

## Local models on this Mac

Hardware (from `system_profiler` / `sysctl`): MacBook Pro Mac14,10, **Apple M2 Pro (8P+4E), 16 GB unified memory**,
macOS 27.0.1. Ollama 0.40.16 is installed with `qwen2.5-coder:7b` (4.7 GB) and `qwen2.5-coder:14b` (9.0 GB).
`iogpu.wired_limit_mb` is 0 (default). By default macOS lets the GPU use only part of RAM, roughly two-thirds on a
16 GB machine (about 10-11 GB). That figure is widely reported but not documented by Apple.

What fits (Ollama library sizes, [ollama.com/library](https://ollama.com/library); memory at Q4 is approximate):

| Size | Examples | Fit on 16 GB |
|---|---|---|
| 3-4B | qwen3:4b, qwen3.5:4b, gemma4:e4b, phi4-mini | Easy; fast; good for tagging/classification |
| 7-9B | qwen3:8b, qwen3.5:9b, llama3.1:8b, granite4.2:8b | Comfortable (about 5-6 GB); a good default for extraction and summaries |
| 12-14B | gemma4:12b, qwen3:14b, ministral-3:14b | Tight (about 8-10 GB); works if little else is open; best local quality |
| 20B MoE | gpt-oss:20b | Marginal to no: about 13-14 GB, past the default GPU limit |
| Embeddings | embeddinggemma (300M), nomic-embed-text, qwen3-embedding:0.6b, bge-m3 | Trivial |

Throughput is a few hours of transcript per night at 8B on an M2 Pro, which fits a nightly batch. MLX (`mlx-lm`)
runs the same model families and is often somewhat faster on Apple silicon. It is not installed. Speed was not
benchmarked here.

## Apple Foundation Models

- **On-device model:** free, no API key, data stays on device. Context is 4,096 tokens on macOS 26 and 8,192 on
  macOS 27 (newer devices) ([WWDC26 session 319](https://developer.apple.com/videos/play/wwdc2026/319/)).
  Supports guided generation and tool calling; macOS 27 adds image input and Vision tools
  ([macOS 27 guide](https://developer.apple.com/wwdc26/guides/macos/)).
- **`fm` CLI and Python SDK:** `fm` ships with macOS 27 (`respond`, `chat`, `schema`) and is pitched for "automation";
  `pip install apple_fm_sdk` needs Python 3.10+, Xcode and Apple silicon
  ([WWDC26 session 334](https://developer.apple.com/videos/play/wwdc2026/334/)). `/usr/bin/fm` exists on this Mac,
  but it refuses to run until someone accepts its license with `sudo fm license`. That was **not** accepted here.
- **Private Cloud Compute model:** much bigger, 32K context, "no token costs to you, the developer"; each user has a
  daily limit counted against their iCloud account (higher with iCloud+); officially "available for apps with less
  than 2M downloads" by application. Apple says user data "is never stored"
  ([session 319](https://developer.apple.com/videos/play/wwdc2026/319/)). Session 334 shows `fm respond ... --model pcc`
  from the terminal, so scripts may be able to use it. Limits and eligibility for non-app use are not published.
- Fit: 8K context is too small for a full lecture transcript (about 12k tokens/hour) without chunking. Good for
  tagging chunks, short task extraction, and quiz grading. PCC's 32K covers a lecture hour.

## Claude: `claude -p` on Pro, and the API

**Pro plan.** $20/month in the US. It has "a five-hour session limit and a weekly limit" shared across Claude and
Claude Code; no hour or token numbers are published, and `/status` shows what is left
([Claude Code with Pro/Max](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan),
[Pro plan](https://support.claude.com/en/articles/8325606-what-is-the-pro-plan)). Older "40-80 hours of Sonnet per
week" figures date from July 2025 press and are not current official numbers.

**Using `claude -p` for personal automation.**
- `claude -p` is the Agent SDK CLI. Without `--bare` it uses the normal login (the subscription). **`--bare` never
  reads OAuth and needs `ANTHROPIC_API_KEY`**, and the docs say `--bare` "will become the default for `-p` in a
  future release" ([headless](https://code.claude.com/docs/en/headless)). A nightly job that relies on the
  subscription could start failing or need a flag when that default changes.
- Terms: Pro is under the Consumer Terms. "Advertised usage limits for Pro, Max, and Ultra plans assume ordinary,
  individual usage of Claude Code and the Agent SDK." OAuth is "designed to support ordinary use of Claude Code";
  developers building products should use API keys; third parties may not route requests through subscription
  credentials for their users ([legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)).
  Amier running the unmodified `claude` binary on his own account for his own pipeline is personal, individual use
  and not a third-party product. The docs do not say outright that scheduled unattended runs are in or out.
- Every nightly run uses the same 5-hour and weekly allowance as interactive coding.
- Training: consumer accounts are used for training only if the "help improve Claude" setting is on (then 5-year
  retention); otherwise 30-day retention. This covers Claude Code on those accounts
  ([consumer terms update](https://www.anthropic.com/news/updates-to-our-consumer-terms)). Check the setting is off
  before sending personal transcripts.

**Claude API** ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)): Haiku 5.5 $0.10 in / $0.50 out
per MTok for prompts up to 100K tokens ($0.50 / $2.50 above); Sonnet 5.5 $2 / $10; Opus 5.5 $4 / $20; Batch API
halves all prices. Commercial Terms: "Anthropic may not train models on Customer Content from Services"
([commercial terms](https://www.anthropic.com/legal/commercial-terms)). At this workload's volume: Haiku 5.5 about
$0.30/month ($0.15 batched), Sonnet 5.5 about $6/month. No free credits or student offer was found on the pricing page.

## Student offers

- **Google AI Pro, free for 1 year (US college students):** redeem by **December 31, 2026**; SheerID verification and a
  payment method required; auto-renews at $19.99/month; includes 4x Gemini app limits, Gemini in Gmail/Docs, 5 TB
  ([Google blog, Aug 19, 2026](https://blog.google/innovation-and-ai/products/gemini-app/student-offer-google-ai/)).
  It is a consumer app plan, **not Gemini API quota**. Secondary sources say AI Pro also carries Gemini CLI / Code
  Assist limits and a monthly Google Cloud credit via the Developer Program (about $10/month, manually activated).
  A Cloud credit on a billing-linked project would make Gemini API calls paid tier (no training) at no cost. This
  was **not confirmed on a Google page**.
- **GitHub Student Developer Pack:** GitHub Copilot Student (unlimited completions, an AI Credits allowance, limited
  chat/agent), Azure for Students ($100 credit), VS Dev Essentials (Azure, $200 first month), free GitHub Pro
  ([pack](https://education.github.com/pack)). Copilot is not a general inference API for a pipeline. Azure credit
  could pay for Azure AI Foundry models.
- **Anthropic:** no student API credit was found.

## Recommendation sketch (for the decision, not a decision)

1. **Personal transcripts → local first.** Ollama with an 8B model (qwen3:8b or similar) plus `embeddinggemma` or
   `nomic-embed-text` costs $0 and nothing leaves the Mac. Apple's on-device model is a second free local option for
   short tasks.
2. **Class material (slides, lecture-only transcripts) → Gemini free tier** is acceptable if Amier is fine with
   Google possibly reading course content. Use 3.5 Flash-Lite for volume; keep 3.8/3.6 Flash for harder calls;
   confirm real RPD in AI Studio.
3. **Quality step-up without privacy cost:** the Claude API on Haiku 5.5 (about $0.30/month, no training) or a
   billing-linked Gemini project (a few $/month, no training) beats free tiers on terms. Workers AI free is a $0
   no-training cloud fallback with older models.
4. **`claude -p` on Pro** is fine for occasional heavy jobs (weekly synthesis) but shares Amier's coding allowance and
   depends on non-bare mode. Not a good primary nightly engine.

## Open questions for Amier

1. Is it OK for **personal** Pocket transcripts to go to any free tier that allows human review or training (Gemini
   free, some OpenRouter providers)? Or should that be local / no-training only?
2. Will you pay about $0.30-$6/month (Claude API Haiku/Sonnet) or a few $/month (Gemini paid) for better quality and
   no-training terms, or stay strictly $0?
3. Can you check the AI Studio rate-limit page for the project's real free RPD per model (3.8/3.6 Flash,
   3.5 Flash-Lite, 4 Hydrogen, embedding-2)? Google no longer publishes them.
4. Do you want to claim the Google AI Pro student offer before Dec 31, 2026, and check whether its Cloud credit can
   fund a paid-tier Gemini API project?
5. Should we accept the `fm` license (`sudo fm license`) and test the on-device and `--model pcc` models from the
   terminal?
6. Is your Claude consumer "help improve Claude" setting off?
