import { useState, useEffect, useCallback } from "react";
import {
  VStack,
  HStack,
  Text,
  Slider,
  SliderTrack,
  SliderFilledTrack,
  SliderThumb,
  Switch,
  Button,
  Select,
  Badge,
  Divider,
  Alert,
  AlertIcon,
  NumberInput,
  NumberInputField,
  NumberInputStepper,
  NumberIncrementStepper,
  NumberDecrementStepper,
  useColorModeValue,
  useToast,
  Box,
  Modal,
  ModalOverlay,
  ModalContent,
  ModalHeader,
  ModalBody,
  ModalCloseButton,
  Spinner,
  useDisclosure,
} from "@chakra-ui/react";
import {
  MdFolderOpen,
  MdArrowBack,
  MdHome,
  MdFolder,
  MdInsertDriveFile,
  MdDelete,
} from "react-icons/md";
import Card from "../common/Card";
import InfoPopover from "../common/InfoPopover";
import * as api from "../../api/client";
import type { LabelClass, ModelInfo, Prediction, PredictionConfig } from "../../types";
import {
  openModelFileDialog,
  openSamModelsFolderDialog,
  openDinoModelsFolderDialog,
  isTauriRuntime,
} from "../../desktop/dialogs";
import { useTranslation } from "react-i18next";

interface Props {
  projectId: string;
  sceneId: string;
  classes: LabelClass[];
  predictions: Prediction[];
  isRunning: boolean;
  progress: {
    done: number;
    total: number;
    detections_so_far: number;
    current_tile?: string;
    model?: string;
    device?: string;
    batch_size?: number;
    resolved_batch_size?: number;
  } | null;
  pendingCount: number;
  acceptedCount: number;
  onRun: () => void | Promise<void>;
  onCancel: () => void | Promise<void>;
  onAcceptAll: () => void;
  onClearAll: () => void;
  onAccept: (ids: string[]) => void;
  onDelete: (ids: string[]) => void;
  onFlipFront?: (id: string) => void;
  selectedSamCheckpoint?: string | null;
  onSamCheckpointChange?: (checkpoint: string | null) => void;
  onSamModelsChange?: (result: api.SamModelsResult) => void;
  exemplarMatchingSettings: api.ExemplarMatchingSettings;
  onExemplarMatchingSettingsChange: (settings: api.ExemplarMatchingSettings) => void;
  showYoloControls?: boolean;
  showResults?: boolean;
}

const getFileNameFromPath = (path: string) => {
  const parts = path.split(/[/\\]/);
  return parts[parts.length - 1] || path;
};

const formatSize = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
};

export default function PredictionPanel({
  projectId,
  classes,
  predictions,
  isRunning,
  progress,
  pendingCount,
  acceptedCount,
  onRun,
  onCancel,
  onAcceptAll,
  onClearAll,
  onAccept,
  onDelete,
  onFlipFront,
  selectedSamCheckpoint,
  onSamCheckpointChange,
  onSamModelsChange,
  exemplarMatchingSettings,
  onExemplarMatchingSettingsChange,
  showYoloControls = true,
  showResults = true,
}: Props) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  const labelColor = useColorModeValue("secondaryGray.600", "secondaryGray.600");
  const mutedColor = useColorModeValue("gray.500", "whiteAlpha.600");
  const hoverBg = useColorModeValue("gray.50", "whiteAlpha.100");
  const toast = useToast();
  const { isOpen: isBrowseOpen, onOpen: onBrowseOpen, onClose: onBrowseClose } = useDisclosure();

  const [models, setModels] = useState<ModelInfo[]>([]);
  const [samModels, setSamModels] = useState<api.SamModelInfo[]>([]);
  const [dinoModels, setDinoModels] = useState<api.DinoModelInfo[]>([]);
  const [dinoModelsDirPath, setDinoModelsDirPath] = useState("");
  const [defaultDinoCheckpoint, setDefaultDinoCheckpoint] = useState<string | null>(null);
  const [dinoRuntimeAvailable, setDinoRuntimeAvailable] = useState(true);
  const [dinoError, setDinoError] = useState("");
  const [capabilities, setCapabilities] = useState(api.getCachedCapabilities());
  const [modelsDirPath, setModelsDirPath] = useState("");
  const [samModelsDirPath, setSamModelsDirPath] = useState("");
  const [defaultSamCheckpoint, setDefaultSamCheckpoint] = useState<string | null>(null);
  const [samInspectError, setSamInspectError] = useState("");
  const [browsePath, setBrowsePath] = useState("");
  const [browseData, setBrowseData] = useState<api.PredictionBrowseResult | null>(null);
  const [browseLoading, setBrowseLoading] = useState(false);
  const [modelClasses, setModelClasses] = useState<{ id: number; name: string }[]>([]);
  const [modelInspectError, setModelInspectError] = useState("");
  const [config, setConfig] = useState<PredictionConfig>({
    model_path: "",
    conf: 0.25,
    iou: 0.4,
    tile_size: 640,
    buffer: 64,
    device: "cpu",
    img_size: null,
    batch_size: "auto",
    prefetch_batches: 2,
    merge_method: "nms",
    progress_interval_ms: 250,
    preprocess_mode: "auto",
    stretch_low: 2,
    stretch_high: 98,
    gamma: 1,
    brightness: 1,
    contrast: 1,
    sam_checkpoint: null,
    sam_models_dir: null,
    sar_exemplar_backbone: null,
    exemplar_engine: "template",
    dino_models_dir: null,
    dino_checkpoint: null,
  });

  const refreshSamModels = useCallback(async () => {
    const data = await api.listSamModels(projectId);
    setSamModels(data.models || []);
    setSamModelsDirPath(data.models_dir_path || "");
    setDefaultSamCheckpoint(data.default_checkpoint || null);
    onSamModelsChange?.(data);
    return data;
  }, [projectId, onSamModelsChange]);

  const refreshDinoModels = useCallback(async () => {
    const data = await api.listDinoModels(projectId);
    setDinoModels(data.models || []);
    setDinoModelsDirPath(data.models_dir_path || "");
    setDefaultDinoCheckpoint(data.default_checkpoint || null);
    setDinoRuntimeAvailable(data.runtime_available);
    return data;
  }, [projectId]);

  useEffect(() => {
    api.getCapabilities().then(setCapabilities).catch(() => {});
  }, []);

  useEffect(() => {
    if (selectedSamCheckpoint === undefined) return;
    setConfig((current) => current.sam_checkpoint === selectedSamCheckpoint
      ? current
      : { ...current, sam_checkpoint: selectedSamCheckpoint });
  }, [selectedSamCheckpoint]);

  useEffect(() => {
    let active = true;
    const loadConfigAndSamModels = async () => {
      try {
        const data = await api.getPredictionConfig(projectId);
        if (!active) return;
        setConfig(data);
        try {
          const modelsData = await refreshSamModels();
          if (active) onSamCheckpointChange?.(data.sam_checkpoint || modelsData.default_checkpoint || null);
        } catch {
          if (active) onSamCheckpointChange?.(data.sam_checkpoint || null);
        }
        refreshDinoModels().catch(() => { if (active) setDinoRuntimeAvailable(false); });
      } catch {
        // The remaining prediction UI can still expose runtime diagnostics.
      }
    };
    void loadConfigAndSamModels();
    if (capabilities.yolo) {
      api.listModels(projectId).then((data) => {
        setModels(data.models_dir || []);
        setModelsDirPath(data.models_dir_path || "");
      }).catch(() => {});
    }
    return () => { active = false; };
  }, [projectId, capabilities.yolo, onSamCheckpointChange, refreshSamModels, refreshDinoModels]);

  const loadBrowsePath = useCallback(async (path: string) => {
    setBrowseLoading(true);
    try {
      const data = await api.browsePredictionModels(projectId, path);
      setBrowseData(data);
      setBrowsePath(data.path || "");
    } catch (err: any) {
      toast({
        title: t("Failed to browse model files"),
        description: err?.response?.data?.detail || err?.message || "Unknown error",
        status: "error",
        duration: 2500,
      });
    } finally {
      setBrowseLoading(false);
    }
  }, [projectId, toast]);

  useEffect(() => {
    if (!isBrowseOpen) return;
    loadBrowsePath("");
  }, [isBrowseOpen, loadBrowsePath]);

  const saveConfig = async (newConfig: PredictionConfig) => {
    const previousConfig = config;
    setConfig(newConfig);
    try {
      const savedConfig = await api.updatePredictionConfig(projectId, newConfig);
      setConfig(savedConfig);
      onSamCheckpointChange?.(savedConfig.sam_checkpoint || null);
    } catch (err: any) {
      setConfig(previousConfig);
      toast({
        title: t("Failed to save config"),
        description: err?.response?.data?.detail || err?.message,
        status: "error",
        duration: 3000,
      });
    }
  };

  const pendingPreds = predictions.filter((p) => p.status === "pending");
  const projectClassNames = new Set(classes.map((cls) => cls.name.toLowerCase()));
  const missingModelClasses = modelClasses.filter((cls) => !projectClassNames.has(cls.name.toLowerCase()));
  const modelInList = models.some((m) => m.path === config.model_path);
  const displayedModels = modelInList || !config.model_path
    ? models
    : [{
      name: getFileNameFromPath(config.model_path),
      path: config.model_path,
      size: 0,
      modified: "",
    }, ...models];
  const supportedSamModels = samModels.filter((model) => model.supported);
  const samModelInList = supportedSamModels.some((model) => model.path === config.sam_checkpoint);
  const displayedSamModels = samModelInList || !config.sam_checkpoint
    ? supportedSamModels
    : [{
      name: getFileNameFromPath(config.sam_checkpoint),
      path: config.sam_checkpoint,
      size: 0,
      modified: "",
      family: null,
      supported: true,
      reason: null,
    }, ...supportedSamModels];
  const activeSamModel = displayedSamModels.find((model) => model.path === config.sam_checkpoint)
    || supportedSamModels.find((model) => model.path === defaultSamCheckpoint);
  const goUpInBrowser = () => {
    loadBrowsePath(browseData?.parent_path || "");
  };

  const enterDirInBrowser = (nextPath: string) => {
    loadBrowsePath(nextPath);
  };

  const selectModelFile = (modelPath: string) => {
    saveConfig({ ...config, model_path: modelPath });
    onBrowseClose();
  };

  useEffect(() => {
    if (!capabilities.yolo || !config.model_path) {
      setModelClasses([]);
      setModelInspectError("");
      return;
    }
    let cancelled = false;
    api.inspectPredictionModel(projectId, config.model_path)
      .then((data) => {
        if (cancelled) return;
        setModelClasses(data.classes || []);
        setModelInspectError("");
      })
      .catch((err: any) => {
        if (cancelled) return;
        setModelClasses([]);
        setModelInspectError(err?.response?.data?.detail || err?.message || "Failed to inspect model");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, capabilities.yolo, config.model_path]);

  useEffect(() => {
    if (!config.sam_checkpoint) {
      setSamInspectError("");
      return;
    }
    let cancelled = false;
    api.inspectSamModel(projectId, config.sam_checkpoint)
      .then((info) => {
        if (cancelled) return;
        setSamInspectError("");
        setSamModels((current) => current.some((model) => model.path === info.path)
          ? current
          : [info, ...current]);
      })
      .catch((err: any) => {
        if (cancelled) return;
        setSamInspectError(err?.response?.data?.detail || err?.message || t("Unsupported SAM model"));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, config.sam_checkpoint, t]);

  const handleBrowseModel = async () => {
    if (isTauriRuntime()) {
      const selected = await openModelFileDialog();
      if (selected) selectModelFile(selected);
      return;
    }
    onBrowseOpen();
  };

  const handleBrowseSamModelsFolder = async () => {
    const selected = await openSamModelsFolderDialog();
    if (!selected) return;
    try {
      const saved = await api.updatePredictionConfig(projectId, {
        ...config,
        sam_models_dir: selected,
        sam_checkpoint: null,
      });
      setConfig(saved);
      setSamInspectError("");
      const data = await refreshSamModels();
      onSamCheckpointChange?.(data.default_checkpoint || null);
    } catch (err: any) {
      setSamInspectError(err?.response?.data?.detail || err?.message || t("Cannot use SAM models folder"));
    }
  };

  const handleBrowseDinoModelsFolder = async () => {
    const selected = await openDinoModelsFolderDialog();
    if (!selected) return;
    try {
      const saved = await api.updatePredictionConfig(projectId, {
        ...config, dino_models_dir: selected, dino_checkpoint: null,
      });
      setConfig(saved);
      setDinoError("");
      await refreshDinoModels();
    } catch (err: any) {
      setDinoError(err?.response?.data?.detail || err?.message || t("Cannot use DINO weights folder"));
    }
  };

  const handleClearSamFolder = async () => {
    try {
      const saved = await api.updatePredictionConfig(projectId, {
        ...config,
        sam_models_dir: null,
        sam_checkpoint: null,
      });
      setConfig(saved);
      setSamInspectError("");
      const data = await refreshSamModels();
      onSamCheckpointChange?.(data.default_checkpoint || null);
    } catch (err: any) {
      setSamInspectError(err?.response?.data?.detail || err?.message || t("Cannot clear SAM models folder"));
    }
  };

  const handleClearDinoFolder = async () => {
    try {
      const saved = await api.updatePredictionConfig(projectId, {
        ...config,
        dino_models_dir: null,
        dino_checkpoint: null,
      });
      setConfig(saved);
      setDinoError("");
      await refreshDinoModels();
    } catch (err: any) {
      setDinoError(err?.response?.data?.detail || err?.message || t("Cannot clear DINO weights folder"));
    }
  };

  const handleRunPrediction = async () => {
    try {
      await onRun();
    } catch (err: any) {
      toast({
        title: t("Prediction failed"),
        description: err?.message || "Unknown error",
        status: "error",
        duration: 5000,
      });
    }
  };

  const handleCancelPrediction = async () => {
    try {
      await onCancel();
    } catch (err: any) {
      toast({
        title: t("Failed to cancel prediction"),
        description: err?.message || "Unknown error",
        status: "error",
        duration: 3000,
      });
    }
  };

  const samRuntimeAvailable = capabilities.sam?.mock || capabilities.sam?.runtime_available;
  const unsupportedSamCount = samModels.filter((model) => !model.supported).length;
  const updateMatchingSettings = (patch: Partial<api.ExemplarMatchingSettings>) => {
    onExemplarMatchingSettingsChange({ ...exemplarMatchingSettings, ...patch });
  };
  const samModelCard = (
    <Card>
      <VStack align="stretch" spacing={3}>
        <HStack spacing={1}>
          <Text fontWeight="bold" color={textColor}>{t("SAM click-to-box model")}</Text>
          <InfoPopover titleKey="info.prediction.samModel.title" bodyKey="info.prediction.samModel.body" />
        </HStack>

        <Select
          size="sm"
          value={config.sam_checkpoint || ""}
          onChange={(event) => {
            setSamInspectError("");
            void saveConfig({ ...config, sam_checkpoint: event.target.value || null });
          }}
        >
          <option value="">
            {defaultSamCheckpoint
              ? `${t("Automatic")} - ${getFileNameFromPath(defaultSamCheckpoint)}`
              : t("Automatic model selection")}
          </option>
          {displayedSamModels.map((model) => (
            <option key={model.path} value={model.path}>
              {`${model.name} · ${(model.family || "SAM").toUpperCase()}${model.size > 0 ? ` · ${(model.size / 1024 / 1024).toFixed(1)} MB` : ""}`}
            </option>
          ))}
        </Select>

        {activeSamModel && (
          <HStack spacing={2}>
            <Badge colorScheme={activeSamModel.family === "sam3" ? "orange" : activeSamModel.family === "sam2" ? "purple" : "blue"}>
              {(activeSamModel.family || "SAM").toUpperCase()}
            </Badge>
            <Text fontSize="xs" color={labelColor} noOfLines={1}>
              {config.sam_checkpoint ? config.sam_checkpoint : `${t("Default")}: ${defaultSamCheckpoint}`}
            </Text>
          </HStack>
        )}

        <HStack align="center">
          {samModelsDirPath && (
            <Text fontSize="xs" color={mutedColor} noOfLines={1} flex="1" title={samModelsDirPath}>
              {t("SAM models directory")}: {samModelsDirPath}
            </Text>
          )}
          <Button
            size="xs"
            variant="outline"
            leftIcon={<MdFolderOpen />}
            onClick={handleBrowseSamModelsFolder}
            isDisabled={!isTauriRuntime()}
            flexShrink={0}
          >
            {t("Change folder")}
          </Button>
          {config.sam_models_dir && (
            <Button size="xs" variant="ghost" colorScheme="red" onClick={handleClearSamFolder} flexShrink={0}>
              {t("Clear path")}
            </Button>
          )}
        </HStack>
        {unsupportedSamCount > 0 && (
          <Text fontSize="xs" color={mutedColor}>
            {t("Unsupported SAM files hidden")}: {unsupportedSamCount}
          </Text>
        )}
        {!samRuntimeAvailable && (
          <Alert status="warning" borderRadius="md" py={2}>
            <AlertIcon />
            <Text fontSize="xs">{t("SAM runtime is not available in this build")}</Text>
          </Alert>
        )}
        {samInspectError && (
          <Alert status="warning" borderRadius="md" py={2}>
            <AlertIcon />
            <Text fontSize="xs">{samInspectError}</Text>
          </Alert>
        )}
      </VStack>
    </Card>
  );

  const dinoModelCard = (
    <Card>
      <VStack align="stretch" spacing={3}>
        <Text fontWeight="bold" color={textColor}>{t("Find-similar engine")}</Text>

        <HStack spacing={2}>
          {(["template", "dino"] as const).map((eng) => (
            <Button
              key={eng}
              size="sm"
              flex="1"
              variant={config.exemplar_engine === eng ? "solid" : "outline"}
              colorScheme={config.exemplar_engine === eng ? "brand" : "gray"}
              onClick={() => void saveConfig({ ...config, exemplar_engine: eng })}
            >
              {eng === "template" ? t("Template (NCC)") : t("DINO (few-shot)")}
            </Button>
          ))}
        </HStack>
        <Text fontSize="xs" color={mutedColor}>
          {t("Template: classic, offline, fast. DINO: semantic few-shot (EO/SAR), GPU recommended.")}
        </Text>

        {config.exemplar_engine === "dino" && (
          <>
            <Select
              size="sm"
              value={config.dino_checkpoint || ""}
              onChange={(event) => { setDinoError(""); void saveConfig({ ...config, dino_checkpoint: event.target.value || null }); }}
            >
              <option value="">
                {defaultDinoCheckpoint
                  ? `${t("Automatic")} - ${getFileNameFromPath(defaultDinoCheckpoint)}`
                  : t("Automatic model selection")}
              </option>
              {dinoModels.map((model) => (
                <option key={model.path} value={model.path}>{`${model.label} · ${model.name}`}</option>
              ))}
            </Select>

            <HStack align="center">
              {dinoModelsDirPath && (
                <Text fontSize="xs" color={mutedColor} noOfLines={1} flex="1" title={dinoModelsDirPath}>
                  {t("DINO weights directory")}: {dinoModelsDirPath}
                </Text>
              )}
              <Button
                size="xs"
                variant="outline"
                leftIcon={<MdFolderOpen />}
                onClick={handleBrowseDinoModelsFolder}
                isDisabled={!isTauriRuntime()}
                flexShrink={0}
              >
                {t("Change folder")}
              </Button>
              {config.dino_models_dir && (
                <Button size="xs" variant="ghost" colorScheme="red" onClick={handleClearDinoFolder} flexShrink={0}>
                  {t("Clear path")}
                </Button>
              )}
            </HStack>

            <VStack align="stretch" spacing={1}>
              <HStack justify="space-between">
                <Text fontSize="sm" color={labelColor}>{t("Minimum similarity")}</Text>
                <Text fontSize="sm" fontWeight="bold" color={textColor}>
                  {exemplarMatchingSettings.dinoThreshold.toFixed(2)}
                </Text>
              </HStack>
              <Slider
                min={0.2}
                max={1.0}
                step={0.05}
                value={exemplarMatchingSettings.dinoThreshold}
                onChange={(value) => updateMatchingSettings({ dinoThreshold: value })}
              >
                <SliderTrack><SliderFilledTrack /></SliderTrack>
                <SliderThumb />
              </Slider>
              <Text fontSize="xs" color={mutedColor}>
                {t("Centered cosine (background ≈ 0). Start around 0.4–0.5; lower if too few results.")}
              </Text>
            </VStack>

            {!dinoRuntimeAvailable && (
              <Alert status="warning" borderRadius="md" py={2}>
                <AlertIcon />
                <Text fontSize="xs">{t("No DINO weights found - point to a folder with .pth files (e.g. dinov3_vitl16_…sat493m.pth).")}</Text>
              </Alert>
            )}
            {dinoError && (
              <Alert status="warning" borderRadius="md" py={2}>
                <AlertIcon />
                <Text fontSize="xs">{dinoError}</Text>
              </Alert>
            )}
          </>
        )}
      </VStack>
    </Card>
  );

  const templateMatchingCard = (
    <Card>
      <VStack align="stretch" spacing={3}>
        <HStack spacing={1}>
          <Text fontWeight="bold" color={textColor}>{t("Template matching settings")}</Text>
          <InfoPopover
            titleKey="info.prediction.templateMatching.title"
            bodyKey="info.prediction.templateMatching.body"
          />
        </HStack>
        <Text fontSize="xs" color={mutedColor}>
          {t("These settings control scale, rotation and similarity when searching for objects similar to the selected annotation.")}
        </Text>
        <HStack align="start" spacing={3}>
          <VStack align="stretch" spacing={1} flex="1">
            <Text fontSize="sm" color={labelColor}>{t("Scale tolerance")}</Text>
            <Select
              size="sm"
              value={exemplarMatchingSettings.scaleTolerance}
              onChange={(event) => updateMatchingSettings({
                scaleTolerance: Number(event.target.value) as api.ExemplarMatchingSettings["scaleTolerance"],
              })}
            >
              <option value={0}>{t("Disabled")}</option>
              <option value={0.1}>+/- 10%</option>
              <option value={0.2}>+/- 20%</option>
            </Select>
          </VStack>
          <VStack align="stretch" spacing={1} flex="1">
            <Text fontSize="sm" color={labelColor}>{t("Rotation tolerance")}</Text>
            <Select
              size="sm"
              value={exemplarMatchingSettings.rotationToleranceDeg}
              onChange={(event) => updateMatchingSettings({
                rotationToleranceDeg: Number(event.target.value) as api.ExemplarMatchingSettings["rotationToleranceDeg"],
              })}
            >
              <option value={0}>{t("Disabled")}</option>
              <option value={20}>+/- 20 deg</option>
              <option value={45}>+/- 45 deg</option>
            </Select>
          </VStack>
        </HStack>
        <VStack align="stretch" spacing={1}>
          <HStack justify="space-between">
            <Text fontSize="sm" color={labelColor}>{t("Minimum similarity")}</Text>
            <Text fontSize="sm" fontWeight="bold" color={textColor}>
              {exemplarMatchingSettings.threshold.toFixed(2)}
            </Text>
          </HStack>
          <Slider
            min={0.5}
            max={0.9}
            step={0.05}
            value={exemplarMatchingSettings.threshold}
            onChange={(value) => updateMatchingSettings({ threshold: value })}
          >
            <SliderTrack><SliderFilledTrack /></SliderTrack>
            <SliderThumb />
          </Slider>
        </VStack>
        <HStack justify="space-between">
          <Text fontSize="sm" color={textColor}>{t("Include edges")}</Text>
          <Switch
            size="sm"
            colorScheme="brand"
            isChecked={exemplarMatchingSettings.useEdges}
            onChange={(event) => updateMatchingSettings({ useEdges: event.target.checked })}
          />
        </HStack>
      </VStack>
    </Card>
  );

  if (!capabilities.yolo || !showYoloControls) {
    return (
      <VStack align="stretch" spacing={4}>
        {samModelCard}
        {dinoModelCard}
        {templateMatchingCard}
        <Card>
          <VStack align="stretch" spacing={3}>
            <Text fontWeight="bold" color={textColor}>
              {t("YOLO Prediction")}
            </Text>
            <Text fontSize="sm" color={mutedColor}>
              {t("YOLO prediction is not included in this desktop build.")}
            </Text>
          </VStack>
        </Card>
      </VStack>
    );
  }

  return (
    <VStack align="stretch" spacing={4}>
      {samModelCard}
      {dinoModelCard}
      {templateMatchingCard}
      <Card>
        <VStack align="stretch" spacing={3}>
          <HStack spacing={1}>
            <Text fontWeight="bold" color={textColor}>{t("YOLO Model")}</Text>
            <InfoPopover titleKey="info.prediction.model.title" bodyKey="info.prediction.model.body" />
          </HStack>

          <HStack align="start">
            <Select
              size="sm"
              placeholder={t("Select model...")}
              value={config.model_path}
              onChange={(e) => saveConfig({ ...config, model_path: e.target.value })}
            >
              {displayedModels.map((model) => (
                <option key={model.path} value={model.path}>
                  {model.size > 0
                    ? `${model.name} (${(model.size / 1024 / 1024).toFixed(1)} MB)`
                    : model.name}
                </option>
              ))}
            </Select>
            <Button
              size="sm"
              variant="outline"
              leftIcon={<MdFolderOpen />}
              onClick={handleBrowseModel}
              flexShrink={0}
            >
              {t("Browse")}
            </Button>
          </HStack>

          {models.length === 0 && (
            <Text fontSize="xs" color={mutedColor}>
              {t("No models found in default folder. Use Browse to pick a local `.pt` file.")}
            </Text>
          )}

          {modelsDirPath && (
            <Text fontSize="xs" color={mutedColor} noOfLines={1}>
              {t("Default models directory")}: {modelsDirPath}
            </Text>
          )}

          {config.model_path && (
            <Text fontSize="xs" color={labelColor} noOfLines={2}>
              {t("Selected")}: {config.model_path}
            </Text>
          )}

          {modelInspectError && (
            <Alert status="warning" borderRadius="md" py={2}>
              <AlertIcon />
              <Text fontSize="xs">{modelInspectError}</Text>
            </Alert>
          )}

          {modelClasses.length > 0 && (
            <Text fontSize="xs" color={mutedColor}>
              {t("Model classes")}: {modelClasses.map((cls) => cls.name).join(", ")}
            </Text>
          )}

          {missingModelClasses.length > 0 && (
            <Alert status="warning" borderRadius="md" py={2}>
              <AlertIcon />
              <Text fontSize="xs">
                {t("Missing project classes")}: {missingModelClasses.map((cls) => cls.name).join(", ")}.
                {` ${t("These predictions will stay pending until classes are added.")}`}
              </Text>
              <InfoPopover titleKey="info.prediction.classMapping.title" bodyKey="info.prediction.classMapping.body" />
            </Alert>
          )}

          <Divider />

          <VStack align="stretch" spacing={1}>
            <HStack justify="space-between">
              <HStack spacing={1}>
                <Text fontSize="sm" color={labelColor}>{t("Confidence")}</Text>
                <InfoPopover titleKey="info.prediction.confidence.title" bodyKey="info.prediction.confidence.body" />
              </HStack>
              <Text fontSize="sm" fontWeight="bold" color={textColor}>{config.conf.toFixed(2)}</Text>
            </HStack>
            <Slider
              min={0.01}
              max={1.0}
              step={0.01}
              value={config.conf}
              onChange={(value) => saveConfig({ ...config, conf: value })}
            >
              <SliderTrack><SliderFilledTrack /></SliderTrack>
              <SliderThumb />
            </Slider>
          </VStack>

          <VStack align="stretch" spacing={1}>
            <HStack justify="space-between">
              <HStack spacing={1}>
                <Text fontSize="sm" color={labelColor}>{t("IoU Threshold")}</Text>
                <InfoPopover titleKey="info.prediction.iou.title" bodyKey="info.prediction.iou.body" />
              </HStack>
              <Text fontSize="sm" fontWeight="bold" color={textColor}>{config.iou.toFixed(2)}</Text>
            </HStack>
            <Slider
              min={0.1}
              max={1.0}
              step={0.05}
              value={config.iou}
              onChange={(value) => saveConfig({ ...config, iou: value })}
            >
              <SliderTrack><SliderFilledTrack /></SliderTrack>
              <SliderThumb />
            </Slider>
          </VStack>

          <VStack align="stretch" spacing={1}>
            <HStack justify="space-between">
              <HStack spacing={1}>
                <Text fontSize="sm" color={labelColor}>{t("Tile Size")}</Text>
                <InfoPopover titleKey="info.prediction.tiling.title" bodyKey="info.prediction.tiling.body" />
              </HStack>
              <Text fontSize="sm" fontWeight="bold" color={textColor}>{config.tile_size}px</Text>
            </HStack>
            <Slider
              min={128}
              max={1024}
              step={64}
              value={config.tile_size}
              onChange={(value) => saveConfig({ ...config, tile_size: value })}
            >
              <SliderTrack><SliderFilledTrack /></SliderTrack>
              <SliderThumb />
            </Slider>
          </VStack>

          <VStack align="stretch" spacing={1}>
            <HStack justify="space-between">
              <Text fontSize="sm" color={labelColor}>{t("Buffer (Overlap)")}</Text>
              <Text fontSize="sm" fontWeight="bold" color={textColor}>{config.buffer}px</Text>
            </HStack>
            <Slider
              min={0}
              max={config.tile_size / 2}
              step={8}
              value={config.buffer}
              onChange={(value) => saveConfig({ ...config, buffer: value })}
            >
              <SliderTrack><SliderFilledTrack /></SliderTrack>
              <SliderThumb />
            </Slider>
          </VStack>

          <Divider />

          <HStack spacing={1}>
            <Text fontWeight="bold" color={textColor}>{t("Inference pipeline")}</Text>
            <InfoPopover titleKey="info.prediction.pipeline.title" bodyKey="info.prediction.pipeline.body" />
          </HStack>

          <HStack align="start" spacing={3}>
            <VStack align="stretch" spacing={1} flex="1">
              <Text fontSize="sm" color={labelColor}>{t("Batch size")}</Text>
              <Select
                size="sm"
                value={String(config.batch_size)}
                onChange={(event) => saveConfig({
                  ...config,
                  batch_size: event.target.value === "auto" ? "auto" : Number(event.target.value),
                })}
              >
                <option value="auto">{t("Auto")}</option>
                {[1, 2, 4, 8, 16, 32].map((value) => (
                  <option key={value} value={value}>{value}</option>
                ))}
              </Select>
            </VStack>
            <VStack align="stretch" spacing={1} flex="1">
              <Text fontSize="sm" color={labelColor}>{t("Prefetch batches")}</Text>
              <NumberInput
                size="sm"
                min={1}
                max={8}
                value={config.prefetch_batches}
                onChange={(_, value) => saveConfig({
                  ...config,
                  prefetch_batches: Number.isFinite(value) ? Math.max(1, Math.min(8, value)) : 2,
                })}
              >
                <NumberInputField />
                <NumberInputStepper>
                  <NumberIncrementStepper />
                  <NumberDecrementStepper />
                </NumberInputStepper>
              </NumberInput>
            </VStack>
          </HStack>

          <HStack align="start" spacing={3}>
            <VStack align="stretch" spacing={1} flex="1">
              <Text fontSize="sm" color={labelColor}>{t("Merge method")}</Text>
              <Select
                size="sm"
                value={config.merge_method}
                onChange={(event) => saveConfig({
                  ...config,
                  merge_method: event.target.value as PredictionConfig["merge_method"],
                })}
              >
                <option value="nms">NMS</option>
              </Select>
            </VStack>
            <VStack align="stretch" spacing={1} flex="1">
              <Text fontSize="sm" color={labelColor}>{t("Progress interval")}</Text>
              <Select
                size="sm"
                value={config.progress_interval_ms}
                onChange={(event) => saveConfig({
                  ...config,
                  progress_interval_ms: Number(event.target.value),
                })}
              >
                {[100, 250, 500, 1000].map((value) => (
                  <option key={value} value={value}>{value} ms</option>
                ))}
              </Select>
            </VStack>
          </HStack>

          <Divider />

          <HStack spacing={1}>
            <Text fontWeight="bold" color={textColor}>{t("Prediction preprocessing")}</Text>
            <InfoPopover titleKey="info.prediction.preprocessing.title" bodyKey="info.prediction.preprocessing.body" />
          </HStack>

          <VStack align="stretch" spacing={1}>
            <Text fontSize="sm" color={labelColor}>{t("Mode")}</Text>
            <Select
              size="sm"
              value={config.preprocess_mode}
              onChange={(e) => saveConfig({ ...config, preprocess_mode: e.target.value as PredictionConfig["preprocess_mode"] })}
            >
              <option value="auto">{t("Auto")}</option>
              <option value="linear">{t("Linear")}</option>
              <option value="log">{t("Log")}</option>
            </Select>
          </VStack>

          <HStack>
            <VStack align="stretch" spacing={1} flex="1">
              <Text fontSize="sm" color={labelColor}>{t("Stretch low")}</Text>
              <NumberInput
                size="sm"
                min={0}
                max={99}
                step={0.5}
                value={config.stretch_low}
                onChange={(_, value) => saveConfig({ ...config, stretch_low: Number.isFinite(value) ? value : config.stretch_low })}
              >
                <NumberInputField />
                <NumberInputStepper>
                  <NumberIncrementStepper />
                  <NumberDecrementStepper />
                </NumberInputStepper>
              </NumberInput>
            </VStack>
            <VStack align="stretch" spacing={1} flex="1">
              <Text fontSize="sm" color={labelColor}>{t("Stretch high")}</Text>
              <NumberInput
                size="sm"
                min={1}
                max={100}
                step={0.5}
                value={config.stretch_high}
                onChange={(_, value) => saveConfig({ ...config, stretch_high: Number.isFinite(value) ? value : config.stretch_high })}
              >
                <NumberInputField />
                <NumberInputStepper>
                  <NumberIncrementStepper />
                  <NumberDecrementStepper />
                </NumberInputStepper>
              </NumberInput>
            </VStack>
          </HStack>

          {[
            ["Brightness", "brightness", 0, 3, 0.05],
            ["Contrast", "contrast", 0, 3, 0.05],
            ["Gamma", "gamma", 0.1, 3, 0.05],
          ].map(([label, key, min, max, step]) => (
            <VStack key={key as string} align="stretch" spacing={1}>
              <HStack justify="space-between">
                <Text fontSize="sm" color={labelColor}>{t(label as string)}</Text>
                <Text fontSize="sm" fontWeight="bold" color={textColor}>
                  {(config[key as keyof PredictionConfig] as number).toFixed(2)}
                </Text>
              </HStack>
              <Slider
                min={min as number}
                max={max as number}
                step={step as number}
                value={config[key as keyof PredictionConfig] as number}
                onChange={(value) => saveConfig({ ...config, [key as string]: value } as PredictionConfig)}
              >
                <SliderTrack><SliderFilledTrack /></SliderTrack>
                <SliderThumb />
              </Slider>
            </VStack>
          ))}

          {progress && (
            <Text fontSize="xs" color={mutedColor}>
              {t("Tile")}: {progress.current_tile || `${progress.done}/${progress.total}`} &bull;
              {t("Detections")}: {progress.detections_so_far} &bull;
              {t("Device")}: {progress.device || capabilities.device || "cpu"} &bull;
              {t("Batch")}: {progress.batch_size || progress.resolved_batch_size || 1}
            </Text>
          )}

          <Button
            colorScheme="brand"
            onClick={handleRunPrediction}
            isLoading={isRunning}
            isDisabled={!config.model_path}
            loadingText={
              progress
                ? `${progress.done}/${progress.total} (${progress.detections_so_far} det.)`
                : t("Running...")
            }
          >
            {t("Run prediction")}
          </Button>
          {isRunning && (
            <Button colorScheme="red" variant="outline" onClick={handleCancelPrediction}>
              {t("Cancel")}
            </Button>
          )}
        </VStack>
      </Card>

      {showResults && predictions.length > 0 && (
        <Card>
          <VStack align="stretch" spacing={3}>
            <HStack spacing={1}>
              <Text fontWeight="bold" color={textColor}>{t("Results")}</Text>
              <InfoPopover titleKey="info.prediction.pendingResults.title" bodyKey="info.prediction.pendingResults.body" />
            </HStack>

            <HStack spacing={2} flexWrap="wrap">
              <Badge colorScheme="orange">{pendingCount} {t("pending")}</Badge>
              <Badge colorScheme="green">{acceptedCount} {t("accepted")}</Badge>
            </HStack>

            {pendingCount > 0 && (
              <Button size="xs" colorScheme="green" onClick={onAcceptAll}>
                {t("Accept all")}
              </Button>
            )}

            <Button size="xs" variant="outline" onClick={onClearAll}>
              {t("Clear predictions")}
            </Button>

            <Divider />

            <Box maxH="250px" overflowY="auto">
              <VStack align="stretch" spacing={1}>
                {pendingPreds.map((pred) => (
                  <HStack
                    key={pred.id}
                    justify="space-between"
                    p={1}
                    borderRadius="md"
                    _hover={{ bg: hoverBg }}
                  >
                    <VStack align="start" spacing={0}>
                      <Text fontSize="xs" color={textColor} fontWeight="bold">
                        {pred.class_name}
                        {pred.needs_front_direction && (
                          <Text as="span" ml={1} fontSize="xx-small" color="orange.400">
                            {t("OBB")}
                          </Text>
                        )}
                      </Text>
                      <Text fontSize="xx-small" color={mutedColor}>
                        conf: {pred.confidence.toFixed(3)}
                      </Text>
                    </VStack>
                    <HStack spacing={1}>
                      {pred.needs_front_direction && onFlipFront && (
                        <Button
                          size="xs"
                          variant="ghost"
                          title={t("Rotate front direction by 90 degrees")}
                          onClick={() => onFlipFront(pred.id)}
                        >
                          ⟲
                        </Button>
                      )}
                      <Button
                        size="xs"
                        colorScheme="green"
                        variant="ghost"
                        onClick={() => onAccept([pred.id])}
                      >
                        +
                      </Button>
                      <Button
                        size="xs"
                        colorScheme="red"
                        variant="ghost"
                        aria-label={t("Delete")}
                        onClick={() => onDelete([pred.id])}
                      >
                        <MdDelete />
                      </Button>
                    </HStack>
                  </HStack>
                ))}
              </VStack>
            </Box>
          </VStack>
        </Card>
      )}

      <Modal isOpen={isBrowseOpen} onClose={onBrowseClose} size="lg" scrollBehavior="inside">
        <ModalOverlay />
        <ModalContent>
          <ModalHeader>{t("Select YOLO model")} (.pt)</ModalHeader>
          <ModalCloseButton />
          <ModalBody pb={4}>
            <HStack mb={3} spacing={1} flexWrap="wrap">
              <Button size="xs" variant="ghost" onClick={() => loadBrowsePath("")} leftIcon={<MdHome />}>
                /
              </Button>
              {browseData?.breadcrumbs.map((crumb) => {
                return (
                  <HStack key={crumb.path} spacing={1}>
                    <Text color={mutedColor}>/</Text>
                    <Button size="xs" variant="ghost" onClick={() => loadBrowsePath(crumb.path)}>
                      {crumb.label}
                    </Button>
                  </HStack>
                );
              })}
            </HStack>

            {browseLoading ? (
              <Box py={8} textAlign="center">
                <Spinner size="lg" color="brand.400" />
              </Box>
            ) : (
              <VStack align="stretch" spacing={0} maxH="420px" overflowY="auto">
                {browsePath && (
                  <HStack
                    px={3}
                    py={2}
                    cursor="pointer"
                    _hover={{ bg: hoverBg }}
                    borderRadius="md"
                    onClick={goUpInBrowser}
                  >
                    <MdArrowBack />
                    <Text fontSize="sm" color={textColor}>..</Text>
                  </HStack>
                )}

                {browseData?.dirs.map((dir) => (
                  <HStack
                    key={dir.path}
                    px={3}
                    py={2}
                    cursor="pointer"
                    _hover={{ bg: hoverBg }}
                    borderRadius="md"
                    onClick={() => enterDirInBrowser(dir.path)}
                  >
                    <MdFolder color="#F6AD55" />
                    <Text fontSize="sm" color={textColor} noOfLines={1}>
                      {dir.name}
                    </Text>
                  </HStack>
                ))}

                {browseData?.files.map((file) => (
                  <HStack
                    key={file.path}
                    px={3}
                    py={2}
                    cursor="pointer"
                    _hover={{ bg: hoverBg }}
                    borderRadius="md"
                    onClick={() => selectModelFile(file.path)}
                  >
                    <MdInsertDriveFile color="#68D391" />
                    <VStack align="start" spacing={0} flex="1" minW={0}>
                      <Text fontSize="sm" color={textColor} noOfLines={1}>
                        {file.name}
                      </Text>
                      <Text fontSize="xs" color={mutedColor} noOfLines={1}>
                        {file.path}
                      </Text>
                    </VStack>
                    <Text fontSize="xs" color={mutedColor}>
                      {formatSize(file.size)}
                    </Text>
                  </HStack>
                ))}

                {browseData && browseData.dirs.length === 0 && browseData.files.length === 0 && (
                  <Box py={8} textAlign="center">
                <Text fontSize="sm" color={mutedColor}>{t("No folders or `.pt` files in this location.")}</Text>
                  </Box>
                )}
              </VStack>
            )}
          </ModalBody>
        </ModalContent>
      </Modal>
    </VStack>
  );
}
