import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import {
  Badge,
  Box,
  Button,
  HStack,
  Input,
  Popover,
  PopoverBody,
  PopoverContent,
  PopoverTrigger,
  Text,
  VStack,
  useColorModeValue,
} from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import type { LabelClass } from "../../types";

interface Props {
  classes: LabelClass[];
  activeClassId: number | null;
  isOpen: boolean;
  onOpen: () => void;
  onClose: () => void;
  onSelect: (classId: number) => void;
}

export default function HotkeyBar({ classes, activeClassId, isOpen, onOpen, onClose, onSelect }: Props) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const activeBorder = useColorModeValue("#7C4FE0", "brand.300");
  const activeBg = useColorModeValue("white", "whiteAlpha.100");
  const mutedColor = useColorModeValue("gray.500", "whiteAlpha.700");
  const activeClass = classes.find((cls) => cls.id === activeClassId) || null;

  const filteredClasses = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const source = normalized
      ? classes.filter((cls) => {
          const tokens = [
            String(cls.id),
            cls.name,
            cls.hotkey ? String(cls.hotkey) : "",
          ].join(" ").toLowerCase();
          return tokens.includes(normalized);
        })
      : classes;
    return [...source].sort((a, b) => a.id - b.id);
  }, [classes, query]);

  useEffect(() => {
    if (isOpen) {
      setQuery("");
      window.setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  const selectClass = (classId: number) => {
    onSelect(classId);
    onClose();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" && filteredClasses[0]) {
      event.preventDefault();
      selectClass(filteredClasses[0].id);
    }
  };

  return (
    <Popover
      isOpen={isOpen}
      onOpen={onOpen}
      onClose={onClose}
      placement="bottom-start"
      initialFocusRef={inputRef}
      returnFocusOnClose={false}
    >
      <PopoverTrigger>
        <Button
          size="sm"
          variant="outline"
          borderColor={activeClass ? activeBorder : "transparent"}
          bg={activeBg}
          maxW="420px"
          minW="260px"
          minH="32px"
          px={3}
          justifyContent="flex-start"
          overflow="hidden"
        >
          <HStack spacing={2} minW={0} w="100%">
            <Text fontSize="xs" color={mutedColor} flexShrink={0}>
              {t("Active class")}:
            </Text>
            {activeClass ? (
              <>
                <Box w="10px" h="10px" borderRadius="sm" bg={activeClass.color} flexShrink={0} />
                <Text fontSize="sm" fontWeight="semibold" noOfLines={1} title={activeClass.name}>
                  {activeClass.hotkey ? `[${activeClass.hotkey}] ` : ""}
                  {activeClass.name}
                </Text>
              </>
            ) : (
              <Text fontSize="sm" color={mutedColor} noOfLines={1}>
                {t("None selected")}
              </Text>
            )}
            <Badge ml="auto" colorScheme="purple" flexShrink={0}>K</Badge>
          </HStack>
        </Button>
      </PopoverTrigger>
      <PopoverContent w="380px" maxW="calc(100vw - 32px)" zIndex={1600}>
        <PopoverBody p={3}>
          <VStack align="stretch" spacing={3}>
            <Text fontSize="sm" fontWeight="bold">{t("Select active class")}</Text>
            <Input
              ref={inputRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t("Search class by name or ID")}
            />
            <Text fontSize="xs" color={mutedColor}>
              {t("Type to filter classes. Press Enter to select the first match.")}
            </Text>
            <VStack align="stretch" spacing={1} maxH="50vh" overflowY="auto">
              {filteredClasses.map((cls) => (
                <Button
                  key={cls.id}
                  justifyContent="flex-start"
                  variant={cls.id === activeClassId ? "solid" : "ghost"}
                  colorScheme={cls.id === activeClassId ? "brand" : "gray"}
                  onClick={() => selectClass(cls.id)}
                  leftIcon={<Box w="12px" h="12px" borderRadius="sm" bg={cls.color} />}
                >
                  <HStack spacing={2} minW={0}>
                    <Badge colorScheme="gray">{cls.id}</Badge>
                    {cls.hotkey && <Badge colorScheme="purple">{cls.hotkey}</Badge>}
                    <Text noOfLines={1} title={cls.name}>{cls.name}</Text>
                  </HStack>
                </Button>
              ))}
              {filteredClasses.length === 0 && (
                <Box px={2} py={4}>
                  <Text fontSize="sm" color={mutedColor}>{t("No classes match search")}</Text>
                </Box>
              )}
            </VStack>
          </VStack>
        </PopoverBody>
      </PopoverContent>
    </Popover>
  );
}
