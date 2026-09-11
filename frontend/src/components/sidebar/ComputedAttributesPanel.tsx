import {
  Badge,
  Box,
  Divider,
  HStack,
  SimpleGrid,
  Text,
  VStack,
  useColorModeValue,
} from "@chakra-ui/react";
import type { Annotation } from "../../types";
import { useTranslation } from "react-i18next";
import InfoPopover from "../common/InfoPopover";

interface ComputedAttributesPanelProps {
  annotation: Annotation | null;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(3).replace(/\.?0+$/, "");
  }
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (Array.isArray(value)) return value.map(formatValue).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function AttributeRow({ label, value }: { label: string; value: unknown }) {
  const labelColor = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const valueColor = useColorModeValue("navy.700", "white");
  return (
    <Box minW={0}>
      <Text fontSize="xx-small" color={labelColor} textTransform="uppercase">
        {label.replace(/_/g, " ")}
      </Text>
      <Text fontSize="xs" color={valueColor} wordBreak="break-word">
        {formatValue(value)}
      </Text>
    </Box>
  );
}

function AttributeSection({
  title,
  values,
  keys,
}: {
  title: string;
  values?: Record<string, unknown>;
  keys: string[];
}) {
  const visibleKeys = keys.filter((key) => values?.[key] !== undefined);
  if (!visibleKeys.length) return null;
  return (
    <VStack align="stretch" spacing={2}>
      <Text fontSize="xs" fontWeight="bold">
        {title}
      </Text>
      <SimpleGrid columns={2} spacing={2}>
        {visibleKeys.map((key) => (
          <AttributeRow key={key} label={key} value={values?.[key]} />
        ))}
      </SimpleGrid>
    </VStack>
  );
}

export default function ComputedAttributesPanel({ annotation }: ComputedAttributesPanelProps) {
  const { t } = useTranslation();
  const mutedColor = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const panelBg = useColorModeValue("gray.50", "whiteAlpha.50");
  const attributes = annotation?.attributes;

  return (
    <VStack align="stretch" spacing={3}>
      <HStack spacing={1}>
        <Text fontSize="sm" fontWeight="bold">
          {t("Computed Attributes")}
        </Text>
        <InfoPopover
          titleKey="info.annotation.computedAttributes.title"
          bodyKey="info.annotation.computedAttributes.body"
        />
      </HStack>
      {!annotation && (
        <Text fontSize="xs" color={mutedColor}>
          {t("Select an annotation to inspect its computed attributes.")}
        </Text>
      )}
      {annotation && !attributes && (
        <Text fontSize="xs" color={mutedColor}>
          {t("Attributes have not been computed yet. Reload the scene to recompute them.")}
        </Text>
      )}
      {attributes && (
        <Box bg={panelBg} borderRadius="md" p={3}>
          <VStack align="stretch" spacing={3}>
            <Box>
              <Badge
                colorScheme={
                  attributes.attribute_status === "computed"
                    ? "green"
                    : attributes.attribute_status === "error"
                      ? "red"
                      : "orange"
                }
              >
                {attributes.attribute_status}
              </Badge>
              <Text fontSize="xx-small" color={mutedColor} mt={1}>
                {t("Version")} {attributes.attribute_version} · {attributes.attribute_computed_at}
              </Text>
            </Box>

            <Divider />

            <AttributeSection
              title={t("Geometry")}
              values={attributes.geometry}
              keys={[
                "geometry_type",
                "centroid_scene_px",
                "bbox_width_px",
                "bbox_height_px",
                "bbox_area_px",
                "geometry_area_px",
                "major_axis_px",
                "minor_axis_px",
                "aspect_ratio_px",
              ]}
            />

            <AttributeSection
              title={t("Orientation")}
              values={attributes.orientation}
              keys={["orientation_px_deg", "orientation_source", "front_vector_scene_px"]}
            />

            <AttributeSection
              title={t("Geospatial")}
              values={attributes.geospatial}
              keys={[
                "available",
                "crs",
                "centroid_lon",
                "centroid_lat",
                "bbox_lonlat",
                "area_m2",
                "major_axis_m",
                "minor_axis_m",
                "equivalent_diameter_m",
                "aspect_ratio_m",
                "orientation_geo_deg",
                "metric_method",
                "reason",
              ]}
            />

            <VStack align="stretch" spacing={1}>
              <Text fontSize="xs" fontWeight="bold">{t("Tile / Export")}</Text>
              <Text fontSize="xs" color={mutedColor}>
                {t("Derived tile attributes are generated during dataset creation and stored in tile_annotation_links.json.")}
              </Text>
            </VStack>

            <AttributeSection
              title={t("Scene Metadata")}
              values={attributes.scene_metadata}
              keys={[
                "provider",
                "sensor",
                "modality",
                "acquisition_datetime_utc",
                "crs",
                "scene_manifest_version",
              ]}
            />

            {(attributes.attribute_errors.length > 0 || attributes.attribute_warnings.length > 0) && (
              <VStack align="stretch" spacing={1}>
                <Text fontSize="xs" fontWeight="bold">{t("Status")}</Text>
                {attributes.attribute_errors.map((message) => (
                  <Text key={message} fontSize="xs" color="red.400">{message}</Text>
                ))}
                {attributes.attribute_warnings.map((message) => (
                  <Text key={message} fontSize="xs" color="orange.400">{message}</Text>
                ))}
              </VStack>
            )}
          </VStack>
        </Box>
      )}
    </VStack>
  );
}
