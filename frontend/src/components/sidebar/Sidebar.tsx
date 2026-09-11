import {
  Box,
  Flex,
  Text,
  VStack,
  Icon,
  useColorModeValue,
  Divider,
  IconButton,
  useColorMode,
  Image,
  Tooltip,
} from "@chakra-ui/react";
import { Link, useLocation, useParams } from "react-router-dom";
import { isLiteEdition, LITE_HIDDEN_SECTIONS } from "../../config/edition";
import {
  MdDashboard,
  MdSettings,
  MdDarkMode,
  MdLightMode,
  MdViewList,
  MdDataset,
  MdModelTraining,
  MdInsights,
  MdBubbleChart,
  MdChevronLeft,
  MdChevronRight,
  MdHelpOutline,
} from "react-icons/md";
import logoUrl from "../../assets/geotile-label.png";
import { useTranslation } from "react-i18next";
import { useHelp } from "../../hooks/useHelp";
import JobsDrawer from "../common/JobsDrawer";

interface NavItem {
  label: string;
  icon: any;
  path: string;
}

interface SidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
}

export default function Sidebar({ collapsed, onToggleCollapse }: SidebarProps) {
  const { t } = useTranslation();
  const { id } = useParams();
  const location = useLocation();
  const { colorMode, toggleColorMode } = useColorMode();
  const openHelp = useHelp();

  const bg = useColorModeValue("white", "#19191B");
  const textColor = useColorModeValue("navy.700", "white");
  const activeColor = useColorModeValue("#7C4FE0", "brand.300");
  const activeBg = useColorModeValue("rgba(124, 79, 224, 0.12)", "rgba(155, 108, 255, 0.16)");

  const baseItems: NavItem[] = [
    { label: t("Projects"), icon: MdDashboard, path: "/projects" },
  ];

  const projectItems: NavItem[] = id
    ? [
        { label: t("Dashboard"), icon: MdViewList, path: `/projects/${id}` },
        { label: t("Dataset"), icon: MdDataset, path: `/projects/${id}/dataset` },
        { label: t("Dataset analysis"), icon: MdBubbleChart, path: `/projects/${id}/analysis` },
        { label: t("Training"), icon: MdModelTraining, path: `/projects/${id}/training` },
        { label: t("Results"), icon: MdInsights, path: `/projects/${id}/results` },
      ].filter(
        // Edycja "lite": ukryj zaawansowane zakładki (Analiza, Trening, Wyniki).
        (item) => !isLiteEdition || !LITE_HIDDEN_SECTIONS.some((suffix) => item.path.endsWith(suffix)),
      )
    : [];

  const bottomItems: NavItem[] = [
    { label: t("Settings"), icon: MdSettings, path: "/settings" },
  ];

  const isActive = (path: string) => location.pathname === path;

  const NavLink = ({ item }: { item: NavItem }) => (
    <Flex
      as={Link}
      to={item.path}
      align="center"
      justify={collapsed ? "center" : "flex-start"}
      gap={collapsed ? 0 : 3}
      px={collapsed ? 2 : 4}
      py={2.5}
      borderRadius="lg"
      fontWeight={isActive(item.path) ? "bold" : "normal"}
      color={isActive(item.path) ? activeColor : textColor}
      bg={isActive(item.path) ? activeBg : "transparent"}
      _hover={{ bg: activeBg }}
      transition="all 0.2s"
    >
      <Icon as={item.icon} boxSize={5} />
      {!collapsed && <Text fontSize="sm">{item.label}</Text>}
    </Flex>
  );

  // Pomoc otwiera osobne okno dokumentacji, a nie trasę aplikacji, więc jest akcją
  // stylizowaną na pozycję nawigacji, nie linkiem routera.
  const HelpButton = () => (
    <Tooltip
      label={`${t("Documentation")} (F1)`}
      placement="right"
      hasArrow
      openDelay={350}
      isDisabled={!collapsed}
    >
      <Flex
        as="button"
        type="button"
        onClick={() => void openHelp()}
        aria-label={t("Documentation")}
        align="center"
        justify={collapsed ? "center" : "flex-start"}
        gap={collapsed ? 0 : 3}
        px={collapsed ? 2 : 4}
        py={2.5}
        borderRadius="lg"
        color={textColor}
        bg="transparent"
        _hover={{ bg: activeBg }}
        transition="all 0.2s"
        w="100%"
      >
        <Icon as={MdHelpOutline} boxSize={5} />
        {!collapsed && <Text fontSize="sm">{t("Documentation")}</Text>}
      </Flex>
    </Tooltip>
  );

  return (
    <Box
      w={collapsed ? "72px" : "240px"}
      minW={collapsed ? "72px" : "240px"}
      h="100vh"
      bg={bg}
      borderRight="1px"
      borderColor={useColorModeValue("rgba(20, 20, 24, 0.10)", "rgba(255, 255, 255, 0.07)")}
      py={6}
      display="flex"
      flexDirection="column"
      transition="width 0.2s ease"
    >
      <Flex
        align="center"
        justify={collapsed ? "center" : "space-between"}
        direction={collapsed ? "column" : "row"}
        gap={collapsed ? 2 : 0}
        px={collapsed ? 2 : 5}
        mb={6}
      >
        {!collapsed && (
          <Flex align="center" gap={3} minW={0}>
            <Image src={logoUrl} alt="GeoTile Label" boxSize="40px" objectFit="contain" />
            <Text
              fontSize="xl"
              fontWeight="bold"
              color={activeColor}
              letterSpacing="-0.2px"
              noOfLines={1}
            >
              GeoTile Label
            </Text>
          </Flex>
        )}
        {collapsed && (
          <Image src={logoUrl} alt="GeoTile Label" boxSize="38px" objectFit="contain" />
        )}
        <IconButton
          aria-label={collapsed ? t("Expand sidebar") : t("Collapse sidebar")}
          icon={collapsed ? <MdChevronRight /> : <MdChevronLeft />}
          variant="ghost"
          size="sm"
          onClick={onToggleCollapse}
        />
      </Flex>

      <VStack spacing={1} align="stretch" px={3} flex="1">
        {baseItems.map((item) => (
          <NavLink key={item.path} item={item} />
        ))}

        {projectItems.length > 0 && (
          <>
            <Divider my={2} />
            {projectItems.map((item) => (
              <NavLink key={item.path} item={item} />
            ))}
          </>
        )}
      </VStack>

      <VStack spacing={1} align="stretch" px={3}>
        <Divider mb={2} />
        <JobsDrawer projectId={id} collapsed={collapsed} />
        <HelpButton />
        {bottomItems.map((item) => (
          <NavLink key={item.path} item={item} />
        ))}
        <Flex px={collapsed ? 2 : 4} py={2} justify={collapsed ? "center" : "flex-start"}>
          <IconButton
            aria-label={t("Toggle color mode")}
            icon={
              colorMode === "dark" ? <MdLightMode /> : <MdDarkMode />
            }
            variant="ghost"
            size="sm"
            onClick={toggleColorMode}
          />
        </Flex>
      </VStack>
    </Box>
  );
}
