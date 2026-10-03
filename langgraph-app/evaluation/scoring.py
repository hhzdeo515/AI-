"""Deterministic grading with explicit denominators and unavailable metrics."""
import itertools
import re
import unicodedata


def choices(text):
    text = unicodedata.normalize("NFKC", str(text)).strip().upper()
    text = re.sub(r"^(?:答案|选项|选择|ANSWER)\s*[:：]?\s*", "", text)
    # Parse only the leading selection, never letters buried in explanations.
    m = re.match(r"^([A-H](?:(?:\s*[,、，/&和及]\s*|\s+|(?=[A-H]))[A-H])*)(?=[^A-Z]|$)", text)
    return sorted(set(re.findall(r"[A-H]", m[1]))) if m else []


def grade_exam(output, gold):
    eligible = gold.get("status") in ("official", "verified")
    answerable = output.get("answerable", False) is True
    if gold["type"] in ("single", "multiple"):
        expected = sorted(gold["answers"])
        predicted = choices(output.get("answer", output.get("candidate", "")))
        return {"eligible": eligible, "answered": answerable and bool(predicted),
                "correct": bool(answerable and predicted == expected) if eligible else None,
                "predicted": predicted, "expected": expected}
    if gold["type"] == "short":
        # Keyword coverage is a diagnostic, not a semantic or official grade.
        text = output.get("answer", "") + " " + output.get("explanation", "")
        points = gold.get("points", [])
        hits = [p for p in points if any(k in text for k in p.get("keywords", []))]
        return {"eligible": False, "answered": answerable, "correct": None,
                "keyword_coverage": sum(p["weight"] for p in hits) / sum(p["weight"] for p in points) if points else None,
                "semantic_score": None, "unsupported_claims": None,
                "missing_points": [p["text"] for p in points if p not in hits], "status": "requires_semantic_review"}
    raise ValueError("Unknown answer type")


def edit_distance(a, b):
    prev = list(range(len(b)+1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(cur[-1]+1, prev[j]+1, prev[j-1]+(x != y)))
        prev = cur
    return prev[-1]


def error_rate(reference, hypothesis, language):
    def normalize(s):
        s = unicodedata.normalize("NFKC", s).lower()
        return list(re.sub(r"[^\w]", "", s)) if language == "zh" else re.findall(r"\w+", s)
    ref, hyp = normalize(reference), normalize(hypothesis)
    return edit_distance(ref, hyp) / len(ref) if ref else None


def diarization_error(reference, hypothesis, duration, step=.02):
    """20ms frame DER, zero collar, overlap included; optimal speaker permutation."""
    ref_ids = sorted({str(x["speaker"]) for x in reference})
    hyp_ids = sorted({str(x["speaker"]) for x in hypothesis})
    if not ref_ids or not hyp_ids or max(len(ref_ids), len(hyp_ids)) > 8:
        return None
    import numpy as np
    frames = int(duration / step)
    r = np.zeros((len(ref_ids), frames), dtype=bool)
    h = np.zeros((len(hyp_ids), frames), dtype=bool)
    for rows, matrix, ids in ((reference, r, ref_ids), (hypothesis, h, hyp_ids)):
        for x in rows:
            begin = max(0, int(x["begin_ms"]/1000/step))
            end = min(frames, int(x["end_ms"]/1000/step))
            matrix[ids.index(str(x["speaker"])), begin:end] = True
    overlaps = r.astype(int) @ h.astype(int).T
    n = max(len(ref_ids), len(hyp_ids))
    best = max(sum(overlaps[i,j] for i,j in enumerate(p) if i < len(ref_ids) and j < len(hyp_ids)) for p in itertools.permutations(range(n)))
    denominator = int(r.sum())
    return (int(np.maximum(r.sum(axis=0), h.sum(axis=0)).sum())-best)/denominator if denominator else None


def aggregate(rows):
    eligible = [r for r in rows if r.get("eligible")]
    answered = [r for r in eligible if r["answered"]]
    correct = sum(r["correct"] for r in eligible)
    return {"eligible": len(eligible), "correct": correct, "answered": len(answered),
            "accuracy": correct/len(eligible) if eligible else None,
            "coverage": len(answered)/len(eligible) if eligible else None,
            "answered_accuracy": correct/len(answered) if answered else None}
