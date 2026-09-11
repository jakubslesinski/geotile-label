import {
  Badge,
  Box,
  Code,
  Divider,
  HStack,
  ListItem,
  SimpleGrid,
  Text,
  UnorderedList,
  VStack,
  useColorModeValue,
} from "@chakra-ui/react";
import type { ReactNode } from "react";
import type { Project, SceneInfo, SceneManifest } from "../../types";
import i18n from "../../i18n";
import { useTranslation } from "react-i18next";

interface SceneMetadataPanelProps {
  project: Project | null;
  sceneInfo: SceneInfo | null;
  manifest: SceneManifest | null;
}

function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(i18n.language.startsWith("pl") ? "pl-PL" : "en-US");
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? i18n.t("Yes") : i18n.t("No");
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

function MetadataRow({ label, value }: { label: string; value: unknown }) {
  const labelColor = useColorModeValue("secondaryGray.600", "whiteAlpha.700");
  const textColor = useColorModeValue("navy.700", "white");
  return (
    <Box>
      <Text fontSize="xs" color={labelColor}>
        {label}
      </Text>
      <Text fontSize="sm" color={textColor} wordBreak="break-word">
        {formatValue(value)}
      </Text>
    </Box>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const textColor = useColorModeValue("navy.700", "white");
  return (
    <VStack align="stretch" spacing={2}>
      <Text fontSize="sm" fontWeight="bold" color={textColor}>
        {title}
      </Text>
      {children}
    </VStack>
  );
}

export default function SceneMetadataPanel({
  project,
  sceneInfo,
  manifest,
}: SceneMetadataPanelProps) {
  const { t } = useTranslation();
  const mutedColor = useColorModeValue("secondaryGray.600", "whiteAlpha.700");
  const warningBg = useColorModeValue("orange.50", "orange.900");
  const infoBg = useColorModeValue("blue.50", "whiteAlpha.100");
  const codeBg = useColorModeValue("gray.50", "whiteAlpha.100");

  if (!manifest) {
    return (
      <Text fontSize="sm" color={mutedColor}>
        {t("Scene manifest is not available yet.")}
      </Text>
    );
  }

  const warnings = manifest.profile_validation?.warnings || [];
  const diagnostics = manifest.parser?.diagnostics || {};
  const providerBlock = manifest.sar || manifest.eo || {};
  const sourceFiles = manifest.metadata_sources?.length
    ? manifest.metadata_sources.map((source) => source.path)
    : manifest.metadata_files || [];

  return (
    <VStack align="stretch" spacing={4}>
      <Section title={t("Profile validation")}>
        {warnings.length > 0 ? (
          <VStack align="stretch" spacing={2}>
            {warnings.map((warning) => (
              <Box key={`${warning.code}-${warning.message}`} bg={warningBg} p={2} borderRadius="md">
                <HStack justify="space-between" align="start" spacing={2}>
                  <Text fontSize="xs" fontWeight="bold">
                    {warning.message}
                  </Text>
                  <Badge colorScheme={warning.severity === "info" ? "blue" : "orange"}>
                    {warning.severity}
                  </Badge>
                </HStack>
                <Text fontSize="xx-small" color={mutedColor} mt={1}>
                  {warning.code}
                </Text>
              </Box>
            ))}
          </VStack>
        ) : (
          <Box bg={infoBg} p={2} borderRadius="md">
            <Text fontSize="xs">{t("Scene metadata matches the project profile.")}</Text>
          </Box>
        )}
      </Section>

      <Divider />

      <Section title={t("Scene summary")}>
        <SimpleGrid columns={2} spacing={3}>
          <MetadataRow label={t("Provider")} value={manifest.provider} />
          <MetadataRow label={t("Sensor")} value={manifest.sensor} />
          <MetadataRow label={t("Modality")} value={manifest.modality} />
          <MetadataRow label={t("Georeferencing")} value={manifest.georeferencing} />
          <MetadataRow label={t("Acquisition")} value={formatDate(manifest.acquisition_datetime_utc)} />
          <MetadataRow label={t("Metadata status")} value={manifest.metadata_status} />
          <MetadataRow label={t("Parser")} value={manifest.parser?.name} />
          <MetadataRow label={t("Source exists")} value={manifest.source_exists} />
          <MetadataRow label={t("Source identity")} value={manifest.source_identity_status} />
          <MetadataRow label={t("Source scene UID")} value={manifest.source_scene_uid} />
        </SimpleGrid>
      </Section>

      <Section title={t("Project profile")}>
        <SimpleGrid columns={2} spacing={3}>
          <MetadataRow label={t("Project modality")} value={project?.profile?.modality} />
          <MetadataRow label={t("Project georef")} value={project?.profile?.georeferencing} />
          <MetadataRow label={t("Selected sensors")} value={project?.profile?.sensors} />
          <MetadataRow label={t("Annotation mode")} value={project?.profile?.annotation_mode} />
        </SimpleGrid>
      </Section>

      <Section title={manifest.sar ? t("SAR metadata") : t("EO metadata")}>
        {Object.keys(providerBlock).length > 0 ? (
          <SimpleGrid columns={2} spacing={3}>
            {Object.entries(providerBlock).map(([key, value]) => (
              <MetadataRow key={key} label={key} value={value} />
            ))}
          </SimpleGrid>
        ) : (
          <Text fontSize="xs" color={mutedColor}>
            {t("No provider-specific metadata parsed.")}
          </Text>
        )}
      </Section>

      <Section title={t("Raster metadata")}>
        <SimpleGrid columns={2} spacing={3}>
          <MetadataRow label={t("Width")} value={sceneInfo?.width ?? manifest.image?.width} />
          <MetadataRow label={t("Height")} value={sceneInfo?.height ?? manifest.image?.height} />
          <MetadataRow label={t("Channels")} value={sceneInfo?.channels ?? manifest.image?.channels} />
          <MetadataRow label="Dtype" value={sceneInfo?.dtype ?? manifest.image?.dtype} />
          <MetadataRow label="CRS" value={sceneInfo?.crs ?? manifest.geospatial?.crs} />
          <MetadataRow label="Bounds WGS84" value={sceneInfo?.bounds ?? manifest.geospatial?.bounds_wgs84} />
        </SimpleGrid>
      </Section>

      <Section title={t("Metadata files")}>
        {sourceFiles.length > 0 ? (
          <UnorderedList spacing={1} pl={3}>
            {sourceFiles.map((path) => (
              <ListItem key={path} fontSize="xs" wordBreak="break-word">
                <Code bg={codeBg} fontSize="xx-small">
                  {fileName(path)}
                </Code>
              </ListItem>
            ))}
          </UnorderedList>
        ) : (
          <Text fontSize="xs" color={mutedColor}>
            {t("No sidecar metadata files were associated with this scene.")}
          </Text>
        )}
      </Section>

      {(diagnostics.warnings?.length || diagnostics.errors?.length) && (
        <Section title={t("Parser diagnostics")}>
          <VStack align="stretch" spacing={2}>
            {(diagnostics.errors || []).map((message) => (
              <Box key={`error-${message}`} bg={warningBg} p={2} borderRadius="md">
                <Text fontSize="xs">{message}</Text>
              </Box>
            ))}
            {(diagnostics.warnings || []).map((message) => (
              <Box key={`warning-${message}`} bg={infoBg} p={2} borderRadius="md">
                <Text fontSize="xs">{message}</Text>
              </Box>
            ))}
          </VStack>
        </Section>
      )}
    </VStack>
  );
}
