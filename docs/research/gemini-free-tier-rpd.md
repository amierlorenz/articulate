# How Gemini free-tier daily limits are counted

Research for the ticket "How Gemini free-tier daily limits are counted". Sources read on 2026-11-19.

Google's doc pages show no "last updated" date, so every quote below is as of that read date.
The Gemini API docs moved from `ai.google.dev/gemini-api/docs/*` to `aistudio.google.com/docs/*`
(the old URLs answer with a 301 redirect).

How much to trust each source:
- **Official**: Google's own docs.
- **Google staff**: a forum post by an account the forum marks as staff, or whose title reads "Google".
- **Community**: anyone else. Treat it as unconfirmed.

## TL;DR for Articulate

- RPD resets at **midnight Pacific time**. It is not a rolling 24-hour window.
- The count is **per project and per model**. Making more API keys in the same project adds no quota.
- Assume **every request that reaches the server counts**, failures included.
  - Google's billing FAQ says 400 and 500 errors count against quota.
  - On 503, Google staff say it is "unrelated to your quota", but many users report otherwise and nobody has settled it.
  - Budget defensively.
- The API has **no "remaining quota" header**.
  - A daily-cap 429 names the exhausted quota in `QuotaFailure.violations[].quotaId`, for example `GenerateRequestsPerDayPerProjectPerModel-FreeTier`.
  - The `RetryInfo.retryDelay` on that error is **not** the time until reset, so don't trust it.
  - Keep our own per-model counter, keyed on the Pacific date.

## 1. Reset: fixed midnight Pacific, not rolling

**Official.** From [Rate limits](https://aistudio.google.com/docs/rate-limits) (the old link was ai.google.dev/gemini-api/docs/rate-limits):

> Rate limits are applied per project, not per API key. Requests per day (**RPD**) quotas reset at midnight Pacific time.

- That page mentions a rolling window in one place only: the paid tiers' spend-based limits ("evaluated on a rolling 10-minute window").
  - The free tier's spend limit is "N/A".
- Midnight Pacific is 00:00 in America/Los_Angeles, so it follows daylight saving: 07:00 UTC in summer, 08:00 UTC in winter.
  - The docs don't spell this out. It is our inference from "Pacific time".
- **Community:** some users report that a free-tier project kept getting the daily 429 after midnight Pacific.
  - Staff replied only with a support form, so treat it as a possible bug, not as how resets are meant to work.
  - Source: [thread 183406](https://discuss.ai.google.dev/t/gemini-api-free-tier-daily-quota-does-not-reset-persistent-429-resource-exhausted/183406), 2026-09-17.

## 2. What counts toward RPD and RPM

### Official statement (400 and 500)

From the [Billing FAQ, "Am I charged for failed requests?"](https://aistudio.google.com/docs/billing#am-i-charged-for-failed-requests):

> If your request fails with a 400 or 500 error, you won't be charged for the tokens used. However, the request will still count against your quota.

- **400**, schema or validation rejections: **counts** (official).
- **500**, INTERNAL: **counts** (official).
- It is not clear whether "a 400 or 500 error" means only those two codes or the whole 4xx and 5xx classes.
  - Other codes are covered below.

The same FAQ also says:

> Requests to the `GetTokens` API are not billed, and they don't count against inference quota.

### 503 UNAVAILABLE (overloaded): conflicting evidence

- **Google staff, pinned post:** Jon_Matthews, forum title "Google", posted [Handling 429 / 503 errors from the Gemini API](https://discuss.ai.google.dev/t/handling-429-503-errors-from-the-gemini-api/124640) on 2026-02-20 and last edited it on 2026-10-09:

  > 503 errors are unrelated to your quota and indicate our services are temporarily overloaded and can't service your request.

  - This reads as a statement that 503s are not quota errors.
  - It does not say outright whether a 503 adds to the RPD counter.
- **Google staff, asked directly, gave no answer.** In [thread 184644](https://discuss.ai.google.dev/t/gemini-api-503-errors-appear-to-consume-free-tier-rpd-leading-to-429-quota-exhaustion/184644) (2026-09-24, gemini-3.8-flash, free tier, 20 RPD):
  - A user logged five 503s and one success, then got a daily-quota 429.
  - Staff (Pooja_Kapse) replied only that "503 errors are due to our services being temporarily overloaded".
  - The user pressed for a yes or no and got no reply.
- **Community reports that 503s do count:**
  - [109581](https://discuss.ai.google.dev/t/503-the-model-is-overloaded-is-being-counted-against-the-rate-limits/109581), 2025-11. Staff asked whether it still happens, and users said yes as late as 2026-04.
  - [109605](https://discuss.ai.google.dev/t/do-api-calls-which-result-in-503-responses-count-towards-the-daily-quota-limit/109605), 2025-11.
  - [144053](https://discuss.ai.google.dev/t/503-retries-are-counted-against-the-rpd-rpm-quota-making-the-effective-quota-a-fraction-of-whats-shown/144053), 2026-05. A user's dashboard counted total requests at a 1-16% success rate.
  - [139510](https://discuss.ai.google.dev/t/failed-request-consume-quota/139510), 2026-04.
- **Verdict: unconfirmed.** Even 500 counts officially, and many users report 503s counting.
  - Treat 503 as **counting** toward RPD and RPM when we plan our budget.

### 429 RESOURCE_EXHAUSTED

- No official statement on whether a 429 itself adds to RPD.
- One community post says 429s consume quota ([139510](https://discuss.ai.google.dev/t/failed-request-consume-quota/139510)). It is untested.
- Once the daily quota is gone, retrying until reset gains nothing either way.

### Client timeout while the server keeps generating

- **No official statement found.**
  - The docs' error table lists `499 CANCELLED`: "The client closed the connection before the API could finish responding" ([API errors, generateContent](https://aistudio.google.com/docs/api-errors.md?generate-content=true)).
  - Nothing says how a 499 is counted.
- The only community answer is speculative, from a non-staff user ([85472](https://discuss.ai.google.dev/t/are-failed-or-pending-requests-using-openai-compatible-gemini-api-endpoints-billed/85472)).
- **Our inference:** the server counted the request when it accepted it, so a client timeout most likely still uses one request.
  - The official SDKs also retry timeouts automatically. The Python SDK retries "up to four times" ([Troubleshooting, Retry strategy](https://aistudio.google.com/docs/troubleshooting#retry-strategy)).
  - **One logical call can therefore cost up to 5 requests.** With a 20-RPD budget, turn off or cap SDK retries.

## 3. Seeing today's count and remaining quota

### AI Studio pages (official links, page behaviour not documented)

- The rate-limits doc links to **https://aistudio.google.com/rate-limit?timeRange=last-28-days** ("View your active rate limits in AI Studio").
  - The `timeRange` query parameter means the time range can be chosen.
  - The docs don't describe the page or list its other time ranges.
- The Billing FAQ ("Where can I view my quota?") points to **https://aistudio.google.com/usage**.
- **Google staff** (pinned post 124640) say to "check the AI Studio Rate Limits page" and to "select the applicable project & time range".
- Free-tier 429 messages say "To monitor your current usage, head to: https://ai.dev/usage?tab=rate-limit" (quoted in [109605](https://discuss.ai.google.dev/t/do-api-calls-which-result-in-503-responses-count-towards-the-daily-quota-limit/109605)).
- **Unconfirmed:** whether any AI Studio view shows a live "used today" count for the current Pacific day, as opposed to peak usage per day.
  - Checking needs a signed-in browser. We did not check.
- **Google Cloud console**, as Google-titled forum users suggest ([96899](https://discuss.ai.google.dev/t/429-errors-despite-waiting-after-retrydelay/96899), [109605](https://discuss.ai.google.dev/t/do-api-calls-which-result-in-503-responses-count-towards-the-daily-quota-limit/109605)):
  - Go to APIs & Services → Generative Language API → Quotas & System Limits and check "Current usage percentage".
  - Cloud's own docs confirm that page has **Current usage** and **Current usage percentage** columns ([View and manage quotas](https://docs.cloud.google.com/docs/quotas/view-manage)).
  - This is the most likely way to see a daily count, but nobody has tested it for the Gemini free-tier quota.

### API: no remaining-quota headers

- None of the rate-limits, troubleshooting, API errors or billing pages document any `x-ratelimit-*` or remaining-quota response header.
  - So the API most likely has none.
- The docs mention response headers in one place only: deprecation warnings for `temperature`, `top_p` and `top_k`.

### 429 error body (generateContent / v1beta)

**Official, general format.** From [API errors (generateContent)](https://aistudio.google.com/docs/api-errors.md?generate-content=true):
- The error JSON has `code` (HTTP status), `message`, `status` (gRPC code, such as `RESOURCE_EXHAUSTED`), and `details[]`.
- `details[]` holds typed `google.rpc.*` messages.
- The example in the docs shows `ErrorInfo` and `LocalizedMessage`, not a quota error.
- That page describes 429 RESOURCE_EXHAUSTED as "You've exceeded one of the API's rate limits (RPM, TPM, RPD, spend, etc.)".

**Field definitions, from the official proto.** [`google/rpc/error_details.proto`](https://github.com/googleapis/googleapis/blob/master/google/rpc/error_details.proto) defines:
- `RetryInfo.retry_delay`, a `Duration`. Its JSON name is `retryDelay`, for example `"1s"`.
- `QuotaFailure.violations[]`, each with:
  - `subject`
  - `description`
  - `api_service` (JSON `apiService`)
  - `quota_metric` (JSON `quotaMetric`)
  - `quota_id` (JSON `quotaId`)
  - `quota_dimensions` (JSON `quotaDimensions`, a map)
  - `quota_value` (JSON `quotaValue`, the enforced limit)
  - `future_quota_value` (JSON `futureQuotaValue`)
- **No field carries usage or remaining quota.** `quotaValue` is the limit, not the count.

**A real free-tier daily 429 body.** Posted by a user in [96899](https://discuss.ai.google.dev/t/429-errors-despite-waiting-after-retrydelay/96899) (2025-08); a Google-titled responder confirmed what it means:

```json
{"error":{"code":429,"status":"RESOURCE_EXHAUSTED",
 "message":"You exceeded your current quota, ...",
 "details":[
  {"@type":"type.googleapis.com/google.rpc.QuotaFailure",
   "violations":[{"quotaMetric":"generativelanguage.googleapis.com/generate_content_free_tier_requests",
                  "quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                  "quotaDimensions":{"model":"gemini-2.5-flash","location":"global"},
                  "quotaValue":"250"}]},
  {"@type":"type.googleapis.com/google.rpc.Help","links":[...]},
  {"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"1s"}]}}
```

- Responder Pannaga_J, forum title "Google", said:
  - this is a daily quota error, so `retryDelay` "is not working for your usecase";
  - to tell a daily quota error from a per-minute one, read `QuotaFailure`, `quotaValue` and `quotaId`.
- The same `quotaId` with `quotaValue: 20` turns up on gemini-3.6-flash, 3.7-flash and 3.8-flash ([183406](https://discuss.ai.google.dev/t/gemini-api-free-tier-daily-quota-does-not-reset-persistent-429-resource-exhausted/183406), 2026-09).
- Google staff (pinned post 124640, edited 2026-10) say 429s now "come with a description field which properly explains the cause".
- **Practical rule** (our reading of the evidence above, not official guidance):
  - If `quotaId` contains `PerDay`, stop for the day.
  - If it contains `PerMinute`, honour `retryDelay` and back off.
- The newer **Interactions API** uses a different, simpler error body ([API errors](https://aistudio.google.com/docs/api-errors)): `{"error":{"code":"quota_exceeded","message":...}}`.
  - `quota_exceeded` means "You have exceeded your daily quota".
  - `rate_limit_exceeded` means a per-minute or per-second limit.
  - It documents no `details` and no quota ID.

## 4. Per project or per key? Per model?

- **Per project, not per key. Official:** "Rate limits are applied per project, not per API key." ([Rate limits](https://aistudio.google.com/docs/rate-limits))
  - Tiers are set at the Cloud billing-account level and passed down to the projects linked to it ([Billing, Projects and API keys](https://aistudio.google.com/docs/billing#projects-api-keys)).
  - A free-tier project has no billing account.
- **Per model. Official:** "Limits vary depending on the specific model being used, and some limits only apply to specific models."
  - Each model has its own limits, but the docs don't spell out that each model also has a separate daily counter.
- **Separate counter per model, from the quota ID and the community:**
  - The quota ID is literally `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, with `quotaDimensions.model` set to the model ([96899](https://discuss.ai.google.dev/t/429-errors-despite-waiting-after-retrydelay/96899)).
  - So each model gets its own daily counter.
  - In [183406](https://discuss.ai.google.dev/t/gemini-api-free-tier-daily-quota-does-not-reset-persistent-429-resource-exhausted/183406), three Flash models each reported `quotaValue: 20` separately.
- **The numbers:**
  - The rate-limits doc no longer prints per-model free-tier numbers. It says limits "can be viewed in Google AI Studio".
  - [Pricing](https://aistudio.google.com/docs/pricing) confirms that `gemini-4-hydrogen`, `gemini-3.8-flash` and `gemini-3.6-flash` all have a free tier ("Free of charge").
  - **Unconfirmed from official docs:** whether each one is exactly 20 RPD.
    - 20 is confirmed only for 3.6 and 3.8 Flash, from user-posted 429 bodies.
    - We found no evidence for 4 Hydrogen.
    - Check the AI Studio rate-limit page for the live numbers.
- **Not covered here:** built-in tools have their own per-project limits.
  - Grounding with Google Search on Gemini 3 and newer has 1,500 RPD per project (rate-limits page).
  - "Rate limits are more restricted for experimental and preview models."

## Sources

Official:
- Rate limits: https://aistudio.google.com/docs/rate-limits
- Billing (FAQ on failed requests, viewing quota): https://aistudio.google.com/docs/billing
- API errors (Interactions API): https://aistudio.google.com/docs/api-errors
- API errors (generateContent): https://aistudio.google.com/docs/api-errors.md?generate-content=true
- Troubleshooting: https://aistudio.google.com/docs/troubleshooting
- Pricing: https://aistudio.google.com/docs/pricing
- Error detail protos: https://github.com/googleapis/googleapis/blob/master/google/rpc/error_details.proto
- Cloud quotas, viewing usage: https://docs.cloud.google.com/docs/quotas/view-manage

Google staff, secondary:
- https://discuss.ai.google.dev/t/handling-429-503-errors-from-the-gemini-api/124640
- https://discuss.ai.google.dev/t/429-errors-despite-waiting-after-retrydelay/96899
- https://discuss.ai.google.dev/t/gemini-api-503-errors-appear-to-consume-free-tier-rpd-leading-to-429-quota-exhaustion/184644
- https://discuss.ai.google.dev/t/free-tier-interactions-api-getmodel-succeeds-but-generation-returns-503-on-gemini-3-8-flash-and-429-on-gemini-3-6-flash/189550

Community, unconfirmed:
- https://discuss.ai.google.dev/t/503-the-model-is-overloaded-is-being-counted-against-the-rate-limits/109581
- https://discuss.ai.google.dev/t/do-api-calls-which-result-in-503-responses-count-towards-the-daily-quota-limit/109605
- https://discuss.ai.google.dev/t/503-retries-are-counted-against-the-rpd-rpm-quota-making-the-effective-quota-a-fraction-of-whats-shown/144053
- https://discuss.ai.google.dev/t/failed-request-consume-quota/139510
- https://discuss.ai.google.dev/t/gemini-api-free-tier-daily-quota-does-not-reset-persistent-429-resource-exhausted/183406
- https://discuss.ai.google.dev/t/are-failed-or-pending-requests-using-openai-compatible-gemini-api-endpoints-billed/85472
