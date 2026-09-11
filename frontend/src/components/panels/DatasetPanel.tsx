import type { ReactNode } from "react";
import {
  Box,
  VStack,
  HStack,
  Text,
  Slider,
  SliderTrack,
  SliderFilledTrack,
  SliderThumb,
  Button,
  FormControl,
  FormLabel,
  NumberInput,
  NumberInputField,
  Input,
  Select,
  useColorModeValue,
  SimpleGrid,
  Checkbox,
  Radio,
  RadioGroup,
  Alert,
  AlertIcon,
  Badge,
  Divider,
} from "@chakra-ui/react";
import Card from "../common/Card";
import InfoPopover from "../common/InfoPopover";
import type {
  ClassMerge,
  DatasetConfig,
  DatasetFilterOptions,
  PreprocessingProfile,
  ProjectGeoreferencing,
  ProjectModality,
} from "../../types";
import { useTranslation } from "react-i18next";

interface Props {
  config: DatasetConfig;
  onConfigChange: (config: DatasetConfig) => void;
  onGenerate: () => void;
  isGenerating?: boolean;
  progress?: { done: number; total: number } | null;
  preprocessingProfiles?: PreprocessingProfile[];
  projectModality?: ProjectModality;
  projectGeoreferencing?: ProjectGeoreferencing;
  filterOptions?: DatasetFilterOptions;
  /** Parametry siatki kafli (z Siatki przeglądu) — pokazywane w Build jako read-only. */
  tiling?: { tile_size: number; buffer: number } | null;
}

export default function DatasetPanel({
  config,
  onConfigChange,
  onGenerate,
  isGenerating,
  progress,
  preprocessingProfiles = [],
  projectModality,
  projectGeoreferencing,
  filterOptions = { catalog_id: null, scenes: [], classes: [], authors: [], annotation_sources: [] },
  tiling = null,
}: Props) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  const labelColor = useColorModeValue("secondaryGray.600", "whiteAlpha.700");
  const borderColor = useColorModeValue("gray.200", "whiteAlpha.300");

  const updateConfig = (patch: Partial<DatasetConfig>) => {
    onConfigChange({ ...config, ...patch });
  };

  const updateRatio = (key: "train_ratio" | "val_ratio", value: number) => {
    const newConfig = { ...config, [key]: value };
    newConfig.test_ratio = Math.max(0, 1 - newConfig.train_ratio - newConfig.val_ratio);
    onConfigChange(newConfig);
  };
  const selectedProfile = preprocessingProfiles.find(
    (profile) => profile.profile_id === config.preprocessing_profile_id
  );
  const profileMismatch = !!(
    selectedProfile && projectModality && selectedProfile.modality !== projectModality
  );
  const toggleStringFilter = (
    key: "scene_ids" | "annotator_emails" | "annotation_sources",
    value: string,
    checked: boolean,
  ) => {
    const current = config[key] || [];
    updateConfig({ [key]: checked ? [...new Set([...current, value])] : current.filter((item) => item !== value) });
  };

  const toggleClassFilter = (classId: number, checked: boolean) => {
    const current = config.class_ids || [];
    updateConfig({ class_ids: checked ? [...new Set([...current, classId])] : current.filter((item) => item !== classId) });
  };

  // Numeric range field: empty clears the bound; `min` guards backend validation
  // (GSD must be > 0). Mirrors the backend range filters in dataset.py.
  const updateNumberBound = (
    key: "gsd_min_m" | "gsd_max_m" | "incidence_min_deg" | "incidence_max_deg",
    valueString: string,
    min: number,
  ) => {
    const parsed = Number(valueString);
    updateConfig({ [key]: valueString.trim() && !Number.isNaN(parsed) && parsed >= min ? parsed : null });
  };

  const parseMetaDate = (value?: string | null): number | null => {
    if (!value) return null;
    const iso = value.length <= 10 ? `${value}T00:00:00Z` : value;
    const ms = new Date(iso).getTime();
    return Number.isNaN(ms) ? null : ms;
  };

  const metaRanges = filterOptions.metadata_ranges;
  // Per-scene metadata only exists once the tile catalog is (re)built with this
  // version; on an older catalog it is absent, so the live counter would be
  // misleading (the build-time filter still reads live scene data and works).
  const hasSceneMetadata = filterOptions.scenes.some(
    (scene) =>
      scene.gsd_m != null ||
      scene.incidence_angle_deg != null ||
      scene.acquisition_datetime_utc != null,
  );
  const anyMetaFilter =
    config.gsd_min_m != null ||
    config.gsd_max_m != null ||
    config.incidence_min_deg != null ||
    config.incidence_max_deg != null ||
    !!config.acquired_after ||
    !!config.acquired_before;

  // Live "X of Y scenes" preview — mirrors scene_matches_metadata_filters on the
  // backend (a scene missing the metadata a filter needs is excluded).
  const matchingSceneCount = filterOptions.scenes.filter((scene) => {
    if (config.gsd_min_m != null || config.gsd_max_m != null) {
      if (typeof scene.gsd_m !== "number") return false;
      if (config.gsd_min_m != null && scene.gsd_m < config.gsd_min_m) return false;
      if (config.gsd_max_m != null && scene.gsd_m > config.gsd_max_m) return false;
    }
    if (config.incidence_min_deg != null || config.incidence_max_deg != null) {
      if (typeof scene.incidence_angle_deg !== "number") return false;
      if (config.incidence_min_deg != null && scene.incidence_angle_deg < config.incidence_min_deg) return false;
      if (config.incidence_max_deg != null && scene.incidence_angle_deg > config.incidence_max_deg) return false;
    }
    if (config.acquired_after || config.acquired_before) {
      const acq = parseMetaDate(scene.acquisition_datetime_utc);
      if (acq == null) return false;
      const after = parseMetaDate(config.acquired_after);
      const before = parseMetaDate(config.acquired_before);
      if (after != null && acq < after) return false;
      if (before != null && acq > before) return false;
    }
    return true;
  }).length;

  // --- Łączenie klas (nieniszczące; nowa wersja datasetu) ---
  const merges: ClassMerge[] = config.class_merges || [];
  const mergedAwayCount = merges.reduce((sum, group) => sum + group.members.length, 0);
  const effectiveClassCount = Math.max(0, filterOptions.classes.length - mergedAwayCount);
  const usedInMerges = new Set<number>();
  merges.forEach((group) => {
    usedInMerges.add(group.into);
    group.members.forEach((id) => usedInMerges.add(id));
  });
  const updateMerges = (next: ClassMerge[]) => updateConfig({ class_merges: next });
  const addMerge = () => {
    const firstFree = filterOptions.classes.find((c) => !usedInMerges.has(c.class_id));
    if (!firstFree) return;
    updateMerges([...merges, { into: firstFree.class_id, members: [] }]);
  };
  const removeMerge = (index: number) => updateMerges(merges.filter((_, i) => i !== index));
  const setMergeInto = (index: number, into: number) =>
    updateMerges(
      merges.map((group, i) =>
        i === index ? { into, members: group.members.filter((x) => x !== into) } : group,
      ),
    );
  const toggleMember = (index: number, id: number, checked: boolean) =>
    updateMerges(
      merges.map((group, i) => {
        if (i !== index) return group;
        const members = checked
          ? [...new Set([...group.members, id])]
          : group.members.filter((x) => x !== id);
        return { ...group, members };
      }),
    );
  const usedElsewhere = (index: number): Set<number> => {
    const set = new Set<number>();
    merges.forEach((group, i) => {
      if (i === index) return;
      set.add(group.into);
      group.members.forEach((x) => set.add(x));
    });
    return set;
  };

  return (
    <Card>
      <VStack align="stretch" spacing={4}>
        <Text fontWeight="bold" color={textColor}>
          {t("Build Configuration")}
        </Text>

        {tiling && (
          <Box borderWidth="1px" borderColor={borderColor} borderRadius="md" p={3}>
            <HStack justify="space-between" flexWrap="wrap" gap={2}>
              <HStack spacing={1}>
                <Text fontSize="sm" fontWeight="semibold" color={textColor}>{t("Tile grid")}</Text>
                <InfoPopover
                  titleKey="info.dataset.tileGrid.title"
                  bodyKey="info.dataset.tileGrid.body"
                  helpPage="annotation/siatka-przegladu.html"
                />
              </HStack>
              <Badge colorScheme="gray">{t("from Review grid")}</Badge>
            </HStack>
            <Text fontSize="sm" color={labelColor} mt={1}>
              {t("Tile size")}: <b>{tiling.tile_size} px</b> · {t("Overlap")}: <b>{tiling.buffer} px</b>
            </Text>
            <Text fontSize="xs" color={labelColor} mt={1}>
              {t("Inherited from the Review grid - the dataset is tiled the same way you review it. Change it in the labeling window → Review grid.")}
            </Text>
          </Box>
        )}

        <FormControl>
          <FormLabel fontSize="sm" color={labelColor}>
            <HStack spacing={1}>
              <Text>{t("Dataset preprocessing profile")}</Text>
              <InfoPopover
                titleKey="info.dataset.preprocessingProfile.title"
                bodyKey="info.dataset.preprocessingProfile.body"
              />
            </HStack>
          </FormLabel>
          <Select
            size="sm"
            value={config.preprocessing_profile_id || ""}
            onChange={(event) => updateConfig({ preprocessing_profile_id: event.target.value })}
          >
            {preprocessingProfiles.map((profile) => (
              <option key={profile.profile_id} value={profile.profile_id}>
                {profile.name} ({profile.modality})
              </option>
            ))}
          </Select>
          {selectedProfile && (
            <Text mt={1} fontSize="xs" color={profileMismatch ? "orange.400" : labelColor}>
              v{selectedProfile.profile_version} · {selectedProfile.radiometric_transform} · {selectedProfile.profile_hash.slice(0, 12)}
              {profileMismatch ? ` · ${t("Profile modality mismatch", { profile: selectedProfile.modality, project: projectModality })}` : ""}
            </Text>
          )}
        </FormControl>

        <Divider />
        <VStack align="stretch" spacing={3}>
          <HStack justify="space-between">
            <Text fontWeight="semibold" color={textColor}>{t("Dataset source filters")}</Text>
            {filterOptions.catalog_id && <Badge colorScheme="purple">{filterOptions.catalog_id.slice(0, 10)}</Badge>}
          </HStack>
          <Text fontSize="xs" color={labelColor}>
            {t("Empty selections include every available record from the raw tile catalog.")}
          </Text>
          {!filterOptions.catalog_id ? (
            <Text fontSize="sm" color="orange.400">{t("Build the tile catalog to configure source filters.")}</Text>
          ) : (
            <SimpleGrid columns={{ base: 1, lg: 4 }} spacing={3}>
              <FilterList title={t("Scenes")} emptyLabel={t("No scenes")}>
                {filterOptions.scenes.map((scene) => (
                  <Checkbox key={scene.scene_id} size="sm" isChecked={(config.scene_ids || []).includes(scene.scene_id)}
                    onChange={(event) => toggleStringFilter("scene_ids", scene.scene_id, event.target.checked)}>
                    <Text fontSize="xs" noOfLines={1} title={scene.filename}>{scene.filename}</Text>
                  </Checkbox>
                ))}
              </FilterList>
              <FilterList title={t("Classes")} emptyLabel={t("No classes")}>
                {filterOptions.classes.map((item) => (
                  <Checkbox key={item.class_id} size="sm" isChecked={(config.class_ids || []).includes(item.class_id)}
                    onChange={(event) => toggleClassFilter(item.class_id, event.target.checked)}>
                    <Text fontSize="xs">{item.name} ({item.annotation_link_count})</Text>
                  </Checkbox>
                ))}
              </FilterList>
              <FilterList title={t("Authors")} emptyLabel={t("No authors")}>
                {filterOptions.authors.map((item) => (
                  <Checkbox key={item.email} size="sm" isChecked={(config.annotator_emails || []).includes(item.email)}
                    onChange={(event) => toggleStringFilter("annotator_emails", item.email, event.target.checked)}>
                    <Text fontSize="xs" noOfLines={1} title={item.email}>{item.email} ({item.annotation_link_count})</Text>
                  </Checkbox>
                ))}
              </FilterList>
              <FilterList title={t("Annotation sources")} emptyLabel={t("No sources")}>
                {filterOptions.annotation_sources.map((item) => (
                  <Checkbox key={item.source} size="sm" isChecked={(config.annotation_sources || []).includes(item.source)}
                    onChange={(event) => toggleStringFilter("annotation_sources", item.source, event.target.checked)}>
                    <Text fontSize="xs">{item.source} ({item.annotation_link_count})</Text>
                  </Checkbox>
                ))}
              </FilterList>
            </SimpleGrid>
          )}
        </VStack>

        <Divider />

        <VStack align="stretch" spacing={3}>
          <HStack justify="space-between">
            <Text fontWeight="semibold" color={textColor}>{t("Metadata filters")}</Text>
            {anyMetaFilter && hasSceneMetadata && (
              <Badge colorScheme={matchingSceneCount === 0 ? "red" : "purple"}>
                {matchingSceneCount} / {filterOptions.scenes.length} {t("scenes_short")}
              </Badge>
            )}
          </HStack>
          <Text fontSize="xs" color={labelColor}>
            {t("Restrict which scenes enter the dataset by their metadata. A scene missing a value is excluded when that filter is active. Empty = no limit.")}
          </Text>
          {!filterOptions.catalog_id ? (
            <Text fontSize="sm" color="orange.400">{t("Build the tile catalog to configure metadata filters.")}</Text>
          ) : (
            <SimpleGrid columns={{ base: 1, lg: 3 }} spacing={3}>
              <FormControl>
                <FormLabel fontSize="sm" color={labelColor}>{t("GSD range (m)")}</FormLabel>
                <HStack spacing={2}>
                  <NumberInput size="sm" min={0} step={0.1} value={config.gsd_min_m ?? ""}
                    onChange={(v) => updateNumberBound("gsd_min_m", v, 0.0000001)}>
                    <NumberInputField placeholder={t("min")} />
                  </NumberInput>
                  <NumberInput size="sm" min={0} step={0.1} value={config.gsd_max_m ?? ""}
                    onChange={(v) => updateNumberBound("gsd_max_m", v, 0.0000001)}>
                    <NumberInputField placeholder={t("max")} />
                  </NumberInput>
                </HStack>
                <Text mt={1} fontSize="xs" color={labelColor}>
                  {metaRanges?.gsd_m
                    ? t("Available: {{min}}–{{max}} m ({{count}} scenes)", {
                        min: metaRanges.gsd_m.min.toFixed(2),
                        max: metaRanges.gsd_m.max.toFixed(2),
                        count: metaRanges.gsd_m.count,
                      })
                    : t("No GSD metadata on scenes.")}
                </Text>
              </FormControl>

              <FormControl>
                <FormLabel fontSize="sm" color={labelColor}>{t("SAR incidence angle (°)")}</FormLabel>
                <HStack spacing={2}>
                  <NumberInput size="sm" min={0} max={90} step={1} value={config.incidence_min_deg ?? ""}
                    onChange={(v) => updateNumberBound("incidence_min_deg", v, 0)}>
                    <NumberInputField placeholder={t("min")} />
                  </NumberInput>
                  <NumberInput size="sm" min={0} max={90} step={1} value={config.incidence_max_deg ?? ""}
                    onChange={(v) => updateNumberBound("incidence_max_deg", v, 0)}>
                    <NumberInputField placeholder={t("max")} />
                  </NumberInput>
                </HStack>
                <Text mt={1} fontSize="xs" color={labelColor}>
                  {metaRanges?.incidence_angle_deg
                    ? t("Available: {{min}}–{{max}}° ({{count}} scenes)", {
                        min: metaRanges.incidence_angle_deg.min.toFixed(1),
                        max: metaRanges.incidence_angle_deg.max.toFixed(1),
                        count: metaRanges.incidence_angle_deg.count,
                      })
                    : t("SAR-only; no incidence metadata on scenes.")}
                </Text>
              </FormControl>

              <FormControl>
                <FormLabel fontSize="sm" color={labelColor}>{t("Acquisition date")}</FormLabel>
                <HStack spacing={2}>
                  <Input type="date" size="sm" value={(config.acquired_after ?? "").slice(0, 10)}
                    onChange={(e) => updateConfig({ acquired_after: e.target.value || null })} />
                  <Input type="date" size="sm" value={(config.acquired_before ?? "").slice(0, 10)}
                    onChange={(e) => updateConfig({ acquired_before: e.target.value || null })} />
                </HStack>
                <Text mt={1} fontSize="xs" color={labelColor}>
                  {metaRanges?.acquisition_datetime_utc
                    ? t("Available: {{min}} – {{max}}", {
                        min: metaRanges.acquisition_datetime_utc.min.slice(0, 10),
                        max: metaRanges.acquisition_datetime_utc.max.slice(0, 10),
                      })
                    : t("No acquisition dates on scenes.")}
                </Text>
              </FormControl>
            </SimpleGrid>
          )}
          {anyMetaFilter && hasSceneMetadata && matchingSceneCount === 0 && filterOptions.scenes.length > 0 && (
            <Text fontSize="xs" color="red.400">
              {t("No scenes match these filters - the build will fail. Widen the ranges.")}
            </Text>
          )}
        </VStack>

        <Divider />

        <VStack align="stretch" spacing={3}>
          <HStack justify="space-between" flexWrap="wrap" gap={2}>
            <HStack spacing={1}>
              <Text fontWeight="semibold" color={textColor}>{t("Class merges")}</Text>
              <InfoPopover
                titleKey="info.dataset.classMerges.title"
                bodyKey="info.dataset.classMerges.body"
                helpPage="datasets/zbuduj-dataset.html"
              />
            </HStack>
            {merges.length > 0 && (
              <Badge colorScheme="purple">
                {filterOptions.classes.length} → {effectiveClassCount} {t("classes").toLowerCase()}
              </Badge>
            )}
          </HStack>
          <Text fontSize="xs" color={labelColor}>
            {t("Non-destructively fold classes into one for this dataset version. Source annotations are untouched - generate a new version to compare.")}
          </Text>
          {!filterOptions.catalog_id ? (
            <Text fontSize="sm" color="orange.400">{t("Build the tile catalog to configure class merges.")}</Text>
          ) : (
            <>
              {merges.map((group, index) => {
                const blocked = usedElsewhere(index);
                const intoOptions = filterOptions.classes.filter(
                  (c) => c.class_id === group.into || !blocked.has(c.class_id),
                );
                const memberOptions = filterOptions.classes.filter(
                  (c) => c.class_id !== group.into && !blocked.has(c.class_id),
                );
                return (
                  <Box key={index} borderWidth="1px" borderColor={borderColor} borderRadius="md" p={3}>
                    <HStack justify="space-between" mb={2} align="flex-end">
                      <FormControl maxW="260px">
                        <FormLabel fontSize="xs" color={labelColor} mb={1}>{t("Primary class (kept)")}</FormLabel>
                        <Select
                          size="sm"
                          value={group.into}
                          onChange={(event) => setMergeInto(index, Number(event.target.value))}
                        >
                          {intoOptions.map((c) => (
                            <option key={c.class_id} value={c.class_id}>{c.name}</option>
                          ))}
                        </Select>
                      </FormControl>
                      <Button size="xs" variant="ghost" colorScheme="red" onClick={() => removeMerge(index)}>
                        {t("Remove")}
                      </Button>
                    </HStack>
                    <Text fontSize="xs" color={labelColor} mb={1}>{t("Folded into it")}</Text>
                    {memberOptions.length === 0 ? (
                      <Text fontSize="xs" color={labelColor}>{t("No other classes available.")}</Text>
                    ) : (
                      <SimpleGrid columns={{ base: 2, md: 3 }} spacing={1}>
                        {memberOptions.map((c) => (
                          <Checkbox
                            key={c.class_id}
                            size="sm"
                            isChecked={group.members.includes(c.class_id)}
                            onChange={(event) => toggleMember(index, c.class_id, event.target.checked)}
                          >
                            <Text fontSize="xs" noOfLines={1} title={c.name}>{c.name}</Text>
                          </Checkbox>
                        ))}
                      </SimpleGrid>
                    )}
                  </Box>
                );
              })}
              <Button
                size="sm"
                variant="outline"
                alignSelf="flex-start"
                onClick={addMerge}
                isDisabled={usedInMerges.size >= filterOptions.classes.length}
              >
                {t("Add merge")}
              </Button>
            </>
          )}
        </VStack>

        <Divider />

        <HStack spacing={1}>
          <Text fontSize="sm" fontWeight="semibold" color={textColor}>
            {t("Train / validation / test split")}
          </Text>
          <InfoPopover titleKey="info.dataset.splitRatios.title" bodyKey="info.dataset.splitRatios.body" />
        </HStack>

        <VStack align="stretch" spacing={1}>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Train")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>
              {Math.round(config.train_ratio * 100)}%
            </Text>
          </HStack>
          <Slider
            min={0.1}
            max={0.9}
            step={0.05}
            value={config.train_ratio}
            onChange={(v) => updateRatio("train_ratio", v)}
          >
            <SliderTrack><SliderFilledTrack /></SliderTrack>
            <SliderThumb />
          </Slider>
        </VStack>

        <VStack align="stretch" spacing={1}>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Validation")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>
              {Math.round(config.val_ratio * 100)}%
            </Text>
          </HStack>
          <Slider
            min={0.05}
            max={0.5}
            step={0.05}
            value={config.val_ratio}
            onChange={(v) => updateRatio("val_ratio", v)}
          >
            <SliderTrack><SliderFilledTrack /></SliderTrack>
            <SliderThumb />
          </Slider>
        </VStack>

        <Text fontSize="xs" color={labelColor}>
          {t("Test")}: {Math.round(config.test_ratio * 100)}%
        </Text>

        <FormControl>
          <FormLabel fontSize="sm" color={labelColor}>
            <HStack spacing={1}>
              <Text>{t("Split Mode")}</Text>
              <InfoPopover titleKey="info.dataset.splitMode.title" bodyKey="info.dataset.splitMode.body" />
            </HStack>
          </FormLabel>
          <Select
            size="sm"
            value={config.split_mode || "random_tile"}
            onChange={(e) => updateConfig({ split_mode: e.target.value as DatasetConfig["split_mode"] })}
          >
            <option value="random_tile">{t("Random tile")}</option>
            <option value="scene_split">{t("Scene split")}</option>
            <option value="image_block_split">{t("Image block split")}</option>
            <option value="spatial_block_split">{t("Spatial block split")}</option>
            <option value="class_balanced_spatial">{t("Class balanced spatial")}</option>
          </Select>
          <Text mt={1} fontSize="xs" color={labelColor}>
            {t("Spatial modes keep neighboring tiles in the same split to reduce leakage.")}
          </Text>
          {projectGeoreferencing === "NO_GEO" && ["spatial_block_split", "class_balanced_spatial"].includes(config.split_mode) && (
            <Text mt={1} fontSize="xs" color="orange.400">
              {t("NO_GEO data cannot use geographic blocks; image-block fallback will be used.")}
            </Text>
          )}
          {projectGeoreferencing === "GEO" && config.split_mode === "image_block_split" && (
            <Text mt={1} fontSize="xs" color="orange.400">
              {t("For GEO projects, spatial block split better protects overlapping areas across scenes.")}
            </Text>
          )}
        </FormControl>

        <FormControl>
          <FormLabel fontSize="sm" color={labelColor}>{t("Resample to common GSD (m, optional)")}</FormLabel>
          <NumberInput
            size="sm"
            min={0}
            step={0.1}
            value={config.target_gsd_m ?? ""}
            onChange={(valueString) => {
              const parsed = Number(valueString);
              updateConfig({ target_gsd_m: valueString.trim() && parsed > 0 ? parsed : null });
            }}
          >
            <NumberInputField placeholder={t("Native resolution")} />
          </NumberInput>
          <Text mt={1} fontSize="xs" color={labelColor}>
            {t("Resample every tile to this ground resolution so scenes of different GSD train at a consistent object scale. Empty = native. Re-tiles from source annotations; per-tile review flags do not apply.")}
          </Text>
          <Text mt={1} fontSize="xs" color={labelColor} fontStyle="italic">
            {t("This rescales pixels - it does not select scenes. To pick which scenes join by native GSD, use the GSD range in Metadata filters.")}
          </Text>
        </FormControl>

        <HStack spacing={3}>
          <FormControl>
            <FormLabel fontSize="sm" color={labelColor}>
              <HStack spacing={1}>
                <Text>{t("Seed")}</Text>
                <InfoPopover titleKey="info.dataset.seed.title" bodyKey="info.dataset.seed.body" />
              </HStack>
            </FormLabel>
            <NumberInput
              size="sm"
              min={0}
              max={999999}
              value={config.split_seed ?? 42}
              onChange={(_, value) => updateConfig({ split_seed: Number.isFinite(value) ? value : 42 })}
            >
              <NumberInputField />
            </NumberInput>
          </FormControl>
          <FormControl>
            <FormLabel fontSize="sm" color={labelColor}>
              <HStack spacing={1}>
                <Text>{t("Block Size")}</Text>
                <InfoPopover titleKey="info.dataset.blockSize.title" bodyKey="info.dataset.blockSize.body" />
              </HStack>
            </FormLabel>
            <NumberInput
              size="sm"
              min={1}
              max={50}
              value={config.block_size_tiles ?? 5}
              isDisabled={(config.split_mode || "random_tile") === "random_tile" || config.split_mode === "scene_split"}
              onChange={(_, value) => updateConfig({ block_size_tiles: Number.isFinite(value) ? value : 5 })}
            >
              <NumberInputField />
            </NumberInput>
          </FormControl>
        </HStack>

        <VStack align="stretch" spacing={1}>
          <HStack justify="space-between">
            <HStack spacing={1}>
              <Text fontSize="sm" color={labelColor}>{t("Min Box Fraction")}</Text>
              <InfoPopover titleKey="info.dataset.minBoxFraction.title" bodyKey="info.dataset.minBoxFraction.body" />
            </HStack>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>
              {Math.round(config.min_box_fraction * 100)}%
            </Text>
          </HStack>
          <Slider
            min={0.05}
            max={1}
            step={0.05}
            value={config.min_box_fraction}
            onChange={(v) => updateConfig({ min_box_fraction: v })}
          >
            <SliderTrack><SliderFilledTrack /></SliderTrack>
            <SliderThumb />
          </Slider>
        </VStack>

        <VStack align="stretch" spacing={2}>
          <HStack spacing={1}>
            <Text fontSize="sm" color={labelColor}>{t("Dataset content")}</Text>
            <InfoPopover titleKey="info.dataset.tileSelection.title" bodyKey="info.dataset.tileSelection.body" />
          </HStack>
          <RadioGroup
            value={config.tile_selection ?? "reviewed_sampled"}
            onChange={(v) => updateConfig({ tile_selection: v as DatasetConfig["tile_selection"] })}
          >
            <VStack align="stretch" spacing={2}>
              <Radio value="reviewed_sampled">
                <Text fontSize="sm">{t("Reviewed with limit")}</Text>
              </Radio>
              {(config.tile_selection ?? "reviewed_sampled") === "reviewed_sampled" && (
                <VStack align="stretch" spacing={1} pl={6}>
                  <HStack justify="space-between">
                    <HStack spacing={1}>
                      <Text fontSize="sm" color={labelColor}>{t("Negative Ratio")}</Text>
                      <InfoPopover titleKey="info.dataset.negativeRatio.title" bodyKey="info.dataset.negativeRatio.body" />
                    </HStack>
                    <Text fontSize="sm" fontWeight="bold" color={textColor}>
                      {Math.round(config.negative_ratio * 100)}%
                    </Text>
                  </HStack>
                  <Slider
                    min={0}
                    max={1}
                    step={0.05}
                    value={config.negative_ratio}
                    onChange={(v) => updateConfig({ negative_ratio: v })}
                  >
                    <SliderTrack><SliderFilledTrack /></SliderTrack>
                    <SliderThumb />
                  </Slider>
                </VStack>
              )}
              <Radio value="all_reviewed">
                <Text fontSize="sm">{t("All reviewed (no excluded)")}</Text>
              </Radio>
              <Radio value="all">
                <Text fontSize="sm">{t("All tiles (with unreviewed and excluded)")}</Text>
              </Radio>
              {(config.tile_selection ?? "reviewed_sampled") === "all" && (
                <Alert status="warning" fontSize="xs" borderRadius="md" py={2} ml={6}>
                  <AlertIcon />
                  {t("info.dataset.tileSelection.allWarning")}
                </Alert>
              )}
            </VStack>
          </RadioGroup>
        </VStack>

        <Button
          colorScheme="brand"
          onClick={onGenerate}
          isLoading={isGenerating}
          loadingText={
            progress
              ? t("generatingProgress", { done: progress.done, total: progress.total })
              : t("Preparing…")
          }
        >
          {t("Generate Dataset")}
        </Button>
        {isGenerating && !progress && (
          <Text fontSize="xs" color={labelColor}>
            {t("Preparing the tile catalog and assembling tiles - for large datasets (e.g. DOTA) this can take a few minutes; the per-tile counter appears once tiles start being written.")}
          </Text>
        )}
      </VStack>
    </Card>
  );
}

function FilterList({
  title,
  emptyLabel,
  children,
}: {
  title: string;
  emptyLabel: string;
  children: ReactNode;
}) {
  const labelColor = useColorModeValue("secondaryGray.600", "whiteAlpha.700");
  const items = Array.isArray(children) ? children : children ? [children] : [];
  return (
    <Box borderWidth="1px" borderRadius="md" p={2} minW={0}>
      <Text fontSize="xs" fontWeight="bold" mb={2}>{title}</Text>
      <VStack align="stretch" spacing={1} maxH="150px" overflowY="auto">
        {items.length ? children : <Text fontSize="xs" color={labelColor}>{emptyLabel}</Text>}
      </VStack>
    </Box>
  );
}
