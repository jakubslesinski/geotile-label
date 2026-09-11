import {
  Alert,
  AlertIcon,
  Badge,
  Box,
  Button,
  Collapse,
  HStack,
  SimpleGrid,
  Table,
  Tbody,
  Td,
  Text,
  Th,
  Thead,
  Tr,
  VStack,
  useColorModeValue,
} from "@chakra-ui/react";
import { useState } from "react";
import { MdExpandLess, MdExpandMore, MdMap } from "react-icons/md";
import { useTranslation } from "react-i18next";
import Card from "../common/Card";
import type { ProjectAnnotationSummary } from "../../api/client";

interface Props {
  summary: ProjectAnnotationSummary;
  onExportGeoParquet: () => void;
  isExporting: boolean;
  exportDisabled?: boolean;
}

export default function ProjectAnnotationSummaryPanel({
  summary,
  onExportGeoParquet,
  isExporting,
  exportDisabled,
}: Props) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  const subtleBg = useColorModeValue("gray.50", "whiteAlpha.50");
  // Domyslnie zwiniete: przy 50+ klasach tabela rozjezdza dashboard. Metryki i
  // ostrzezenia zostaja widoczne, chowa sie tylko dlugi rozklad szczegolowy.
  const [detailsExpanded, setDetailsExpanded] = useState(false);

  const metrics = [
    [t("Scenes with annotations"), `${summary.scenes_with_annotations}/${summary.scene_count}`],
    [t("Annotations"), summary.annotation_count],
    [t("Annotations without author"), summary.missing_author_count],
    [t("Annotation imports"), summary.import_count],
  ];

  return (
    <Card>
      <VStack align="stretch" spacing={4}>
        <HStack justify="space-between" flexWrap="wrap" gap={2}>
          <VStack align="start" spacing={0}>
            <Text fontWeight="bold" color={textColor}>{t("Annotation Summary")}</Text>
            <Text fontSize="xs" color="secondaryGray.600">
              {t("Manager overview of source-scene annotation completeness and provenance.")}
            </Text>
          </VStack>
          <HStack>
            <Button
              size="sm"
              leftIcon={<MdMap />}
              variant="outline"
              onClick={onExportGeoParquet}
              isLoading={isExporting}
              loadingText={t("Exporting")}
              isDisabled={exportDisabled}
            >
              {t("Export annotations to GeoParquet")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              rightIcon={detailsExpanded ? <MdExpandLess /> : <MdExpandMore />}
              onClick={() => setDetailsExpanded((expanded) => !expanded)}
              aria-expanded={detailsExpanded}
            >
              {detailsExpanded ? t("Hide details") : t("Show details")}
            </Button>
          </HStack>
        </HStack>

        <SimpleGrid columns={{ base: 2, xl: 4 }} spacing={3}>
          {metrics.map(([label, value]) => (
            <Box key={String(label)} bg={subtleBg} borderRadius="lg" p={3}>
              <Text fontSize="xs" color="secondaryGray.600">{label}</Text>
              <Text fontSize="xl" fontWeight="bold" color={textColor}>{value}</Text>
            </Box>
          ))}
        </SimpleGrid>

        {!!summary.scenes_without_annotations.length && (
          <Alert status="warning" borderRadius="md">
            <AlertIcon />
            {t("scenesWithoutAnnotationsSummary", { count: summary.scenes_without_annotations.length })}
          </Alert>
        )}
        {!!summary.missing_author_count && (
          <Alert status="warning" borderRadius="md">
            <AlertIcon />
            {t("annotationsWithoutAuthorSummary", { count: summary.missing_author_count })}
          </Alert>
        )}

        <Collapse in={detailsExpanded} animateOpacity>
          <VStack align="stretch" spacing={4}>
            <SimpleGrid columns={{ base: 1, xl: 3 }} spacing={4}>
              <SummaryTable
                title={t("Annotations per class")}
                rows={summary.per_class.map((item) => [item.class_name, item.annotation_count])}
              />
              <SummaryTable
                title={t("Annotations per author")}
                rows={summary.per_author.map((item) => [item.annotator_email, item.annotation_count])}
              />
              <SummaryTable
                title={t("Import history")}
                rows={summary.recent_imports.map((item) => [
                  item.applied_at ? new Date(item.applied_at).toLocaleString() : item.import_id,
                  item.imported_annotation_count,
                ])}
                emptyText={t("No annotation imports yet")}
              />
            </SimpleGrid>

            {summary.import_count > 0 && (
              <HStack spacing={2} flexWrap="wrap">
                <Badge colorScheme="green">
                  {t("Imported")}: {summary.import_totals.imported_annotation_count}
                </Badge>
                <Badge colorScheme="gray">
                  {t("Duplicates")}: {summary.import_totals.duplicate_annotation_count}
                </Badge>
                <Badge colorScheme={summary.import_totals.changed_annotation_count ? "red" : "gray"}>
                  {t("Changed (not updated)")}: {summary.import_totals.changed_annotation_count}
                </Badge>
                <Badge colorScheme={summary.import_totals.blocked_annotation_count ? "orange" : "gray"}>
                  {t("Skipped")}: {summary.import_totals.blocked_annotation_count}
                </Badge>
              </HStack>
            )}
          </VStack>
        </Collapse>
      </VStack>
    </Card>
  );
}

function SummaryTable({
  title,
  rows,
  emptyText,
}: {
  title: string;
  rows: Array<[string, number]>;
  emptyText?: string;
}) {
  return (
    <Box overflowX="auto">
      <Text fontSize="sm" fontWeight="bold" mb={1}>{title}</Text>
      {rows.length ? (
        <Table size="sm" variant="simple">
          <Thead><Tr><Th>{title}</Th><Th isNumeric>#</Th></Tr></Thead>
          <Tbody>
            {rows.map(([label, value]) => (
              <Tr key={label}><Td maxW="220px" overflow="hidden" textOverflow="ellipsis">{label}</Td><Td isNumeric>{value}</Td></Tr>
            ))}
          </Tbody>
        </Table>
      ) : (
        <Text fontSize="xs" color="secondaryGray.600">{emptyText || "-"}</Text>
      )}
    </Box>
  );
}
