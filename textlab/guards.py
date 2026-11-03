"""Conservative local invariants; semantic equivalence still needs human review."""
from collections import Counter
from difflib import SequenceMatcher
import re


def protected_fragments(text: str) -> Counter:
    patterns = [
        r"(?<![\d.])[+-]?(?:\d+(?:[.,:/-]\d+)*|\.\d+)(?:[eE][+-]?\d+)?(?:\s*[%％])?",
        r'"[^"\n]+"|“[^”]+”|‘[^’]+’|「[^」]+」|『[^』]+』',
        r"(?<!\w)'[^'\n]+'(?!\w)",
        r"https?://[^\s<>]+",
        r"\[[^\]\n]{1,200}\]",
        r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,}(?![A-Za-z0-9])",
    ]
    return Counter(fragment for pattern in patterns for fragment in re.findall(pattern, text))


def preservation_errors(original: str, candidate: str, terms: list[str]) -> list[str]:
    errors = []
    if protected_fragments(original) != protected_fragments(candidate):
        errors.append("수치·인용문·URL·인용 번호·대문자 약어의 변경 또는 추가")
    for term in terms:
        if original.count(term) != candidate.count(term):
            errors.append(f"보호 용어 변경: {term}")
    # A bibliography is an immutable tail rather than material for rewriting.
    match = re.search(r"(?im)^\s*(?:#{1,6}\s*)?(?:참고\s*문헌|references|bibliography)\s*:?\s*$", original)
    if match and not candidate.endswith(original[match.start():]):
        errors.append("참고문헌 변경")
    return errors


def change_ratio(before: str, after: str) -> float:
    return 1 - SequenceMatcher(None, before, after, autojunk=True).ratio()


def analyze(text: str) -> dict:
    words = re.findall(r"\w+", text)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", text) if s.strip()]
    counts = Counter(words)
    return {
        "characters": len(text),
        "words": len(words),
        "sentences": len(sentences),
        "mean_sentence_length": round(sum(map(len, sentences)) / max(1, len(sentences)), 2),
        "vocabulary_diversity": round(len(counts) / max(1, len(words)), 3),
        "repeated_words": {word: n for word, n in counts.most_common(12) if n > 1},
        "note": "단순 통계입니다. 문법·전문성·문맥의 정확성은 모델 검토와 사용자 확인이 필요합니다.",
    }
