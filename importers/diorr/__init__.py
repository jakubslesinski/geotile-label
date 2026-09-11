"""Importer benchmarku DIOR-R (OBB) → kanoniczny projekt GeoTile Label."""

from .parse import collect_class_names, parse_diorr_xml, to_canonical

__all__ = ["parse_diorr_xml", "collect_class_names", "to_canonical"]
