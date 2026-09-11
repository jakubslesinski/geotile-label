import { Box, Flex } from "@chakra-ui/react";
import { useState } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "../components/sidebar/Sidebar";
import { useHelpHotkey } from "../hooks/useHelp";

export default function AppLayout() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true);
  useHelpHotkey();

  return (
    <Flex h="100vh" overflow="hidden" bg="app.main">
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed((prev) => !prev)}
      />
      <Box flex="1" overflow="auto" p={4} bg="app.main">
        <Outlet />
      </Box>
    </Flex>
  );
}
