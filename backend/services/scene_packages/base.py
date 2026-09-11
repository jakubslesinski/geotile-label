"""Common package resolver types and utilities."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


from services.scene_packages import archives
from services.scene_packages.contracts import (
    ROLE_ARCHIVE,
    ROLE_MEASUREMENT,
    ROLE_PRODUCT_METADATA,
    ROLE_RPC,
    ROLE_TILE_MANIFEST,
    graph_v2_enabled,
)
from services.scene_packages.roles import classify_asset_role

#: Role plikow, ktore moga SWIADCZYC o tym, ze katalog jest paczka produktu. Dokument
#: obejmujacy cala dostawe (arkusz zestawienia, README, DeliveryMetadata) opisuje ZBIOR
#: paczek, a nie paczke — uznanie go za sygnature scala cale zrodlo w jedna scene
#: (sekcja 4.3: 432 rastry i 611 metadanych jako jeden pakiet).
SIGNATURE_ROLES = frozenset({ROLE_MEASUREMENT, ROLE_PRODUCT_METADATA, ROLE_RPC, ROLE_TILE_MANIFEST})

RASTER_EXTENSIONS = {".tif", ".tiff", ".jp2", ".png", ".jpg", ".jpeg"}
METADATA_EXTENSIONS = {
    ".json", ".xml", ".imd", ".til", ".rpb", ".tfw", ".j2w", ".kml",
    ".kmz", ".html", ".htm", ".txt",
}


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PackageCandidate:
    root: Path
    relative_root: str


class ProviderResolver:
    provider = "generic"
    # Bump whenever resolver output semantics change.  This value participates in the
    # persistent scan-cache key, so stale inventories are never reused after an upgrade.
    version = 2
    modality = "EO"

    def validate_source(self, root: Path) -> list[dict[str, str]]:
        diagnostics: list[dict[str, str]] = []
        if not root.is_dir():
            diagnostics.append({"level": "error", "code": "source_not_found", "message": str(root)})
        return diagnostics

    def discover_packages(self, root: Path) -> list[PackageCandidate]:
        if self._looks_like_package_root(root):
            return [PackageCandidate(root=root, relative_root="."), *self._archive_candidates(root)]
        candidates: list[PackageCandidate] = []
        for child in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: natural_key(item.name)):
            if self._contains_raster(child):
                candidates.append(PackageCandidate(root=child, relative_root=child.name))
        return [*candidates, *self._archive_candidates(root)]

    def _archive_candidates(self, root: Path) -> list[PackageCandidate]:
        """Archiwa jako OSOBNE kandydaty (P1.3a).

        Archiwum celowo nie wchodzi do inwentarza pakietu z rastrami. Gdyby wchodzilo,
        zmienialoby liste assetow, odcisk tozsamosci i snapshot juz zaimportowanych scen —
        czyli etap widocznosci archiwow wymuszalby migracje manifestow. Jako osobny kandydat
        archiwum jest addytywne: nic istniejacego sie nie przesuwa.

        Glebokosc jest ograniczona do korzenia zrodla i jego bezposrednich podkatalogow. Obie
        konwencje z korpusu miesza sie w tym zakresie (WV2 trzyma ZIP-y w korzeniu, Airbus w
        katalogu dostawy), a pelny `rglob` po udziale sieciowym oznaczalby drugie przejscie
        calego drzewa przy kazdym skanie.
        """
        if not graph_v2_enabled():
            return []
        found: list[Path] = []
        try:
            directories = [root, *(item for item in root.iterdir() if item.is_dir())]
        except OSError:
            return []
        for directory in directories:
            try:
                entries = [item for item in directory.iterdir() if item.is_file()]
            except OSError:
                continue
            found.extend(item for item in entries if archives.is_archive(item.name))
        return [
            PackageCandidate(root=path, relative_root=path.relative_to(root).as_posix())
            for path in sorted(found, key=lambda item: natural_key(item.relative_to(root).as_posix()))
        ]

    def _looks_like_package_root(self, root: Path) -> bool:
        try:
            direct_files = [item for item in root.iterdir() if item.is_file()]
        except OSError:
            return False
        if any(item.suffix.casefold() in RASTER_EXTENSIONS for item in direct_files):
            return True
        signature_files = (
            [item for item in direct_files if classify_asset_role(item.name) in SIGNATURE_ROLES]
            if graph_v2_enabled()
            else direct_files
        )
        names = " ".join(item.name.upper() for item in signature_files)
        signatures = {
            "pleiades_neo": ("VOL_PNEO", "DIM_PNEO"),
            "worldview": ("DELIVERYMETADATA", "README", ".IMD"),
            "iceye": ("ICEYE",),
            "capella": ("CAPELLA",),
            "umbra": ("UMBRA",),
            "blacksky": ("BLACKSKY", "BSG-"),
        }.get(self.provider, ())
        return any(signature in names for signature in signatures)

    def _contains_raster(self, root: Path) -> bool:
        try:
            return any(path.is_file() and path.suffix.casefold() in RASTER_EXTENSIONS for path in root.rglob("*"))
        except OSError:
            return False

    def build_inventory(self, source_root: Path, candidate: PackageCandidate) -> dict[str, Any]:
        if candidate.root.is_file() and archives.is_archive(candidate.root.name):
            return self._archive_inventory(source_root, candidate)
        assets: list[dict[str, Any]] = []
        for path in sorted((item for item in candidate.root.rglob("*") if item.is_file()), key=lambda item: natural_key(item.as_posix())):
            suffix = path.suffix.casefold()
            if suffix not in RASTER_EXTENSIONS | METADATA_EXTENSIONS:
                continue
            relative = path.relative_to(source_root).as_posix()
            stat = path.stat()
            assets.append({
                "asset_id": stable_id("asset", f"{self.provider}:{relative.casefold()}"),
                "role": "raster_candidate" if suffix in RASTER_EXTENSIONS else "metadata",
                # Rola v2 jest DODATKOWA: `role` powyzej zostaje bez zmian, zeby czytelnicy
                # inwentarza (audyt, API, testy) dzialali tak samo przy wylaczonej fladze.
                "asset_role": classify_asset_role(relative),
                "relative_path": relative,
                "package_relative_path": path.relative_to(candidate.root).as_posix(),
                "format": suffix.lstrip("."),
                "part_id": self._part_id(path.name),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": None,
                "sha256_method": None,
                "content_signature": None,
                "content_signature_method": None,
                "identity_strength": None,
            })
        package_key = f"{self.provider}:{candidate.relative_root.casefold()}"
        return {
            "package_id": stable_id("pkg", package_key),
            "provider": self.provider,
            "modality": self.modality,
            "package_root_relative": candidate.relative_root,
            "assets": assets,
        }

    def _archive_inventory(self, source_root: Path, candidate: PackageCandidate) -> dict[str, Any]:
        """Inwentarz pakietu-archiwum: dokladnie jeden asset, nigdy `raster_candidate`."""
        path = candidate.root
        relative = path.relative_to(source_root).as_posix()
        stat = path.stat()
        return {
            "package_id": stable_id("pkg", f"{self.provider}:archive:{relative.casefold()}"),
            "provider": self.provider,
            "modality": self.modality,
            "package_kind": "archive",
            "package_root_relative": relative,
            "assets": [{
                "asset_id": stable_id("asset", f"{self.provider}:{relative.casefold()}"),
                "role": ROLE_ARCHIVE,
                "asset_role": ROLE_ARCHIVE,
                "relative_path": relative,
                "package_relative_path": path.name,
                "format": path.suffix.lstrip(".").casefold(),
                "part_id": None,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": None,
                "sha256_method": None,
                "content_signature": None,
                "content_signature_method": None,
                "identity_strength": None,
            }],
        }

    def rank_archive(self, inventory: dict[str, Any], source_root: Path) -> dict[str, Any]:
        """Opisz archiwum i jego zwiazek z rozpakowana kopia (P1.3a).

        Wynik jest selekcja tylko z nazwy — `asset_ids` jest PUSTE i pozostaje puste. Archiwum
        nie jest produktem do etykietowania, wiec katalogowanie musi je pominac, a nie probowac
        z niego zrobic scene.
        """
        relative = str(inventory.get("package_root_relative") or "")
        path = source_root / relative
        index = archives.read_archive_index(path)
        warnings: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []

        if not index.is_readable:
            status = archives.ARCHIVE_STATUS_UNREADABLE
            match = archives.ExtractionMatch()
            errors.append({
                "code": "archive_unreadable",
                "message": f"Archive index could not be read: {index.error}",
            })
        else:
            match = archives.match_extracted(path, index, source_root)
            status = match.status
            warnings.append({
                "code": status,
                "message": {
                    archives.ARCHIVE_STATUS_ONLY: (
                        "Delivery exists only inside this archive; no extracted copy was found "
                        "next to it"
                    ),
                    archives.ARCHIVE_STATUS_DUPLICATE: (
                        f"Archive duplicates the extracted delivery in {match.base_relative!r} "
                        f"({match.present}/{match.total} files)"
                    ),
                    archives.ARCHIVE_STATUS_INCOMPLETE: (
                        f"Extracted copy in {match.base_relative!r} is incomplete: "
                        f"{match.present}/{match.total} files present"
                    ),
                }[status],
            })

        if index.unsafe_names:
            warnings.append({
                "code": "archive_unsafe_entries",
                "message": (
                    f"{len(index.unsafe_names)} archive entry name(s) escape the extraction root "
                    "and were excluded from the index"
                ),
            })
        if index.truncated:
            warnings.append({
                "code": "archive_index_truncated",
                "message": (
                    f"Archive index was truncated at {archives.MAX_INDEXED_ENTRIES} entries; "
                    "completeness is reported for the indexed prefix only"
                ),
            })

        missing = list(match.missing) + list(match.size_mismatch)
        return {
            "status": status,
            "product_type": "ARCHIVE",
            "raster_kind": "archive",
            "asset_ids": [],
            "identity_asset_ids": [],
            "metadata_asset_ids": [],
            "rgb_bands": None,
            "selected_by": "resolver",
            "provider_scene_id": path.stem,
            "archive": {
                "entry_count": index.entry_count,
                "uncompressed_bytes": index.uncompressed_bytes,
                "compressed_bytes": int(inventory["assets"][0]["size"]),
                "delivery_root": index.delivery_root,
                "unsafe_entry_count": len(index.unsafe_names),
                "truncated": index.truncated,
                "extracted_root_relative": match.base_relative,
                "extracted_present": match.present,
                "extracted_total": match.total,
                "missing_count": len(missing),
                "missing_paths": sorted(missing)[: archives.MAX_REPORTED_MISSING],
            },
            "alternatives": [],
            "diagnostics": {"warnings": warnings, "errors": errors, "metadata_conflicts": []},
        }

    def rank_products(self, inventory: dict[str, Any], source_root: Path) -> dict[str, Any]:
        rasters = self._rasters(inventory)
        if len(rasters) == 1:
            return self._selection(inventory, rasters, product_type="IMAGE")
        return self._decision(inventory, rasters, "Several image candidates require a selection")

    #: Gramatyka uzywana przy doprecyzowaniu RECZNEGO wyboru; `None` oznacza brak operacji.
    #: Jest jawnym atrybutem klasy, a nie czyms dziedziczonym po cichu, bo kilku dostawcow
    #: dziedziczy po sobie dla wspolnej logiki decyzyjnej, majac ROZNE konwencje nazw.
    manual_grammar: str | None = None

    def refine_manual_selection(self, package: dict[str, Any], selection: dict[str, Any]) -> None:
        """Uzupelnij selekcje po RECZNYM wyborze assetow przez uzytkownika.

        Domyslnie nic nie robi. Dostawcy, ktorzy potrafia odczytac typ produktu z nazwy,
        nadpisuja te metode — bez tego reczny wybor zostawia `product_type=UNRESOLVED`
        i metadane zwiazane z poprzedniej, automatycznej selekcji (sekcja 4.4).
        """
        return None


    def _bind_metadata_with(
        self,
        inventory: dict[str, Any],
        selection: dict[str, Any],
        *,
        bind,
        orphans,
        is_complex,
        measurement_override: "list[str] | None" = None,
    ) -> None:
        """Przypisz do selekcji WYLACZNIE sidecary jej wlasnych rastrow.

        Ten sam mechanizm naprawia trzy usterki opisane osobno w audycie, bo wszystkie
        wynikaja z plaskiej puli metadanych pakietu:

        - sekcja 4.3 (ICEYE): 914 konfliktow w 84 scenach i `product_level=VID` na GRD,
        - sekcja 4.4 (Capella): metadane wiazane z innej akwizycji tej samej dostawy,
        - sekcja 4.6 (WorldView): szesc pozornych konfliktow miedzy komponentami MUL i PAN.

        Algorytm jest wspolny (`providers/binding.py`); parametry `bind`/`orphans` wnosza
        gramatyke konkretnego dostawcy.
        """
        by_path = {
            str(asset.get("package_relative_path") or ""): asset
            for asset in inventory.get("assets", [])
        }
        if measurement_override is not None:
            # Produkt pochodny sklada sie z KILKU komponentow, ale opisuje go metadata
            # jednego z nich. Wolajacy przekazuje wtedy wprost, ktore rastry maja byc
            # podstawa wiazania — patrz `primary_component` w resolverze WorldView.
            measurement = list(measurement_override)
        else:
            chosen_ids = set(selection.get("identity_asset_ids") or selection.get("asset_ids") or [])
            measurement = [path for path, asset in by_path.items() if asset.get("asset_id") in chosen_ids]
        if not measurement:
            return
        metadata = [path for path, asset in by_path.items() if asset.get("role") == "metadata"]
        bound = bind(measurement, metadata)
        selection["metadata_asset_ids"] = [
            by_path[path]["asset_id"]
            for path in sorted({item for values in bound.values() for item in values})
            if path in by_path
        ]

        # Ostrzegamy WYLACZNIE o sidecarach, ktore nie naleza do zadnego produktu w paczce.
        # Sidecar innego produktu, innej akwizycji albo innego komponentu tej samej dostawy
        # jest normalnym skladnikiem paczki — ostrzeganie o nim byloby szumem.
        all_measurement = [
            path
            for path, asset in by_path.items()
            # `primary_raster` to ten sam raster PO zapisaniu sceny (`_save_package_scene()`
            # przepisuje role wybranym assetom). Pominiecie go tutaj kazaloby uznac jego
            # wlasny sidecar za osierocony przy ponownym wiazaniu po recznym wyborze.
            if asset.get("role") in {"raster_candidate", "primary_raster"} and not is_complex(path)
        ]
        unplaced = orphans(all_measurement, metadata)
        if unplaced:
            selection["diagnostics"].setdefault("warnings", []).append({
                "code": "metadata_not_bound",
                "message": (
                    f"{len(unplaced)} metadata sidecar(s) could not be bound to any product "
                    "in this package and were excluded"
                ),
            })

    def _refine_manual_with(self, package, selection, *, product_of, rebind) -> None:
        """Wspolna sciezka doprecyzowania recznego wyboru.

        Dwie rzeczy, ktorych `select-asset` nie robil: nie ustawial typu produktu (selekcja
        zostawala `UNRESOLVED` mimo statusu `ready`) i nie przeliczal wiazania metadanych,
        wiec scena mogla zachowac sidecary poprzedniego, automatycznego wyboru.
        """
        by_id = {
            str(asset.get("asset_id")): str(asset.get("package_relative_path") or "")
            for asset in package.get("assets") or []
        }
        chosen = [by_id[aid] for aid in (selection.get("asset_ids") or []) if aid in by_id]
        if not chosen:
            return
        detected = {product_of(path) for path in chosen}
        detected.discard(None)
        if len(detected) == 1:
            selection["product_type"] = detected.pop()
        elif detected:
            # Mieszanka typow to nie jest scena — zostawiamy UNRESOLVED i mowimy dlaczego.
            selection["diagnostics"].setdefault("warnings", []).append({
                "code": "mixed_product_types",
                "message": f"Selected assets mix product types: {', '.join(sorted(detected))}",
            })
        rebind(package, selection)

    def identity_assets(self, selection: dict[str, Any], inventory: dict[str, Any]) -> list[dict[str, Any]]:
        selected = set(selection.get("identity_asset_ids") or selection.get("asset_ids") or [])
        return [asset for asset in inventory.get("assets", []) if asset.get("asset_id") in selected]

    def _rasters(self, inventory: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = [asset for asset in inventory.get("assets", []) if asset.get("role") == "raster_candidate"]
        if not graph_v2_enabled():
            return candidates
        # Kontrakt v2: kandydatem na obraz do etykietowania jest wylacznie asset o roli
        # `measurement`. To odcina BROWSE.JPG, LAYOUT.JPG i `<produkt>_preview.tif`, ktore
        # w modelu opartym na rozszerzeniu byly nierozroznialne od danych pomiarowych.
        measurements = [asset for asset in candidates if asset.get("asset_role") == ROLE_MEASUREMENT]
        # Jesli rola nie zostawila niczego, oddajemy pelna liste: brak wyboru jest gorszy
        # od wyboru niedoskonalego, a diagnostyka i tak zaraportuje sytuacje.
        return measurements or candidates

    def _labeling_rasters(self, inventory: dict[str, Any]) -> list[dict[str, Any]]:
        rasters = self._rasters(inventory)
        if graph_v2_enabled():
            # Pod v2 `_rasters()` odsiewa juz podglady po roli. Stara heurystyka nazw jest tu
            # nie tylko zbedna, ale i szkodliwa: wyklucza token `pan`, czyli odrzucalaby
            # legalne produkty panchromatyczne (sekcja 17.7.2).
            return rasters
        primary = [
            asset for asset in rasters
            if not re.search(
                r"(?:^|[_-])(?:mask|browse|preview|quicklook|pan)(?:[_-]|\.)",
                str(asset.get("package_relative_path") or ""),
                re.IGNORECASE,
            )
        ]
        return primary or rasters

    def _matching(self, inventory: dict[str, Any], *patterns: str) -> list[dict[str, Any]]:
        compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        return [
            asset for asset in self._rasters(inventory)
            if any(pattern.search(asset.get("package_relative_path", "")) for pattern in compiled)
        ]

    def _selection(
        self,
        inventory: dict[str, Any],
        assets: list[dict[str, Any]],
        *,
        product_type: str,
        status: str = "ready",
        raster_kind: str | None = None,
        alternatives: list[dict[str, Any]] | None = None,
        rgb_bands: list[int] | None = None,
        provider_scene_id: str | None = None,
    ) -> dict[str, Any]:
        ordered = sorted(assets, key=lambda item: natural_key(item.get("package_relative_path", "")))
        if raster_kind is None:
            raster_kind = "direct" if len(ordered) == 1 else "virtual_mosaic"
        display_name = provider_scene_id or Path(inventory.get("package_root_relative") or self.provider).name
        return {
            "status": status,
            "product_type": product_type,
            "provider_scene_id": provider_scene_id or self._provider_scene_id(inventory, ordered),
            "display_name": display_name,
            "asset_ids": [asset["asset_id"] for asset in ordered],
            "identity_asset_ids": [asset["asset_id"] for asset in ordered],
            "raster_kind": raster_kind,
            "rgb_bands": rgb_bands,
            "mosaic_parts_order": [asset.get("part_id") for asset in ordered if asset.get("part_id")],
            "alternatives": alternatives or [],
            "diagnostics": {"warnings": [], "errors": [], "metadata_conflicts": []},
        }

    def _decision(self, inventory: dict[str, Any], assets: list[dict[str, Any]], message: str) -> dict[str, Any]:
        alternatives = [
            {"asset_ids": [asset["asset_id"]], "label": asset.get("package_relative_path")}
            for asset in assets
        ]
        result = self._selection(
            inventory,
            assets[:1],
            product_type="UNRESOLVED",
            status="decision_required",
            alternatives=alternatives,
        )
        result["asset_ids"] = []
        result["identity_asset_ids"] = []
        result["diagnostics"]["warnings"].append({"code": "decision_required", "message": message})
        return result

    def _provider_scene_id(self, inventory: dict[str, Any], assets: list[dict[str, Any]]) -> str:
        root_name = Path(inventory.get("package_root_relative") or "").name
        if root_name and root_name != ".":
            return root_name
        if assets:
            return Path(assets[0].get("package_relative_path", "scene")).stem
        return inventory.get("package_id", "scene")

    @staticmethod
    def _part_id(filename: str) -> str | None:
        match = re.search(r"R(\d+)C(\d+)", filename, re.IGNORECASE)
        return f"R{int(match.group(1))}C{int(match.group(2))}" if match else None
