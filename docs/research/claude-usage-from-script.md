# Reading Claude Pro session and weekly usage from a headless script

Date: 2026-11-18. Scope: a launchd job (Python 3.12, 02:00, nobody at the keyboard) that must decide whether to call `claude -p` on a Pro subscription login. The rule: skip if weekly ≥ 50 % or session ≥ 70 %, and skip if usage can't be read.

## Answer

- **Use this:** run `claude -p "/usage" --output-format stream-json --verbose` (no `--bare`). Read `usage_report.rate_limits.limits` from the `type: "assistant"` line (a top-level key, not inside `message`). Take the row with `kind == "session"` as session % and `kind == "weekly_all"` as weekly %. Each row has `percent` (0–100) and `resets_at` (ISO 8601). `/usage` is a local command: it cost $0 and 0 tokens on this Mac. ([Agent SDK TS reference, SDKAssistantMessage / SDKUsageReport](https://code.claude.com/docs/en/agent-sdk/typescript))
- **Fail closed** if the exit code isn't 0, if there is no `usage_report`, if `rate_limits` or `limits` is `null` or empty, or if either row is missing. On a timeout, kill the process and fail closed too.
- **Fallback:** retry once a few minutes later, because the usage endpoint behind `/usage` can be rate limited ([costs: "When the usage request fails"](https://code.claude.com/docs/en/costs)). If it still fails, skip the night. Use a status-line snapshot only as an extra check, never as the only gate (see Source 2).
- **Run-time brake:** during the real `claude -p` run, watch `rate_limit_event` messages in the stream. Stop early if the status becomes `allowed_warning` or `rejected`.
- **Don't** call the undocumented `api.anthropic.com/api/oauth/usage` endpoint. It needs the OAuth token from the Keychain.

### Checked on this Mac (2026-11-18, Claude Code 2.1.327, Pro login)

`claude -p "/usage" --output-format stream-json --verbose` exited 0 in about 3.7 s. The relevant lines:

- **Assistant line:** `usage_report` sits at the top level of the line, next to `message`, not inside it. `local_command_run` is `{"command": "usage"}`. `usage_report.rate_limits.limits` had exactly two rows on Pro:
  - `{"kind": "session", "group": "session", "percent": 81, "resets_at": "2026-11-18T23:40:00…+00:00", "scope": null, "severity": "warning", "is_active": true}`
  - `{"kind": "weekly_all", "group": "weekly", "percent": 12, "resets_at": "2026-11-25T10:00:00…+00:00", "scope": null, "severity": "normal", "is_active": false}`
- **`rate_limits.extra_usage`:** `is_enabled: false`.
- **Result line:** `total_cost_usd: 0`, `num_turns: 0`, and every `usage` token count 0. So the check spends no usage.
- **Login expiry:** the `result` line also carries `credential_expires_at` (an ISO timestamp, about 3 weeks out). Articulate can warn before the login expires.
- **Text output:** the human-readable text (`Current session: 81% used · resets …`) is in `result` too, but parse `usage_report`, not the text.

That settles open questions 1 and 6. The rest of this note was written from docs, because the research session couldn't run shell commands.

---

## Source 1: Claude Code commands (`/usage`, `/status`, `/cost`) and `claude -p` output

### 1a. `/usage` sent as a `-p` prompt

- **What it gives:** session % and weekly %, each with a reset time, and sometimes extra rows (per-model weekly, usage credits). The report arrives as an assistant message: `message.content` holds the text, and `usage_report` holds the structured copy. ([TS reference: SDKAssistantMessage](https://code.claude.com/docs/en/agent-sdk/typescript))
- **Shape:** `usage_report.rate_limits.limits[]`, where each row has these fields:
  - `kind`, such as `session`, `weekly_all` or `weekly_scoped`. "Classify a row on this, never on a label."
  - `group`
  - `percent` (0–100)
  - `resets_at` (ISO 8601 or `null`)
  - `scope`, `severity` (`normal` / `warning` / `critical`) and `is_active`

  Source: [TS reference: SDKUsageReport](https://code.claude.com/docs/en/agent-sdk/typescript).
- **Conditions for `usage_report`:** the session authenticates with a claude.ai credential, the credential shows a known plan type or carries the `user:profile` scope, and the account isn't on usage-based billing. A `claude setup-token` token passed as `CLAUDE_CODE_OAUTH_TOKEN` "doesn't qualify by default" because it has only `user:inference`. ([TS reference](https://code.claude.com/docs/en/agent-sdk/typescript)) So the job should use the normal `/login` credential, not a setup-token.
- **How to call:** use `stream-json --verbose`, because the plain `json` format returns only the final result object ([headless docs](https://code.claude.com/docs/en/headless)). Don't pass `--bare`, because "In bare mode, Claude Code never reads OAuth credentials or the system keychain" ([headless docs](https://code.claude.com/docs/en/headless)). Consider `--no-session-persistence` so each check doesn't leave a transcript ([.claude directory docs](https://code.claude.com/docs/en/claude-directory)).
- **Stability:** this is documented, but marked "The type is experimental: its shape may change." It needs Agent SDK v0.3.273 or later. `severity` and `is_active` are required only from v0.3.277. ([TS reference](https://code.claude.com/docs/en/agent-sdk/typescript))
- **Failure behaviour, as documented:**
  - `rate_limits` is `null` "when Claude Code couldn't get the plan's usage, for example when the session's OAuth token lacks the `user:profile` scope".
  - `limits: []` means the server reported no meters, and `limits: null` means "no rows to report".
  - API-key sessions and older versions send the text without `usage_report`.

  Source: [TS reference](https://code.claude.com/docs/en/agent-sdk/typescript).
  - Process failures: `claude -p` exits 0 on success and non-zero on failure. Run-time failures such as missing auth are printed "as the result on stdout" ([headless docs](https://code.claude.com/docs/en/headless)).
- **Stale data risk:** when the usage request fails, interactive `/usage` "shows the last usage bars it loaded on this machine within the past 60 minutes" with a `Showing last-known usage` note. Without such a snapshot, it reports that the usage endpoint is rate limited ([costs docs](https://code.claude.com/docs/en/costs)). The docs don't say whether `usage_report` marks the data as last-known. That is an open question.
- **Cost:** `/usage` is a local command. "When you send a command such as `/context` or `/usage` as a prompt, its output arrives as an SDKAssistantMessage" ([TS reference: SDKLocalCommandOutputMessage](https://code.claude.com/docs/en/agent-sdk/typescript)). The docs don't say outright that no tokens are spent. To check, confirm the final `result` message shows `total_cost_usd: 0` and zero tokens in `usage`.

### 1b. `/status`, `/cost`, `/stats`

- `/cost` and `/stats` are aliases of `/usage` ([commands reference](https://code.claude.com/docs/en/commands)).
- `/status` opens the Settings Status tab, which shows version, model, account and connectivity. It doesn't list usage percentages ([commands reference](https://code.claude.com/docs/en/commands)).

### 1c. `rate_limit_event` in `stream-json`

- **What it gives:**
  - TS type: `{type: "rate_limit_event", rate_limit_info: {status: "allowed" | "allowed_warning" | "rejected", resetsAt?, utilization?, ...}}` ([TS reference: SDKRateLimitEvent](https://code.claude.com/docs/en/agent-sdk/typescript)).
  - The Python SDK models it with `rate_limit_type` (`five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet`, `overage`). In Python, `utilization` is a "Fraction of the rate limit consumed (0.0 to 1.0)" and `raw` holds the full dict ([Python reference: RateLimitEvent / RateLimitInfo](https://code.claude.com/docs/en/agent-sdk/python)).
- **Limits:**
  - It describes one window per event.
  - It is "Emitted when rate limit status changes", so it isn't a full snapshot of both windows ([Python reference](https://code.claude.com/docs/en/agent-sdk/python)).
  - `utilization` is optional.
  - It arrives only after a model request, so it can't be a pre-flight check.
- **Use:** a mid-run brake. Stop when `status` is `allowed_warning` or `rejected`.
- **Stability:** documented, and part of the SDK message union.

### 1d. The final `result` object (`--output-format json`)

- It has `total_cost_usd`, `usage` and `modelUsage`. These are client-side estimates. There are no plan-limit percentages ([TS reference: SDKResultMessage](https://code.claude.com/docs/en/agent-sdk/typescript), [headless docs](https://code.claude.com/docs/en/headless)).
- A rate-limit failure shows as an `api_retry` event with `error: "rate_limit"` and as a non-zero exit ([headless docs](https://code.claude.com/docs/en/headless)).

## Source 2: status-line input JSON

- **What it gives:**
  - `rate_limits.five_hour.used_percentage` and `rate_limits.seven_day.used_percentage` (0–100).
  - `rate_limits.*.resets_at` (Unix epoch seconds).

  Source: [status line docs, Available data](https://code.claude.com/docs/en/statusline).
- **When it is present:** "only for claude.ai Pro, Max, and Ultra subscribers … only after the first API response in the session." Each window "may be independently absent", and Claude Code drops a window once its `resets_at` passes ([status line docs](https://code.claude.com/docs/en/statusline)).
- **Version:** v2.1.80 added it, according to a release aggregator ([newreleases.io v2.1.80](https://newreleases.io/project/github/anthropics/claude-code/release/v2.1.80)). This is a community pointer. The official changelog line wasn't located.
- **Headless access:** none, really. The status line is a bar in the interactive UI. The script runs when a session starts and on UI events ([status line docs](https://code.claude.com/docs/en/statusline)). A 02:00 job has no interactive session. The only route is a status-line script that writes a snapshot file while the user works, which the job reads later.
- **Why the snapshot is weak:**
  - It is only as fresh as the last interactive session.
  - It misses usage from claude.ai web or mobile and from other machines.
  - Users report regressions where the field went missing (for example v2.1.138 and v2.1.156) and drift between open terminals. These are community issue reports, not verified: [#60612](https://claudeissues.com/issue/60612-statusline-hook-payload-no-longer-includes-rate-limits-field-v2-1-138), [#63659](https://claudeissues.com/issue/63659-bug-rate-limits-absent-from-statusline-json-for-claude-max-plan-v2-1-156-windows), [#75408](https://claudeissues.com/issue/75408-statusline-rate-limits-five-hour-used-percentage-drifts-across-concurrently-open).
  - On the plus side, a stored `resets_at` that has passed does tell you that window has reset.
- **Cost:** "The status line runs locally and does not consume API tokens" ([status line docs](https://code.claude.com/docs/en/statusline)).
- **Failure:** the field is simply missing. A snapshot file can also be old.

## Source 3: hook inputs

- The common hook input fields (`session_id`, `transcript_path`, `cwd`, `permission_mode`, `effort`, `hook_event_name` and so on) carry no usage data ([hooks reference](https://code.claude.com/docs/en/hooks)).
- `StopFailure` can match on error type `rate_limit`, but that only names the error. No percentages were found ([hooks reference](https://code.claude.com/docs/en/hooks)).
- `Notification` input is `message`, `title` and `notification_type`. The quota types `quota_auto_resume_fired`, `_stale` and `_disabled` (v2.1.234+) report auto-resume events, not percentages ([hooks reference](https://code.claude.com/docs/en/hooks)).
- **Verdict:** hooks give no session or weekly %.

## Source 4: local files under `~/.claude`

Listing `~/.claude` wasn't possible, because Bash was blocked. This section uses the documented layout instead.

- **`stats-cache.json`:** "Aggregated token and cost counts shown by `/usage`." These are local history totals, not plan-limit percentages ([.claude directory docs](https://code.claude.com/docs/en/claude-directory)). The file wasn't present at `~/.claude/stats-cache.json` (local: Read tool, file does not exist).
- **`usage-data/`:** `/insights` reports only.
- **`statsig/`:** a legacy folder that is no longer written ([.claude directory docs](https://code.claude.com/docs/en/claude-directory)).
- **`projects/*.jsonl` transcripts:** they hold messages, tool calls and request parameters. No documented limit-percentage field ([.claude directory docs](https://code.claude.com/docs/en/claude-directory)).
- **The 60-minute last-known `/usage` snapshot** ([costs docs](https://code.claude.com/docs/en/costs)): it must be stored somewhere, but its location and format are undocumented.
- **Verdict:** no documented local file holds current plan-limit %. Don't build on undocumented caches.

## Source 5: the OAuth usage endpoint (`api.anthropic.com/api/oauth/usage`)

- **What it is:** community tools describe `GET https://api.anthropic.com/api/oauth/usage` with `Authorization: Bearer <OAuth access token>` and `anthropic-beta: oauth-2025-04-20` ([cship source, community](https://docs.rs/crate/cship/1.7.0/source/src/usage_limits.rs)).
- **Response:** it returns `five_hour` and `seven_day` objects, each with `utilization` (0–100) and `resets_at`. It also returns `seven_day_opus`, `seven_day_sonnet`, other per-surface windows and `extra_usage` ([cship source](https://docs.rs/crate/cship/1.7.0/source/src/usage_limits.rs)). This looks like the server data behind `/usage`. The Claude Code docs mention only "the usage endpoint" and that it gets rate limited ([costs docs](https://code.claude.com/docs/en/costs)).
- **Credential:** the claude.ai OAuth access token. Community tools read it from the macOS Keychain or `~/.claude/.credentials.json` ([search summary of community tools](https://docs.rs/crate/claude-usage/0.2.3/source/README.md)). The job would have to read and refresh a secret that Claude Code owns.
- **Stability:** undocumented by Anthropic. The beta header and schema can change without notice.
- **Failure:** plain HTTP status codes, such as 401 for an expired token and 429 when rate limited. Some community clients turn a `null` window into 0 %, which is unsafe here ([cship source](https://docs.rs/crate/cship/1.7.0/source/src/usage_limits.rs)).
- **Cost:** no model call. Not tested: per the brief, the endpoint wasn't called.
- **Verdict:** avoid it. It needs secret handling and it is undocumented. Source 1a gives the same data through a documented path.

## Source 6: official docs on Pro limits, and Admin or Analytics APIs

- **Limits:** subscription usage is a rolling five-hour window plus a weekly window. The docs describe this for Team and Enterprise seats. The same "session limit" and "weekly limit" messages apply to subscription plans ([costs docs](https://code.claude.com/docs/en/costs)).
- **`claude -p` counts against the plan:**
  - The October 7, 2026 update says: "Nothing changes for your plan: when you're signed in with your Claude plan, the Claude Agent SDK and `claude -p` still draw from your subscription limits." The monthly API credits went to Max, Ultra and Team, not Pro.
  - The earlier change announced for June 15 was paused.

  Source: [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan).
- **Admin and Analytics APIs:**
  - The Enterprise Analytics API is Enterprise only.
  - The Claude Code Analytics API needs a Console Admin API key and covers API organizations.
  - "On an individual Pro, Max, or Ultra plan you have no organization to manage" ([costs docs](https://code.claude.com/docs/en/costs)).
  - **Verdict:** none of these covers a Pro subscription.
- **No programmatic access for Pro:** the help article on Claude Code limits gives no programmatic way to read usage ([support 14552983](https://support.claude.com/en/articles/14552983-models-usage-and-limits-in-claude-code)). The Pro usage article URL returned 404 ([support 8324991](https://support.claude.com/en/articles/8324991-about-claude-s-pro-plan-usage)).

## Comparison

| Source | Session % | Weekly % | Reset times | Headless at 02:00 | Documented | Costs usage | Failure looks like |
|---|---|---|---|---|---|---|---|
| 1a `claude -p "/usage"` + `usage_report` | yes (`kind: session`) | yes (`kind: weekly_all`) | yes, ISO 8601 | yes | yes, "experimental" | no model call expected (verify) | missing `usage_report`, `rate_limits: null`, empty or null `limits`, non-zero exit. Maybe silently last-known data (unknown) |
| 1c `rate_limit_event` | one window per event | one window per event | `resetsAt` | only during a real run | yes | it rides on a real call | event absent, `utilization` absent |
| 1d `result` object | no | no | no | yes | yes | — | — |
| 2 Status-line JSON | yes | yes | yes, epoch | no (snapshot only) | yes | no | field absent, old snapshot |
| 3 Hooks | no | no | no | — | yes | — | — |
| 4 Local files | no | no | no | — | partly | — | — |
| 5 `/api/oauth/usage` | yes | yes | yes | yes | no | no | HTTP 401/429, null windows |
| 6 Admin/Analytics API | no (not for Pro) | no | — | — | yes | — | — |

## Open questions (check once on this Mac)

1. ~~Run `/usage` once and confirm `usage_report`, the row kinds and zero cost.~~ Done: present, `session` + `weekly_all`, $0 and 0 tokens (see "Checked on this Mac").
2. When the usage endpoint is rate limited, does `usage_report` carry last-known data without a flag? If yes, compare `resets_at` with now, or treat a repeated identical value with suspicion.
3. Does a launchd **LaunchAgent**, running in the user's login session, reach the Keychain credential `claude` needs? A LaunchDaemon likely can't. This is general macOS behaviour, not from Anthropic docs. Test with `claude auth status` from the agent.
4. What is the exit code and stdout when the login has expired? The docs say the failure is printed as the result on stdout ([headless docs](https://code.claude.com/docs/en/headless)).
5. Does `rate_limit_event` arrive on every first API response, or only on a status change? The TS and Python docs word it differently.
6. ~~Check the installed version.~~ Done: 2.1.327 emits `usage_report`. Log `claude_code_version` from the `init` line each night, so a later shape change is easy to trace.

## Sources

- Claude Code docs:
  - Status line: https://code.claude.com/docs/en/statusline
  - Run Claude Code programmatically (headless): https://code.claude.com/docs/en/headless
  - Manage costs (`/usage`, failure fallback, org analytics): https://code.claude.com/docs/en/costs
  - Commands reference: https://code.claude.com/docs/en/commands
  - Hooks reference: https://code.claude.com/docs/en/hooks
  - .claude directory: https://code.claude.com/docs/en/claude-directory
- Agent SDK references:
  - TypeScript (SDKUsageReport, SDKRateLimitEvent, SDKResultMessage, SDKLocalCommandOutputMessage): https://code.claude.com/docs/en/agent-sdk/typescript
  - Python (RateLimitEvent, RateLimitInfo): https://code.claude.com/docs/en/agent-sdk/python
- Claude Code CHANGELOG: https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md (newest 2.1.327)
- Support articles:
  - Use the Claude Agent SDK with your Claude plan: https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan
  - Models, usage and limits in Claude Code: https://support.claude.com/en/articles/14552983-models-usage-and-limits-in-claude-code
  - About Claude's Pro plan usage (404 on fetch): https://support.claude.com/en/articles/8324991-about-claude-s-pro-plan-usage
- Community pointers, not authoritative:
  - cship `usage_limits.rs`: https://docs.rs/crate/cship/1.7.0/source/src/usage_limits.rs
  - claude-usage crate: https://docs.rs/crate/claude-usage/0.2.3/source/README.md
  - v2.1.80 release aggregator: https://newreleases.io/project/github/anthropics/claude-code/release/v2.1.80
  - Status-line issue mirrors: https://claudeissues.com/issue/60612-statusline-hook-payload-no-longer-includes-rate-limits-field-v2-1-138, https://claudeissues.com/issue/63659-bug-rate-limits-absent-from-statusline-json-for-claude-max-plan-v2-1-156-windows, https://claudeissues.com/issue/75408-statusline-rate-limits-five-hour-used-percentage-drifts-across-concurrently-open
- Local:
  - `~/.claude/cache/changelog.md` (top entry 2.1.327)
  - `~/.claude/stats-cache.json` (not present)
  - `claude --version` and `claude -p "/usage" --output-format stream-json --verbose` on 2.1.327 (2026-11-18, run from the main session)
