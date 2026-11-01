"""Check retrieval quality (and optionally full answers) against questions with known right answers.

Run from backend/:
    python scripts/eval_rag.py                  # retrieval only: cheap, one embedding call per question
    python scripts/eval_rag.py --answers        # also print the LLM's answer with and without RAG
    python scripts/eval_rag.py --answers -q "Can police arrest me without telling me why?"
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402
from app.services import llm, rag  # noqa: E402

# (question, acceptable Article numbers). Expected Articles were checked against the Constitution text.
# An empty set means "nothing in the Constitution answers this": no passage should be returned.
CASES = [
    ("Can police arrest me without telling me the reason?", {"22"}),
    ("Is education a fundamental right for children?", {"21A", "45"}),
    ("Can the government take my land without paying me?", {"300A", "31A", "31"}),
    ("Which court can I go to if my fundamental rights are violated?", {"32", "226"}),
    ("Is untouchability allowed in India?", {"17"}),
    ("Do I have freedom of speech, and what are its limits?", {"19"}),
    ("Can I be punished twice for the same offence?", {"20"}),
    ("Can the police force me to testify against myself?", {"20"}),
    ("Can children work in factories?", {"24"}),
    ("Am I free to follow and preach any religion?", {"25"}),
    ("What does equality before the law mean?", {"14"}),
    ("Can someone force me to do bonded labour or trafficking?", {"23"}),
    ("Can the state discriminate against me because of my caste or religion?", {"15"}),
    ("Is there reservation or equal opportunity in government jobs?", {"16"}),
    ("When can the President declare a national emergency?", {"352", "356", "360"}),
    ("What is the right to life and personal liberty?", {"21"}),
    ("Who can be elected President of India and at what age?", {"58"}),
    # Out of scope for the Constitution: the LLM should answer these on its own.
    ("hi", set()),
    ("thanks a lot", set()),
    ("what is the recipe for biryani", set()),
    ("my landlord is not returning my security deposit, what can I do", set()),
]


def retrieval_report(cases) -> None:
    hits = misses = 0
    for q, expected in cases:
        found = rag.retrieve(q)
        top = [(p["number"], round(p["score"], 2)) for p in found]
        if expected:
            ok = any(n in expected for n, _ in top)
            rank = next((i + 1 for i, (n, _) in enumerate(top) if n in expected), None)
            label = f"PASS (rank {rank})" if ok else "MISS"
        else:
            ok = not found
            label = "PASS (no context)" if ok else "NOTE: got context"
        hits += ok
        misses += not ok
        print(f"{label:20} {q[:62]:62} -> {top}")
        time.sleep(0.3)
    print(f"\n{hits}/{hits + misses} as expected  (RAG_MIN_SCORE={config.RAG_MIN_SCORE}, RAG_TOP_K={config.RAG_TOP_K})")


def ask(q: str, passages: list[dict] | None) -> str:
    """generate_answer with a pause between calls and a patient retry if the API says it is busy (429)."""
    for _ in range(5):
        try:
            time.sleep(4)
            return llm.generate_answer(q, [], passages)
        except llm.LLMError as e:
            print(f"  ({e} waiting 30s)")
            time.sleep(30)
    return "<gave up>"


def answers_report(questions: list[str]) -> None:
    for q in questions:
        passages = rag.retrieve(q)
        print("=" * 78 + f"\nQ: {q}\nretrieved: {[(p['number'], round(p['score'], 2)) for p in passages]}")
        print("\n--- WITH RAG" if passages else "\n--- (no passages: plain LLM)")
        print(ask(q, passages))
        if passages:
            print("\n--- WITHOUT RAG (old behaviour)")
            print(ask(q, None))
        print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--answers", action="store_true", help="also generate full LLM answers")
    ap.add_argument("-q", "--question", action="append", help="use your own question(s) instead of the built-in set")
    args = ap.parse_args()
    if args.question:
        answers_report(args.question)
    else:
        retrieval_report(CASES)
        if args.answers:
            answers_report([c[0] for c in CASES[:5]] + ["hi", "my landlord is not returning my security deposit, what can I do"])
