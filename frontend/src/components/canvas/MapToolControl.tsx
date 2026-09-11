import {
  Box,
  IconButton,
  Tooltip,
  VStack,
  useColorModeValue,
} from "@chakra-ui/react";
import {
  MdEdit,
  MdCheckBox,
  MdClear,
  MdFormatColorFill,
  MdBlock,
  MdPanTool,
  MdSelectAll,
  MdStraighten,
} from "react-icons/md";
import { useTranslation } from "react-i18next";
import type { IconType } from "react-icons";
import type { MapTool } from "../../types";

interface Props {
  activeTool: MapTool;
  onToolChange: (tool: MapTool) => void;
}

interface ToolItem {
  id: MapTool;
  labelKey: string;
  icon: IconType;
  shortcut: string;
}

const tools: ToolItem[] = [
  { id: "select", labelKey: "Pan / select", icon: MdPanTool, shortcut: "V" },
  { id: "draw", labelKey: "Draw annotation", icon: MdEdit, shortcut: "D" },
  { id: "multi_select", labelKey: "Multi-select", icon: MdSelectAll, shortcut: "A" },
  { id: "measure", labelKey: "Measure", icon: MdStraighten, shortcut: "M" },
  { id: "class_paint", labelKey: "Class paint", icon: MdFormatColorFill, shortcut: "P" },
  { id: "grid_review", labelKey: "Mark grid reviewed", icon: MdCheckBox, shortcut: "" },
  { id: "grid_clear", labelKey: "Clear grid review", icon: MdClear, shortcut: "" },
  { id: "grid_exclude", labelKey: "Exclude grid cells", icon: MdBlock, shortcut: "" },
];

export default function MapToolControl({ activeTool, onToolChange }: Props) {
  const { t } = useTranslation();
  const bg = useColorModeValue("white", "rgba(28, 28, 31, 0.94)");
  const borderColor = useColorModeValue("secondaryGray.200", "rgba(255, 255, 255, 0.10)");
  const activeBg = useColorModeValue("brand.500", "brand.200");
  const inactiveColor = useColorModeValue("navy.700", "whiteAlpha.900");

  return (
    <Box
      position="absolute"
      left="16px"
      top="16px"
      zIndex={1100}
      bg={bg}
      borderWidth="1px"
      borderColor={borderColor}
      borderRadius="12px"
      boxShadow="lg"
      backdropFilter="blur(10px)"
      p="4px"
      pointerEvents="auto"
    >
      <VStack spacing="4px">
        {tools.map((tool) => {
          const Icon = tool.icon;
          const isActive = activeTool === tool.id;
          const label = tool.shortcut ? `${t(tool.labelKey)} (${tool.shortcut})` : t(tool.labelKey);
          return (
            <Tooltip key={tool.id} label={label} placement="right" hasArrow>
              <IconButton
                aria-label={label}
                icon={<Icon size={18} />}
                size="sm"
                minW="34px"
                h="34px"
                borderRadius="9px"
                variant={isActive ? "solid" : "ghost"}
                bg={isActive ? activeBg : "transparent"}
                color={isActive ? "white" : inactiveColor}
                _hover={{ bg: isActive ? activeBg : "blackAlpha.100" }}
                onClick={() => onToolChange(tool.id)}
              />
            </Tooltip>
          );
        })}
      </VStack>
    </Box>
  );
}
