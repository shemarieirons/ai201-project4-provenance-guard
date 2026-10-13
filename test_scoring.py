"""Run with: python test_scoring.py

Part 1 tests score() offline with hand-picked inputs (no API calls).
Part 2 runs the reference texts through all three real signals and prints a table
you can paste into your README.
"""
from app import llm_signal, pattern_signal, score, stylometric_signal

# ---------------------------------------------------------------- part 1
print("PART 1: score() unit tests (llm, stylo, pattern, word_count)")
cases = [
    # (llm, stylo, pattern, words, expected attribution, expected note fragment)
    (0.9, 0.9, 0.9, 60, "likely_ai", None),
    (0.9, 0.2, 0.2, 60, "uncertain", "signals_disagree"),         # spread 0.7 > 0.5
    (0.2, 0.2, 0.2, 60, "likely_human", None),
    (None, 0.8, 0.8, 60, "uncertain", "llm_unavailable"),         # LLM failed
    (0.97, 0.58, 0.9, 60, "uncertain", "ai_guard_downgrade"),     # stylo below 0.60
    (0.95, 0.9, 0.48, 60, "uncertain", "ai_guard_downgrade"),     # pattern below 0.50
    (0.97, 0.9, 0.4, 60, "uncertain", "signals_disagree"),        # spread 0.57 > 0.5
    (0.6, 0.6, 0.6, 60, "uncertain", None),                       # mid scores
    (0.9, 0.9, 0.9, 30, "likely_ai", "short_text_weights"),       # short text weights
]
ok = True
for llm, stylo, pattern, words, expected, note_frag in cases:
    ai, conf, attr, notes = score(llm, stylo, pattern, words)
    good = attr == expected and (note_frag is None or (notes and note_frag in notes))
    if not good:
        ok = False
    flag = "PASS" if good else "FAIL"
    print(f"  {flag}  llm={llm} stylo={stylo} pattern={pattern} w={words} "
          f"-> ai={ai} conf={conf} {attr} ({notes})")
print("  All unit tests passed.\n" if ok else "  SOME TESTS FAILED.\n")

# ---------------------------------------------------------------- part 2
TEXTS = {
    "Clearly AI": (
        "Artificial intelligence represents a transformative paradigm shift in modern society. "
        "It is important to note that while the benefits of AI are numerous, it is equally "
        "essential to consider the ethical implications. Furthermore, stakeholders across "
        "various sectors must collaborate to ensure responsible deployment."
    ),
    "Clearly human": (
        "ok so i finally tried that new ramen place downtown and honestly? "
        "underwhelming. the broth was fine but they put WAY too much sodium in it and "
        "i was thirsty for like three hours after. my friend got the spicy version and "
        "said it was better. probably won't go back unless someone drags me there"
    ),
    "Formal human": (
        "The relationship between monetary policy and asset price inflation has been "
        "extensively studied in the literature. Central banks face a fundamental tension "
        "between their mandate for price stability and the unintended consequences of "
        "prolonged low interest rates on equity and real estate valuations."
    ),
    "Lightly edited AI": (
        "I've been thinking a lot about remote work lately. There are genuine tradeoffs "
        "\u2014 flexibility and no commute on one side, isolation and blurred work-life "
        "boundaries on the other. Studies show productivity varies widely by individual "
        "and role type."
    ),
    "Repetitive poem": (
        "The rain falls down. The rain falls down.\n"
        "The rain falls down on the town.\n"
        "The sky is gray. The sky is gray.\n"
        "The sky is gray all day."
    ),
    "Very short": "She waited by the door. He never came back that night.",
}

print("PART 2: reference inputs (uses the Groq API)")
print(f"{'Input':<18} {'llm':>5} {'stylo':>6} {'pattern':>8} {'ai_score':>9} {'conf':>6}  attribution")
print("-" * 80)
for name, text in TEXTS.items():
    llm, _reason = llm_signal(text)
    stylo, _sd = stylometric_signal(text)
    pattern, pdetails = pattern_signal(text)
    ai, conf, attr, notes = score(llm, stylo, pattern, len(text.split()))
    llm_str = "None" if llm is None else f"{llm:.2f}"
    print(f"{name:<18} {llm_str:>5} {stylo:>6.2f} {pattern:>8.2f} {ai:>9.3f} {conf:>6.3f}  {attr}"
          + (f"  [{notes}]" if notes else ""))
    print(f"{'':<18} pattern metrics: {pdetails}")