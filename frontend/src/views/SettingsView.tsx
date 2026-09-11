import { useEffect, useState } from "react";
import {
  VStack,
  Text,
  HStack,
  Switch,
  Badge,
  Button,
  Code,
  Divider,
  SimpleGrid,
  Select,
  useColorMode,
  useColorModeValue,
  useToast,
} from "@chakra-ui/react";
import Card from "../components/common/Card";
import InfoPopover from "../components/common/InfoPopover";
import * as api from "../api/client";
import { openCudaPackDialog } from "../desktop/dialogs";
import { APP_AUTHOR, APP_CONTACT_EMAIL } from "../config/contact";
import { useHelp } from "../hooks/useHelp";
import { useTranslation } from "react-i18next";

export default function SettingsView() {
  const { t, i18n } = useTranslation();
  const openHelp = useHelp();
  const { colorMode, toggleColorMode } = useColorMode();
  const [diagnostics, setDiagnostics] = useState<api.DiagnosticsInfo | null>(null);
  const [cudaPack, setCudaPack] = useState<api.CudaPackStatus | null>(null);
  // Capabilities z Tauri to migawka zrobiona przy starcie backendu. Do decyzji
  // "czy GPU jest już używane" pytamy backend na żywo — migawka mogła powstać,
  // zanim torch zdążył się zaimportować.
  const [liveCapabilities, setLiveCapabilities] = useState<api.BackendCapabilities | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const toast = useToast();
  const textColor = useColorModeValue("navy.700", "white");
  const labelColor = useColorModeValue("secondaryGray.600", "secondaryGray.600");

  const loadDiagnostics = async () => {
    if (!api.isTauriRuntime()) return;
    try {
      setDiagnostics(await api.getAppDiagnostics());
    } catch (err: any) {
      toast({
        title: t("Diagnostics unavailable"),
        description: err?.message || String(err),
        status: "warning",
      });
    }
    try {
      setCudaPack(await api.getCudaPackStatus());
    } catch {
      setCudaPack(null);
    }
    try {
      setLiveCapabilities(await api.getCapabilities());
    } catch {
      setLiveCapabilities(null);
    }
  };

  const handleInstallCudaPack = async () => {
    const paths = await openCudaPackDialog();
    if (!paths.length) return;
    setBusy("cuda");
    try {
      const status = await api.installCudaPack(paths);
      setCudaPack(status);
      toast({
        title: t("CUDA pack installed"),
        description: t("Restart the application to start using the GPU runtime."),
        status: "success",
        duration: 10000,
        isClosable: true,
      });
    } catch (err: any) {
      // Odrzucony pakiet jest usuwany po stronie Rusta, a aplikacja zostaje na CPU —
      // komunikat z backendu niesie powód i musi dotrzeć w całości.
      toast({
        title: t("CUDA pack rejected"),
        description: err?.message || String(err),
        status: "error",
        duration: 12000,
        isClosable: true,
      });
      await loadDiagnostics();
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    loadDiagnostics();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runAction = async (name: string, action: () => Promise<void>, successKey: string) => {
    setBusy(name);
    try {
      await action();
      toast({ title: t(successKey), status: "success" });
      await loadDiagnostics();
    } catch (err: any) {
      toast({
        title: t("Action failed", { action: t(successKey) }),
        description: err?.message || String(err),
        status: "error",
      });
    } finally {
      setBusy(null);
    }
  };

  return (
    <VStack align="stretch" spacing={6} maxW="900px">
      <Text fontSize="2xl" fontWeight="bold" color={textColor}>
        {t("Settings")}
      </Text>

      <Card>
        <VStack align="stretch" spacing={4}>
          <HStack justify="space-between">
            <VStack align="start" spacing={0}>
              <Text fontWeight="bold" color={textColor}>
                {t("Dark Mode")}
              </Text>
              <Text fontSize="sm" color={labelColor}>
                {t("Toggle between light and dark theme")}
              </Text>
            </VStack>
            <Switch
              isChecked={colorMode === "dark"}
              onChange={toggleColorMode}
              colorScheme="brand"
            />
          </HStack>
        </VStack>
      </Card>

      <Card>
        <HStack justify="space-between" align="center">
          <VStack align="start" spacing={0}>
            <Text fontWeight="bold" color={textColor}>{t("Interface language")}</Text>
            <Text fontSize="sm" color={labelColor}>{t("Select the language used by the application interface")}</Text>
          </VStack>
          <Select
            maxW="220px"
            value={i18n.resolvedLanguage?.startsWith("en") ? "en" : "pl"}
            onChange={(event) => void i18n.changeLanguage(event.target.value)}
          >
            <option value="pl">{t("Polish")}</option>
            <option value="en">{t("English")}</option>
          </Select>
        </HStack>
      </Card>

      <Card>
        <HStack justify="space-between" align="center">
          <VStack align="start" spacing={0}>
            <Text fontWeight="bold" color={textColor}>{t("Documentation")}</Text>
            <Text fontSize="sm" color={labelColor}>
              {t("Offline user guide. Opens in a separate window; shortcut F1.")}
            </Text>
          </VStack>
          <Button size="sm" variant="outline" onClick={() => void openHelp()}>
            {t("Open documentation")}
          </Button>
        </HStack>
      </Card>

      <Card>
        <VStack align="stretch" spacing={2}>
          <Text fontWeight="bold" color={textColor}>
            {t("About")}
          </Text>
          <Text fontSize="sm" color={labelColor}>
            GeoTile Label v{diagnostics?.appVersion || "0.1.4"}
          </Text>
          <Text fontSize="sm" color={labelColor}>
            {t("Satellite scene labeling and ML dataset generator.")}
          </Text>
          <Text fontSize="sm" color={labelColor}>
            {t("Supports PNG, JPEG, and GeoTIFF scenes with YOLO, COCO, and Pascal VOC export formats.")}
          </Text>
          <Divider my={1} />
          <Text fontSize="sm" color={labelColor}>
            {t("Author")}: {APP_AUTHOR}
          </Text>
          <Text fontSize="sm" color={labelColor}>
            {t("Contact")}: {APP_CONTACT_EMAIL}
          </Text>
        </VStack>
      </Card>

      <Card>
        <VStack align="stretch" spacing={4}>
          <HStack justify="space-between">
            <VStack align="start" spacing={0}>
              <HStack spacing={1}>
                <Text fontWeight="bold" color={textColor}>{t("Diagnostics")}</Text>
                <InfoPopover titleKey="info.diagnostics.backend.title" bodyKey="info.diagnostics.backend.body" />
              </HStack>
              <Text fontSize="sm" color={labelColor}>
                {t("Runtime state, logs, and support package.")}
              </Text>
            </VStack>
            <Button size="sm" variant="outline" onClick={loadDiagnostics}>
              {t("Refresh")}
            </Button>
          </HStack>

          {diagnostics ? (
            <SimpleGrid columns={{ base: 1, md: 2 }} spacing={3}>
              <DiagnosticItem label={t("Backend")} value={diagnostics.backendRunning ? t("running") : t("not running")} />
              <DiagnosticItem label={t("Version")} value={diagnostics.appVersion} />
              <DiagnosticItem label={t("Build")} value={diagnostics.capabilities.build_variant || diagnostics.capabilities.buildVariant || "yolo"} />
              <DiagnosticItem
                label={t("Release feature flags")}
                value={Object.entries(
                  diagnostics.capabilities.feature_flags
                    || diagnostics.capabilities.featureFlags
                    || {},
                )
                  .map(([name, enabled]) => `${name.replace("GEOTILE_", "")}:${enabled ? "1" : "0"}`)
                  .join(" · ") || "-"}
              />
              <DiagnosticItem label="OS" value={`${diagnostics.os} ${diagnostics.arch}`} />
              <DiagnosticItem label="YOLO" value={diagnostics.capabilities.yolo ? t("enabled") : t("disabled")} />
              <DiagnosticItem label={t("Device")} value={diagnostics.capabilities.device || "cpu"} />
              <DiagnosticItem label="Torch" value={diagnostics.capabilities.torch || "-"} />
              <DiagnosticItem label="Ultralytics" value={diagnostics.capabilities.ultralytics || "-"} />
              <DiagnosticItem label={t("Data")} value={diagnostics.dataDir} />
              <DiagnosticItem label={t("Logs")} value={diagnostics.logsDir} />
              <DiagnosticItem label={t("Runtime")} value={diagnostics.runtimeDir} />
              <DiagnosticItem label="Backend URL" value={diagnostics.backendBaseUrl || "-"} />
            </SimpleGrid>
          ) : (
            <Text fontSize="sm" color={labelColor}>
              {t("Diagnostics are available in the desktop app.")}
            </Text>
          )}

          {diagnostics?.lastBackendError && (
            <Code whiteSpace="pre-wrap" p={3}>
              {diagnostics.lastBackendError}
            </Code>
          )}

          {api.isTauriRuntime() && (
            <>
              <Divider />
              <VStack align="stretch" spacing={2}>
                <HStack>
                  <Text fontWeight="bold" color={textColor}>{t("GPU training pack")}</Text>
                  <Badge colorScheme={cudaPack?.usable ? "green" : cudaPack?.installed ? "red" : "gray"}>
                    {cudaPack?.usable
                      ? t("installed")
                      : cudaPack?.installed
                        ? t("unusable")
                        : t("not installed")}
                  </Badge>
                </HStack>
                <Text fontSize="sm" color={labelColor}>
                  {t("The base installation runs on CPU and covers labeling, SAM and prediction. Training needs the separate CUDA pack - point at the file you received.")}
                </Text>
                {cudaPack?.detail && (
                  <Text fontSize="xs" color={cudaPack.usable ? labelColor : "red.400"}>
                    {cudaPack.usable ? `CUDA ${cudaPack.detail}` : cudaPack.detail}
                  </Text>
                )}
                <HStack wrap="wrap">
                  <Button
                    size="sm"
                    variant="outline"
                    isLoading={busy === "cuda"}
                    onClick={handleInstallCudaPack}
                  >
                    {cudaPack?.installed ? t("Replace CUDA pack") : t("Select CUDA pack")}
                  </Button>
                  {cudaPack?.installed && (
                    <Button
                      size="sm"
                      variant="ghost"
                      colorScheme="red"
                      isLoading={busy === "cuda-remove"}
                      onClick={() =>
                        runAction("cuda-remove", api.removeCudaPack, "CUDA pack removed")
                      }
                    >
                      {t("Remove")}
                    </Button>
                  )}
                </HStack>
                {cudaPack?.usable && liveCapabilities?.device === "cuda" && (
                  <Text fontSize="sm" color="green.400">
                    {t("The GPU runtime is active.")}
                  </Text>
                )}
                {cudaPack?.usable && liveCapabilities !== null && liveCapabilities.device !== "cuda" && (
                  <Text fontSize="sm" color="orange.400">
                    {t("Restart the application to start using the GPU runtime.")}
                  </Text>
                )}
              </VStack>
            </>
          )}

          <Divider />
          <HStack wrap="wrap">
            <Button
              size="sm"
              variant="outline"
              isLoading={busy === "logs"}
              onClick={() => runAction("logs", api.openLogsDir, "Logs folder opened")}
            >
              {t("Open logs folder")}
            </Button>
            <HStack spacing={1}>
              <Button
                size="sm"
                variant="outline"
                isLoading={busy === "zip"}
                onClick={() =>
                  runAction("zip", async () => {
                    const path = await api.exportDiagnosticsZip();
                    toast({
                      title: t("Diagnostics ZIP exported"),
                      description: path,
                      status: "success",
                      duration: 6000,
                    });
                  }, "Diagnostics exported")
                }
              >
                {t("Export diagnostics ZIP")}
              </Button>
              <InfoPopover titleKey="info.diagnostics.exportZip.title" bodyKey="info.diagnostics.exportZip.body" />
            </HStack>
            {/* Bez adresu użytkownik ma gotową paczkę i nie wie, komu ją wysłać. */}
            <Text fontSize="sm" color={labelColor}>
              {t("Send the diagnostics package to")}: {APP_CONTACT_EMAIL}
            </Text>
            <Button
              size="sm"
              variant="outline"
              isLoading={busy === "restart"}
              onClick={() => runAction("restart", async () => { await api.restartBackend(); }, "Backend restarted")}
            >
              {t("Restart backend")}
            </Button>
            <HStack spacing={1}>
              <Button
                size="sm"
                colorScheme="red"
                variant="outline"
                isLoading={busy === "clear"}
                onClick={() => runAction("clear", api.clearRuntimeCache, "Runtime/cache cleared")}
              >
                {t("Clear runtime/cache")}
              </Button>
              <InfoPopover titleKey="info.diagnostics.clearRuntime.title" bodyKey="info.diagnostics.clearRuntime.body" />
            </HStack>
          </HStack>
        </VStack>
      </Card>

      <Card>
        <VStack align="stretch" spacing={2}>
          <Text fontWeight="bold" color={textColor}>
            {t("Keyboard Shortcuts")}
          </Text>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Select class")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>1-9</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Change class")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>K</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Pan / select")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>V</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Draw annotation")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>D</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Multi-select")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>A</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Measure")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>M</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Class paint")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>P</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Toggle grid visibility")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>S</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Toggle scene raster visibility")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>Z</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Decrease scene opacity by 10%")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>[</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Increase scene opacity by 10%")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>]</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Decrease display gamma")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>Ctrl+[</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Increase display gamma")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>Ctrl+]</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Undo last annotation")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>Ctrl+Z</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Cancel drawing")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>Esc / {t("Right-click")}</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Pan")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>{t("Middle-click")} / Alt+klik</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Zoom")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>{t("Scroll wheel")}</Text>
          </HStack>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Delete selected")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>Delete / Backspace</Text>
          </HStack>
        </VStack>
      </Card>
    </VStack>
  );
}

function DiagnosticItem({ label, value }: { label: string; value: string }) {
  const textColor = useColorModeValue("navy.700", "white");
  const labelColor = useColorModeValue("secondaryGray.600", "secondaryGray.600");
  return (
    <VStack align="start" spacing={0}>
      <Text fontSize="xs" color={labelColor}>{label}</Text>
      <Text fontSize="sm" color={textColor} wordBreak="break-all">{value}</Text>
    </VStack>
  );
}
