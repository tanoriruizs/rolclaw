from itertools import pairwise

import pytest

from assistant.company import Company, Document
from assistant.rag.chunking import INTRO_SECTION, chunk_document, split_text


def _document(contenido: str) -> Document:
    return Document(archivo="doc.md", titulo="Doc", roles=frozenset({"rh"}), contenido=contenido)


def test_splits_by_headers_and_keeps_metadata() -> None:
    chunks = chunk_document(_document("> Nota\n\n## 1. Uno\nTexto uno\n\n## 2. Dos\nTexto dos"))

    assert [(c.seccion, c.texto) for c in chunks] == [
        (INTRO_SECTION, "> Nota"),
        ("1. Uno", "Texto uno"),
        ("2. Dos", "Texto dos"),
    ]
    assert {c.id for c in chunks} == {"doc.md:0", "doc.md:1", "doc.md:2"}
    assert all(c.roles == {"rh"} and c.titulo == "Doc" for c in chunks)
    assert chunks[1].embedding_text == "Doc > 1. Uno\nTexto uno"


def test_document_without_headers_is_one_chunk() -> None:
    chunks = chunk_document(_document("Solo texto."))

    assert [(c.seccion, c.texto) for c in chunks] == [(INTRO_SECTION, "Solo texto.")]


def test_html_comments_and_empty_sections_are_dropped() -> None:
    chunks = chunk_document(_document("## A\n<!-- nota interna -->\nVisible\n\n## B\n"))

    assert [(c.seccion, c.texto) for c in chunks] == [("A", "Visible")]


def test_long_text_is_split_with_overlap_and_without_losing_words() -> None:
    words = [f"palabra{i}" for i in range(300)]
    pieces = split_text(" ".join(words), max_chars=200, overlap=40)

    assert len(pieces) > 1
    assert all(len(p) <= 200 for p in pieces)
    for previous, following in pairwise(pieces):
        assert previous.split()[-1] in following.split()[:6]  # hay traslape
    assert {w for p in pieces for w in p.split()} == set(words)


def test_text_without_spaces_is_still_split() -> None:
    pieces = split_text("x" * 250, max_chars=100, overlap=20)

    assert all(len(p) <= 100 for p in pieces)
    assert "".join(p[20:] if i else p for i, p in enumerate(pieces)) == "x" * 250


def test_overlap_must_be_smaller_than_window() -> None:
    with pytest.raises(ValueError, match="overlap"):
        split_text("texto", max_chars=10, overlap=10)


def test_real_documents_fit_the_model_window(company: Company) -> None:
    for document in company.documentos:
        assert all(len(c.texto) <= 600 for c in chunk_document(document))
