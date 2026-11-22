"""Parte cada documento por encabezados y divide las secciones largas con traslape."""

from __future__ import annotations

import re
from dataclasses import dataclass

from assistant.company import Document

# El modelo trunca a 128 tokens (~600 caracteres en español); más texto no se "vería".
MAX_CHARS = 600
OVERLAP_CHARS = 100
INTRO_SECTION = "Introducción"

_HEADER = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Chunk:
    id: str
    archivo: str
    titulo: str
    seccion: str
    texto: str
    roles: frozenset[str]

    @property
    def embedding_text(self) -> str:
        # El título y la sección dan contexto al vector ("Política de Vacaciones > Días...").
        return f"{self.titulo} > {self.seccion}\n{self.texto}"


def chunk_document(
    document: Document, max_chars: int = MAX_CHARS, overlap: int = OVERLAP_CHARS
) -> list[Chunk]:
    content = _HTML_COMMENT.sub("", document.contenido)
    chunks: list[Chunk] = []
    for section, body in _sections(content):
        for piece in split_text(body, max_chars, overlap):
            chunks.append(
                Chunk(
                    id=f"{document.archivo}:{len(chunks)}",
                    archivo=document.archivo,
                    titulo=document.titulo,
                    seccion=section,
                    texto=piece,
                    roles=document.roles,
                )
            )
    return chunks


def _sections(content: str) -> list[tuple[str, str]]:
    headers = list(_HEADER.finditer(content))
    bounds = [(INTRO_SECTION, 0, headers[0].start() if headers else len(content))]
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(content)
        bounds.append((header.group(1), header.end(), end))
    sections = [(name, content[start:end].strip()) for name, start, end in bounds]
    return [(name, body) for name, body in sections if body]


def split_text(text: str, max_chars: int, overlap: int) -> list[str]:
    """Ventanas de hasta max_chars que se traslapan ~overlap caracteres, cortando en espacios."""
    if overlap >= max_chars:
        raise ValueError("overlap debe ser menor que max_chars")
    pieces: list[str] = []
    start = 0
    while True:
        end = min(start + max_chars, len(text))
        if end < len(text):
            space = text.rfind(" ", start + overlap + 1, end)
            if space != -1:
                end = space
        pieces.append(text[start:end].strip())
        if end >= len(text):
            return pieces
        start = end - overlap
        space = text.find(" ", start, end)
        if space != -1:
            start = space + 1
