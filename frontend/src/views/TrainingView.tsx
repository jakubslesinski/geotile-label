import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Alert,
  AlertIcon,
  Badge,
  Box,
  Button,
  ButtonGroup,
  Code,
  Divider,
  FormControl,
  FormHelperText,
  FormLabel,
  HStack,
  Input,
  Progress,
  Select,
  SimpleGrid,
  Stat,
  StatLabel,
  StatNumber,
  Text,
  Textarea,
  VStack,
  useColorModeValue,
  useToast,
} from "@chakra-ui/react";
import { MdContentCopy, MdPlayArrow, MdStop } from "react-icons/md";
import Card from "../components/common/Card";
import InfoPopover from "../components/common/InfoPopover";
import * as api from "../api/client";
import type { DatasetRunSummary } from "../types";
import { useTranslation } from "react-i18next";

const POLL_MS = 2000;

type FormConfig = Pick<api.TrainingConfig, "epochs" | "imgsz" | "batch" | "seed" | "patience" | "device">;

const DEFAULT_CONFIG: FormConfig = {
  epochs: 100,
  imgsz: 640,
  batch: -1,
  seed: 0,
  patience: 25,
  device: "auto",
};

const BASIC_KEYS = new Set(Object.keys(DEFAULT_CONFIG));
const LOCKED_YAML_KEYS = new Set(["data", "project", "name", "model", "mode", "task", "resume", "pretrained", "exist_ok"]);
const ADVANCED_YAML_KEYS = new Set([
  "optimizer", "lr0", "lrf", "momentum", "weight_decay", "warmup_epochs",
  "warmup_momentum", "warmup_bias_lr", "box", "cls", "dfl", "cos_lr",
  "close_mosaic", "amp", "workers", "cache", "deterministic", "save_period",
  "plots", "rect", "multi_scale", "single_cls", "fraction", "freeze", "dropout",
  "hsv_h", "hsv_s", "hsv_v", "degrees", "translate", "scale", "shear",
  "perspective", "flipud", "fliplr", "bgr", "mosaic", "mixup", "cutmix",
  "copy_paste", "copy_paste_mode", "auto_augment", "erasing", "crop_fraction",
]);

function yamlValue(value: api.TrainingOptionValue): string {
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(yamlValue).join(", ")}]`;
  return String(value);
}

function serializeYaml(config: FormConfig, advanced: Record<string, api.TrainingOptionValue>): string {
  const lines = [
    `epochs: ${config.epochs}`,
    `imgsz: ${config.imgsz}`,
    `batch: ${config.batch}`,
    `seed: ${config.seed}`,
    `patience: ${config.patience}`,
    `device: ${config.device}`,
  ];
  for (const key of Object.keys(advanced).sort()) lines.push(`${key}: ${yamlValue(advanced[key])}`);
  return `${lines.join("\n")}\n`;
}

function parseScalar(raw: string): api.TrainingOptionValue {
  const value = raw.trim();
  if (!value) return "";
  if (value.startsWith("[") && value.endsWith("]")) {
    const body = value.slice(1, -1).trim();
    return body ? body.split(",").map((item) => parseScalar(item.trim()) as string | number | boolean) : [];
  }
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    return value.slice(1, -1);
  }
  if (value === "true") return true;
  if (value === "false") return false;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : value;
}

function parseYaml(text: string, current: FormConfig) {
  const values: Record<string, api.TrainingOptionValue> = {};
  for (const [index, line] of text.split("\n").entries()) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const separator = trimmed.indexOf(":");
    if (separator < 1) throw new Error(`Line ${index + 1} must contain 'key: value'`);
    const key = trimmed.slice(0, separator).trim();
    if (LOCKED_YAML_KEYS.has(key)) throw new Error(`Application-managed key: ${key}`);
    if (!BASIC_KEYS.has(key) && !ADVANCED_YAML_KEYS.has(key)) throw new Error(`Unsupported key: ${key}`);
    values[key] = parseScalar(trimmed.slice(separator + 1));
  }

  const numberValue = (key: keyof FormConfig, fallback: number): number => {
    const value = values[key] ?? fallback;
    if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`'${key}' must be a number`);
    return value;
  };
  const device = (values.device ?? current.device) as string;
  if (!["auto", "cpu", "cuda"].includes(device)) throw new Error("'device' must be auto, cpu or cuda");

  const config: FormConfig = {
    epochs: numberValue("epochs", current.epochs),
    imgsz: numberValue("imgsz", current.imgsz),
    batch: numberValue("batch", current.batch),
    seed: numberValue("seed", current.seed),
    patience: numberValue("patience", current.patience),
    device: device as FormConfig["device"],
  };
  const advanced = Object.fromEntries(
    Object.entries(values).filter(([key]) => ADVANCED_YAML_KEYS.has(key))
  );
  return { config, advanced };
}

function optionForCli(value: api.TrainingOptionValue): string {
  return Array.isArray(value) ? value.join(",") : String(value);
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 GB";
  return `${(value / (1024 ** 3)).toFixed(1)} GB`;
}

export default function TrainingView() {
  const { id } = useParams<{ id: string }>();
  const { t } = useTranslation();
  const toast = useToast();
  const mutedColor = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const codeBg = useColorModeValue("gray.50", "whiteAlpha.100");
  const stickyBg = useColorModeValue("rgba(255,255,255,0.96)", "rgba(32,32,35,0.96)");

  const [runs, setRuns] = useState<DatasetRunSummary[]>([]);
  const [datasetRunId, setDatasetRunId] = useState("");
  const [catalog, setCatalog] = useState<api.BaseModelCatalog | null>(null);
  const [baseModel, setBaseModel] = useState("");
  const [config, setConfig] = useState<FormConfig>(DEFAULT_CONFIG);
  const [advancedOptions, setAdvancedOptions] = useState<Record<string, api.TrainingOptionValue>>({});
  const [configurationMode, setConfigurationMode] = useState<"basic" | "advanced">("basic");
  const [yamlText, setYamlText] = useState(() => serializeYaml(DEFAULT_CONFIG, {}));
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [preflight, setPreflight] = useState<api.TrainingPreflight | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<api.TrainingRunSummary | null>(null);
  const [capabilities, setCapabilities] = useState<api.BackendCapabilities | null>(null);

  const trainingBlocked = capabilities?.training && !capabilities.training.available;
  const selectedDataset = runs.find((run) => run.run_id === datasetRunId);
  const selectedModel = catalog?.models.find((model) => model.file === baseModel);

  const fullConfig = useMemo<api.TrainingConfig | null>(() => (
    datasetRunId && baseModel
      ? { ...config, dataset_run_id: datasetRunId, base_model: baseModel, advanced_options: advancedOptions }
      : null
  ), [advancedOptions, baseModel, config, datasetRunId]);

  const cliCommand = useMemo(() => {
    if (!fullConfig) return "";
    const options = Object.entries(advancedOptions).map(([key, value]) => `${key}=${optionForCli(value)}`);
    return [
      "yolo train",
      `model=${fullConfig.base_model}`,
      "data=<dataset>/data.yaml",
      `epochs=${config.epochs}`,
      `imgsz=${config.imgsz}`,
      `batch=${config.batch}`,
      `seed=${config.seed}`,
      `patience=${config.patience}`,
      `device=${config.device}`,
      ...options,
    ].join(" ");
  }, [advancedOptions, config, fullConfig]);

  useEffect(() => { setPreflight(null); }, [fullConfig]);

  const updateConfig = <K extends keyof FormConfig>(key: K, value: FormConfig[K]) => {
    const next = { ...config, [key]: value };
    setConfig(next);
    setYamlText(serializeYaml(next, advancedOptions));
  };

  const updateAdvancedOption = (key: string, value: api.TrainingOptionValue | undefined) => {
    const next = { ...advancedOptions };
    if (value === undefined) delete next[key];
    else next[key] = value;
    setAdvancedOptions(next);
    setYamlText(serializeYaml(config, next));
  };

  const applyResourceRecommendation = () => {
    const recommendation = preflight?.resource_recommendation;
    if (!recommendation) return;
    const recommended = recommendation.recommended_options;
    const nextConfig = { ...config, batch: recommended.batch };
    const nextAdvanced = {
      ...advancedOptions,
      workers: recommended.workers,
      cache: recommended.cache,
    };
    setConfig(nextConfig);
    setAdvancedOptions(nextAdvanced);
    setYamlText(serializeYaml(nextConfig, nextAdvanced));
    setPreflight(null);
    toast({
      title: t("Training recommendation applied"),
      description: t("Run preflight again to validate the applied configuration."),
      status: "success",
    });
  };

  const applyYaml = (text: string) => {
    setYamlText(text);
    try {
      const parsed = parseYaml(text, config);
      setConfig(parsed.config);
      setAdvancedOptions(parsed.advanced);
      setYamlError(null);
    } catch (error) {
      setYamlError(error instanceof Error ? error.message : String(error));
    }
  };

  const loadStatic = useCallback(async () => {
    if (!id) return;
    try {
      const [index, models, caps] = await Promise.all([
        api.getDatasetRuns(id),
        api.getBaseModels(id),
        api.getCapabilities().catch(() => null),
      ]);
      const published = index.runs.filter(
        (run) => run.status === "complete" && run.publication_status === "published"
      );
      setRuns(published);
      setCatalog(models);
      setCapabilities(caps);
      setDatasetRunId((current) => published.some((run) => run.run_id === current) ? current : published[0]?.run_id || "");
      setBaseModel((current) => {
        if (models.models.some((model) => model.file === current && model.available)) return current;
        return models.models.find((model) => model.recommended)?.file
          || models.models.find((model) => model.available && model.pretrained)?.file
          || models.models.find((model) => model.available)?.file
          || "";
      });
    } catch (error: any) {
      toast({ title: t("Could not load training data"), description: error?.message, status: "error" });
    }
  }, [id, t, toast]);

  useEffect(() => { loadStatic(); }, [loadStatic]);

  const refreshActiveRun = useCallback(async () => {
    if (!id) return;
    try {
      const listing = await api.getTrainingRuns(id);
      setActiveRun(listing.runs.find((run) => run.status === "running" || run.status === "queued") || null);
    } catch {
      setActiveRun(null);
    }
  }, [id]);

  useEffect(() => { refreshActiveRun(); }, [refreshActiveRun]);
  useEffect(() => {
    if (!activeRun) return;
    const timer = setInterval(refreshActiveRun, POLL_MS);
    return () => clearInterval(timer);
  }, [activeRun, refreshActiveRun]);

  const runPreflight = async () => {
    if (!id || !fullConfig || yamlError) return;
    setBusy("preflight");
    try {
      setPreflight(await api.postTrainingPreflight(id, fullConfig));
    } catch (error: any) {
      setPreflight(null);
      toast({
        title: t("Preflight failed"),
        description: error.response?.data?.detail || error.message,
        status: "error",
        duration: 9000,
      });
    } finally {
      setBusy(null);
    }
  };

  const startTraining = async () => {
    if (!id || !fullConfig || yamlError) return;
    setBusy("start");
    try {
      const started = await api.startTrainingRun(id, fullConfig);
      toast({
        title: t("Training started"),
        description: t("trainingStartedDetail", { attempt: started.attempt }),
        status: "success",
      });
      await refreshActiveRun();
    } catch (error: any) {
      toast({
        title: t("Could not start training"),
        description: error.response?.data?.detail || error.message,
        status: "error",
        duration: 12000,
        isClosable: true,
      });
    } finally {
      setBusy(null);
    }
  };

  const cancelTraining = async () => {
    if (!id || !activeRun) return;
    setBusy("cancel");
    try {
      await api.cancelTrainingRun(id, activeRun.training_run_id);
      await refreshActiveRun();
      toast({ title: t("Training cancelled"), status: "info" });
    } catch (error: any) {
      toast({ title: t("Could not cancel training"), description: error?.message, status: "error" });
    } finally {
      setBusy(null);
    }
  };

  const copyCli = async () => {
    await navigator.clipboard.writeText(cliCommand);
    toast({ title: t("CLI command copied"), status: "success", duration: 1800 });
  };

  const modelFamilies = useMemo(() => {
    const groups = new Map<string, api.BaseModelInfo[]>();
    for (const model of catalog?.models ?? []) {
      const family = /^(YOLOv?\d+)/.exec(model.name)?.[1] ?? model.name;
      groups.set(family, [...(groups.get(family) || []), model]);
    }
    return [...groups.entries()];
  }, [catalog]);

  const progress = activeRun?.epochs_total
    ? Math.round((activeRun.epochs_done / activeRun.epochs_total) * 100)
    : 0;

  return (
    <VStack align="stretch" spacing={5} p={2} pb={24}>
      {trainingBlocked && (
        <Alert status="warning" borderRadius="md"><AlertIcon />{capabilities?.training?.reason}</Alert>
      )}
      {capabilities?.training?.cpu_fallback && !trainingBlocked && (
        <Alert status="info" borderRadius="md"><AlertIcon />{capabilities.training.reason}</Alert>
      )}

      {activeRun && (
        <Card>
          <VStack align="stretch" spacing={2}>
            <HStack justify="space-between" flexWrap="wrap">
              <HStack>
                <Badge colorScheme="purple">{t(`trainingStatus.${activeRun.status}`)}</Badge>
                <Text fontWeight="bold">{activeRun.base_model}</Text>
                <Text fontSize="sm" color={mutedColor}>
                  {t("attemptNumber", { count: activeRun.attempt || 1 })} · {activeRun.device}
                </Text>
              </HStack>
              <Button size="sm" colorScheme="red" variant="outline" leftIcon={<MdStop />} isLoading={busy === "cancel"} onClick={cancelTraining}>
                {t("Cancel training")}
              </Button>
            </HStack>
            <Progress value={progress} size="sm" colorScheme="brand" borderRadius="full" hasStripe isAnimated />
            <Text fontSize="sm" color={mutedColor}>{t("epochProgress", { done: activeRun.epochs_done, total: activeRun.epochs_total || "?" })}</Text>
          </VStack>
        </Card>
      )}

      <Card>
        <VStack align="stretch" spacing={4}>
          <Text fontSize="lg" fontWeight="bold">1 · {t("Dataset")}</Text>
          <FormControl>
            <FormLabel>{t("Published dataset version")}</FormLabel>
            <Select value={datasetRunId} onChange={(event) => setDatasetRunId(event.target.value)}>
              {runs.length === 0 && <option value="">{t("No published dataset runs")}</option>}
              {runs.map((run) => (
                <option key={run.run_id} value={run.run_id}>
                  {run.publication_label || run.run_id} · {run.total_tiles} {t("Tiles").toLowerCase()}
                </option>
              ))}
            </Select>
            <FormHelperText>{t("Only published dataset versions can start a training run.")}</FormHelperText>
          </FormControl>
          {selectedDataset && (
            <SimpleGrid columns={{ base: 2, md: 5 }} spacing={3}>
              <Stat><StatLabel>{t("Tiles")}</StatLabel><StatNumber fontSize="xl">{selectedDataset.total_tiles}</StatNumber></Stat>
              <Stat><StatLabel>{t("Positive")}</StatLabel><StatNumber fontSize="xl">{selectedDataset.positive_tiles}</StatNumber></Stat>
              <Stat><StatLabel>{t("Negative")}</StatLabel><StatNumber fontSize="xl">{selectedDataset.negative_tiles}</StatNumber></Stat>
              <Stat><StatLabel>{t("Annotations")}</StatLabel><StatNumber fontSize="xl">{selectedDataset.total_annotations}</StatNumber></Stat>
              <Stat><StatLabel>{t("Audit score")}</StatLabel><StatNumber fontSize="xl">{selectedDataset.audit_quality_score ?? "-"}</StatNumber></Stat>
            </SimpleGrid>
          )}
        </VStack>
      </Card>

      <Card>
        <VStack align="stretch" spacing={4}>
          <Text fontSize="lg" fontWeight="bold">2 · {t("Model")}</Text>
          <FormControl>
            <FormLabel>
              <HStack spacing={1}>
                <Text>{t("Base model")}</Text>
                <InfoPopover titleKey="info.training.baseModel.title" bodyKey="info.training.baseModel.body" helpPage="training/uruchom-trening.html" />
              </HStack>
            </FormLabel>
            <Select value={baseModel} onChange={(event) => setBaseModel(event.target.value)}>
              {!baseModel && <option value="">{t("unavailable")}</option>}
              {modelFamilies.map(([family, models]) => (
                <optgroup key={family} label={family}>
                  {models.map((model) => (
                    <option key={model.file} value={model.file} disabled={!model.available}>
                      {model.name} ({model.task}, {model.params_m}M){!model.available ? ` - ${t("unavailable")}` : ""}
                    </option>
                  ))}
                </optgroup>
              ))}
            </Select>
          </FormControl>
          {selectedModel && (
            <HStack spacing={2} flexWrap="wrap">
              {selectedModel.recommended && <Badge colorScheme="purple">{t("recommended")}</Badge>}
              <Badge colorScheme={selectedModel.pretrained ? "green" : "orange"}>{t(selectedModel.pretrained ? "pretrained" : "from scratch")}</Badge>
              <Badge>{selectedModel.task.toUpperCase()}</Badge>
              <Badge>{selectedModel.params_m}M</Badge>
              {selectedModel.size && <Badge>{(selectedModel.size / (1024 ** 2)).toFixed(0)} MB</Badge>}
              <Badge colorScheme="blue">~{selectedModel.estimated_vram_gb} GB VRAM</Badge>
            </HStack>
          )}
          {selectedModel && !selectedModel.pretrained && <Alert status="warning" borderRadius="md"><AlertIcon />{t("No pretrained weights - the network starts from random initialisation. Expect markedly weaker results than a pretrained model unless the dataset is large and training long.")}</Alert>}
          <Divider />
          <HStack spacing={2} flexWrap="wrap">
            <Badge colorScheme={capabilities?.training?.device === "cuda" ? "green" : "gray"}>
              {capabilities?.training?.device === "cuda" ? "CUDA" : "CPU"}
            </Badge>
            {capabilities?.training?.gpu_name && <Text fontSize="sm">{capabilities.training.gpu_name}</Text>}
            {capabilities?.training?.vram_total_gb != null && (
              <Text fontSize="sm" color={mutedColor}>
                {capabilities.training.vram_free_gb} / {capabilities.training.vram_total_gb} GB VRAM {t("free")}
              </Text>
            )}
          </HStack>
        </VStack>
      </Card>

      <Card>
        <VStack align="stretch" spacing={4}>
          <HStack justify="space-between" flexWrap="wrap">
            <HStack spacing={1}>
              <Text fontSize="lg" fontWeight="bold">3 · {t("Parameters")}</Text>
              <InfoPopover titleKey="info.training.parameters.title" bodyKey="info.training.parameters.body" helpPage="training/uruchom-trening.html" />
            </HStack>
            <ButtonGroup size="sm" isAttached>
              <Button variant={configurationMode === "basic" ? "solid" : "outline"} colorScheme={configurationMode === "basic" ? "brand" : undefined} onClick={() => setConfigurationMode("basic")}>{t("Basic")}</Button>
              <Button variant={configurationMode === "advanced" ? "solid" : "outline"} colorScheme={configurationMode === "advanced" ? "brand" : undefined} onClick={() => { setConfigurationMode("advanced"); setYamlText(serializeYaml(config, advancedOptions)); }}>{t("Advanced")}</Button>
            </ButtonGroup>
          </HStack>

          {configurationMode === "basic" ? (
            <SimpleGrid columns={{ base: 1, md: 3, xl: 4 }} spacing={4}>
              <FormControl><FormLabel>{t("Epochs")}</FormLabel><Input type="number" value={config.epochs} onChange={(event) => updateConfig("epochs", Number(event.target.value))} /></FormControl>
              <FormControl><FormLabel>{t("Image size")}</FormLabel><Input type="number" value={config.imgsz} onChange={(event) => updateConfig("imgsz", Number(event.target.value))} /></FormControl>
              <FormControl><FormLabel>{t("Batch")}</FormLabel><Input type="number" value={config.batch} onChange={(event) => updateConfig("batch", Number(event.target.value))} /></FormControl>
              <FormControl>
                <FormLabel>{t("DataLoader workers")}</FormLabel>
                <Input
                  type="number"
                  min={0}
                  max={64}
                  value={typeof advancedOptions.workers === "number" ? advancedOptions.workers : ""}
                  placeholder={t("Ultralytics default")}
                  onChange={(event) => updateAdvancedOption("workers", event.target.value === "" ? undefined : Number(event.target.value))}
                />
              </FormControl>
              <FormControl>
                <FormLabel>{t("Image cache")}</FormLabel>
                <Select
                  value={advancedOptions.cache === undefined ? "auto" : advancedOptions.cache === true ? "ram" : String(advancedOptions.cache)}
                  onChange={(event) => {
                    const value = event.target.value;
                    updateAdvancedOption("cache", value === "auto" ? undefined : value === "false" ? false : value);
                  }}
                >
                  <option value="auto">{t("Ultralytics default")}</option>
                  <option value="false">{t("Disabled")}</option>
                  <option value="ram">RAM</option>
                  <option value="disk">{t("Disk")}</option>
                </Select>
              </FormControl>
              <FormControl><FormLabel>{t("Seed")}</FormLabel><Input type="number" value={config.seed} onChange={(event) => updateConfig("seed", Number(event.target.value))} /></FormControl>
              <FormControl><FormLabel>{t("Patience")}</FormLabel><Input type="number" value={config.patience} onChange={(event) => updateConfig("patience", Number(event.target.value))} /></FormControl>
              <FormControl><FormLabel>{t("Compute device")}</FormLabel><Select value={config.device} onChange={(event) => updateConfig("device", event.target.value as FormConfig["device"])}><option value="auto">auto</option><option value="cuda">cuda</option><option value="cpu">cpu</option></Select></FormControl>
            </SimpleGrid>
          ) : (
            <VStack align="stretch" spacing={3}>
              <Text fontSize="sm" color={mutedColor}>{t("Advanced YAML accepts supported Ultralytics training options. Unknown and application-managed keys are rejected.")}</Text>
              <Box p={3} bg={codeBg} borderRadius="md">
                <Text fontSize="xs" fontFamily="mono"># {t("managed by the application - read only")}</Text>
                <Text fontSize="xs" fontFamily="mono">data: &lt;{t("set automatically")}&gt;</Text>
                <Text fontSize="xs" fontFamily="mono">project: &lt;{t("set automatically")}&gt;</Text>
                <Text fontSize="xs" fontFamily="mono">name: &lt;{t("set automatically")}&gt;</Text>
                <Text fontSize="xs" fontFamily="mono">model: &lt;{t("set automatically")}&gt;</Text>
              </Box>
              <Textarea fontFamily="mono" minH="280px" value={yamlText} onChange={(event) => applyYaml(event.target.value)} isInvalid={Boolean(yamlError)} />
              {yamlError && <Text color="red.400" fontSize="sm">{yamlError}</Text>}
              <HStack justify="space-between" align="start" flexWrap="wrap">
                <Box flex="1" minW="280px"><HStack spacing={1}><Text fontSize="sm" fontWeight="600">{t("Equivalent CLI command")}</Text><InfoPopover titleKey="info.training.cliCommand.title" bodyKey="info.training.cliCommand.body" helpPage="training/uruchom-trening.html" /></HStack><Code display="block" p={2} mt={1} whiteSpace="normal">{cliCommand || "-"}</Code></Box>
                <Button leftIcon={<MdContentCopy />} variant="outline" onClick={copyCli} isDisabled={!cliCommand}>{t("Copy CLI command")}</Button>
              </HStack>
            </VStack>
          )}
        </VStack>
      </Card>

      <Card>
        <VStack align="stretch" spacing={3}>
          <HStack justify="space-between"><HStack spacing={1}><Text fontSize="lg" fontWeight="bold">4 · {t("Preflight")}</Text><InfoPopover titleKey="info.training.preflight.title" bodyKey="info.training.preflight.body" helpPage="training/preflight.html" /></HStack>{preflight && <Badge colorScheme={preflight.can_start ? "green" : "red"}>{t(preflight.can_start ? "preflight.ready" : "preflight.blocked")}</Badge>}</HStack>
          {!preflight && <Text color={mutedColor}>{t("Run preflight to verify the dataset, labels, device, VRAM and free disk space.")}</Text>}
          {preflight?.checks.map((check) => (
            <Alert key={`${check.name}-${check.detail}`} status={check.status === "ok" ? "success" : check.status} borderRadius="md" py={2}>
              <AlertIcon /><Box><Text fontWeight="600" fontSize="sm">{check.name}</Text><Text fontSize="sm">{check.detail}</Text></Box>
            </Alert>
          ))}
          {preflight?.resource_recommendation && (() => {
            const recommendation = preflight.resource_recommendation;
            const options = recommendation.recommended_options;
            const loader = recommendation.loader_benchmark;
            const memory = recommendation.memory_safety;
            return (
              <Box borderWidth="1px" borderRadius="lg" p={4}>
                <HStack justify="space-between" align="start" flexWrap="wrap" spacing={3}>
                  <Box>
                    <HStack spacing={2} flexWrap="wrap">
                      <Text fontWeight="700">{t("Adaptive training recommendation")}</Text>
                      <Badge colorScheme={recommendation.bottleneck.kind === "io" ? "orange" : recommendation.bottleneck.kind === "cpu_decode" ? "blue" : "purple"}>
                        {t(`trainingBottleneck.${recommendation.bottleneck.kind}`)}
                      </Badge>
                      <Badge>{t(`trainingConfidence.${recommendation.bottleneck.confidence}`)}</Badge>
                    </HStack>
                    <Text mt={1} fontSize="sm" color={mutedColor}>
                      {t("The recommendation is advisory and is applied only after confirmation.")}
                    </Text>
                  </Box>
                  {!recommendation.is_recommended_applied && (
                    <Button colorScheme="brand" size="sm" onClick={applyResourceRecommendation}>
                      {t("Apply recommendation")}
                    </Button>
                  )}
                </HStack>
                <SimpleGrid columns={{ base: 2, md: 3, xl: 6 }} spacing={4} mt={4}>
                  <Stat><StatLabel>{t("Batch")}</StatLabel><StatNumber fontSize="lg">{options.batch === -1 ? "auto (-1)" : options.batch}</StatNumber></Stat>
                  <Stat><StatLabel>{t("DataLoader workers")}</StatLabel><StatNumber fontSize="lg">{options.workers}</StatNumber></Stat>
                  <Stat><StatLabel>{t("Image cache")}</StatLabel><StatNumber fontSize="lg">{options.cache === false ? t("Disabled") : options.cache.toUpperCase()}</StatNumber></Stat>
                  <Stat><StatLabel>{t("Loader throughput")}</StatLabel><StatNumber fontSize="lg">{loader.images_per_second?.toFixed(1) ?? "-"} img/s</StatNumber></Stat>
                  <Stat><StatLabel>{t("Read throughput")}</StatLabel><StatNumber fontSize="lg">{loader.read_mib_per_second?.toFixed(1) ?? "-"} MiB/s</StatNumber></Stat>
                  <Stat><StatLabel>{t("Loader sample")}</StatLabel><StatNumber fontSize="lg">{loader.decoded_count}/{loader.sample_count}</StatNumber></Stat>
                </SimpleGrid>
                <Divider my={3} />
                <Text fontSize="sm" color={memory.ram_cache_safe ? "green.400" : mutedColor}>
                  {memory.ram_cache_safe
                    ? t("RAM cache safety margin is available: estimated {{cache}}, remaining {{remaining}}.", { cache: formatBytes(memory.effective_ram_cache_bytes), remaining: formatBytes(memory.remaining_after_cache_bytes) })
                    : t("RAM cache is not recommended: estimated {{cache}} would leave less than the required {{headroom}} headroom.", { cache: formatBytes(memory.effective_ram_cache_bytes), headroom: formatBytes(memory.required_headroom_bytes) })}
                </Text>
                {Object.keys(recommendation.overrides).length > 0 && (
                  <Text mt={2} fontSize="sm" color={mutedColor}>
                    {t("Current manual differences")}: {Object.keys(recommendation.overrides).join(", ")}
                  </Text>
                )}
              </Box>
            );
          })()}
          {preflight && <Text fontSize="sm" color={mutedColor}>{t("Estimated duration")}: {t(preflight.duration_hint)} · {t("attemptNumber", { count: preflight.next_attempt })}</Text>}
        </VStack>
      </Card>

      <Box position="sticky" bottom={2} zIndex={10} bg={stickyBg} borderWidth="1px" borderRadius="xl" boxShadow="lg" p={3} backdropFilter="blur(10px)">
        <HStack justify="flex-end" flexWrap="wrap">
          <Button variant="outline" isLoading={busy === "preflight"} isDisabled={!fullConfig || Boolean(yamlError) || Boolean(trainingBlocked) || Boolean(activeRun)} onClick={runPreflight}>{t("Run preflight")}</Button>
          <Button colorScheme="brand" leftIcon={<MdPlayArrow />} isLoading={busy === "start"} isDisabled={!fullConfig || Boolean(yamlError) || Boolean(trainingBlocked) || Boolean(activeRun) || !preflight?.can_start} onClick={startTraining}>{t("Start training")}</Button>
        </HStack>
      </Box>
    </VStack>
  );
}
