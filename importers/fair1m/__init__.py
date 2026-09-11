"""Importer benchmarku FAIR1M → kanoniczny projekt GeoTile Label."""

from .geometry import polygon_bbox, rotated_bbox_from_polygon, rounded_polygon
from .parse import collect_class_names, parse_fair1m_xml, to_canonical

__all__ = [
    "parse_fair1m_xml",
    "collect_class_names",
    "to_canonical",
    "rotated_bbox_from_polygon",
    "polygon_bbox",
    "rounded_polygon",
]
