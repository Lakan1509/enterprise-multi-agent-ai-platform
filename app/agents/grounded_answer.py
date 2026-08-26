import re


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "does",
    "for",
    "is",
    "of",
    "on",
    "the",
    "to",
    "what",
}


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if token not in STOPWORDS
    }


def _sentences(text: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if sentence.strip()
    ]


def grounded_answer_agent(
    query: str,
    retrieved_context: list[dict],
) -> str:
    """
    Deterministic extractive answer for simple enterprise lookups.

    Selects the sentence with the strongest lexical overlap with the
    user's query and attaches the exact retrieved-source citation.
    """

    if not retrieved_context:
        return (
            "I could not find enough information in the internal "
            "knowledge base to answer this question."
        )

    query_tokens = _tokens(query)

    candidates: list[tuple[float, str, str]] = []

    for item in retrieved_context:
        citation = (
            f"[{item['document_id']}:{item['chunk_id']}]"
        )

        for sentence in _sentences(item["text"]):
            sentence_tokens = _tokens(sentence)

            overlap = len(
                query_tokens.intersection(sentence_tokens)
            )

            if overlap == 0:
                continue

            score = overlap / max(len(query_tokens), 1)

            candidates.append(
                (
                    score,
                    sentence,
                    citation,
                )
            )

    if not candidates:
        return (
            "I could not find enough information in the internal "
            "knowledge base to answer this question."
        )

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    _, sentence, citation = candidates[0]

    return f"{sentence} {citation}"


def normalize_citations(
    answer: str,
    retrieved_context: list[dict],
) -> str:
    """
    Normalize citations when exactly one source chunk is available.
    """

    if not answer.strip() or not retrieved_context:
        return answer

    allowed = {
        f"[{item['document_id']}:{item['chunk_id']}]"
        for item in retrieved_context
    }

    citations = re.findall(
        r"\[[^\[\]]+:\d+\]",
        answer,
    )

    if len(allowed) != 1:
        return answer

    valid_citation = next(iter(allowed))

    if not citations:
        return f"{answer.rstrip()} {valid_citation}"

    normalized = answer

    for citation in citations:
        if citation not in allowed:
            normalized = normalized.replace(
                citation,
                valid_citation,
            )

    return normalized
