import {
  VStack,
  HStack,
  Text,
  IconButton,
  Box,
  useColorModeValue,
  Badge,
  Select,
  SimpleGrid,
} from "@chakra-ui/react";
import { useMemo, useState } from "react";
import { MdDelete, MdCategory } from "react-icons/md";
import type { Annotation, LabelClass } from "../../types";
import { useTranslation } from "react-i18next";

interface Props {
  annotations: Annotation[];
  classes: LabelClass[];
  onDelete: (annId: string) => void;
  selectedAnnotationId?: string | null;
  selectedAnnotationIds?: string[];
  onSelect?: (annId: string) => void;
  onDoubleClick?: (annId: string) => void;
  /** Change the class of a single annotation from its row (inline dropdown). */
  onClassChange?: (annId: string, classId: number) => void;
}

export default function AnnotationList({
  annotations,
  classes,
  onDelete,
  selectedAnnotationId,
  selectedAnnotationIds = [],
  onSelect,
  onDoubleClick,
  onClassChange,
}: Props) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  const selectedBg = useColorModeValue("rgba(124, 79, 224, 0.12)", "rgba(155, 108, 255, 0.15)");
  const hoverBg = useColorModeValue("gray.100", "#29292E");
  const classMap = new Map(classes.map((c) => [c.id, c]));
  const [editingClassAnnId, setEditingClassAnnId] = useState<string | null>(null);
  const [classFilter, setClassFilter] = useState("");
  const [authorFilter, setAuthorFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [provenanceFilter, setProvenanceFilter] = useState("");
  const authors = useMemo(
    () => [...new Set(annotations.map((item) => item.annotator_email).filter(Boolean) as string[])].sort(),
    [annotations],
  );
  const sources = useMemo(
    () => [...new Set(annotations.map((item) => item.annotation_source || "manual"))].sort(),
    [annotations],
  );
  const imports = useMemo(
    () => [...new Set(annotations.map((item) => item.import_id).filter(Boolean) as string[])].sort(),
    [annotations],
  );
  const packages = useMemo(
    () => [...new Set(annotations.map((item) => item.source_package_id).filter(Boolean) as string[])].sort(),
    [annotations],
  );
  const filteredAnnotations = useMemo(() => annotations.filter((annotation) => {
    if (classFilter && annotation.class_id !== Number(classFilter)) return false;
    if (authorFilter && annotation.annotator_email !== authorFilter) return false;
    if (sourceFilter && (annotation.annotation_source || "manual") !== sourceFilter) return false;
    if (provenanceFilter.startsWith("import:") && annotation.import_id !== provenanceFilter.slice(7)) return false;
    if (
      provenanceFilter.startsWith("package:") &&
      annotation.source_package_id !== provenanceFilter.slice(8)
    ) return false;
    return true;
  }), [annotations, classFilter, authorFilter, sourceFilter, provenanceFilter]);
  const selectedAnnotationSet = useMemo(() => new Set(selectedAnnotationIds), [selectedAnnotationIds]);

  return (
    <VStack align="stretch" spacing={1} maxH="300px" overflowY="auto">
      <Text fontSize="sm" fontWeight="bold" color={textColor} mb={1}>
        {t("Annotations")} ({filteredAnnotations.length}/{annotations.length})
      </Text>
      <SimpleGrid columns={2} spacing={1} mb={2}>
        <Select size="xs" value={classFilter} onChange={(event) => setClassFilter(event.target.value)}>
          <option value="">{t("All classes")}</option>
          {classes.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </Select>
        <Select size="xs" value={authorFilter} onChange={(event) => setAuthorFilter(event.target.value)}>
          <option value="">{t("All authors")}</option>
          {authors.map((author) => <option key={author} value={author}>{author}</option>)}
        </Select>
        <Select size="xs" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}>
          <option value="">{t("All sources")}</option>
          {sources.map((source) => <option key={source} value={source}>{source}</option>)}
        </Select>
        <Select
          size="xs"
          value={provenanceFilter}
          onChange={(event) => setProvenanceFilter(event.target.value)}
        >
          <option value="">{t("All imports and packages")}</option>
          {imports.map((value) => (
            <option key={`import:${value}`} value={`import:${value}`}>{t("Import")} {value.slice(0, 8)}</option>
          ))}
          {packages.map((value) => (
            <option key={`package:${value}`} value={`package:${value}`}>{t("Package")} {value.slice(0, 8)}</option>
          ))}
        </Select>
      </SimpleGrid>
      {filteredAnnotations.map((ann) => {
        const cls = classMap.get(ann.class_id);
        const [x0, y0, x1, y1] = ann.bbox;
        const isSelected = selectedAnnotationId === ann.id || selectedAnnotationSet.has(ann.id);
        return (
          <Box key={ann.id} borderRadius="md" bg={isSelected ? selectedBg : "transparent"}>
          <HStack justify="space-between" py={1} px={2} cursor="pointer"
            onClick={() => onSelect?.(ann.id)} onDoubleClick={() => onDoubleClick?.(ann.id)}
            _hover={{ bg: isSelected ? selectedBg : hoverBg }}>
            <HStack>
              <Box w={2.5} h={2.5} borderRadius="sm" bg={cls?.color || "#ccc"} />
              <Text fontSize="xs" color={textColor}>
                {cls?.name || "?"}
              </Text>
              <Badge fontSize="xx-small" variant="subtle">
                {Math.round(x1 - x0)}x{Math.round(y1 - y0)}
              </Badge>
              {ann.geometry_type === "rotated_bbox" && (
                <Badge fontSize="xx-small" colorScheme="purple">
                  OBB
                  {ann.orientation_angle_deg !== null && ann.orientation_angle_deg !== undefined
                    ? ` ${Math.round(ann.orientation_angle_deg)}°`
                    : ""}
                </Badge>
              )}
              {ann.is_negative && (
                <Badge fontSize="xx-small" colorScheme="red">
                  NEG
                </Badge>
              )}
            </HStack>
            <HStack spacing={0}>
              {onClassChange && (
                <IconButton
                  aria-label={t("Change class")}
                  title={t("Change class")}
                  icon={<MdCategory />}
                  size="xs"
                  variant="ghost"
                  colorScheme={editingClassAnnId === ann.id ? "brand" : undefined}
                  onClick={(e) => {
                    e.stopPropagation();
                    setEditingClassAnnId((current) => (current === ann.id ? null : ann.id));
                  }}
                />
              )}
              <IconButton
                aria-label={t("Delete")}
                icon={<MdDelete />}
                size="xs"
                variant="ghost"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(ann.id);
                }}
              />
            </HStack>
          </HStack>
          {onClassChange && editingClassAnnId === ann.id && (
            <Box px={2} pb={2} pl={7} onClick={(e) => e.stopPropagation()}>
              <Select
                size="xs"
                autoFocus
                value={ann.class_id}
                onChange={(event) => {
                  const nextClassId = Number(event.target.value);
                  setEditingClassAnnId(null);
                  if (nextClassId !== ann.class_id) onClassChange(ann.id, nextClassId);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setEditingClassAnnId(null);
                }}
                onBlur={() => setEditingClassAnnId(null)}
              >
                {classes.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </Select>
            </Box>
          )}
          </Box>
        );
      })}
      {filteredAnnotations.length === 0 && (
        <Text fontSize="xs" color="secondaryGray.600">
          {annotations.length
            ? t("No annotations match filters")
            : t("No annotations yet. Select a class and draw on the scene.")}
        </Text>
      )}
    </VStack>
  );
}
