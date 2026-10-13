"""Run with: python test_scoring.py

Part 1 tests score() offline with hand-picked inputs (no API calls).
Part 2 runs the reference texts through both real signals and prints a table
you can paste into your README.
"""
from app import llm_signal, score, stylometric_signal

# ---------------------------------------------------------------- part 1
print("PART 1: score() unit tests (llm, stylo, word_count)")
cases = [
    # (llm, stylo, words, expected attribution)
    (0.9, 0.9, 60, "likely_ai"),
    (0.9, 0.2, 60, "uncertain"),     # signals disagree
    (0.2, 0.2, 60, "likely_human"),
    (None, 0.8, 60, "uncertain"),    # LLM failed
    (0.95, 0.5, 60, "uncertain"),    # guard: stylo below 0.60, blocks AI label
    (0.6, 0.6, 60, "uncertain"),     # mid scores
    (0.97, 0.58, 60, "uncertain"),   # score >= 0.80 but stylo < 0.60, guard blocks AI label
]
ok = True
for llm, stylo, words, expected in cases:
    ai, conf, attr, notes = score(llm, stylo, words)
    flag = "PASS" if attr == expected else "FAIL"
    if attr != expected:
        ok = False
    print(f"  {flag}  llm={llm} stylo={stylo} -> ai={ai} conf={conf} {attr} ({notes})")
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
print(f"{'Input':<18} {'llm':>5} {'stylo':>6} {'ai_score':>9} {'conf':>6}  attribution")
print("-" * 70)
for name, text in TEXTS.items():
    llm, _reason = llm_signal(text)
    stylo, details = stylometric_signal(text)
    ai, conf, attr, notes = score(llm, stylo, len(text.split()))
    llm_str = "None" if llm is None else f"{llm:.2f}"
    print(f"{name:<18} {llm_str:>5} {stylo:>6.2f} {ai:>9.3f} {conf:>6.3f}  {attr}"
          + (f"  [{notes}]" if notes else ""))
    print(f"{'':<18} metrics: {details}")