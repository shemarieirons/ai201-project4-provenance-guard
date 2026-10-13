# Provenance Guard: Planning Document

This document explains how I plan to build Provenance Guard before implementing the system. It covers the detection signals, how their scores will be combined, how uncertainty will be handled, what users will see, and how creators can appeal a classification. I will use this document as a reference when implementing the project in Milestones 3 to 5.

## 0. Problem and design principles

Provenance Guard is a backend system that a creative-sharing platform can use to check whether submitted text is more likely to be AI-generated or human-written. It combines two detection signals to produce a score, assigns one of three possible classifications, and generates a transparency label that explains the result to the reader. Creators can also appeal if they believe their work was incorrectly classified.

I made a few decisions to guide how the system will work:

1. **False positives are worse than false negatives.** I do not want to label someone's original work as AI-generated without strong evidence. Missing some AI-generated text is preferable to incorrectly questioning a human creator's work. Because of this, the system requires both detection signals to agree before assigning the high-confidence AI label.
2. **A score of 0.5 means the system cannot tell.** It should not be treated as a weak AI classification. When the evidence is unclear or the signals disagree, the system should communicate that uncertainty instead of forcing a decision.
3. **The label should reflect the result.** Someone reading the label should be able to understand whether the system thinks the work is likely human-written, likely AI-generated, or uncertain without needing to understand the underlying scores.
4. **Creators should be able to appeal, and decisions should be recorded.** If someone believes the system got it wrong, they should be able to explain why and request a review. The original classification and the appeal should both remain in the audit log.

---

## 1. Detection signals

I will use two signals to classify submitted text: an LLM-based assessment through Groq and a set of stylometric heuristics written in Python.

I chose these two approaches because they look at different things. The LLM considers the text as a whole, while the Python heuristics look at measurable patterns such as sentence length variation, punctuation, and common phrases. Using both gives the system more information than relying on either signal alone.

Both signals return a score between 0 and 1:

- 0.0 means the text looks human-written.
- 1.0 means the text looks AI-generated.

These scores are estimates, not proof of who wrote the text. Both signals have limitations, which is why the system also accounts for uncertainty.

### Signal 1: LLM judgment (Groq)

- **Model:** `openai/gpt-oss-20b` via Groq, temperature 0.
- **What it measures:** The overall writing style, including generic phrasing, balanced or cautious language, a lack of specific details, smooth transitions, and repetitive structure.
- **Why I chose it:** AI-generated text can sometimes sound polished and predictable, with general statements and similar sentence structures. Human writing may contain more personal details, unusual phrasing, and changes in tone. The LLM can consider these patterns together instead of looking at just one feature.
- **Output format:** The model is prompted to return only JSON: `{"ai_probability": <float 0-1>, "reasoning": "<one sentence>"}`. The function returns a pair (score, reasoning), with the score clamped to the range 0 to 1, or (None, None) on failure. The explanation is saved in the audit log.
- **Failure handling:** If the API request fails or the response cannot be parsed, the function returns `None`. The system treats the result as uncertain rather than making a confident classification without the LLM score.
- **Limitations:**
  - Formal human writing, including academic and professional writing, may look AI-generated.
  - Non-native English speakers who write carefully or formally may be unfairly flagged.
  - The model may give inconsistent or overly confident judgments.
  - AI-generated text that has been edited to include personal details may be harder to identify.

### Signal 2: Stylometric heuristics (pure Python)

I will calculate three metrics in Python and combine them into one `stylo_score`. Each metric is normalized to a score between 0 and 1 (where 1 means AI-like), and the three scores are averaged with equal weight. The metrics focus on sentence length variation, informality and punctuation, and the use of common formulaic phrases.

| Metric | How computed | Why it differs | Normalization (linear, clamped) |
|---|---|---|---|
| **Sentence length variation** | Split into sentences, count words per sentence, compute the coefficient of variation (std / mean). | AI sentences tend to be uniform in length. Humans mix short and long. | CV >= 0.70 maps to 0.0 (human-like). CV <= 0.25 maps to 1.0 (AI-like). |
| **Informality and punctuation variety** | Count informal markers per 100 words: exclamation marks, ellipses, dashes, parentheses, sentences starting lowercase, ALL-CAPS words of 3 or more letters (so acronyms like AI do not count), question marks. | Human casual writing uses irregular punctuation and capitalization. AI text is typically clean and conventional. | 0 markers per 100 words maps to 1.0. 4 or more per 100 words maps to 0.0. |
| **Formulaic phrase density** | Count hits from a fixed list of stock AI phrases per 100 words (for example "it is important to note", "furthermore", "moreover", "in conclusion", "delve", "tapestry", "paradigm", "plays a crucial role", "stakeholders", "landscape"). | Models overuse certain transitions and abstract vocabulary. | 0 hits per 100 words maps to 0.0. 3 or more hits per 100 words maps to 1.0. |

Notes on these choices:

- **Type-token ratio was considered and rejected.** It depends heavily on text length (short texts score artificially high), so it would add noise on exactly the short pieces a creative platform sees.
- **Minimum length.** Text under **10 words** is rejected with HTTP 400. Text from 10 to 39 words is scored, but with reduced stylometric weight (see the combination rules below).
- **Limitations:**
  - Repetitive poetry with simple vocabulary and uniform line lengths looks AI-like on sentence variation.
  - Formal academic or legal prose is uniform and has no informal markers, so it looks AI-like.
  - Very short texts give unreliable statistics.
  - Poetry has non-standard sentence boundaries, so sentence splitting is unreliable.
  - The formulaic phrase list is easy to evade and will go stale as models change.
- **Sentence splitting.** Sentences are split on . ! or ? followed by a space, and also on line breaks, so each line of a poem counts as a sentence. If a text has only one sentence, the sentence-variation metric gets a neutral 0.5, because variation cannot be measured.

### Combining the signals

After calculating both scores, I will combine them into one `ai_score`. The LLM score has more weight because it considers the overall text, while the stylometric score is based on a smaller set of measurable features. I also account for cases where the signals disagree or one of them fails. Since false positives are the bigger concern, the system requires both signals to support an AI classification before returning `likely_ai`.

Let `llm` be the signal 1 score and `stylo` be the signal 2 score.

1. **Base score:** `base = 0.6 * llm + 0.4 * stylo`. If the text is under 40 words, use `base = 0.75 * llm + 0.25 * stylo` instead, because the stylometric statistics are less reliable on short text.
2. **Disagreement penalty:** if `abs(llm - stylo) > 0.40`, pull the score toward 0.5: `ai_score = 0.5 + (base - 0.5) * 0.5`. Otherwise `ai_score = base`. Strongly conflicting signals should never produce a confident verdict.
3. **LLM failure:** if signal 1 returned `None`, set `ai_score = 0.5`, set attribution to `uncertain`, and record `"llm_unavailable"` in the log's notes field.
4. **Agreement guard for the AI label:** a result can only be `likely_ai` if `ai_score >= 0.80` **and** both `llm >= 0.60` and `stylo >= 0.60`. If the score reaches 0.80 but a signal falls below 0.60, the result is downgraded to `uncertain`. This is the main false-positive protection. Both the threshold and the guard must be implemented explicitly in code.

The final response reports `ai_score` (the combined score) and `confidence` (see section 2).

---

## 2. Uncertainty representation

I do not want the system to treat every submission as either definitely AI-generated or definitely human-written. A score near 0.5 should mean that the system does not have enough evidence to make a clear decision.

### What the numbers mean

- `ai_score` is the combined score from both detection signals. It ranges from 0 to 1, with lower scores leaning toward human-written text and higher scores leaning toward AI-generated text.
- A score of 0.5 means the system cannot tell. This could happen when the signals provide little evidence either way or when their results contradict each other.
- `confidence` represents how strongly the result supports the assigned attribution. Its calculation depends on the attribution:
  - `likely_ai`: `confidence = ai_score`
  - `likely_human`: `confidence = 1 - ai_score`
  - `uncertain`: `confidence = 1 - 2 * abs(ai_score - 0.5)`

For an uncertain result, this value measures how close the score is to the midpoint. It is highest (1.0) when `ai_score` is exactly 0.5 and decreases as the score moves toward either edge. It describes how little evidence the system has, not the probability that the "uncertain" call is correct. This lets the system distinguish between a result where the signals provide almost no useful evidence and one that leans toward AI or human writing but does not meet the threshold for a clear classification.

For example, an `ai_score` of 0.62 leans toward AI-generated text, but it does not meet the threshold for the AI label. The system returns `uncertain` rather than presenting that result as a likely AI classification.

**Important caveat:** these scores are not calibrated probabilities. A confidence of 0.90 does not mean the system is right 90% of the time, because I have not measured that. I will say this clearly in the README.

### Thresholds

| `ai_score` range | Attribution | Label variant |
|---|---|---|
| >= 0.80 (and the agreement guard passes) | `likely_ai` | High-confidence AI |
| 0.36 to 0.79 | `uncertain` | Uncertain |
| <= 0.35 | `likely_human` | High-confidence human |

### Why these thresholds

- The uncertain band is intentionally wide (0.44 of the range) and **asymmetric**. The AI threshold sits 0.30 above the midpoint, while the human threshold sits 0.15 below it. Labeling someone's work as AI requires much stronger evidence than labeling it human, which builds the false-positive asymmetry into the numbers.
- Raw signals are not calibrated probabilities, so these thresholds are starting points. I will adjust them after testing the reference inputs and record any change in the README spec reflection.

### How I will test that scores are meaningful

I will test the system using four different types of text: clearly AI-generated, clearly human-written, formal human writing, and lightly edited AI-generated text. I will also test the edge cases listed in section 5. For each input, I will record the individual signal scores, the combined score, and the final attribution to see whether the system distinguishes between different types of writing.

Pass criteria:

- Clearly AI text scores above 0.70 and clearly human text scores below 0.40.
- The two borderline inputs land in the uncertain band.
- Scores are not constant across inputs, and the spread between the clear AI and clear human examples is at least 0.30.

---

## 3. Transparency label variants

The labels need to make sense to someone who has not seen the system's internal scores or technical details. Each label explains the result without making it sound like the system can prove who wrote the text. The AI label also explains that the creator can appeal, while the uncertain label makes it clear that the system has not reached a conclusion.

**High-confidence AI** (`likely_ai`, `ai_score >= 0.80`)

> "Our automated analysis suggests this work was likely created with AI assistance. This is an estimate, not a certainty. If you are the creator and this is wrong, you can appeal and a person will review it."

**High-confidence human** (`likely_human`, `ai_score <= 0.35`)

> "Our automated analysis suggests this work was likely written by a person. This is an estimate, not a guarantee."

**Uncertain** (`uncertain`, `0.36 <= ai_score <= 0.79`)

> "We could not tell whether this work was written by a person or created with AI. Our checks gave mixed or weak results, so no verdict has been applied. If you are the creator, you can request a review."

The API response also returns the numeric `ai_score` and `confidence` for the platform to use however it likes, but the label text above is what a reader sees.

**Label testing plan:** I will show the three labels to someone who has not worked on this project and ask them what they think each label means and how certain they believe the system is. If they misunderstand any of the labels, I will revise the wording and document the changes in the README.

---

## 4. Appeals workflow

Creators should have a way to challenge a classification if they believe the system has incorrectly labeled their work. For this project, a creator can submit an appeal with an explanation of why they disagree with the result.

There is no real authentication system in this version, so the API uses `creator_id` to check whether the appeal comes from the creator associated with the original submission. In a real application, this check would be tied to the creator's logged-in account.

### What the creator provides

The creator sends a `POST /appeal` request containing:

- `content_id` to identify the original submission.
- `creator_id` to identify the creator (must match the submission).
- `creator_reasoning` explaining why they believe the classification is incorrect (required, non-empty, up to 2000 characters).

### What the system does

1. Validates the fields. Returns 400 if fields are missing, 404 if the `content_id` is unknown, 403 if `creator_id` does not match, and 409 if the content is already `under_review`.
2. Changes the content `status` from `classified` to `under_review`.
3. Stores `appeal_reasoning` and `appeal_timestamp` on the same record as the original decision, so the original `attribution`, `ai_score`, `llm_score`, and `stylo_score` are preserved and never overwritten.
4. Appends a separate audit log event of type `appeal` that repeats the original decision fields, so the appeal is logged next to the decision it contests.
5. Returns a confirmation JSON including `content_id`, the new `status`, and a message that the appeal was received.

Automated re-classification is not performed. The response includes a `display_note` so the platform can show that the content is under review.

### What a human reviewer would see in the queue

For each appeal: `content_id`, `creator_id`, the original text, the original attribution and confidence, both signal scores, the LLM's one-sentence reasoning, the creator's reasoning, and the submission and appeal timestamps. Appeals are listed oldest first. Possible outcomes (defined but not built): `upheld`, `overturned`, `inconclusive`.

### Status values

`classified`, then `under_review`. Reserved for future use: `review_upheld`, `review_overturned`.

---

## 5. Anticipated edge cases

The system will not classify every type of writing correctly. Some writing styles naturally share patterns that the detection signals associate with AI-generated text, while editing and formatting can make AI-generated text harder to recognize. These are the cases I want to consider when designing and testing the system.

1. **Repetitive, simple-vocabulary poetry.** A poem built on repeated lines and short, similar-length lines has very low sentence-length variation and almost no informal markers, so the stylometric score will read AI-like. The LLM may also find it generic. Expected failure: a false positive. Mitigation: the agreement guard and the wide uncertain band mean the poem will most likely land in `uncertain`, and the creator can appeal.
2. **Formal academic prose or a non-native speaker's careful writing.** Long, evenly structured sentences, no informal punctuation, and abstract vocabulary push both signals toward AI. This is the case where both signals agree, so the agreement guard does not help. Expected failure: a false positive, and a bias risk against non-native English writers. Mitigation: this is the strongest reason the appeal path exists. It is documented as a known limitation.
3. **Very short text (10 to 39 words), such as a haiku or a two-line micro-story.** Sentence variance and phrase density are statistically meaningless on a few sentences. Mitigation: reduced stylometric weight and the 10-word minimum.
4. **Lightly edited AI output.** A person who rewrites a few sentences in a casual voice adds informal markers and varies sentence lengths, which lowers the stylometric score while the content remains AI-authored. Expected failure: a false negative, or an `uncertain` result. I accept this because false negatives are the cheaper error.
5. **Heavily formatted text (lists, dialogue-heavy fiction, code blocks).** Sentence splitting and punctuation counts behave unpredictably. Not handled specially in this version.

---

## 6. API contract

### `POST /submit`

Rate limited. Request:

```json
{"text": "string, 10+ words", "creator_id": "string"}
```

Response 200:

```json
{
  "content_id": "uuid",
  "creator_id": "string",
  "attribution": "likely_ai | uncertain | likely_human",
  "ai_score": 0.0,
  "confidence": 0.0,
  "signals": {"llm_score": 0.0, "stylo_score": 0.0},
  "label": "full label text",
  "status": "classified"
}
```

Errors: 400 (missing fields, text under 10 words), 429 (rate limit exceeded).

Note: Flask-Limiter counts a request toward the limit even if the handler later returns 400, so any rate-limit test must use text of at least 10 words.

### `POST /appeal`

Request: `{"content_id": "...", "creator_id": "...", "creator_reasoning": "..."}`.
Response 200: `{"content_id": "...", "status": "under_review", "message": "Appeal received."}`.
Errors: 400, 403, 404, 409 as described in section 4.

### `GET /log`

Returns `{"entries": [ ... ]}`, most recent first. Optional `?limit=N` (default 50).

### Audit log schema (SQLite, table `audit_log`)

`content_id`, `creator_id`, `timestamp` (ISO 8601 UTC), `event_type` (`submission` or `appeal`), `attribution`, `ai_score`, `confidence`, `llm_score`, `stylo_score`, `llm_reasoning`, `status`, `appeal_reasoning`, `appeal_timestamp`, `notes`.

Each submission writes one row. An appeal updates the status on the content's record and also writes a separate `appeal` event row that repeats the original decision fields. The original scores are never overwritten.

### Rate limiting (planned)

`10 per minute;100 per day` per IP on `/submit`, using Flask-Limiter with `storage_uri="memory://"`.

- A real writer submits a handful of pieces per session, so 10 per minute is generous and still stops scripts.
- 100 per day caps a determined adversary probing the classifier for evasion, and keeps usage within the Groq free tier.
- `/appeal` gets a looser limit of `5 per minute` to prevent spam without blocking genuine use.

---

## Architecture

```
SUBMIT FLOW

Client
  |  POST /submit {text, creator_id}
  v
Rate Limiter (10/min; 100/day)  --- exceeded --> 429
  |  validated request
  v
Input validation (fields present, >= 10 words)  --- invalid --> 400
  |  raw text
  +-----------------------------+
  v                             v
Signal 1: Groq LLM        Signal 2: Stylometrics
  |  llm_score (0-1)          |  stylo_score (0-1)
  +-------------+---------------+
                v
Confidence Scorer
  (weighted blend, disagreement penalty, agreement guard)
                |  ai_score, confidence, attribution
                v
Label Generator
                |  label text
                v
Audit Log (SQLite)  <-- writes: content_id, creator_id, timestamp,
                |       attribution, ai_score, llm_score, stylo_score,
                |       status = "classified"
                v
Response {content_id, attribution, ai_score, confidence, signals, label, status}


APPEAL FLOW

Client
  |  POST /appeal {content_id, creator_id, creator_reasoning}
  v
Validation + lookup in Audit Log  --- missing/unknown/mismatch/duplicate --> 400/404/403/409
  |  original decision record
  v
Status update: classified -> under_review
  |  appeal_reasoning, appeal_timestamp
  v
Audit Log (appeal event row + original decision fields preserved)
  |
  v
Response {content_id, status: "under_review", message}


REVIEWER VIEW (not built; defined for completeness)
GET /log filtered to status = under_review --> original text, scores, reasoning
```

**Narrative.** When someone submits text, the API first checks the request and applies the rate limit. The text then goes through both detection signals, and the scorer combines their results to determine the attribution, confidence, and transparency label. The system saves the decision in the audit log before returning the response.

When a creator submits an appeal, the API finds the original submission, checks the creator ID, and changes the status to `under_review`. It records the appeal alongside the original decision without replacing the original scores, so the classification and the reason for the appeal can both be reviewed later.

---

## AI Tool Plan

I will use AI assistance to help implement the project, but I will use this document to guide what gets generated. For each milestone, I will provide the relevant sections of the spec, ask for specific functions or endpoints, and check the generated code against my requirements before using it. I will test each part before moving on to the next milestone and commit changes after verifying that they work.

### M3: Submission endpoint and first signal

- **Give the AI:** Section 1 (Signal 1 only), Section 6 (API contract and audit log schema), and the Architecture diagram.
- **Ask for:** a Flask app skeleton with `POST /submit` and `GET /log`, a `llm_signal(text)` function that calls Groq and returns a clamped float or `None`, and SQLite helpers (`init_db`, `write_log`, `get_log`). Use placeholder confidence and label.
- **Verify:**
  1. Call `llm_signal()` directly in a Python shell on the AI sample, the human sample, and a borderline sample. Confirm it returns a float between 0 and 1 and returns `None` when the API key is bad.
  2. Run the app and send the curl example. Confirm `content_id` appears in the response and a log row exists.
  3. Check that the function signature and response fields match Section 6.

### M4: Second signal and confidence scoring

- **Give the AI:** Section 1 (Signal 2 table and combination rules), Section 2 (all thresholds), and the Architecture diagram.
- **Ask for:** `stylometric_signal(text)` implementing the three metrics with the exact normalization ranges in the table, and `score(llm, stylo, word_count)` returning `(ai_score, confidence, attribution)` that implements the weights, disagreement penalty, `None` handling, and agreement guard.
- **Verify:**
  1. Check the generated constants against this document line by line: weights 0.6/0.4 and 0.75/0.25, disagreement limit 0.40, thresholds 0.80 and 0.35, agreement guard 0.60 on both signals.
  2. Unit-test `score()` with hand-picked inputs such as `(0.9, 0.9)`, `(0.9, 0.2)`, `(0.2, 0.2)`, and `(None, 0.8)` and confirm each lands in the expected band.
  3. Run the four reference inputs and the two edge cases (repetitive poem, short text). Print both signal scores separately. Confirm clearly AI and clearly human text are at least 0.30 apart.
  4. Update the audit log to store both signal scores and the combined score.

### M5: Production layer

- **Give the AI:** Section 3 (label variants), Section 4 (appeals workflow), Section 6 (rate limits and log schema), and the Architecture diagram.
- **Ask for:** `generate_label(attribution)` returning the three exact label texts, the `POST /appeal` endpoint with the specified status codes and logging behavior, and the Flask-Limiter configuration with the chosen limits.
- **Verify:**
  1. Print all three label strings and compare them against Section 3 character for character.
  2. Submit inputs that reach all three variants.
  3. File an appeal with a real `content_id` and confirm `GET /log` shows `under_review`, `appeal_reasoning`, and the original scores unchanged. Test the 400, 403, 404, and 409 paths.
  4. Run the 12-request rate-limit loop using text of at least 10 words, and confirm ten 200s followed by 429s. Save the output for the README.
  5. Confirm at least 3 log entries exist, including one appeal.

### Stretch features

I have not started any stretch features yet. Before implementing one, I will update this document with the design and explain how the new feature will fit into the existing system. My first choice would be to add a third detection signal based on repetition and n-gram overlap, with its contribution to the final score documented before implementation.
