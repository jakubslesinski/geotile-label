import { useEffect, useState } from "react";
import {
  Alert,
  AlertIcon,
  Box,
  Button,
  Code,
  HStack,
  Image,
  Spinner,
  Text,
  VStack,
  useColorModeValue,
  useToast,
} from "@chakra-ui/react";
import Card from "../components/common/Card";
import * as api from "../api/client";
import logoUrl from "../assets/geotile-label.png";
import { useHelp } from "../hooks/useHelp";
import { useTranslation } from "react-i18next";

type StartupStatus = "starting" | "ready" | "failed";

interface StartupViewProps {
  onReady: () => void;
}

export default function StartupView({ onReady }: StartupViewProps) {
  const { t } = useTranslation();
  const openHelp = useHelp();
  const [status, setStatus] = useState<StartupStatus>("starting");
  const [message, setMessage] = useState(() => t("Preparing runtime and starting backend..."));
  const [diagnostics, setDiagnostics] = useState<api.DiagnosticsInfo | null>(null);
  const toast = useToast();
  const textColor = useColorModeValue("navy.700", "white");
  const mutedColor = useColorModeValue("secondaryGray.600", "secondaryGray.400");

  const loadDiagnostics = async () => {
    if (!api.isTauriRuntime()) return;
    try {
      setDiagnostics(await api.getAppDiagnostics());
    } catch {
      // Diagnostics are best-effort during startup.
    }
  };

  const start = async (restart = false) => {
    setStatus("starting");
    setMessage(restart ? t("Restarting backend...") : t("Preparing runtime and starting backend..."));
    try {
      if (restart && api.isTauriRuntime()) {
        await api.restartBackend();
      } else {
        await api.initializeApi();
      }
      setStatus("ready");
      onReady();
    } catch (err: any) {
      const detail = err?.message || String(err);
      setStatus("failed");
      setMessage(detail);
      await loadDiagnostics();
    }
  };

  useEffect(() => {
    start(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleOpenLogs = async () => {
    try {
      await api.openLogsDir();
    } catch (err: any) {
      toast({
        title: t("Cannot open logs folder"),
        description: err?.message || String(err),
        status: "error",
      });
    }
  };

  const handleExportDiagnostics = async () => {
    try {
      const path = await api.exportDiagnosticsZip();
      toast({
        title: t("Diagnostics ZIP exported"),
        description: path,
        status: "success",
        duration: 6000,
      });
    } catch (err: any) {
      toast({
        title: t("Diagnostics export failed"),
        description: err?.message || String(err),
        status: "error",
      });
    }
  };

  return (
    <Box minH="100vh" display="flex" alignItems="center" justifyContent="center" p={6}>
      <Card maxW="760px" w="100%">
        <VStack align="stretch" spacing={5}>
          <HStack align="center" spacing={4}>
            <Image src={logoUrl} alt="GeoTile Label" boxSize="56px" objectFit="contain" />
            <VStack align="start" spacing={1}>
              <Text fontSize="2xl" fontWeight="bold" color={textColor}>
                GeoTile Label
              </Text>
              <Text fontSize="sm" color={mutedColor}>
                {t("Desktop runtime startup")}
              </Text>
            </VStack>
          </HStack>

          {status === "starting" && (
            <HStack>
              <Spinner color="brand.400" />
              <Text color={textColor}>{message}</Text>
            </HStack>
          )}

          {status === "failed" && (
            <VStack align="stretch" spacing={4}>
              <Alert status="error" borderRadius="lg">
                <AlertIcon />
                {t("Backend failed to start.")}
              </Alert>
              <Code whiteSpace="pre-wrap" p={3}>
                {message}
              </Code>
              {diagnostics && (
                <VStack align="stretch" spacing={1} fontSize="sm" color={mutedColor}>
                  <Text>{t("Logs")}: {diagnostics.logsDir}</Text>
                  <Text>{t("Data")}: {diagnostics.dataDir}</Text>
                  <Text>{t("Runtime")}: {diagnostics.runtimeDir}</Text>
                  <Text>{t("Version")}: {diagnostics.appVersion}</Text>
                </VStack>
              )}
              <HStack wrap="wrap">
                <Button colorScheme="brand" onClick={() => start(true)}>
                  {t("Retry backend startup")}
                </Button>
                <Button variant="outline" onClick={handleOpenLogs}>
                  {t("Open logs folder")}
                </Button>
                <Button variant="outline" onClick={handleExportDiagnostics}>
                  {t("Export diagnostics ZIP")}
                </Button>
                {/* Dokumentacja nie zależy od backendu, więc jest dostępna
                    dokładnie wtedy, gdy jest najbardziej potrzebna. */}
                <Button
                  variant="outline"
                  onClick={() => void openHelp("troubleshooting/index.html")}
                >
                  {t("Open documentation")}
                </Button>
              </HStack>
            </VStack>
          )}
        </VStack>
      </Card>
    </Box>
  );
}
