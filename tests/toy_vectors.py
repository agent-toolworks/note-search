"""Toy embeddings for tests: each dimension is a concept, and a text points
along the concepts its words belong to. Words of one concept share no
letters, so only "meaning" can bring them together."""
import re

CONCEPTS = [
    {"bill", "billed", "billing", "payment", "payments", "card", "charge", "charges", "pay"},
    {"logout", "logged", "signed", "kicked", "session", "sessions", "login"},
    {"compost", "pile", "heap", "turn", "stir", "mix"},
    {"disk", "storage", "space", "full", "exhausted"},
]


def vector(text: str) -> list:
    words = re.findall(r"[a-z]+", text.lower())
    v = [0.0] * (len(CONCEPTS) + 1)
    for w in words:
        for i, c in enumerate(CONCEPTS):
            if w in c:
                v[i] += 1.0
    v[-1] = 0.1  # every text a little alike, as real models make them
    return v
