"""Pascal VOC XML export — adapted from labelImg/libs/pascal_voc_io.py."""

from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree
import xml.dom.minidom

from services.export_yolo import copy_or_link


def write_voc_annotation(
    out_path: str | Path,
    image_filename: str,
    img_w: int,
    img_h: int,
    annotations: list[list[float]],
    class_names: dict[int, str],
    folder: str = "images",
) -> None:
    """Write a single Pascal VOC XML annotation file.

    annotations: list of [class_id, x_min, y_min, x_max, y_max]
    """
    top = Element("annotation")

    folder_el = SubElement(top, "folder")
    folder_el.text = folder

    filename_el = SubElement(top, "filename")
    filename_el.text = image_filename

    source = SubElement(top, "source")
    db = SubElement(source, "database")
    db.text = "GeoTile Label"

    size = SubElement(top, "size")
    SubElement(size, "width").text = str(img_w)
    SubElement(size, "height").text = str(img_h)
    SubElement(size, "depth").text = "3"

    SubElement(top, "segmented").text = "0"

    for ann in annotations:
        cls_id = int(ann[0])
        cls_name = class_names.get(cls_id, str(cls_id))
        x_min, y_min, x_max, y_max = ann[1], ann[2], ann[3], ann[4]

        obj = SubElement(top, "object")
        SubElement(obj, "name").text = cls_name
        SubElement(obj, "pose").text = "Unspecified"
        SubElement(obj, "truncated").text = "0"
        SubElement(obj, "difficult").text = "0"

        bndbox = SubElement(obj, "bndbox")
        SubElement(bndbox, "xmin").text = str(int(x_min))
        SubElement(bndbox, "ymin").text = str(int(y_min))
        SubElement(bndbox, "xmax").text = str(int(x_max))
        SubElement(bndbox, "ymax").text = str(int(y_max))

    # Pretty-print
    from xml.etree.ElementTree import tostring
    raw_xml = tostring(top, encoding="unicode")
    dom = xml.dom.minidom.parseString(raw_xml)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(dom.toprettyxml(indent="\t"))


def export_voc_dataset(
    tile_annotations: dict[str, list[list[float]]],
    dataset_dir: str | Path,
    tile_size: int,
    class_names: dict[int, str],
    *,
    output_dir: str | Path | None = None,
) -> Path:
    """Generate Pascal VOC XML files for all tiles in the dataset."""
    dataset_dir = Path(dataset_dir)
    target_dir = Path(output_dir) if output_dir else dataset_dir

    for split in ("train", "val", "test"):
        images_dir = dataset_dir / split / "images"
        labels_dir = target_dir / split / ("annotations" if output_dir else "labels")
        output_images = target_dir / split / "images"
        if not images_dir.exists():
            continue
        labels_dir.mkdir(parents=True, exist_ok=True)
        if output_dir:
            output_images.mkdir(parents=True, exist_ok=True)

        for img_path in sorted(images_dir.glob("*.png")):
            if output_dir:
                copy_or_link(img_path, output_images / img_path.name)
            anns = tile_annotations.get(img_path.name, [])
            xml_path = labels_dir / img_path.name.replace(".png", ".xml")
            write_voc_annotation(
                xml_path, img_path.name, tile_size, tile_size, anns, class_names
            )

    return target_dir
