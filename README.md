# Provenance Guard

A backend API that a creative-sharing platform could plug into to classify submitted text as likely AI-generated, likely human-written, or uncertain. It returns a confidence score, a plain-language transparency label, and lets creators appeal a classification. Every decision and appeal is written to a structured audit log.

`planning.md` was written before any implementation code. It holds the full spec, the architecture diagram, and the AI tool plan.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file (never commit it):

```
GROQ_API_KEY=your_key_here
```

Optional: set `GROQ_MODEL` in `.env` to override the default model (`openai/gpt-oss-20b`).

Run the server (port 5001, because macOS uses port 5000 for AirPlay):

```bash
python app.py
```

Run the scoring tests:

```bash
python test_scoring.py
```

### Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /submit` | Classify text. Body: `{"text": "...", "creator_id": "..."}`. Text must be at least 10 words. |
| `POST /appeal` | Contest a classification. Body: `{"content_id": "...", "creator_id": "...", "creator_reasoning": "..."}` |
| `GET /log` | Recent audit log entries as JSON (optional `?limit=N`). |

---

## 1. Architecture overview

When someone submits text, the API first checks the request and applies the rate limit. The same raw text then goes independently to two detection signals: an LLM judgment through Groq and a set of stylometric heuristics in pure Python. The scorer blends the two scores, applies a disagreement penalty and an agreement guard, and maps the result to one of three attributions and a matching transparency label. The full decision is written to the audit log before the response is returned.

When a creator appeals, the API finds the original submission, checks that the creator ID matches, and changes the status to `under_review`. It records the appeal next to the original decision without overwriting the original scores.

```
SUBMIT:  Client -> Rate limiter -> Validation -> [Signal 1: Groq LLM] + [Signal 2: Stylometrics]
         -> Confidence scorer -> Label generator -> Audit log -> Response
APPEAL:  Client -> Validation + lookup -> Status = under_review -> Audit log (appeal event) -> Response
```

The full diagram is in `planning.md` under `## Architecture`.

---

## 2. Detection signals

I use two signals that look at different things. The LLM reads the text as a whole. The stylometrics measure structural patterns. Both return a score from 0 (human-like) to 1 (AI-like).

### Signal 1: LLM judgment (Groq)

- **What it measures:** the overall feel of the writing: generic phrasing, balanced or cautious tone, lack of specific personal detail, smooth transitions, repetitive structure.
- **Why I chose it:** AI text tends to be polished and predictable. Human writing tends to include specific detail, tangents, and changes in tone. An LLM can weigh these together, which a simple statistic cannot.
- **Model:** `openai/gpt-oss-20b` via Groq at temperature 0. The prompt asks for JSON only: an `ai_probability` and a one-sentence reason. The reason is stored in the audit log.
- **What it misses:** polished or formal human writing looks AI-like to it. Careful writing by non-native English speakers can be flagged. It is also inconsistent: the same AI-style text scored 0.8 in one run and 0.65 in another at temperature 0. Lightly edited AI text with a few personal details can fool it.
- **Failure behavior:** if the API call fails or the output cannot be parsed, the signal returns nothing and the result is forced to `uncertain`.

### Signal 2: Stylometric heuristics

Three metrics, each normalized to 0 to 1 (1 = AI-like) and averaged with equal weight:

| Metric | What it measures | Normalization |
|---|---|---|
| Sentence length variation | Coefficient of variation of sentence lengths. AI sentences tend to be uniform. | CV >= 0.70 gives 0.0, CV <= 0.25 gives 1.0 |
| Informality and punctuation | Markers per 100 words: `!`, `?`, `(`, ellipses, dashes, lowercase sentence starts, ALL-CAPS words of 3 or more letters | 0 per 100 gives 1.0, 4 or more per 100 gives 0.0 |
| Formulaic phrase density | Stock AI phrases per 100 words ("it is important to note", "furthermore", "paradigm", "stakeholders", and so on) | 0 per 100 gives 0.0, 3 or more per 100 gives 1.0 |

- **Why I chose it:** it is independent of the LLM. It cannot be talked into a verdict by fluent-sounding text, and it is deterministic and free.
- **Why not type-token ratio:** it depends heavily on text length and would add noise on short creative pieces.
- **What it misses:** repetitive poetry with uniform lines, formal academic or legal prose, and very short texts all look AI-like to these metrics. The phrase list is easy to evade and will go stale as models change.

---

## 3. Confidence scoring

### Method

1. **Blend:** `ai_score = 0.6 * llm + 0.4 * stylo`. For texts under 40 words, `0.75 * llm + 0.25 * stylo`, because the statistics are less reliable on short text.
2. **Disagreement penalty:** if the signals differ by more than 0.40, the score is pulled halfway toward 0.5.
3. **LLM failure:** forced to 0.5 and `uncertain`.
4. **Agreement guard:** `likely_ai` requires `ai_score >= 0.80` and both signals at 0.60 or higher. Otherwise the result is downgraded to `uncertain`.

| `ai_score` | Attribution |
|---|---|
| >= 0.80 (and guard passes) | `likely_ai` |
| 0.36 to 0.79 | `uncertain` |
| <= 0.35 | `likely_human` |

I decided what 0.5 means before writing any math: **0.5 means the system cannot tell.** It is not a weak AI verdict. Because a false positive (calling a human's work AI) is worse than a false negative, the thresholds are asymmetric: the AI label needs a score 0.30 above the midpoint, while the human label needs only 0.15 below it. The AI label also needs both signals to agree.

The `confidence` field expresses how strongly the result supports the attribution. For `likely_ai` it is `ai_score`. For `likely_human` it is `1 - ai_score`. For `uncertain` it is `1 - 2 * abs(ai_score - 0.5)`, which measures closeness to the midpoint. That means an uncertain result near 0.5 shows a high `confidence` number, which can look odd. It is the confidence that the evidence is genuinely mixed, not confidence that the content is human or AI.

**These scores are not calibrated probabilities.** A confidence of 0.88 does not mean the system is right 88% of the time. I have not measured that.

### How I tested it

First, `test_scoring.py` runs seven offline unit tests on `score()` with hand-picked inputs, including the disagreement penalty, the LLM-failure case, and the agreement guard (an input with `ai_score` 0.814 that is downgraded because one signal is below 0.60). All pass.

Second, it runs six reference texts through both real signals:

| Input | LLM | Stylo | `ai_score` | Confidence | Attribution |
|---|---|---|---|---|---|
| Clearly AI | 0.80 | 0.90 | 0.842 | 0.842 | `likely_ai` |
| Clearly human | 0.15 | 0.07 | 0.116 | 0.884 | `likely_human` |
| Formal human | 0.60 | 0.66 | 0.625 | 0.750 | `uncertain` |
| Lightly edited AI | 0.40 | 0.35 | 0.389 | 0.778 | `uncertain` |
| Repetitive poem | 0.80 | 0.67 | 0.767 | 0.466 | `uncertain` |
| Very short text | 0.60 | 0.67 | 0.617 | 0.766 | `uncertain` |

The pass criteria from my planning doc were: clear AI above 0.70, clear human below 0.40, at least 0.30 between them, and borderline inputs in the uncertain band. The gap between clearly AI and clearly human was 0.73. Both borderline inputs landed in `uncertain`. The repetitive poem, my predicted false-positive case, was caught by the uncertain band and not labeled AI.

### Two example submissions with different confidence

Both come from live `POST /submit` calls.

**High confidence (AI-style essay text):** llm 0.8, stylo 1.0, `ai_score` 0.88, confidence 0.88, attribution `likely_ai`.

**Lower confidence (formal economics prose, written by a person):** llm 0.6, stylo 0.662, `ai_score` 0.625, confidence 0.75, attribution `uncertain`. The system leans slightly toward AI but is nowhere near the 0.80 bar, so it declines to give a verdict.

---

## 4. Transparency label

The label changes with the attribution. All three exact texts:

| Variant | Shown when | Exact text |
|---|---|---|
| High-confidence AI | `ai_score >= 0.80` and both signals agree | "Our automated analysis suggests this work was likely created with AI assistance. This is an estimate, not a certainty. If you are the creator and this is wrong, you can appeal and a person will review it." |
| High-confidence human | `ai_score <= 0.35` | "Our automated analysis suggests this work was likely written by a person. This is an estimate, not a guarantee." |
| Uncertain | `0.36 <= ai_score <= 0.79`, or the AI guard blocked the label | "We could not tell whether this work was written by a person or created with AI. Our checks gave mixed or weak results, so no verdict has been applied. If you are the creator, you can request a review." |

**Label check with people:** I showed the three labels (in mixed order, with no scores or explanation) to three people who had not seen the project. For each label I asked what it tells them about the work, how sure they think the system is, and what they would do if it appeared on their post.

- **Uncertain label:** All three read it as the system being unable to decide. One said it was "not saying the work is bad or that anything is wrong," which was my main concern. None treated it as a flag against the creator. Two said they would request a review only if the result mattered to them.
- **AI label:** All three understood it as an estimate and not proof ("not 100% sure," "not proof that they did"). All three said they would appeal if they had written the work themselves, so the appeal wording in the label did its job.
- **Human label:** All three understood the system could still be wrong, and one said it "has not verified" that a person wrote it. Confidence estimates ranged from "moderately" to "pretty sure." None read it as an official certification. Most would leave the post alone.

**Changes made:** None. Nobody misread the intended meaning of any label, so I kept the wording as written in `planning.md`.

**What I'd watch for:** The AI label could still sting a creator who didn't use AI, which is why it includes the appeal path. With only three participants this is a small check, not a user study.

---

## 5. Rate limiting

| Endpoint | Limit |
|---|---|
| `POST /submit` | `10 per minute;100 per day` per IP |
| `POST /appeal` | `5 per minute` per IP |

Implemented with Flask-Limiter using `get_remote_address` and in-memory storage (`storage_uri="memory://"`). The configuration is in `app.py`.

**Reasoning:**
- A person submitting their own writing sends a handful of pieces in a session, so 10 per minute is generous for real use but stops a script.
- 100 per day caps an adversary probing the classifier for ways to evade it, and keeps usage within the free Groq tier, since every submission makes an API call.
- Appeals are rarer and cost no API call, so 5 per minute is enough for genuine use and blocks spam.

**Evidence:** 12 rapid requests to `/submit` (the test text is over 10 words so it passes validation and reaches the limiter):

```
200
200
200
200
200
200
200
200
200
200
429
429
```

Notes: in-memory storage resets when the server restarts, and Flask-Limiter counts a request even if the handler later returns a 400. A production system would use a shared store such as Redis.

---

## 6. Audit log

Every submission and appeal is written to a SQLite table (`audit_log.db`) and exposed through `GET /log`. Each row includes the timestamp, content ID, creator ID, attribution, confidence, both signal scores, the LLM's reasoning, status, and any appeal text. An appeal updates the original row's status and also adds a separate `appeal` event that repeats the original decision, so the original scores are never overwritten.

Sample from `GET /log` (4 entries, newest first; `text` shortened here for readability):

```json
[
  {
    "id": 4,
    "event_type": "appeal",
    "timestamp": "2026-10-13T22:44:00.765064+00:00",
    "content_id": "2128dcf0-aaad-4cfc-bdb7-6612aca74e6b",
    "creator_id": "test-user-3",
    "attribution": "uncertain",
    "ai_score": 0.625,
    "confidence": 0.75,
    "llm_score": 0.6,
    "stylo_score": 0.662,
    "status": "under_review",
    "appeal_reasoning": "I wrote this myself as part of my economics coursework. My academic writing style is formal by nature.",
    "appeal_timestamp": "2026-10-13T22:44:00.764647+00:00",
    "text": "The relationship between monetary policy and asset price inflation ..."
  },
  {
    "id": 3,
    "event_type": "submission",
    "timestamp": "2026-10-13T22:42:13.486928+00:00",
    "content_id": "2128dcf0-aaad-4cfc-bdb7-6612aca74e6b",
    "creator_id": "test-user-3",
    "attribution": "uncertain",
    "ai_score": 0.625,
    "confidence": 0.75,
    "llm_score": 0.6,
    "stylo_score": 0.662,
    "status": "under_review",
    "appeal_reasoning": "I wrote this myself as part of my economics coursework. My academic writing style is formal by nature.",
    "text": "The relationship between monetary policy and asset price inflation ..."
  },
  {
    "id": 2,
    "event_type": "submission",
    "timestamp": "2026-10-13T22:41:52.258231+00:00",
    "content_id": "443683cf-da3a-430f-984e-faf646a4a63c",
    "creator_id": "test-user-2",
    "attribution": "likely_human",
    "ai_score": 0.116,
    "confidence": 0.884,
    "llm_score": 0.15,
    "stylo_score": 0.066,
    "status": "classified",
    "appeal_reasoning": null,
    "text": "ok so i finally tried that new ramen place downtown ..."
  },
  {
    "id": 1,
    "event_type": "submission",
    "timestamp": "2026-10-13T22:41:41.093384+00:00",
    "content_id": "9cb6ec27-ec06-4060-958e-cf5af896b7ba",
    "creator_id": "test-user-1",
    "attribution": "likely_ai",
    "ai_score": 0.88,
    "confidence": 0.88,
    "llm_score": 0.8,
    "stylo_score": 1.0,
    "status": "classified",
    "appeal_reasoning": null,
    "text": "In todays rapidly evolving digital landscape, artificial intelligence ..."
  }
]
```

### Appeals

`POST /appeal` takes a `content_id`, the `creator_id`, and `creator_reasoning`. It returns 400 for missing fields, 404 for an unknown content ID, 403 if the creator ID does not match the original submission, and 409 if the content is already under review. On success it sets the status to `under_review`, stores the reasoning and an appeal timestamp, and adds an `appeal` event to the log. I tested all four error paths and the success path (see the log above). Automated re-classification is not implemented.

---

## 7. Known limitations

- **Repetitive, simple-vocabulary poetry.** A poem built on repeated lines of similar length has very low sentence-length variation and no informal markers, so the stylometric score reads AI-like. My repetitive test poem scored 0.80 from the LLM and 0.67 from the stylometrics, giving `ai_score` 0.767. The wide uncertain band and the agreement guard kept it from being labeled AI, but a poem with slightly more uniform lines could cross the threshold.
- **Formal academic writing and careful non-native English.** Long, evenly structured sentences with no informal punctuation push both signals toward AI at once, so the agreement guard does not help. My formal human test scored 0.625, closer to the AI zone than I would like. This is a real fairness risk for non-native English writers, and it is the main reason the appeal path exists.
- **Lightly edited AI text.** A few casual edits raise sentence variation and add informal markers, lowering the stylometric score while the content stays AI-authored. My test case scored 0.389 and was close to being called human. I accept this because false negatives are the cheaper error.
- **LLM inconsistency.** The same text scored 0.8 and 0.65 on different runs at temperature 0. Near a threshold, the label could change between submissions of identical text.
- **Short and heavily formatted text.** Texts under 40 words get reduced stylometric weight, but statistics on a few sentences are still unreliable. Lists, dialogue-heavy fiction, and code are not handled specially.
- **No authentication.** Appeals are checked only by matching `creator_id`, and `GET /log` is open.

If I deployed this for real, I would collect labeled human and AI samples to calibrate the scores into real probabilities, run the LLM signal several times and average it, and send the appeal queue to an actual reviewer interface.

---

## 8. Spec reflection

**How the spec helped.** Writing exact weights, thresholds, and normalization ranges in `planning.md` before coding let me unit-test `score()` offline with hand-picked inputs before making any API calls. Having the thresholds written down also gave me something concrete to check generated code against. The spec also forced me to decide what 0.5 means before writing math, which shaped the asymmetric thresholds and the agreement guard.

**How the implementation diverged.**
- The Groq model in the spec and assignment (Llama 4 Scout) returned a 404 on my account, so I listed available models and switched to `openai/gpt-oss-20b`.
- My test run showed that the ALL-CAPS check counted "AI" as shouting, which pulled a clearly AI text's informality score down and kept it from reaching `likely_ai`. I changed the rule to require 3 or more letters.
- My first spec set a 20-word minimum, which would have made the assignment's own rate-limit test text fail validation. I lowered it to 10 words before building.
- `llm_signal` returns a pair of the score and the reasoning, not just a float, so the reasoning can be logged. The sentence splitter also splits on line breaks so poems are measured line by line, and a single-sentence text gets a neutral 0.5 on that metric.

---

## 9. AI usage

I used an AI assistant throughout, directing it with my spec.

1. **App code from the spec.** I had the AI generate `app.py` (Flask routes, the Groq signal function, the SQLite audit log) from my `planning.md` sections. What it produced used the Llama 4 Scout model named in the spec, which returned a 404 on my account. I overrode it by listing the models my key could access, switching to `openai/gpt-oss-20b`, and updating `planning.md` to match.
2. **Stylometric and scoring code.** I had it write `stylometric_signal` and `score()` against my thresholds and checked every constant against the spec. Running my reference inputs showed the generated ALL-CAPS rule treated the acronym "AI" as informal shouting. I changed the rule to 3 or more letters and added a unit test for the agreement guard, which the original tests did not exercise.
3. **Spec review.** I had the AI review my `planning.md`. It caught that my 20-word minimum conflicted with the rate-limit test text in the assignment, and I fixed the number in the spec before writing code.

---

## 10. Walkthrough video

[FILL IN: link to your short walkthrough video]

## 11. Stretch features

None implemented.