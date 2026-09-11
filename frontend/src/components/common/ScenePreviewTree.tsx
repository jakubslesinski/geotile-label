import { useState } from "react";
import {
  Alert,
  AlertIcon,
  Badge,
  Box,
  Button,
  Collapse,
  HStack,
  Input,
  Select,
  Text,
  VStack,
  useColorModeValue,
} from "@chakra-ui/react";
import { MdChevronRight, MdExpandMore } from "react-icons/md";
import { useTranslation } from "react-i18next";
import type {
  SceneImportPackage,
  ScenePreviewArchive,
  ScenePreviewProduct,
  ScenePreviewTree as PreviewTree,
} from "../../api/client";

/**
 * Hierarchiczny podgląd importu (DESIGN_DECISIONS.md, scene-import P2.1).
 *
 * Płaska tabela pakietów kazała czytelnikowi samodzielnie składać w głowie, które wiersze
 * należą do jednej akwizycji. Tutaj hierarchia jest widoczna, a informacja podzielona na to,
 * co widać zawsze, i to, co rozwija się dla JEDNEGO produktu.
 *
 * Świadome ograniczenie: sekcja 2.1 roadmapy wymienia dziesięć grup informacji. Pokazanie ich
 * wszystkich przy każdym wierszu zamienia podgląd w zrzut danych, w którym nie widać tego, co
 * wymaga decyzji. W wierszu jest więc etykieta produktu, status, licznik ostrzeżeń i — TYLKO
 * gdy niepełna — kompletność. Reszta czeka w rozwinięciu, a całość w raporcie importu (P1.6).
 */

interface Props {
  tree: PreviewTree;
  packagesById: Record<string, SceneImportPackage>;
  decisionKeyOf: (pkg: SceneImportPackage) => string;
  decisions: Record<string, string[]>;
  rgbDecisions: Record<string, number[]>;
  skipped: Record<string, boolean>;
  onDecision: (key: string, assetIds: string[]) => void;
  onRgb: (key: string, bands: number[]) => void;
  onToggleSkip: (key: string) => void;
}

const STATUS_COLORS: Record<string, string> = {
  ready: "green",
  prepare_required: "blue",
  decision_required: "orange",
  archive_duplicate: "purple",
  archive_only: "purple",
  archive_extracted_incomplete: "red",
  archive_unreadable: "red",
};

function formatBytes(bytes?: number | null): string {
  if (bytes === null || bytes === undefined) return "-";
  const gib = bytes / 1024 ** 3;
  if (gib >= 1) return `${gib.toFixed(2)} GiB`;
  return `${(bytes / 1024 ** 2).toFixed(0)} MiB`;
}

function ArchiveLine({ archive }: { archive: ScenePreviewArchive }) {
  const { t } = useTranslation();
  const muted = useColorModeValue("gray.600", "gray.400");
  const complete =
    archive.extracted_total && archive.extracted_present === archive.extracted_total;
  return (
    <HStack fontSize="xs" color={muted} pl={6} spacing={2}>
      <Badge colorScheme={STATUS_COLORS[archive.status] || "gray"} fontSize="0.65rem">
        {t(archive.status)}
      </Badge>
      <Text fontFamily="mono" noOfLines={1} title={archive.name}>{archive.name}</Text>
      {archive.extracted_total ? (
        <Text color={complete ? muted : "red.400"}>
          {archive.extracted_present}/{archive.extracted_total}
        </Text>
      ) : null}
      <Text>{formatBytes(archive.compressed_bytes)}</Text>
    </HStack>
  );
}

function ProductRow({
  product,
  pkg,
  decisionKey,
  decisions,
  rgbDecisions,
  skipped,
  onDecision,
  onRgb,
  onToggleSkip,
}: {
  product: ScenePreviewProduct;
  pkg?: SceneImportPackage;
  decisionKey: string;
  decisions: Record<string, string[]>;
  rgbDecisions: Record<string, number[]>;
  skipped: Record<string, boolean>;
  onDecision: (key: string, assetIds: string[]) => void;
  onRgb: (key: string, bands: number[]) => void;
  onToggleSkip: (key: string) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const muted = useColorModeValue("gray.600", "gray.400");
  const panel = useColorModeValue("gray.50", "whiteAlpha.100");
  const detail = product.detail;
  const isSkipped = skipped[decisionKey];
  const needsDecision = product.status === "decision_required" && detail.alternatives.length > 0;

  return (
    <Box pl={4} opacity={isSkipped ? 0.55 : 1}>
      <HStack spacing={2} align="center" py={0.5}>
        <Button
          size="xs"
          variant="ghost"
          minW="auto"
          px={1}
          onClick={() => setOpen((value) => !value)}
          aria-label={t("Details")}
        >
          {open ? <MdExpandMore /> : <MdChevronRight />}
        </Button>
        <Text fontSize="sm" fontWeight="medium" minW="180px">{product.label}</Text>
        <Badge colorScheme={STATUS_COLORS[product.status] || "gray"}>
          {isSkipped ? t("Skipped") : t(product.status)}
        </Badge>
        {/* Kompletność pojawia się TYLKO gdy niepełna — `6/6` nie wymaga uwagi. */}
        {product.incomplete && (
          <Badge colorScheme="red" variant="outline">
            {t("Incomplete")} {product.part_count}/{product.declared_parts}
          </Badge>
        )}
        {product.blocking_count > 0 && (
          <Badge colorScheme="red">{product.blocking_count} ⛔</Badge>
        )}
        {product.warning_count > 0 && (
          <Badge colorScheme="yellow" variant="subtle">{product.warning_count} ⚠</Badge>
        )}
        <Box flex="1" />
        <Button size="xs" variant="ghost" colorScheme={isSkipped ? "gray" : "red"}
                onClick={() => onToggleSkip(decisionKey)}>
          {isSkipped ? t("Restore") : t("Skip")}
        </Button>
      </HStack>

      {needsDecision && (
        <VStack align="stretch" pl={8} pb={1} spacing={2}>
          <HStack spacing={2}>
          <Select
            size="xs"
            maxW="320px"
            placeholder={t("Select product")}
            value={(decisions[decisionKey] || []).join(",")}
            isDisabled={isSkipped}
            onChange={(event) =>
              onDecision(decisionKey, event.target.value ? event.target.value.split(",") : [])
            }
          >
            {detail.alternatives.map((alternative) => (
              <option key={alternative.asset_ids.join(",")} value={alternative.asset_ids.join(",")}>
                {alternative.label}
              </option>
            ))}
          </Select>
          {product.product_type === "MUL+PAN" && (
            <Input
              size="xs"
              maxW="160px"
              placeholder={t("RGB bands, e.g. 3,2,1")}
              value={(rgbDecisions[decisionKey] || []).join(",")}
              onChange={(event) =>
                onRgb(
                  decisionKey,
                  event.target.value
                    .split(/[,;\s]+/)
                    .map((value) => Number(value))
                    .filter((value) => Number.isInteger(value) && value > 0),
                )
              }
            />
          )}
          </HStack>
          {(decisions[decisionKey] || []).length > 0 && !isSkipped && (
            <Alert status="warning" borderRadius="md" py={1} px={2} fontSize="xs">
              <AlertIcon boxSize={3} />
              {t("Manual product selection consequences")}
            </Alert>
          )}
        </VStack>
      )}

      <Collapse in={open} animateOpacity>
        <Box bg={panel} borderRadius="md" p={3} ml={8} mb={2} fontSize="xs">
          <VStack align="stretch" spacing={1}>
            <Text color={muted}>
              {t("Why this product")}: {detail.selection_reason.message}
            </Text>
            <Text color={muted}>
              {t("Assets")}: {detail.assets.measurement} measurement · {detail.assets.metadata} metadata
              {detail.assets.browse > 0 ? ` · ${detail.assets.browse} browse` : ""}
              {detail.assets.auxiliary > 0 ? ` · ${detail.assets.auxiliary} auxiliary` : ""}
            </Text>
            {detail.polarization_or_bands && (
              <Text color={muted}>{t("Bands or polarization")}: {detail.polarization_or_bands}</Text>
            )}
            {detail.missing_parts_total > 0 && (
              <Text color="red.400">
                {t("Missing parts")} ({detail.missing_parts_total}
                {detail.parts_source ? `, ${t("from")} ${detail.parts_source}` : ""}):{" "}
                {detail.missing_parts.join(", ")}
                {detail.missing_parts_total > detail.missing_parts.length ? " …" : ""}
              </Text>
            )}
            {detail.diagnostics.blocking.map((item) => (
              <Text key={item.code} color="red.400">⛔ {item.code}: {item.message}</Text>
            ))}
            {detail.diagnostics.warnings.map((item) => (
              <Text key={item.code} color="yellow.500">⚠ {item.code}: {item.message}</Text>
            ))}
            {detail.preparation && (
              <Text color={detail.preparation.fits === false ? "red.400" : muted}>
                {t("Estimated preparation output")}: ~
                {formatBytes(detail.preparation.estimated_output_bytes)} · {t("free space")}:{" "}
                {formatBytes(detail.preparation.free_bytes)}
              </Text>
            )}
            {detail.archive.length > 0 && (
              <Text color={muted}>
                {t("Archive")}: {detail.archive.map((item) => item.name).join(", ")}
              </Text>
            )}
            {!pkg && <Text color="red.400">{t("Package payload is missing from the preview")}</Text>}
          </VStack>
        </Box>
      </Collapse>
    </Box>
  );
}

export default function ScenePreviewTree({
  tree,
  packagesById,
  decisionKeyOf,
  decisions,
  rgbDecisions,
  skipped,
  onDecision,
  onRgb,
  onToggleSkip,
}: Props) {
  const { t } = useTranslation();
  const muted = useColorModeValue("gray.600", "gray.400");
  const border = useColorModeValue("gray.200", "whiteAlpha.300");

  return (
    <VStack align="stretch" spacing={3}>
      {tree.sources.map((source) => (
        <Box key={source.source_id} borderWidth="1px" borderColor={border} borderRadius="md" p={3}>
          <HStack spacing={2} mb={2}>
            <Badge colorScheme="teal">{source.provider}</Badge>
            <Text fontSize="sm" fontFamily="mono" noOfLines={1} title={source.root_path || ""}>
              {source.root_path}
            </Text>
          </HStack>

          {source.deliveries.map((delivery) => (
            <Box key={delivery.delivery_id} mb={2}>
              <Text fontSize="sm" fontWeight="bold" noOfLines={1} title={delivery.label}>
                {delivery.label}
              </Text>
              {delivery.acquisitions.map((acquisition) => (
                <Box key={acquisition.acquisition_id} pl={2}>
                  <Text fontSize="xs" color={muted} fontFamily="mono" noOfLines={1}>
                    {acquisition.acquisition_id}
                  </Text>
                  {acquisition.products.map((product) => {
                    const pkg = packagesById[product.package_id];
                    const key = pkg ? decisionKeyOf(pkg) : product.package_id;
                    return (
                      <ProductRow
                        key={product.package_id}
                        product={product}
                        pkg={pkg}
                        decisionKey={key}
                        decisions={decisions}
                        rgbDecisions={rgbDecisions}
                        skipped={skipped}
                        onDecision={onDecision}
                        onRgb={onRgb}
                        onToggleSkip={onToggleSkip}
                      />
                    );
                  })}
                </Box>
              ))}
              {delivery.archives.map((archive) => (
                <ArchiveLine key={archive.package_id} archive={archive} />
              ))}
            </Box>
          ))}
        </Box>
      ))}
    </VStack>
  );
}
