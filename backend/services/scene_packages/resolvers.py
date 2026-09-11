"""Resolver registry and provider-specific product ranking."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from services.scene_packages.base import PackageCandidate, ProviderResolver, natural_key, stable_id
from services.scene_packages.contracts import graph_v2_enabled
from services.scene_packages.roles import classify_asset_role
from services.scene_packages.providers import airbus as airbus_grammar
from services.scene_packages.providers import capella as capella_grammar
from services.scene_packages.providers import iceye as iceye_grammar
from services.scene_packages.providers import worldview as worldview_grammar
from services.scene_packages.dimap import read_dimap_bands
from services.scene_packages.tile_manifests import EMPTY_MANIFEST, TileManifest, read_tile_manifest


PROVIDER_MODALITY = {
    "iceye": "SAR",
    "capella": "SAR",
    "umbra": "SAR",
    "pleiades_neo": "EO",
    "worldview": "EO",
    "blacksky": "EO",
    "generic": None,
}


class GenericResolver(ProviderResolver):
    provider = "generic"

    def discover_packages(self, root: Path) -> list[PackageCandidate]:
        # REKURENCYJNIE: pliki obrazowe w PODFOLDERACH też są scenami
        # (np. <folder dostawy>/<lokalizacja>/*.tif).
        # Wcześniej skanowano tylko pliki bezpośrednie (root.iterdir), więc wskazanie folderu-rodzica
        # z samymi podfolderami dawało 0 scen. Pomijamy ukryte/derived katalogi (nazwa od kropki).
        exts = {".tif", ".tiff", ".jp2", ".png", ".jpg", ".jpeg"}
        rasters = sorted(
            (
                item
                for item in root.rglob("*")
                if item.is_file()
                and item.suffix.casefold() in exts
                and not any(part.startswith(".") for part in item.relative_to(root).parts)
            ),
            key=lambda item: natural_key(item.relative_to(root).as_posix()),
        )
        return [
            PackageCandidate(root=path, relative_root=path.relative_to(root).as_posix())
            for path in rasters
        ]

    def build_inventory(self, source_root: Path, candidate: PackageCandidate) -> dict[str, Any]:
        if candidate.root.is_file():
            path = candidate.root
            stat = path.stat()
            relative = path.relative_to(source_root).as_posix()
            inventory = {
                "package_id": self._package_id(relative),
                "provider": self.provider,
                "modality": None,
                "package_root_relative": relative,
                "assets": [{
                    "asset_id": self._asset_id(relative),
                    "role": "raster_candidate",
                    # Rola kontraktowa brakowala tu od P0.2: kandydat generyczny jest jednym
                    # plikiem, wiec nie przechodzil przez `build_inventory()` klasy bazowej.
                    # Skutkiem bylo `asset_role=None`, czyli scena generyczna wygladala
                    # w raportach na dostawe bez ani jednego assetu measurement.
                    "asset_role": classify_asset_role(relative),
                    "relative_path": relative,
                    "package_relative_path": path.name,
                    "format": path.suffix.casefold().lstrip("."),
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
            return inventory
        return super().build_inventory(source_root, candidate)

    @staticmethod
    def _asset_id(relative: str) -> str:
        from services.scene_packages.base import stable_id
        return stable_id("asset", f"generic:{relative.casefold()}")

    @staticmethod
    def _package_id(relative: str) -> str:
        from services.scene_packages.base import stable_id
        return stable_id("pkg", f"generic:{relative.casefold()}")


class IceyeResolver(ProviderResolver):
    provider = "iceye"
    modality = "SAR"

    def rank_products(self, inventory: dict[str, Any], source_root: Path) -> dict[str, Any]:
        if graph_v2_enabled():
            candidates = self._grd_by_product_level(inventory)
        else:
            candidates = self._matching(inventory, r"(?:^|[_-])GRD(?:[_-]|$).+\.(?:tif|tiff)$")
        result = self._one_or_decision(inventory, candidates, "GRD", "ICEYE GRD TIFF was not found")
        if graph_v2_enabled():
            self._bind_metadata(inventory, result)
            # Warianty polaryzacyjne sa OSOBNYMI scenami i kazdy potrzebuje wlasnych
            # metadanych. Bez tego sidecar HH i HV trafialyby do wspolnej puli — czyli
            # dokladnie ten sam blad, ktory naprawiamy, tylko o poziom nizej.
            for logical in result.get("logical_selections") or []:
                self._bind_metadata(inventory, logical)
        return result

    def _grd_by_product_level(self, inventory: dict[str, Any]) -> list[dict[str, Any]]:
        r"""Wybierz produkty GRD po ODCZYTANYM poziomie przetworzenia, nie po regexie nazwy.

        Stary wzorzec `(?:^|[_-])GRD(?:[_-]|$).+\.tif$` wymagal, zeby po tokenie `GRD` stal
        jeszcze `_`, `-` albo koniec napisu, a potem co najmniej jeden znak przed rozszerzeniem.
        Nazwa konczaca sie `_GRD.tif` — wymieniona wprost w zakresie P0.3 — nie spelnia tego
        warunku, bo po `GRD` stoi kropka. Gramatyka nazw nie ma tego ograniczenia.
        """
        selected: list[dict[str, Any]] = []
        for asset in self._rasters(inventory):
            path = str(asset.get("package_relative_path") or "")
            if iceye_grammar.is_complex_product(path):
                # Bramka P0.3: SLC/CSI w kontenerze HDF5 nie jest obrazem do etykietowania.
                continue
            if iceye_grammar.parse_product_name(path).product_level == "GRD":
                selected.append(asset)
        return selected

    def _bind_metadata(self, inventory: dict[str, Any], selection: dict[str, Any]) -> None:
        """Zwiaz z selekcja wylacznie sidecary jej wlasnego produktu ICEYE."""
        self._bind_metadata_with(
            inventory,
            selection,
            bind=iceye_grammar.bind_metadata_to_products,
            orphans=iceye_grammar.orphan_metadata,
            is_complex=iceye_grammar.is_complex_product,
        )

    def _one_or_decision(self, inventory, candidates, product, missing):
        if len(candidates) == 1:
            return self._selection(inventory, candidates, product_type=product)
        polarization_groups: dict[str, list[dict[str, Any]]] = {}
        for asset in candidates:
            match = re.search(r"(?:^|[_-])(HH|HV|VH|VV)(?:[_-]|\.)", asset.get("package_relative_path", ""), re.IGNORECASE)
            if match:
                polarization_groups.setdefault(match.group(1).upper(), []).append(asset)
        if len(polarization_groups) > 1:
            result = self._decision(inventory, candidates, f"Several {product} polarizations were found")
            result["logical_selections"] = [
                {
                    **self._selection(inventory, assets, product_type=product),
                    "polarization_or_bands": [polarization],
                    "provider_scene_id": f"{self._provider_scene_id(inventory, assets)}_{polarization}",
                }
                for polarization, assets in sorted(polarization_groups.items())
            ]
            return result
        return self._decision(inventory, candidates or self._rasters(inventory), missing if not candidates else f"Several {product} products were found")


    #: Gramatyka uzywana przy doprecyzowaniu RECZNEGO wyboru. Umbra i BlackSky dziedzicza po
    #: tej klasie dla `_one_or_decision()`, ale ich nazwy plikow rzadza sie wlasnymi regulami —
    #: dlatego gramatyka jest jawnym atrybutem klasy, a nie czyms dziedziczonym po cichu.
    #: `None` oznacza: hook jest brakiem operacji, dokladnie jak przed P0.3.
    manual_grammar = "iceye"

    def refine_manual_selection(self, package: dict[str, Any], selection: dict[str, Any]) -> None:
        if not graph_v2_enabled() or self.manual_grammar is None:
            return
        if self.manual_grammar == "iceye":
            self._refine_manual_with(
                package,
                selection,
                product_of=lambda path: iceye_grammar.parse_product_name(path).product_level,
                rebind=self._bind_metadata,
            )

class CapellaResolver(IceyeResolver):
    provider = "capella"

    def rank_products(self, inventory: dict[str, Any], source_root: Path) -> dict[str, Any]:
        if not graph_v2_enabled():
            candidates = self._matching(inventory, r"(?:^|[_-])GEC(?:[_-]|$).+\.(?:tif|tiff)$", r"GEC.*\.(?:tif|tiff)$")
            return self._one_or_decision(inventory, candidates, "GEC", "Capella GEC TIFF was not found")
        return self._rank_by_acquisition(inventory)

    def _rank_by_acquisition(self, inventory: dict[str, Any]) -> dict[str, Any]:
        """Jedna selekcja na AKWIZYCJE, nie jedna na folder (sekcja 4.4).

        Folder dostawy Capelli bywa plaskim workiem na kilka przelotow, wiec topologia
        katalogow nie niesie tu informacji o scenie — niesie ja nazwa pliku. Zamiast
        rozszczepiac paczke na poziomie `discover_packages()`, korzystamy z istniejacego
        mechanizmu `logical_selections`, ktory `scan_source()` juz rozwija w osobne sceny
        (tak samo dziala rozdzial polaryzacji ICEYE).
        """
        rasters = self._rasters(inventory)
        by_path = {str(asset.get("package_relative_path") or ""): asset for asset in rasters}
        groups = capella_grammar.group_by_acquisition(list(by_path))

        selections: list[dict[str, Any]] = []
        for acquisition in sorted(groups):
            if not acquisition:
                # Pliki bez rozpoznawalnej akwizycji nie sa scalane ani zgadywane.
                continue
            product_type, chosen = capella_grammar.preferred_products(groups[acquisition])
            if not chosen:
                continue
            assets = [by_path[path] for path in chosen]
            selection = self._selection(
                inventory,
                assets,
                product_type=product_type,
                provider_scene_id=Path(chosen[0]).stem,
            )
            self._bind_capella_metadata(inventory, selection)
            selections.append(selection)

        if len(selections) == 1:
            return selections[0]
        if selections:
            result = self._decision(inventory, rasters, "Several Capella acquisitions were found")
            result["logical_selections"] = selections
            return result
        return self._decision(inventory, rasters, "Capella GEO or GEC product was not found")

    manual_grammar = "capella"

    def refine_manual_selection(self, package: dict[str, Any], selection: dict[str, Any]) -> None:
        if not graph_v2_enabled():
            return
        self._refine_manual_with(
            package,
            selection,
            product_of=lambda path: capella_grammar.parse_product_name(path).product_type,
            rebind=self._bind_capella_metadata,
        )

    def _bind_capella_metadata(self, inventory: dict[str, Any], selection: dict[str, Any]) -> None:
        """Zwiaz `extended.json` i pozostale sidecary z rastrem TEJ SAMEJ akwizycji.

        Sekcja 4.4 konczy sie uwaga, ze reczny wybor "moze powiazac metadata z innej
        akwizycji". Przy plaskim folderze z dwoma przelotami to nie jest hipoteza — bez
        wiazania obie sceny dostaja te sama pule sidecarow.
        """
        self._bind_metadata_with(
            inventory,
            selection,
            bind=capella_grammar.bind_metadata_to_products,
            orphans=capella_grammar.orphan_metadata,
            is_complex=lambda path: False,
        )


class UmbraResolver(IceyeResolver):
    provider = "umbra"
    # Gramatyka nazw tego dostawcy nie jest jeszcze zaimplementowana (P1); dopoki jej nie ma,
    # reczny wybor ma zachowywac sie dokladnie tak, jak przed P0.3.
    manual_grammar = None

    def rank_products(self, inventory: dict[str, Any], source_root: Path) -> dict[str, Any]:
        candidates = self._matching(inventory, r"(?:^|[_-])GEC(?:[_-]|$).+\.(?:tif|tiff)$", r"GEC.*\.(?:tif|tiff)$")
        return self._one_or_decision(inventory, candidates, "GEC", "Umbra GEC TIFF was not found")


class BlackSkyResolver(IceyeResolver):
    provider = "blacksky"
    modality = "EO"
    # Gramatyka nazw tego dostawcy nie jest jeszcze zaimplementowana (P1); dopoki jej nie ma,
    # reczny wybor ma zachowywac sie dokladnie tak, jak przed P0.3.
    manual_grammar = None

    def rank_products(self, inventory: dict[str, Any], source_root: Path) -> dict[str, Any]:
        candidates = self._matching(inventory, r"_ortho\.(?:tif|tiff)$")
        result = self._one_or_decision(inventory, candidates, "ORTHO_RGB", "BlackSky ortho TIFF was not found")
        if result.get("status") == "ready":
            result["rgb_bands"] = [1, 2, 3]
        return result


class PleiadesNeoResolver(ProviderResolver):
    provider = "pleiades_neo"
    modality = "EO"
    manual_grammar = "airbus"

    def rank_products(self, inventory: dict[str, Any], source_root: Path) -> dict[str, Any]:
        if graph_v2_enabled():
            return self._rank_by_airbus_product(inventory, source_root)
        return self._rank_legacy(inventory, source_root)

    def refine_manual_selection(self, package: dict[str, Any], selection: dict[str, Any]) -> None:
        if not graph_v2_enabled():
            return
        self._refine_manual_with(
            package,
            selection,
            product_of=lambda path: airbus_grammar.parse_path(path).spectral,
            rebind=self._bind_airbus_metadata,
        )

    def _rank_by_airbus_product(self, inventory: dict[str, Any], source_root: Path) -> dict[str, Any]:
        """Wybor produktu po GRAMATYCE nazw Airbusa, nie po jednym wzorcu PNEO.

        Stara sciezka szukala wylacznie `PMS-FS.*_RGB`, wiec dziesiec dostaw PHR w korpusie
        — nazwanych `..._PMS_..._R1C1.TIF`, bez tokenu `RGB` — nie mialo jak zostac scena.
        Tutaj produkt jest identyfikowany przez uuid z nazwy pliku, a wariant barwny i kafle
        rozdzielaja sie same.
        """
        rasters = self._rasters(inventory)
        by_path = {str(asset.get("package_relative_path") or ""): asset for asset in rasters}
        products = airbus_grammar.group_by_product(list(by_path))

        labeling = {
            key: paths
            for key, paths in products.items()
            if any(airbus_grammar.is_labeling_product(path) for path in paths)
        }
        if labeling:
            key = sorted(labeling, key=lambda item: (airbus_grammar.spectral_rank(labeling[item][0]), item))[0]
            paths = sorted(labeling[key], key=natural_key)
            assets = [by_path[path] for path in paths]
            spectral = airbus_grammar.parse_path(paths[0]).spectral or "PMS"
            rgb_bands = self._airbus_rgb_bands(source_root, inventory, paths[0])
            # Alternatywy sa SEMANTYCZNE: pozostale produkty tej dostawy, np. wariant `NED`.
            alternatives = [
                {
                    "asset_ids": [by_path[item]["asset_id"] for item in sorted(other, key=natural_key)],
                    "label": self._airbus_alternative_label(other[0]),
                }
                for other_key, other in sorted(products.items())
                if other_key != key
            ]
            selection = self._selection(
                inventory,
                assets,
                product_type=spectral,
                status="ready" if rgb_bands else "decision_required",
                alternatives=alternatives,
                rgb_bands=rgb_bands,
            )
            if not rgb_bands:
                # Bez deklaracji z DIMAP nie zgadujemy kolejnosci kanalow: zla kolejnosc daje
                # obraz o przeklamanych barwach, ktorego nie widac po samym podgladzie.
                selection["diagnostics"]["warnings"].append({
                    "code": "rgb_mapping_required",
                    "message": "Airbus band display order could not be read from the DIMAP metadata",
                })
            self._bind_airbus_metadata(inventory, selection)
            return selection

        derived = self._airbus_derived_selection(inventory)
        if derived is not None:
            self._bind_airbus_metadata(inventory, derived)
            return derived
        return self._decision(inventory, rasters, "Airbus labelling product was not found")

    def _airbus_alternative_label(self, path: str) -> str:
        key = airbus_grammar.parse_path(path)
        parts = [value for value in (key.spectral, key.variant) if value]
        return " ".join(parts) or PurePosixPath(path).name

    def _airbus_derived_selection(self, inventory: dict[str, Any]) -> dict[str, Any] | None:
        """MS-FS + PAN jako produkt pochodny, gdy dostawa nie ma gotowego pansharpened."""
        rasters = self._rasters(inventory)
        multispectral = [
            asset for asset in rasters
            if airbus_grammar.parse_path(str(asset.get("package_relative_path") or "")).spectral
            in {"MS-FS", "MS"}
        ]
        panchromatic = [
            asset for asset in rasters
            if airbus_grammar.parse_path(str(asset.get("package_relative_path") or "")).spectral
            in {"PAN", "P"}
        ]
        if not multispectral or not panchromatic:
            return None
        selected = sorted(
            multispectral + panchromatic,
            key=lambda item: natural_key(str(item.get("package_relative_path") or "")),
        )
        selection = self._selection(
            inventory,
            selected,
            product_type="MS-FS_RGB+PAN",
            status="prepare_required",
            raster_kind="derived",
            rgb_bands=[1, 2, 3],
        )
        selection["multispectral_asset_ids"] = [asset["asset_id"] for asset in multispectral]
        selection["panchromatic_asset_ids"] = [asset["asset_id"] for asset in panchromatic]
        return selection

    def _airbus_rgb_bands(
        self,
        source_root: Path,
        inventory: dict[str, Any],
        measurement_path: str,
    ) -> list[int] | None:
        """Kolejnosc kanalow z DIMAP-u opisujacego TEN produkt.

        Wiazanie idzie po uuid produktu, wiec nie da sie wziac kolejnosci z DIMAP-u innej
        akwizycji tej samej dostawy.
        """
        product = airbus_grammar.parse_path(measurement_path).product_uuid
        for asset in inventory.get("assets", []):
            if str(asset.get("format") or "").casefold() != "xml":
                continue
            relative = str(asset.get("package_relative_path") or "")
            if not PurePosixPath(relative).name.upper().startswith("DIM"):
                continue
            if product and airbus_grammar.parse_path(relative).product_uuid != product:
                continue
            bands = read_dimap_bands(source_root / str(asset.get("relative_path") or ""))
            resolved = bands.rgb_bands(PurePosixPath(measurement_path).name)
            if resolved:
                return resolved
        return None

    def _bind_airbus_metadata(self, inventory: dict[str, Any], selection: dict[str, Any]) -> None:
        """Zwiaz `DIM_*`/`VOL_*` z produktem po uuid, a nie z cala paczka."""
        self._bind_metadata_with(
            inventory,
            selection,
            bind=airbus_grammar.bind_metadata_to_products,
            orphans=airbus_grammar.orphan_metadata,
            is_complex=lambda path: False,
        )

    def _rank_legacy(self, inventory: dict[str, Any], source_root: Path) -> dict[str, Any]:
        pms = self._matching(inventory, r"PMS-FS.*_RGB(?:_|-).*\.(?:tif|tiff|jp2)$")
        if pms:
            extension_groups: dict[str, list[dict[str, Any]]] = {}
            for asset in pms:
                extension_groups.setdefault(asset.get("format", ""), []).append(asset)
            chosen_format = "tif" if "tif" in extension_groups else "tiff" if "tiff" in extension_groups else "jp2"
            chosen = extension_groups.get(chosen_format, pms)
            return self._selection(inventory, chosen, product_type="PMS-FS_RGB_ORTHO", rgb_bands=[1, 2, 3])

        ms = self._matching(inventory, r"MS-FS.*_RGB(?:_|-).*\.(?:tif|tiff|jp2)$")
        pan = self._matching(inventory, r"(?:^|[/_])PAN(?:-|_|/).*\.(?:tif|tiff|jp2)$", r"PNEO.*PAN.*\.(?:tif|tiff|jp2)$")
        if ms and pan:
            selected = sorted(ms + pan, key=lambda item: natural_key(item.get("package_relative_path", "")))
            result = self._selection(
                inventory,
                selected,
                product_type="MS-FS_RGB+PAN",
                status="prepare_required",
                raster_kind="derived",
                rgb_bands=[1, 2, 3],
            )
            result["multispectral_asset_ids"] = [asset["asset_id"] for asset in ms]
            result["panchromatic_asset_ids"] = [asset["asset_id"] for asset in pan]
            return result
        return self._decision(inventory, self._labeling_rasters(inventory), "Pleiades Neo PMS-FS RGB ORTHO was not found")


#: Typ produktu odpowiadajacy komponentowi — uzywany przy doprecyzowaniu recznego wyboru.
_WORLDVIEW_PRODUCT_TYPES = {
    worldview_grammar.COMPONENT_PANSHARPENED: "PANSHARPENED_RGB",
    worldview_grammar.COMPONENT_MULTISPECTRAL: "MUL",
    worldview_grammar.COMPONENT_PANCHROMATIC: "PAN",
}


class WorldViewResolver(ProviderResolver):
    provider = "worldview"
    modality = "EO"

    manual_grammar = "worldview"

    def rank_products(self, inventory: dict[str, Any], source_root: Path) -> dict[str, Any]:
        if graph_v2_enabled():
            return self._rank_by_component(inventory, source_root)
        return self._rank_legacy(inventory, source_root)

    def refine_manual_selection(self, package: dict[str, Any], selection: dict[str, Any]) -> None:
        if not graph_v2_enabled():
            return
        by_id = {
            str(asset.get("asset_id")): str(asset.get("package_relative_path") or "")
            for asset in package.get("assets") or []
        }
        chosen = [by_id[aid] for aid in (selection.get("asset_ids") or []) if aid in by_id]
        if not chosen:
            return

        components = worldview_grammar.group_by_component(chosen)
        components.pop("", None)
        component_names = set(components)
        if component_names == {
            worldview_grammar.COMPONENT_MULTISPECTRAL,
            worldview_grammar.COMPONENT_PANCHROMATIC,
        }:
            selection["product_type"] = "MUL+PAN"
            primary_component = worldview_grammar.COMPONENT_MULTISPECTRAL
        elif len(component_names) == 1:
            primary_component = next(iter(component_names))
            selection["product_type"] = _WORLDVIEW_PRODUCT_TYPES[primary_component]
        else:
            primary_component = ""
            selection["product_type"] = "UNRESOLVED"
            selection["diagnostics"].setdefault("warnings", []).append({
                "code": "mixed_product_types",
                "message": f"Selected assets mix WorldView components: {', '.join(sorted(component_names))}",
            })

        selection["source_components"] = {
            component: [
                asset_id
                for asset_id in (selection.get("asset_ids") or [])
                if by_id.get(asset_id) in paths
            ]
            for component, paths in sorted(components.items())
        }
        selection.pop("multispectral_asset_ids", None)
        selection.pop("panchromatic_asset_ids", None)
        if worldview_grammar.COMPONENT_MULTISPECTRAL in selection["source_components"]:
            selection["multispectral_asset_ids"] = list(
                selection["source_components"][worldview_grammar.COMPONENT_MULTISPECTRAL]
            )
        if worldview_grammar.COMPONENT_PANCHROMATIC in selection["source_components"]:
            selection["panchromatic_asset_ids"] = list(
                selection["source_components"][worldview_grammar.COMPONENT_PANCHROMATIC]
            )
        if primary_component:
            selection["primary_component"] = primary_component
            primary_paths = components.get(primary_component) or chosen
            self._bind_worldview_metadata(package, selection, measurement=primary_paths)
        else:
            self._bind_worldview_metadata(package, selection)
        selection["component_metadata_asset_ids"] = {
            component: self._component_metadata_ids(package, paths)
            for component, paths in sorted(components.items())
        }

    def _rank_by_component(self, inventory: dict[str, Any], source_root: Path) -> dict[str, Any]:
        """Wybor produktu po KOMPONENCIE, z lista czesci wziete z TIL (sekcja 4.6).

        Stara sciezka miala tylko dwie galezie — gotowy RGB i MUL+PAN — wiec poprawna dostawa
        panchromatyczna nie miala jak zostac scena. Kolejnosc galezi odpowiada temu, co jest
        najblizej gotowego obrazu do etykietowania.
        """
        rasters = self._rasters(inventory)
        by_path = {str(asset.get("package_relative_path") or ""): asset for asset in rasters}
        manifests = self._tile_manifests(inventory, source_root)
        acquisitions = {
            key: paths
            for key, paths in worldview_grammar.group_by_acquisition(list(by_path)).items()
            if key
        }

        # Katalog dostawy moze zawierac kilka przelotow albo produkty P001/P002. Topologia
        # katalogu nie jest wtedy wiarygodna granica sceny; identyfikatory dostawcy sa.
        if len(acquisitions) > 1:
            selections = [
                self._rank_one_acquisition(
                    inventory,
                    source_root,
                    by_path,
                    manifests,
                    acquisitions[key],
                    provider_scene_id=key,
                )
                for key in sorted(acquisitions)
            ]
            result = self._decision(inventory, rasters, "Several WorldView acquisitions/products were found")
            result["logical_selections"] = selections
            return result

        paths = next(iter(acquisitions.values())) if acquisitions else list(by_path)
        return self._rank_one_acquisition(inventory, source_root, by_path, manifests, paths)

    def _rank_one_acquisition(
        self,
        inventory: dict[str, Any],
        source_root: Path,
        by_path: dict[str, dict[str, Any]],
        manifests: dict[str, TileManifest],
        paths: list[str],
        *,
        provider_scene_id: str | None = None,
    ) -> dict[str, Any]:
        """Wybierz wariant jednej logicznej akwizycji/produktu WorldView."""
        components = worldview_grammar.group_by_component(paths)

        pansharpened = components.get(worldview_grammar.COMPONENT_PANSHARPENED) or []
        multispectral = components.get(worldview_grammar.COMPONENT_MULTISPECTRAL) or []
        panchromatic = components.get(worldview_grammar.COMPONENT_PANCHROMATIC) or []

        if pansharpened:
            return self._component_selection(
                inventory, by_path, manifests,
                {worldview_grammar.COMPONENT_PANSHARPENED: pansharpened},
                product_type="PANSHARPENED_RGB", status="ready", rgb_bands=[1, 2, 3],
                primary_component=worldview_grammar.COMPONENT_PANSHARPENED,
                provider_scene_id=provider_scene_id,
            )

        if multispectral and panchromatic:
            rgb_bands = self._confirmed_rgb_bands(source_root, inventory, multispectral)
            selection = self._component_selection(
                inventory, by_path, manifests,
                {
                    worldview_grammar.COMPONENT_MULTISPECTRAL: multispectral,
                    worldview_grammar.COMPONENT_PANCHROMATIC: panchromatic,
                },
                product_type="MUL+PAN",
                status="prepare_required" if rgb_bands else "decision_required",
                primary_component=worldview_grammar.COMPONENT_MULTISPECTRAL,
                raster_kind="derived",
                rgb_bands=rgb_bands,
                provider_scene_id=provider_scene_id,
            )
            # Te listy sa bezposrednim wejsciem `prepare_pansharpened_cog`; musza miec te sama
            # kolejnosc z TIL co mozaiki komponentow, a nie kolejnosc zwrocona przez katalog.
            selection["multispectral_asset_ids"] = list(
                selection["source_components"][worldview_grammar.COMPONENT_MULTISPECTRAL]
            )
            selection["panchromatic_asset_ids"] = list(
                selection["source_components"][worldview_grammar.COMPONENT_PANCHROMATIC]
            )
            if not rgb_bands:
                if not selection["alternatives"]:
                    selection["alternatives"] = [{
                        "asset_ids": list(selection["asset_ids"]),
                        "label": "MUL + PAN (RGB band mapping required)",
                    }]
                selection["diagnostics"]["warnings"].append({
                    "code": "rgb_mapping_required",
                    "message": "WorldView RGB band order could not be confirmed from IMD metadata",
                })
            return selection

        if multispectral:
            return self._component_selection(
                inventory, by_path, manifests,
                {worldview_grammar.COMPONENT_MULTISPECTRAL: multispectral},
                product_type="MUL", status="ready",
                primary_component=worldview_grammar.COMPONENT_MULTISPECTRAL,
                rgb_bands=self._confirmed_rgb_bands(source_root, inventory, multispectral),
                provider_scene_id=provider_scene_id,
            )

        if panchromatic:
            # PAN-only jest PELNOPRAWNA scena panchromatyczna. Nie nadajemy jej `rgb_bands`:
            # produkt jednopasmowy nie ma odwzorowania RGB, a udawanie, ze ma, wprowadzaloby
            # w blad warstwe wyswietlania.
            return self._component_selection(
                inventory, by_path, manifests,
                {worldview_grammar.COMPONENT_PANCHROMATIC: panchromatic},
                product_type="PAN", status="ready",
                primary_component=worldview_grammar.COMPONENT_PANCHROMATIC,
                provider_scene_id=provider_scene_id,
            )

        unknown = [by_path[path] for path in paths if path in by_path]
        return self._decision(inventory, unknown, "WorldView product components were not recognised")

    def _tile_manifests(self, inventory: dict[str, Any], source_root: Path) -> dict[str, TileManifest]:
        """Wczytaj TIL kazdego PRODUKTU i komponentu. Brak TIL to pusty manifest, nie blad."""
        manifests: dict[str, TileManifest] = {}
        for asset in inventory.get("assets", []):
            if str(asset.get("format") or "").casefold() != "til":
                continue
            relative = str(asset.get("package_relative_path") or "")
            parsed = worldview_grammar.parse_path(relative)
            key = parsed.product_key or parsed.component or ""
            # Nie nadpisuj po cichu pierwszego TIL drugim. Rozdzielenie `P001`/`P002` w kluczu
            # usuwa poprawne kolizje; pozostala oznacza niejednoznaczna paczke.
            manifests.setdefault(
                key,
                read_tile_manifest(source_root / str(asset.get("relative_path") or "")),
            )
        return manifests

    @staticmethod
    def _manifest_for_paths(paths: list[str], manifests: dict[str, TileManifest]) -> TileManifest:
        if not paths:
            return EMPTY_MANIFEST
        parsed = worldview_grammar.parse_path(paths[0])
        return manifests.get(parsed.product_key or parsed.component or "") or EMPTY_MANIFEST

    def _component_selection(
        self,
        inventory: dict[str, Any],
        by_path: dict[str, dict[str, Any]],
        manifests: dict[str, TileManifest],
        components: dict[str, list[str]],
        *,
        product_type: str,
        status: str,
        primary_component: str,
        raster_kind: str | None = None,
        rgb_bands: list[int] | None = None,
        provider_scene_id: str | None = None,
    ) -> dict[str, Any]:
        """Zbuduj selekcje z czesci uporzadkowanych wedlug TIL i oceniona kompletnoscia.

        `primary_component` wskazuje komponent, ktory OPISUJE wynikowa scene. Dla produktu
        pochodnego MUL+PAN jest nim multispectral: working view ma byc pansharpened RGB, wiec
        charakterystyke spektralna wnosi MUL, a PAN dokłada wylacznie szczegol przestrzenny.
        Sekcja 4.6 odnotowala odwrotnosc — wynikowa metadata opisywala glownie PAN.
        """
        ordered_paths: list[str] = []
        declared_total = 0
        missing_total: list[str] = []
        unnamed_missing_total = 0
        source_components: dict[str, list[str]] = {}

        for component in sorted(components):
            manifest = self._manifest_for_paths(components[component], manifests)
            paths = sorted(
                components[component],
                key=lambda item, manifest=manifest: (manifest.order_key(item), natural_key(item)),
            )
            ordered_paths.extend(paths)
            source_components[component] = [by_path[path]["asset_id"] for path in paths]
            if not manifest.is_empty:
                declared_total += manifest.expected_count
                missing_total.extend(manifest.missing(paths))
                unnamed_missing_total += manifest.unnamed_declared_count

        assets = [by_path[path] for path in ordered_paths]
        if raster_kind is None:
            raster_kind = "direct" if len(assets) == 1 else "virtual_mosaic"

        # `_selection()` sortuje po nazwie; kolejnosc z TIL nadpisujemy po jego wywolaniu,
        # bo to deklaracja dostawcy jest wiazaca, a nie porzadek leksykalny.
        selection = self._selection(
            inventory, assets, product_type=product_type, status=status,
            raster_kind=raster_kind, rgb_bands=rgb_bands,
            provider_scene_id=provider_scene_id,
        )
        selection["asset_ids"] = [asset["asset_id"] for asset in assets]
        selection["identity_asset_ids"] = list(selection["asset_ids"])
        selection["mosaic_parts_order"] = [
            asset.get("part_id") for asset in assets if asset.get("part_id")
        ]
        selection["source_components"] = source_components
        selection["declared_parts"] = declared_total or None
        selection["completeness"] = (
            "unknown"
            if not declared_total
            else "partial"
            if missing_total or unnamed_missing_total
            else "complete"
        )

        if missing_total or unnamed_missing_total:
            # Bramka P0.6: brakujaca czesc wymieniona w TIL BLOKUJE `ready`. Uzytkownik moze
            # swiadomie zaimportowac niepelne pokrycie, ale musi to byc jego decyzja.
            selection["status"] = "decision_required"
            selection["diagnostics"]["warnings"].append({
                "code": "partial_delivery",
                "message": (
                    f"{len(missing_total) + unnamed_missing_total} of {declared_total} part(s) declared by the TIL "
                    "manifest are missing from the delivery"
                ),
            })
            selection["missing_parts"] = list(missing_total)
            selection["missing_parts_unknown_count"] = unnamed_missing_total
            selection["alternatives"] = [{
                "asset_ids": list(selection["asset_ids"]),
                "label": (
                    f"Import available coverage only ({len(assets)}/{declared_total} parts; partial)"
                ),
            }]
            if unnamed_missing_total:
                selection["diagnostics"]["warnings"].append({
                    "code": "tile_manifest_count_mismatch",
                    "message": (
                        f"TIL declares {declared_total} part(s), but names only "
                        f"{declared_total - unnamed_missing_total}"
                    ),
                })

        component_paths = {component: list(paths) for component, paths in components.items()}
        self._bind_worldview_metadata(
            inventory, selection, measurement=component_paths.get(primary_component) or ordered_paths
        )
        selection["primary_component"] = primary_component
        # Metadane pozostalych komponentow NIE znikaja — sa dostepne osobno, zeby dalo sie
        # je pokazac i uzyc, ale nie sa scalane z metadanymi komponentu wiodacego. To wlasnie
        # scalanie dawalo szesc pozornych konfliktow (`band_id`, GSD, czas co do mikrosekundy).
        selection["component_metadata_asset_ids"] = {
            component: self._component_metadata_ids(inventory, paths)
            for component, paths in sorted(component_paths.items())
        }
        return selection

    def _component_metadata_ids(self, inventory: dict[str, Any], paths: list[str]) -> list[str]:
        by_path = {
            str(asset.get("package_relative_path") or ""): asset
            for asset in inventory.get("assets", [])
        }
        metadata = [path for path, asset in by_path.items() if asset.get("role") == "metadata"]
        bound = worldview_grammar.bind_metadata_to_products(paths, metadata)
        return [
            by_path[path]["asset_id"]
            for path in sorted({item for values in bound.values() for item in values})
            if path in by_path
        ]

    def _bind_worldview_metadata(
        self,
        inventory: dict[str, Any],
        selection: dict[str, Any],
        measurement: "list[str] | None" = None,
    ) -> None:
        """Zwiaz `.IMD`/`.TIL`/`.RPB` z KOMPONENTEM, nie z folderem.

        Sekcja 4.6: parser scalal metadata PAN i MUL w jeden obiekt, dajac szesc pozornych
        konfliktow i wynikowa metadata opisujaca glownie PAN.
        """
        self._bind_metadata_with(
            inventory,
            selection,
            bind=worldview_grammar.bind_metadata_to_products,
            orphans=worldview_grammar.orphan_metadata,
            is_complex=lambda path: False,
            measurement_override=measurement,
        )

    def _rank_legacy(self, inventory: dict[str, Any], source_root: Path) -> dict[str, Any]:
        ready = self._matching(inventory, r"(?:PSH|PANSHARP|RGB).*\.(?:tif|tiff)$")
        if ready:
            return self._selection(inventory, ready, product_type="PANSHARPENED_RGB", rgb_bands=[1, 2, 3])
        mul = self._matching(inventory, r"(?:_MUL[/\\]|M2AS|MULTI).*\.(?:tif|tiff)$")
        pan = self._matching(inventory, r"(?:_PAN[/\\]|P1BS|PAN).*\.(?:tif|tiff)$")
        if mul and pan:
            rgb_bands = self._confirmed_rgb_bands(source_root, inventory)
            selected = sorted(mul + pan, key=lambda item: natural_key(item.get("package_relative_path", "")))
            status = "prepare_required" if rgb_bands else "decision_required"
            result = self._selection(
                inventory,
                selected,
                product_type="MUL+PAN",
                status=status,
                raster_kind="derived",
                rgb_bands=rgb_bands,
                alternatives=[] if rgb_bands else [{
                    "asset_ids": [asset["asset_id"] for asset in selected],
                    "label": "MUL + PAN (RGB band mapping required)",
                }],
            )
            result["multispectral_asset_ids"] = [asset["asset_id"] for asset in mul]
            result["panchromatic_asset_ids"] = [asset["asset_id"] for asset in pan]
            if not rgb_bands:
                result["diagnostics"]["warnings"].append({
                    "code": "rgb_mapping_required",
                    "message": "WorldView RGB band order could not be confirmed from IMD metadata",
                })
            return result
        return self._decision(inventory, self._rasters(inventory), "WorldView pansharpened RGB or MUL+PAN was not found")

    @staticmethod
    def _confirmed_rgb_bands(
        source_root: Path,
        inventory: dict[str, Any],
        measurement_paths: list[str] | None = None,
    ) -> list[int] | None:
        metadata = [asset for asset in inventory.get("assets", []) if asset.get("format") == "imd"]
        if measurement_paths:
            metadata_by_path = {
                str(asset.get("package_relative_path") or ""): asset for asset in metadata
            }
            bound = worldview_grammar.bind_metadata_to_products(
                measurement_paths,
                list(metadata_by_path),
            )
            selected_metadata = {
                path for paths in bound.values() for path in paths
            }
            metadata = [
                metadata_by_path[path]
                for path in sorted(selected_metadata)
                if path in metadata_by_path
            ]
        for asset in metadata:
            path = source_root / asset["relative_path"]
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").upper()
            except OSError:
                continue
            # Standard WV 8-band products explicitly enumerate these groups.
            if all(token in text for token in ("BAND_C", "BAND_B", "BAND_G", "BAND_R")):
                return [5, 3, 2]
            if all(token in text for token in ("BAND_B", "BAND_G", "BAND_R")):
                return [3, 2, 1]
        return None


_RESOLVERS: dict[str, ProviderResolver] = {
    resolver.provider: resolver
    for resolver in (
        GenericResolver(), IceyeResolver(), CapellaResolver(), UmbraResolver(),
        PleiadesNeoResolver(), WorldViewResolver(), BlackSkyResolver(),
    )
}


def supported_providers() -> list[str]:
    return sorted(_RESOLVERS)


def get_resolver(provider: str) -> ProviderResolver:
    try:
        return _RESOLVERS[provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported scene provider: {provider}") from exc


def resolve_package_candidate(
    resolver: ProviderResolver,
    source_root: Path,
    candidate: PackageCandidate,
) -> list[dict[str, Any]]:
    """Resolve one candidate into one or more logical scene packages."""

    inventory = resolver.build_inventory(source_root, candidate)
    # Archiwum ma wlasna sciezke opisu (P1.3a). Rozdzial jest tutaj, a nie w `rank_products`
    # kazdego dostawcy, bo klasyfikacja archiwum nie zalezy od gramatyki dostawcy — wynika z
    # central directory i z tego, co lezy obok na dysku.
    if inventory.get("package_kind") == "archive":
        return [{**inventory, "selection": resolver.rank_archive(inventory, source_root)}]
    selection = resolver.rank_products(inventory, source_root)
    logical_selections = selection.pop("logical_selections", [])
    if logical_selections:
        return [
            {
                **inventory,
                "package_id": stable_id(
                    "pkg",
                    f"{inventory['package_id']}:{','.join(logical.get('identity_asset_ids') or [])}",
                ),
                "selection": logical,
            }
            for logical in logical_selections
        ]
    return [{**inventory, "selection": selection}]


def scan_source(root: Path, provider: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    resolver = get_resolver(provider)
    diagnostics = resolver.validate_source(root)
    if any(item.get("level") == "error" for item in diagnostics):
        return [], diagnostics
    packages: list[dict[str, Any]] = []
    seen_roots: set[str] = set()
    for candidate in resolver.discover_packages(root):
        key = str(candidate.root.resolve(strict=False)).casefold()
        if key in seen_roots:
            continue
        seen_roots.add(key)
        packages.extend(resolve_package_candidate(resolver, root, candidate))
    return packages, diagnostics
