"""Consulta del catálogo de productos (empresa/catalogo.yaml)."""

from __future__ import annotations

import unicodedata
from decimal import Decimal

from pydantic import BaseModel

from assistant.company.models import Catalog


class CatalogItem(BaseModel):
    id: str
    nombre: str
    categoria: str
    precio: Decimal
    moneda: str


class ProductCatalog:
    def __init__(self, catalog: Catalog) -> None:
        self._items = [
            CatalogItem(
                id=p.id,
                nombre=p.nombre,
                categoria=p.categoria,
                precio=p.precio,
                moneda=catalog.moneda,
            )
            for p in catalog.productos
        ]

    def search(self, query: str | None = None) -> list[CatalogItem]:
        """Sin búsqueda devuelve todo; con búsqueda filtra por id, nombre o categoría."""
        words = _plain(query or "").split()
        return [
            item
            for item in self._items
            if all(w in _plain(f"{item.id} {item.nombre} {item.categoria}") for w in words)
        ]


def _plain(text: str) -> str:
    # Sin acentos ni mayúsculas, y "laptops" encuentra "laptop": singulariza lo simple.
    ascii_text = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return " ".join(word.removesuffix("s") for word in ascii_text.split())
