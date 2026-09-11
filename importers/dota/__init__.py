"""Importer benchmarku DOTA (OBB) → kanoniczny projekt GeoTile Label."""

from .parse import collect_class_names, parse_dota_txt, to_canonical

__all__ = ["parse_dota_txt", "collect_class_names", "to_canonical"]
