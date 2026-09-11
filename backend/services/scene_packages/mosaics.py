"""Kontrola geometrii mozaiki wirtualnej (DESIGN_DECISIONS.md, scene-import P1.4).

Do tej pory zgodnosc czesci sprawdzal `working_view._validate_mosaic_parts()`: porownywal
`str(src.crs)`, krotke rozdzielczosci i wyrownanie do siatki, a przy pierwszej roznicy rzucal
`ValueError`. Ma to trzy wady, ktore wymienia zakres P1.4:

1. **CRS porownywany jako napis.** `EPSG:32634` i rownowazny WKT to ten sam uklad, ale rozne
   napisy. Odwrotnie, dwa rozne uklady moga miec ten sam `str()` po roznych sciezkach zapisu.
   Rownosc `rasterio.crs.CRS` jest semantyczna i to ona ma tu decydowac.
2. **Rozdzielczosc porownywana po zaokragleniu do 12 miejsc.** Dla GSD rzedu 0,5 m to w praktyce
   porownanie doslowne; dostawy z zapisem zmiennoprzecinkowym roznia sie w ostatnich bitach.
   Uzywamy tolerancji WZGLEDNEJ.
3. **Brak wykrywania duplikatow, nakladek i luk.** Czesci moga byc parami zgodne co do siatki,
   a mimo to pokrycie ma dziure albo dwie czesci opisuja ten sam obszar. VRT zbuduje sie bez
   protestu, a scena bedzie niepelna lub niejednoznaczna.

Modul liczy pokrycie DOKLADNIE, a nie probkujac: wspolrzedne krawedzi czesci sa kompresowane
do siatki komorek (`xs` × `ys`), wiec kazda komorka jest w calosci pokryta przez ustalony
zbior czesci. Przy kilkunastu czesciach jest to kilkaset komorek — koszt pomijalny wobec
otwarcia rastrow.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Tolerancja wzgledna rozdzielczosci: rozne sciezki zapisu tej samej wartosci roznia sie
#: w ostatnich bitach mantysy.
RESOLUTION_REL_TOL = 1e-9
#: Tolerancja wyrownania do siatki, w pikselach.
GRID_TOL_PIXELS = 1e-6
#: Ile par nakladajacych sie czesci wymieniamy z nazwy.
MAX_REPORTED_PAIRS = 20
#: Od jakiego udzialu niepokrytej powierzchni ostrzegamy o BRZEGOWEJ luce. Poszarpany brzeg
#: dostawy tiled jest normalny (rzeczywista dostawa WV2 PAN: 0,05%); brak calego kafla to
#: kilkanascie procent. Prog rozdziela te dwa przypadki z duzym zapasem.
GAP_WARNING_SHARE = 0.01


@dataclass(frozen=True)
class PartGeometry:
    """Geometria jednej czesci wyrazona w pikselach siatki czesci odniesienia."""

    path: Path
    name: str
    width: int
    height: int
    col_off: float = 0.0
    row_off: float = 0.0

    @property
    def window(self) -> tuple[float, float, float, float]:
        return (self.col_off, self.row_off, self.col_off + self.width, self.row_off + self.height)


@dataclass(frozen=True)
class MosaicReport:
    """Wynik kontroli. `errors` blokuje budowe VRT, `warnings` nie."""

    parts: tuple[PartGeometry, ...] = ()
    errors: tuple[dict[str, str], ...] = ()
    warnings: tuple[dict[str, str], ...] = ()
    coverage: dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocking(self) -> bool:
        return bool(self.errors)

    @property
    def first_error(self) -> str:
        return self.errors[0]["message"] if self.errors else ""


def _open_geometry(path: Path):
    import rasterio

    with rasterio.open(path) as src:
        return {
            "crs": src.crs,
            "dtypes": tuple(src.dtypes),
            "count": int(src.count),
            "res": (abs(float(src.res[0])), abs(float(src.res[1]))),
            "transform": tuple(float(value) for value in src.transform[:6]),
            "width": int(src.width),
            "height": int(src.height),
        }


def _linear_terms(transform: tuple[float, ...]) -> tuple[float, float, float, float]:
    # Affine w kolejnosci (a, b, c, d, e, f); czlony liniowe to a, b, d, e.
    return (transform[0], transform[1], transform[3], transform[4])


def _cell_coverage(parts: tuple[PartGeometry, ...]) -> dict[str, Any]:
    """Policz dokladne pokrycie, nakladki i luki w prostokacie obejmujacym wszystkie czesci.

    Luki sa dzielone na BRZEGOWE i WEWNETRZNE. Rozroznienie jest konieczne, bo dostawa tiled
    ma poszarpany brzeg z natury: w rzeczywistej dostawie WV2 PAN kafle prawej kolumny maja
    szerokosc 10559, 10568 i 10574 pikseli, wiec prostokat obejmujacy zawiera 0,05% pikseli,
    ktorych nie pokrywa zadna czesc. Ostrzeganie o tym przy kazdej dostawie byloby szumem.
    Luka wewnetrzna — nieosiagalna z zewnatrz prostokata sciezka po komorkach niepokrytych —
    oznacza natomiast czesc, ktorej brakuje w srodku obrazu.
    """
    if not parts:
        return {
            "bounding_pixels": 0, "covered_pixels": 0, "gap_pixels": 0,
            "interior_gap_pixels": 0, "overlap_pixels": 0,
        }

    xs = sorted({value for part in parts for value in (part.window[0], part.window[2])})
    ys = sorted({value for part in parts for value in (part.window[1], part.window[3])})
    bounding = (xs[-1] - xs[0]) * (ys[-1] - ys[0])
    columns, rows = len(xs) - 1, len(ys) - 1
    covered_cells: list[list[bool]] = [[False] * rows for _ in range(columns)]
    covered = 0.0
    overlap = 0.0
    for x_index in range(columns):
        x0, x1 = xs[x_index], xs[x_index + 1]
        for y_index in range(rows):
            y0, y1 = ys[y_index], ys[y_index + 1]
            area = (x1 - x0) * (y1 - y0)
            if area <= 0:
                covered_cells[x_index][y_index] = True  # komorka zerowa nie jest luka
                continue
            hits = sum(
                1
                for part in parts
                if part.window[0] <= x0 and part.window[2] >= x1
                and part.window[1] <= y0 and part.window[3] >= y1
            )
            if hits:
                covered += area
                covered_cells[x_index][y_index] = True
            if hits > 1:
                # Powierzchnia policzona RAZ, niezaleznie od tego ile czesci ja pokrywa —
                # interesuje nas obszar sporny, a nie suma nadmiarowych odczytow.
                overlap += area

    # Zalanie od brzegu po komorkach niepokrytych: co zostanie nieodwiedzone, jest dziura.
    outside: set[tuple[int, int]] = set()
    stack = [
        (x_index, y_index)
        for x_index in range(columns)
        for y_index in range(rows)
        if (x_index in (0, columns - 1) or y_index in (0, rows - 1))
        and not covered_cells[x_index][y_index]
    ]
    while stack:
        cell = stack.pop()
        if cell in outside:
            continue
        outside.add(cell)
        x_index, y_index = cell
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x_index + dx, y_index + dy
            if 0 <= nx < columns and 0 <= ny < rows and not covered_cells[nx][ny]:
                if (nx, ny) not in outside:
                    stack.append((nx, ny))
    interior = 0.0
    for x_index in range(columns):
        for y_index in range(rows):
            if covered_cells[x_index][y_index] or (x_index, y_index) in outside:
                continue
            interior += (xs[x_index + 1] - xs[x_index]) * (ys[y_index + 1] - ys[y_index])

    return {
        "bounding_pixels": int(round(bounding)),
        "covered_pixels": int(round(covered)),
        "gap_pixels": int(round(bounding - covered)),
        "interior_gap_pixels": int(round(interior)),
        "overlap_pixels": int(round(overlap)),
    }


def _overlapping_pairs(parts: tuple[PartGeometry, ...]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for index, first in enumerate(parts):
        for second in parts[index + 1:]:
            left = max(first.window[0], second.window[0])
            right = min(first.window[2], second.window[2])
            top = max(first.window[1], second.window[1])
            bottom = min(first.window[3], second.window[3])
            if right > left and bottom > top:
                pairs.append((first.name, second.name))
    return pairs


def inspect_parts(paths: list[Path]) -> MosaicReport:
    """Sprawdz czesci mozaiki i opisz wynik zamiast przerywac na pierwszym problemie.

    Rozdzial na `errors` i `warnings` jest celowy: niezgodny CRS albo nieprzystajaca siatka
    czynia mozaike niemozliwa, natomiast nakladka lub luka to STAN POKRYCIA — dostawy tiled
    bywaja niepelne, a decyzja o imporcie takiego pokrycia nalezy do uzytkownika (ta sama
    zasada co `partial_delivery` w P0.6).
    """
    from affine import Affine

    if not paths:
        return MosaicReport(errors=({"code": "no_parts", "message": "At least one raster part is required"},))

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    parts: list[PartGeometry] = []

    reference: dict[str, Any] | None = None
    reference_transform: Any = None
    for path in paths:
        try:
            current = _open_geometry(path)
        except Exception as exc:
            errors.append({
                "code": "part_unreadable",
                "message": f"Raster part could not be read: {path.name} ({exc})",
            })
            continue

        if reference is None:
            reference = current
            reference_transform = Affine(*current["transform"])
            parts.append(PartGeometry(
                path=path, name=path.name, width=current["width"], height=current["height"]
            ))
            continue

        if current["crs"] != reference["crs"]:
            errors.append({
                "code": "crs_mismatch",
                "message": f"Raster part has a different CRS: {path.name}",
            })
            continue
        if current["dtypes"] != reference["dtypes"] or current["count"] != reference["count"]:
            errors.append({
                "code": "band_mismatch",
                "message": f"Raster part has a different band layout or dtype: {path.name}",
            })
            continue
        if not all(
            math.isclose(left, right, rel_tol=RESOLUTION_REL_TOL)
            for left, right in zip(current["res"], reference["res"])
        ):
            errors.append({
                "code": "resolution_mismatch",
                "message": f"Raster part has a different resolution: {path.name}",
            })
            continue

        current_transform = Affine(*current["transform"])
        scale = max(reference["res"]) or 1.0
        if any(
            abs(left - right) > RESOLUTION_REL_TOL * scale
            for left, right in zip(_linear_terms(reference["transform"]), _linear_terms(current["transform"]))
        ):
            errors.append({
                "code": "transform_mismatch",
                "message": f"Raster part has an incompatible affine transform: {path.name}",
            })
            continue

        column_offset, row_offset = (~reference_transform) * (current_transform.c, current_transform.f)
        if (
            abs(column_offset - round(column_offset)) > GRID_TOL_PIXELS
            or abs(row_offset - round(row_offset)) > GRID_TOL_PIXELS
        ):
            errors.append({
                "code": "grid_misaligned",
                "message": f"Raster part is not aligned to the common pixel grid: {path.name}",
            })
            continue

        parts.append(PartGeometry(
            path=path,
            name=path.name,
            width=current["width"],
            height=current["height"],
            col_off=round(column_offset),
            row_off=round(row_offset),
        ))

    by_window: dict[tuple[float, float, float, float], list[str]] = {}
    for part in parts:
        by_window.setdefault(part.window, []).append(part.name)
    duplicates = {window: names for window, names in by_window.items() if len(names) > 1}
    if duplicates:
        listed = "; ".join(", ".join(names) for names in list(duplicates.values())[:MAX_REPORTED_PAIRS])
        warnings.append({
            "code": "duplicate_parts",
            "message": f"{len(duplicates)} group(s) of parts cover exactly the same window: {listed}",
        })

    coverage = _cell_coverage(tuple(parts))
    pairs = _overlapping_pairs(tuple(parts))
    if pairs and coverage["overlap_pixels"] > 0:
        listed = "; ".join(f"{left} ∩ {right}" for left, right in pairs[:MAX_REPORTED_PAIRS])
        warnings.append({
            "code": "parts_overlap",
            "message": (
                f"{len(pairs)} pair(s) of parts overlap on {coverage['overlap_pixels']} pixel(s): {listed}"
            ),
        })
    share = coverage["gap_pixels"] / max(coverage["bounding_pixels"], 1)
    if coverage["interior_gap_pixels"] > 0:
        warnings.append({
            "code": "coverage_gaps",
            "message": (
                f"Mosaic has a hole enclosed by other parts: "
                f"{coverage['interior_gap_pixels']} pixel(s) are not covered by any part"
            ),
        })
    elif share > GAP_WARNING_SHARE:
        warnings.append({
            "code": "coverage_gaps",
            "message": (
                f"Mosaic covers only {1 - share:.1%} of its bounding rectangle: "
                f"{coverage['gap_pixels']} of {coverage['bounding_pixels']} pixel(s) are not "
                "covered by any part"
            ),
        })

    return MosaicReport(
        parts=tuple(parts),
        errors=tuple(errors),
        warnings=tuple(warnings),
        coverage=coverage,
    )
