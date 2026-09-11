import { useState, useEffect, useCallback } from "react";
import {
  Modal,
  ModalOverlay,
  ModalContent,
  ModalHeader,
  ModalBody,
  ModalFooter,
  ModalCloseButton,
  VStack,
  HStack,
  Text,
  Button,
  Badge,
  Box,
  Spinner,
  useColorModeValue,
} from "@chakra-ui/react";
import { MdFolder, MdArrowBack, MdCheck, MdInsertDriveFile, MdHome } from "react-icons/md";
import * as api from "../../api/client";
import type { BrowseResult } from "../../api/client";
import { useTranslation } from "react-i18next";

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSelect: (path: string, classesFile?: string, displayPath?: string) => void;
  title?: string;
  mode?: "scene_folder" | "classes_file";
}

export default function FolderBrowser({
  isOpen,
  onClose,
  onSelect,
  title,
  mode = "scene_folder",
}: Props) {
  const { t } = useTranslation();
  const isClassesMode = mode === "classes_file";
  const [currentPath, setCurrentPath] = useState("");
  const [result, setResult] = useState<BrowseResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedClassesFile, setSelectedClassesFile] = useState<string | null>(null);
  const [selectedClassesFileName, setSelectedClassesFileName] = useState<string | null>(null);

  const textColor = useColorModeValue("navy.700", "white");
  const hoverBg = useColorModeValue("gray.50", "whiteAlpha.100");
  const dirBg = useColorModeValue("blue.50", "whiteAlpha.50");

  const loadPath = useCallback(async (path: string) => {
    setLoading(true);
    try {
      const data = await api.browse(path, isClassesMode ? "class" : "scene");
      setResult(data);
      setCurrentPath(data.path);
      setSelectedClassesFile(null);
      setSelectedClassesFileName(null);
    } catch {
      // stay on current path
    } finally {
      setLoading(false);
    }
  }, [isClassesMode]);

  useEffect(() => {
    if (isOpen) {
      loadPath("");
    }
  }, [isOpen, loadPath]);

  const goUp = () => {
    loadPath(result?.parent_path || "");
  };

  const enterDir = (path: string) => {
    loadPath(path);
  };

  const handleSelect = () => {
    if (isClassesMode && !selectedClassesFile) return;
    const displayPath = result?.breadcrumbs.length
      ? result.breadcrumbs.map((crumb) => crumb.label).join(" / ")
      : "/";
    onSelect(currentPath, selectedClassesFile || undefined, displayPath);
    onClose();
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} size="lg" scrollBehavior="inside">
      <ModalOverlay />
      <ModalContent>
        <ModalHeader>{title || t("Select scene folder")}</ModalHeader>
        <ModalCloseButton />

        <ModalBody>
          {/* Breadcrumb */}
          <HStack mb={3} spacing={1} flexWrap="wrap">
            <Button size="xs" variant="ghost" onClick={() => loadPath("")} leftIcon={<MdHome />}>
              /
            </Button>
            {result?.breadcrumbs.map((crumb) => {
              return (
                <HStack key={crumb.path} spacing={1}>
                  <Text color="secondaryGray.500">/</Text>
                  <Button size="xs" variant="ghost" onClick={() => loadPath(crumb.path)}>
                    {crumb.label}
                  </Button>
                </HStack>
              );
            })}
          </HStack>

          {/* Current folder info */}
          {!isClassesMode && result && result.image_count > 0 && (
            <Badge colorScheme="green" mb={3}>
              {t("Images in folder", { count: result.image_count })}
            </Badge>
          )}

          {loading ? (
            <Box textAlign="center" py={8}>
              <Spinner size="lg" color="brand.400" />
            </Box>
          ) : (
            <VStack align="stretch" spacing={0} maxH="400px" overflowY="auto">
              {/* Back button */}
              {currentPath && (
                <HStack
                  px={3}
                  py={2}
                  cursor="pointer"
                  _hover={{ bg: hoverBg }}
                  borderRadius="md"
                  onClick={goUp}
                >
                  <MdArrowBack />
                  <Text fontSize="sm" color={textColor}>..</Text>
                </HStack>
              )}

              {/* Directories */}
              {result?.dirs.map((dir) => (
                <HStack
                  key={dir.path}
                  px={3}
                  py={2}
                  cursor="pointer"
                  _hover={{ bg: hoverBg }}
                  bg={dirBg}
                  borderRadius="md"
                  onClick={() => enterDir(dir.path)}
                >
                  <MdFolder color="#F6AD55" />
                  <Text fontSize="sm" color={textColor} flex="1" noOfLines={1}>
                    {dir.name}
                  </Text>
                  {dir.image_count > 0 && (
                    <Badge fontSize="xx-small" colorScheme="blue">
                      {dir.image_count} img
                    </Badge>
                  )}
                </HStack>
              ))}

              {/* JSON files (for classes) */}
              {result?.files
                .filter((f) => f.type === "json")
                .map((file) => (
                  <HStack
                    key={file.path}
                    px={3}
                    py={2}
                    cursor="pointer"
                    _hover={{ bg: hoverBg }}
                    borderRadius="md"
                    bg={selectedClassesFile === file.path ? "green.100" : undefined}
                    onClick={() =>
                      {
                        const nextValue = selectedClassesFile === file.path ? null : file.path;
                        setSelectedClassesFile(nextValue);
                        setSelectedClassesFileName(nextValue ? file.name : null);
                      }
                    }
                  >
                    <MdInsertDriveFile color="#68D391" />
                    <Text fontSize="sm" color={textColor} flex="1" noOfLines={1}>
                      {file.name}
                    </Text>
                    <Text fontSize="xs" color="secondaryGray.500">
                      {formatSize(file.size)}
                    </Text>
                    {selectedClassesFile === file.path && (
                      <Badge colorScheme="green" fontSize="xx-small">{t("Classes").toLowerCase()}</Badge>
                    )}
                  </HStack>
                ))}

              {/* Summary of image files */}
              {!isClassesMode && result?.image_count !== undefined && result.image_count > 0 && (
                <Box px={3} py={2}>
                  <Text fontSize="xs" color="secondaryGray.500">
                    + {t("Image files", { count: result.image_count })} (tif, png, jpg…)
                  </Text>
                </Box>
              )}

              {/* Empty state */}
              {result && result.dirs.length === 0 && result.files.length === 0 && result.image_count === 0 && (
                <Box textAlign="center" py={6}>
                  <Text fontSize="sm" color="secondaryGray.500">{t("Empty folder")}</Text>
                </Box>
              )}
            </VStack>
          )}
        </ModalBody>

        <ModalFooter>
          {selectedClassesFileName && (
            <Text fontSize="xs" color="green.500" mr="auto">
              {t("Classes")}: {selectedClassesFileName}
            </Text>
          )}
          <Button variant="ghost" mr={3} onClick={onClose}>
            {t("Cancel")}
          </Button>
          <Button
            colorScheme="brand"
            onClick={handleSelect}
            isDisabled={!result || (isClassesMode ? !selectedClassesFile : result.image_count === 0)}
            leftIcon={<MdCheck />}
          >
            {isClassesMode
              ? t("Select classes file")
              : t("Select images", { count: result?.image_count || 0 })}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
