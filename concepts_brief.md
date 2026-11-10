# RFC: Live In-Place Single-Message Master Synthesizer (Gemini + Shelby + Third Arbiter)

* **Author:** wewe00220033
* **Date:** 2026-11-09
* **Project:** Gemini Voice Transcriber (Branch: `A-test`)
* **Status:** Implemented and Fully Verified (Merged into `A-test` @ `642331c` across Milestones M1 through M5). All 636 tests passing (100%), 360° forensic audit passing (8/8 angles), GitHub Actions CI passing (Python 3.10 & 3.12). See `audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.12.md`.

---

## 1. Overview

This RFC defines the transformation of the dual-polish execution model into an **In-Place Live-Updating Single-Message Master Synthesizer Architecture**. Instead of presenting the user with two side-by-side or separate outputs from Gemini Flash-Lite and local Shelby ChatGPT, the system runs both models asynchronously in the background. A third agent (The Master Synthesizer / السبّاك) ingests the original raw transcript, the 12-section editorial prompt, and candidate outputs to forge a single unified, authoritative result. The progression is tracked and rendered in-place within a live Telegram message coordinated by a Single-Writer `LiveMessageController`.

---

## 2. Technical State Machine & Lifecycle

The message lifecycle consists of 4 distinct sequential states:

```text
[Voice Uploaded]
       │
       ▼
[State 0: Raw Audio Transcription via Gemini Transcriber]
       │
       ▼
[State 1: Send Telegram Message with Live Status Header + Raw Text]
       │
  ┌────┴────────────────────────┐
  ▼                             ▼
[Thread A: Gemini Polish]    [Thread B: Shelby #1 Polish (Timeout: 180s)]
  │                             │
  ▼                             ▼
[Gemini Finishes ~1.5s]       [Shelby Status: Running / Done / Timeout]
  │                             │
  └──────────────┬──────────────┘
                 ▼
[State 2: In-Place Status Update & Third Arbiter Launch]
   - Primary Synthesizer: Shelby #2 (Budget: min(180s, remaining of 420s))
   - Fallback Synthesizer: Gemini Flash-Lite (invoked if Shelby #2 fails or times out)
   - Fallback Invariant: If all synthesis fails, finalize with best available draft or raw text
                 │
                 ▼
[State 3: Master Golden Output Replaces Body in Live Message]
   - Full 12-Section formatted transcript rendered within balanced <pre> blocks
   - Chunking: Up to 8 balanced <pre> chunks if parsed length > 4000 UTF-16 code units
   - Live telemetry footer: Gemini time, Shelby time, Synthesizer time, Total time
```

---

## 3. Telegram API Rate Limits & SRE Constraints

1. **Telegram EditMessageText Rate Limit:**
   - Telegram enforces a limit of approximately **1 edit per second per chat** (and max 20-30 edits per minute globally across chats).
   - If edited too frequently, Telegram returns `429 Too Many Requests (Retry-After)`.
   - **SRE Safeguard:** Status transitions are event-driven rather than polling-driven, throttled via `LiveMessageController` with single-writer coordination to prevent Telegram 429 errors:
     - Transition 1: When Gemini completes (~1.5s).
     - Transition 2: When Shelby #1 completes or times out.
     - Transition 3: When the final synthesized output is delivered.
     - Rate-limited to max 3 edits per voice note, with a 1.5–2.0s cooldown floor between edits.

2. **Payload Size & UTF-16 Measurement:**
   - Total payload size per edit must strictly adhere to Telegram's limit: `parsed_utf16 <= 4000` UTF-16 code units (after entity decoding and tag parsing).
   - If output exceeds 4000 UTF-16 units, the controller splits into balanced chunks or provides an interactive download.

3. **Fallback & Process Management:**
   - If any local Shelby process exceeds 180.0 seconds, `subprocess.run(timeout=180.0)` raises `TimeoutExpired`.
   - The thread terminates the hung process group, logs `TIMEOUT`, and fails over to Flash-Lite synthesis within the remaining `SYNTH_TOTAL_BUDGET_SEC = 420.0s`.

---

## 4. Goals vs. Non-Goals

### In-Scope Goals:
- Single Telegram message updated in-place (no chat clutter).
- Background parallel execution (Gemini Flash-Lite + Shelby ChatGPT).
- Hard 180s timeout ceiling per local run with process tree isolation.
- Third synthesis agent taking raw text + prompt + candidate drafts to output one master text.
- Full telemetry footer showing exact seconds per stage.

### Out-of-Scope Non-Goals:
- Storing intermediate drafts permanently in database if user only requested the final master.
- Creating custom Telegram webapps; the UI remains strictly native Telegram messages.
- Exposing web dashboard while `WEB_ENABLED=0` remains enforced.
