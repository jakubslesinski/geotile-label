import { useState, useEffect, useRef } from "react";
import {
  Box,
  SimpleGrid,
  Text,
  VStack,
  HStack,
  IconButton,
  Input,
  Button,
  Badge,
  FormControl,
  FormLabel,
  FormHelperText,
  Select,
  AlertDialog,
  AlertDialogBody,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogOverlay,
  useColorModeValue,
  useToast,
  useDisclosure,
} from "@chakra-ui/react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import ScenePreviewTree from "../components/common/ScenePreviewTree";
import { MdDelete, MdFolderOpen, MdUploadFile } from "react-icons/md";
import Card from "../components/common/Card";
import ImportProgressModal from "../components/common/ImportProgressModal";
import * as api from "../api/client";
import type { ImportJob, SceneImportMode, SceneImportPreview, SceneProvider, SceneSourceInput } from "../api/client";
import type {
  Project,
  ProjectGeoreferencing,
  ProjectModality,
  ProjectProfile,
  ProjectSourceType,
  SplitMode,
} from "../types";
import {
  isTauriRuntime,
  openBackupFileDialog,
  openClassesFileDialog,
  openProjectLocationDialog,
  openProjectFolderDialog,
  openSceneFolderDialog,
} from "../desktop/dialogs";

const AUTHOR_EMAIL_STORAGE_KEY = "geotile.labelingAuthorEmail";
const PROVIDER_OPTIONS: Array<{ id: SceneProvider; label: string; modality?: ProjectModality }> = [
  { id: "generic", label: "Generic" },
  { id: "iceye", label: "ICEYE", modality: "SAR" },
  { id: "capella", label: "Capella", modality: "SAR" },
  { id: "umbra", label: "Umbra", modality: "SAR" },
  { id: "pleiades_neo", label: "Pleiades Neo", modality: "EO" },
  { id: "worldview", label: "WorldView", modality: "EO" },
  { id: "blacksky", label: "BlackSky", modality: "EO" },
];
const SENSOR_BY_PROVIDER: Partial<Record<SceneProvider, string>> = {
  iceye: "ICEYE",
  capella: "Capella",
  umbra: "UMBRA",
  pleiades_neo: "Pleiades Neo",
  worldview: "WorldView",
  blacksky: "BlackSky",
};

function sensorsForSceneSources(sources: SceneSourceInput[]): string[] {
  return Array.from(new Set(
    sources
      .map((source) => SENSOR_BY_PROVIDER[source.provider])
      .filter((sensor): sensor is string => Boolean(sensor)),
  ));
}

function scenePackageDecisionKey(item: api.SceneImportPackage): string {
  return item.decision_uid || item.package_id;
}

function getSavedAuthorEmail(): string {
  return window.localStorage.getItem(AUTHOR_EMAIL_STORAGE_KEY) || "";
}

function defaultPreprocessingProfile(modality: ProjectModality): string {
  return modality === "SAR" ? "sar_log_percentile" : "eo_rgb_percentile";
}

//: Domyślne wartości formularza nowego projektu. Trzymane jako stałe, bo są używane także
//: przy resecie po utworzeniu projektu — rozjazd między tymi dwoma miejscami byłby niewidoczny.
const DEFAULT_IMPORT_MODE: SceneImportMode = "prepare_all";
const DEFAULT_TILE_OVERLAP_PX = 100;

function defaultSplitStrategy(georeferencing: ProjectGeoreferencing): SplitMode {
  return georeferencing === "GEO" ? "spatial_block_split" : "image_block_split";
}

function createDefaultProjectProfile(
  modality: ProjectModality = "EO",
  georeferencing: ProjectGeoreferencing = "GEO",
): ProjectProfile {
  return {
    modality,
    allowed_modalities: [modality],
    georeferencing,
    allowed_georeferencing: [georeferencing],
    allow_mixed_scenes: false,
    sensors: [],
    annotation_mode: "rotated_bbox",
    labeling_author_email: getSavedAuthorEmail() || null,
    project_role: "labeling",
    default_preprocessing_profile: defaultPreprocessingProfile(modality),
    default_split_strategy: defaultSplitStrategy(georeferencing),
  };
}

// Cache poza komponentem: po powrocie na /projects widok montuje się od nowa, więc bez
// cache stan zaczynałby od pustej listy i kafelki „mrugały" (znikały do czasu refetchu).
let cachedProjects: Project[] = [];

export default function ProjectsView() {
  const { t } = useTranslation();
  const [projects, setProjects] = useState<Project[]>(cachedProjects);
  const [newName, setNewName] = useState("");
  const [projectLocation, setProjectLocation] = useState(
    () => window.localStorage.getItem("geotile.projectLocation") || ""
  );
  const [projectProfile, setProjectProfile] = useState<ProjectProfile>(() =>
    createDefaultProjectProfile()
  );
  const [classesFile, setClassesFile] = useState("");
  const [tileSize, setTileSize] = useState(640);
  const [tileBuffer, setTileBuffer] = useState(DEFAULT_TILE_OVERLAP_PX);
  const [sceneImportMode, setSceneImportMode] = useState<SceneImportMode>(DEFAULT_IMPORT_MODE);
  const [creating, setCreating] = useState(false);
  const [sceneSources, setSceneSources] = useState<SceneSourceInput[]>([
    { provider: "generic", root_path: "", enabled: true },
  ]);
  const [sourcePreview, setSourcePreview] = useState<SceneImportPreview | null>(null);
  const [scanningSources, setScanningSources] = useState(false);
  const [sourceDecisions, setSourceDecisions] = useState<Record<string, string[]>>({});
  const [sourceRgbDecisions, setSourceRgbDecisions] = useState<Record<string, number[]>>({});
  const [skippedSourcePackages, setSkippedSourcePackages] = useState<Record<string, boolean>>({});

  // --- Typ projektu ---
  const [sourceType, setSourceType] = useState<ProjectSourceType>("local_scenes");
  // --- Airborne NITF (sensor-geometry) project state ---
  const [nitfFolder, setNitfFolder] = useState("");
  const [importingBackup, setImportingBackup] = useState(false);
  const [importingProjectFolder, setImportingProjectFolder] = useState(false);
  const {
    isOpen: isDeleteDialogOpen,
    onOpen: openDeleteDialog,
    onClose: closeDeleteDialog,
  } = useDisclosure();
  const [projectToDelete, setProjectToDelete] = useState<Project | null>(null);
  const [deletingProject, setDeletingProject] = useState(false);
  const [importModal, setImportModal] = useState<{ projectId: string; jobId: string } | null>(null);
  const deleteCancelRef = useRef<HTMLButtonElement>(null);
  const navigate = useNavigate();
  const toast = useToast();
  const textColor = useColorModeValue("navy.700", "white");
  const formatSceneFolder = (path: string) => path || "/";

  const loadProjects = async () => {
    try {
      const data = await api.listProjects();
      cachedProjects = data;
      setProjects(data);
    } catch {
      // Pod obciążeniem żądanie może paść — zostaw dotychczas pokazane kafelki zamiast
      // je czyścić (znikałyby aż do kolejnego wejścia/restartu).
    }
  };

  useEffect(() => {
    loadProjects();
  }, []);

  useEffect(() => {
    if (sourceType !== "local_scenes") return;
    const sensors = sensorsForSceneSources(sceneSources);
    setProjectProfile((current) => {
      if (current.sensors.length === sensors.length && current.sensors.every((sensor) => sensors.includes(sensor))) {
        return current;
      }
      return { ...current, sensors };
    });
  }, [sceneSources, sourceType]);

  const updateSceneSource = (index: number, patch: Partial<SceneSourceInput>) => {
    setSceneSources((current) => current.map((source, itemIndex) => (
      itemIndex === index ? { ...source, ...patch } : source
    )));
    setSourcePreview(null);
  };

  const browseSceneSource = async (index: number) => {
    const selected = isTauriRuntime()
      ? await openSceneFolderDialog()
      : window.prompt(t("Scene folder"), sceneSources[index]?.root_path || "");
    if (!selected) return;
    updateSceneSource(index, { root_path: selected });
    if (index === 0) {
      if (!newName) {
        const parts = selected.split(/[/\\]/).filter(Boolean);
        if (parts.length) setNewName(parts[parts.length - 1]);
      }
    }
  };

  const scanSceneSources = async () => {
    const configured = sceneSources.filter((source) => source.root_path.trim());
    if (!configured.length) {
      toast({ title: t("Select at least one scene source"), status: "warning" });
      return;
    }
    setScanningSources(true);
    try {
      const preview = await api.previewSceneSources({
        sources: configured,
        // The provider scene-sources flow only ever uses SAR/EO; AERIAL_EO is a
        // separate creation path (NITF) and never reaches this call.
        modality: projectProfile.modality as "SAR" | "EO",
      });
      setSourcePreview(preview);
      setSourceDecisions({});
      setSourceRgbDecisions({});
      setSkippedSourcePackages({});
      toast({
        title: t("Scene sources scanned"),
        description: t("Detected logical scenes", { count: preview.packages.length }),
        status: preview.diagnostics.some((item) => item.level === "error") ? "warning" : "success",
      });
    } catch (err: any) {
      toast({
        title: t("Scene source scan failed"),
        description: err.response?.data?.detail || err.message,
        status: "error",
      });
    } finally {
      setScanningSources(false);
    }
  };

  const handleBrowseProjectLocation = async () => {
    try {
      let selected: string | null = null;
      if (isTauriRuntime()) {
        selected = await openProjectLocationDialog();
      } else {
        selected = window.prompt(t("Project storage location"), projectLocation);
      }
      if (!selected) return;
      setProjectLocation(selected);
      window.localStorage.setItem("geotile.projectLocation", selected);
    } catch (err: any) {
      toast({
        title: t("Project location selection failed"),
        description: err?.message || String(err),
        status: "error",
      });
    }
  };

  const updateProjectProfile = (patch: Partial<ProjectProfile>) => {
    setProjectProfile((current) => ({ ...current, ...patch }));
  };

  const handleModalityChange = (modality: ProjectModality) => {
    setProjectProfile((current) => ({
      ...current,
      modality,
      allowed_modalities: [modality],
      default_preprocessing_profile: defaultPreprocessingProfile(modality),
    }));
    setSceneSources((current) => current.map((source) => {
      const option = PROVIDER_OPTIONS.find((item) => item.id === source.provider);
      return option?.modality && option.modality !== modality
        ? { ...source, provider: modality === "SAR" ? "iceye" : "pleiades_neo" }
        : source;
    }));
    setSourcePreview(null);
    setSkippedSourcePackages({});
  };

  const handleGeoreferencingChange = (georeferencing: ProjectGeoreferencing) => {
    setProjectProfile((current) => ({
      ...current,
      georeferencing,
      allowed_georeferencing: [georeferencing],
      default_split_strategy: defaultSplitStrategy(georeferencing),
    }));
  };

  const handleAuthorEmailChange = (email: string) => {
    const normalized = email.trim();
    updateProjectProfile({ labeling_author_email: normalized || null });
    if (normalized) {
      window.localStorage.setItem(AUTHOR_EMAIL_STORAGE_KEY, normalized);
    } else {
      window.localStorage.removeItem(AUTHOR_EMAIL_STORAGE_KEY);
    }
  };

  const handleCreate = async () => {
    if (!newName.trim() || !sceneSources.some((source) => source.root_path.trim()) || !sourcePreview) {
      toast({ title: t("Provide a name and select a scene folder"), status: "warning" });
      return;
    }
    const authorEmail = projectProfile.labeling_author_email?.trim() || "";
    if (authorEmail && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(authorEmail)) {
      toast({ title: t("Provide a valid labeling author email"), status: "warning" });
      return;
    }
    setCreating(true);
    try {
      const unresolved = sourcePreview.packages.filter((item) => (
        !skippedSourcePackages[scenePackageDecisionKey(item)] && item.selection.status === "decision_required" && (
          !(sourceDecisions[scenePackageDecisionKey(item)]?.length)
          || (item.selection.product_type === "MUL+PAN" && sourceRgbDecisions[scenePackageDecisionKey(item)]?.length !== 3)
        )
      ));
      if (unresolved.length) {
        toast({ title: t("Resolve ambiguous scene products first"), status: "warning" });
        return;
      }
      const configuredSources = sceneSources.filter((source) => source.root_path.trim());
      const project = await api.createProjectFromSources({
        name: newName.trim(),
        preview_id: sourcePreview.preview_id,
        sources: configuredSources,
        decisions: [
          ...sourcePreview.packages
            .filter((item) => sourceDecisions[scenePackageDecisionKey(item)] && !skippedSourcePackages[scenePackageDecisionKey(item)])
            .map((item) => ({
              package_id: item.package_id,
              decision_uid: item.decision_uid,
              action: "import" as const,
              asset_ids: sourceDecisions[scenePackageDecisionKey(item)],
              rgb_bands: sourceRgbDecisions[scenePackageDecisionKey(item)],
            })),
          ...sourcePreview.packages
            .filter((item) => skippedSourcePackages[scenePackageDecisionKey(item)])
            .map((item) => ({
              package_id: item.package_id,
              decision_uid: item.decision_uid,
              action: "skip" as const,
              asset_ids: [],
            })),
        ],
        classes_file: classesFile.trim() || undefined,
        project_location: projectLocation.trim() || undefined,
        tile_size: tileSize,
        buffer: Math.max(0, Math.min(Math.floor(tileSize / 2), tileBuffer)),
        import_mode: sceneImportMode,
        profile: {
          ...projectProfile,
          sensors: sensorsForSceneSources(configuredSources),
          labeling_author_email: authorEmail ? authorEmail.toLowerCase() : null,
        },
      });
      setNewName("");
      setClassesFile("");
      setSceneSources([{ provider: "generic", root_path: "", enabled: true }]);
      setSourcePreview(null);
      setSourceDecisions({});
      setSourceRgbDecisions({});
      setSkippedSourcePackages({});
      setProjectProfile(createDefaultProjectProfile());
      setSceneImportMode(DEFAULT_IMPORT_MODE);
      await loadProjects();
      // Import scen biegnie teraz w tle (wątek roboczy) — pokazujemy pasek postępu
      // z aktualnym plikiem i logiem, zamiast blokować tworzenie projektu.
      setImportModal({ projectId: project.id, jobId: project.import_job_id });
    } catch (err: any) {
      toast({
        title: t("Creation failed"),
        description: err.response?.data?.detail || err.message,
        status: "error",
      });
    } finally {
      setCreating(false);
    }
  };

  const handleImportDone = (jobResult: ImportJob) => {
    const count = jobResult.report?.total ?? jobResult.done ?? 0;
    toast({ title: t("projectCreated", { count }), status: "success", duration: 2000 });
    const pid = jobResult.project_id ?? importModal?.projectId;
    setImportModal(null);
    loadProjects();
    if (pid) navigate(`/projects/${pid}`);
  };

  const handleImportClose = () => {
    // „W tle": zostaw import działający, przejdź do projektu (sceny dochodzą na bieżąco).
    const pid = importModal?.projectId;
    setImportModal(null);
    loadProjects();
    if (pid) navigate(`/projects/${pid}`);
  };

  const handleCreateNitf = async () => {
    if (!newName.trim()) {
      toast({ title: t("Provide a project name"), status: "warning" });
      return;
    }
    if (!nitfFolder.trim()) {
      toast({ title: t("Select a folder with .ntf files"), status: "warning" });
      return;
    }
    setCreating(true);
    try {
      const project = await api.createNitfProject({
        name: newName.trim(),
        scene_folder: nitfFolder.trim(),
        classes_file: classesFile.trim() || undefined,
        project_location: projectLocation.trim() || undefined,
        annotation_mode: projectProfile.annotation_mode,
      });
      toast({
        title: t("projectCreated", { count: project.scene_count }),
        status: "success",
        duration: 2000,
      });
      setNewName("");
      setClassesFile("");
      setNitfFolder("");
      setProjectProfile(createDefaultProjectProfile());
      await loadProjects();
      navigate(`/projects/${project.id}`);
    } catch (err: any) {
      toast({
        title: t("Creation failed"),
        description: err.response?.data?.detail || err.message,
        status: "error",
      });
    } finally {
      setCreating(false);
    }
  };

  const handleBrowseNitfFolder = async () => {
    const selected = isTauriRuntime()
      ? await openSceneFolderDialog()
      : window.prompt(t("Folder with .ntf files"), nitfFolder);
    if (selected) setNitfFolder(selected);
  };

  const requestProjectDelete = (project: Project) => {
    setProjectToDelete(project);
    openDeleteDialog();
  };

  const handleDelete = async () => {
    if (!projectToDelete) return;
    setDeletingProject(true);
    try {
      await api.deleteProject(projectToDelete.id);
      toast({ title: t("Project deleted"), status: "success" });
      closeDeleteDialog();
      setProjectToDelete(null);
      await loadProjects();
    } catch (err: any) {
      toast({
        title: t("Project deletion failed"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    } finally {
      setDeletingProject(false);
    }
  };

  const handleImportBackup = async () => {
    setImportingBackup(true);
    try {
      let backupFile: string | null = null;
      let selectedSceneFolder: string | null = null;

      if (isTauriRuntime()) {
        backupFile = await openBackupFileDialog();
        if (!backupFile) return;
        selectedSceneFolder = await openSceneFolderDialog();
        if (!selectedSceneFolder) return;
      } else {
        backupFile = window.prompt(t("Backup ZIP path"));
        if (!backupFile) return;
        selectedSceneFolder = window.prompt(t("Scene folder path"));
        if (!selectedSceneFolder) return;
      }

      const result = await api.importProjectBackup({
        backup_file: backupFile,
        scene_folder: selectedSceneFolder,
      });
      toast({
        title: t("projectImported", { imported: result.imported_scenes, total: result.total_scenes }),
        description: [
          result.missing_scenes.length ? `${t("Missing")}: ${result.missing_scenes.join(", ")}` : null,
        ].filter(Boolean).join(" ") || undefined,
        status: result.missing_scenes.length ? "warning" : "success",
        duration: 7000,
      });
      await loadProjects();
      navigate(`/projects/${result.project_id}`);
    } catch (err: any) {
      toast({
        title: t("Import failed"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    } finally {
      setImportingBackup(false);
    }
  };

  const handleImportProjectFolder = async () => {
    setImportingProjectFolder(true);
    try {
      let projectFolder: string | null = null;
      let selectedSceneFolder: string | null = null;

      if (isTauriRuntime()) {
        projectFolder = await openProjectFolderDialog();
        if (!projectFolder) return;
        selectedSceneFolder = await openSceneFolderDialog();
        if (!selectedSceneFolder) return;
      } else {
        projectFolder = window.prompt(t("Existing project folder path"));
        if (!projectFolder) return;
        selectedSceneFolder = window.prompt(t("Scene folder path"));
        if (!selectedSceneFolder) return;
      }

      const result = await api.importProjectFolder({
        project_folder: projectFolder,
        scene_folder: selectedSceneFolder,
      });
      toast({
        title: t("projectFolderImported", { imported: result.imported_scenes, total: result.total_scenes }),
        description: [
          result.missing_scenes.length ? `${t("Missing")}: ${result.missing_scenes.join(", ")}` : null,
        ].filter(Boolean).join(" ") || undefined,
        status: result.missing_scenes.length ? "warning" : "success",
        duration: 7000,
      });
      await loadProjects();
      navigate(`/projects/${result.project_id}`);
    } catch (err: any) {
      toast({
        title: t("Folder import failed"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    } finally {
      setImportingProjectFolder(false);
    }
  };

  return (
    <VStack align="stretch" spacing={6}>
      <HStack justify="space-between">
        <Text fontSize="2xl" fontWeight="bold" color={textColor}>
          {t("Projects")}
        </Text>
        <HStack>
          <Button
            size="sm"
            leftIcon={<MdUploadFile />}
            variant="outline"
            isLoading={importingBackup}
            onClick={handleImportBackup}
          >
            {t("Import ZIP backup")}
          </Button>
          <Button
            size="sm"
            leftIcon={<MdFolderOpen />}
            variant="outline"
            isLoading={importingProjectFolder}
            onClick={handleImportProjectFolder}
          >
            {t("Import project folder")}
          </Button>
        </HStack>
      </HStack>

      <VStack align="stretch" spacing={3}>
        <HStack justify="space-between">
          <Text fontWeight="bold" color={textColor}>{t("Existing projects")}</Text>
          <Badge colorScheme="brand">{projects.length}</Badge>
        </HStack>
        {projects.length > 0 ? (
          <SimpleGrid columns={{ base: 1, md: 2, lg: 3 }} gap={6}>
            {projects.map((p) => (
              <Card
                key={p.id}
                minW={0}
                overflow="hidden"
                cursor="pointer"
                _hover={{ transform: "translateY(-2px)", transition: "0.2s" }}
                onClick={() => navigate(`/projects/${p.id}`)}
              >
                <VStack align="start" spacing={2} p={2} w="full" minW={0}>
                  <Text fontWeight="bold" color={textColor} fontSize="lg" noOfLines={1} title={p.name}>
                    {p.name}
                  </Text>
                  <Text
                    fontSize="xs"
                    color="secondaryGray.600"
                    w="full"
                    minW={0}
                    overflow="hidden"
                    textOverflow="ellipsis"
                    whiteSpace="nowrap"
                    title={p.scene_folder_display || formatSceneFolder(p.scene_folder)}
                  >
                    {p.scene_folder_display || formatSceneFolder(p.scene_folder)}
                  </Text>
                  {p.project_root && (
                    <Text
                      fontSize="xs"
                      color="secondaryGray.500"
                      w="full"
                      minW={0}
                      overflow="hidden"
                      textOverflow="ellipsis"
                      whiteSpace="nowrap"
                      title={`${t("Project")}: ${p.project_root}`}
                    >
                      {t("Project")}: {p.project_root}
                    </Text>
                  )}
                  <HStack spacing={2} flexWrap="wrap">
                    <Badge colorScheme="brand">{t("sceneCount", { count: p.scene_count })}</Badge>
                    {p.profile?.modality && <Badge colorScheme="purple">{p.profile.modality}</Badge>}
                    {p.profile?.georeferencing && (
                      <Badge colorScheme={p.profile.georeferencing === "GEO" ? "green" : "blue"}>
                        {p.profile.georeferencing}
                      </Badge>
                    )}
                    {p.profile?.labeling_author_email && (
                      <Badge colorScheme="cyan">{p.profile.labeling_author_email}</Badge>
                    )}
                    {p.created_in_appdata && <Badge colorScheme="gray">{t("AppData")}</Badge>}
                  </HStack>
                </VStack>
                <HStack justify="flex-end" mt={2}>
                  <IconButton
                    aria-label={t("Delete")}
                    icon={<MdDelete />}
                    size="sm"
                    variant="ghost"
                    onClick={(e) => {
                      e.stopPropagation();
                      requestProjectDelete(p);
                    }}
                  />
                </HStack>
              </Card>
            ))}
          </SimpleGrid>
        ) : (
          <Card>
            <Text color="secondaryGray.600">{t("No projects yet. Create the first project below.")}</Text>
          </Card>
        )}
      </VStack>

      <Card>
        <VStack align="stretch" spacing={4}>
          <Text fontWeight="bold" color={textColor}>{t("New Project")}</Text>
          <Input
            placeholder={t("Project name")}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />

          <FormControl>
            <FormLabel fontSize="sm">{t("Project type")}</FormLabel>
            <Select
              size="sm"
              value={sourceType}
              onChange={(event) => setSourceType(event.target.value as ProjectSourceType)}
            >
              <option value="local_scenes">{t("Local scenes")}</option>
              <option value="nitf_sensor">{t("NITF (sensor geometry)")}</option>
            </Select>
          </FormControl>

          {sourceType === "local_scenes" && (
            <VStack align="stretch" spacing={3}>
              <Text fontWeight="bold" color={textColor}>{t("Scene sources")}</Text>
              {/* Modalnosc przed dostawca: lista dostawcow zalezy od EO/SAR, wiec
                  wybiera sie ja pierwsza. */}
              <FormControl maxW="220px">
                <FormLabel fontSize="xs">{t("Modality")}</FormLabel>
                <Select
                  size="sm"
                  value={projectProfile.modality}
                  onChange={(event) => handleModalityChange(event.target.value as ProjectModality)}
                >
                  <option value="EO">EO</option>
                  <option value="SAR">SAR</option>
                </Select>
              </FormControl>
              {sceneSources.map((source, index) => (
                <HStack key={`${index}-${source.provider}`} align="end">
                  <FormControl maxW="220px">
                    <FormLabel fontSize="xs">{t("Provider")}</FormLabel>
                    <Select
                      size="sm"
                      value={source.provider}
                      onChange={(event) => updateSceneSource(index, { provider: event.target.value as SceneProvider })}
                    >
                      {PROVIDER_OPTIONS.filter((item) => !item.modality || item.modality === projectProfile.modality).map((item) => (
                        <option key={item.id} value={item.id}>{item.label}</option>
                      ))}
                    </Select>
                  </FormControl>
                  <FormControl flex="1">
                    <FormLabel fontSize="xs">{t("Source folder")}</FormLabel>
                    <Input size="sm" value={source.root_path} isReadOnly placeholder={t("Scene package folder")} />
                  </FormControl>
                  <Button size="sm" leftIcon={<MdFolderOpen />} onClick={() => browseSceneSource(index)}>
                    {t("Browse")}
                  </Button>
                  {sceneSources.length > 1 && (
                    <IconButton
                      size="sm"
                      aria-label={t("Remove source")}
                      icon={<MdDelete />}
                      variant="ghost"
                      onClick={() => {
                        setSceneSources((current) => current.filter((_, itemIndex) => itemIndex !== index));
                        setSourcePreview(null);
                      }}
                    />
                  )}
                </HStack>
              ))}
              <HStack>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setSceneSources((current) => [
                    ...current,
                    { provider: projectProfile.modality === "SAR" ? "iceye" : "pleiades_neo", root_path: "", enabled: true },
                  ])}
                >
                  {t("Add scene source")}
                </Button>
                <Button size="sm" colorScheme="teal" onClick={scanSceneSources} isLoading={scanningSources}>
                  {t("Scan and verify")}
                </Button>
                {sourcePreview && (
                  <>
                    <Badge colorScheme={sourcePreview.packages.some((item) => item.package_kind !== "archive" && item.selection.status === "decision_required") ? "orange" : "green"}>
                      {t("Logical scenes")}: {sourcePreview.packages.filter((item) => item.package_kind !== "archive").length}
                    </Badge>
                    {/* Archiwa sa liczone osobno: nie sa scenami do etykietowania, tylko druga
                        reprezentacja dostawy (P1.3a). */}
                    {sourcePreview.packages.some((item) => item.package_kind === "archive") && (
                      <Badge colorScheme="purple">
                        {t("Archives")}: {sourcePreview.packages.filter((item) => item.package_kind === "archive").length}
                      </Badge>
                    )}
                  </>
                )}
              </HStack>

              {/* P2.1: hierarchia zrodlo -> dostawa -> akwizycja -> produkt zamiast plaskiej
                  tabeli. Grupowanie liczy backend, bo klucz akwizycji jest gramatyka dostawcy. */}
              {sourcePreview?.tree && (
                <ScenePreviewTree
                  tree={sourcePreview.tree}
                  packagesById={Object.fromEntries(
                    sourcePreview.packages.map((item) => [item.package_id, item]),
                  )}
                  decisionKeyOf={scenePackageDecisionKey}
                  decisions={sourceDecisions}
                  rgbDecisions={sourceRgbDecisions}
                  skipped={skippedSourcePackages}
                  onDecision={(key, assetIds) =>
                    setSourceDecisions((current) => ({ ...current, [key]: assetIds }))
                  }
                  onRgb={(key, bands) =>
                    setSourceRgbDecisions((current) => ({ ...current, [key]: bands }))
                  }
                  onToggleSkip={(key) =>
                    setSkippedSourcePackages((current) => ({ ...current, [key]: !current[key] }))
                  }
                />
              )}
            </VStack>
          )}

          <HStack>
            <Input
              placeholder={t("Project parent folder (optional, defaults to app data)")}
              value={projectLocation}
              isReadOnly
              flex="1"
            />
            <Button
              leftIcon={<MdFolderOpen />}
              onClick={handleBrowseProjectLocation}
              variant="outline"
            >
              {t("Location")}
            </Button>
          </HStack>

          <VStack align="stretch" spacing={3}>
            <Text fontWeight="bold" color={textColor}>{t("Project Profile")}</Text>

            <HStack align="start" spacing={4}>
              {sourceType === "local_scenes" ? (
                <FormControl>
                  <FormLabel fontSize="sm">{t("Georeferencing")}</FormLabel>
                  <Select
                    size="sm"
                    value={projectProfile.georeferencing}
                    onChange={(event) => handleGeoreferencingChange(event.target.value as ProjectGeoreferencing)}
                  >
                    <option value="NO_GEO">NO GEO</option>
                    <option value="GEO">GEO</option>
                  </Select>
                </FormControl>
              ) : (
                <HStack spacing={2} pt={1}>
                  <Badge colorScheme="purple">EO</Badge>
                  <Badge colorScheme="green">GEO</Badge>
                  <Badge colorScheme="blue">EPSG:3857</Badge>
                </HStack>
              )}
              <FormControl>
                <FormLabel fontSize="sm">{t("Annotation mode")}</FormLabel>
                <Select
                  size="sm"
                  value={projectProfile.annotation_mode}
                  onChange={(event) =>
                    updateProjectProfile({ annotation_mode: event.target.value as ProjectProfile["annotation_mode"] })
                  }
                >
                  <option value="bbox">{t("Axis-aligned bbox")}</option>
                  <option value="rotated_bbox">{t("Rotated bbox")}</option>
                </Select>
              </FormControl>
            </HStack>

            <FormControl>
              <FormLabel fontSize="sm">{t("Labeling author email")}</FormLabel>
              <Input
                size="sm"
                type="email"
                placeholder="name@example.com"
                value={projectProfile.labeling_author_email || ""}
                onChange={(event) => handleAuthorEmailChange(event.target.value)}
              />
            </FormControl>

            <FormControl>
              <FormLabel fontSize="sm">{t("Project role")}</FormLabel>
              <Select
                size="sm"
                value={projectProfile.project_role || "labeling"}
                onChange={(event) =>
                  updateProjectProfile({ project_role: event.target.value as ProjectProfile["project_role"] })
                }
              >
                <option value="labeling">{t("Labeling (analyst) - exports annotation packages")}</option>
                <option value="review">{t("Review (manager) - imports packages, builds datasets")}</option>
              </Select>
              <FormHelperText fontSize="xs">
                {projectProfile.project_role === "review"
                  ? t("Review projects merge analyst packages; annotation-package export is disabled.")
                  : t("Labeling projects export annotation packages; package import is disabled.")}
              </FormHelperText>
            </FormControl>

            <HStack align="start" spacing={4}>
              {sourceType === "local_scenes" && (
              <FormControl>
                <FormLabel fontSize="sm">{t("Default preprocessing")}</FormLabel>
                <Select
                  size="sm"
                  value={projectProfile.default_preprocessing_profile}
                  onChange={(event) =>
                    updateProjectProfile({ default_preprocessing_profile: event.target.value })
                  }
                >
                  {projectProfile.modality === "EO" ? (
                    <>
                      <option value="eo_rgb_percentile">{t("EO RGB percentile 2-98")}</option>
                      <option value="eo_linear_full_range">{t("EO linear full range")}</option>
                    </>
                  ) : (
                    <>
                      <option value="sar_log_percentile">{t("SAR log percentile 2-98")}</option>
                      <option value="sar_linear_percentile">{t("SAR linear percentile 2-98")}</option>
                    </>
                  )}
                </Select>
              </FormControl>
              )}
              {/* Strategia podziału wybierana jest przy budowie datasetu (panel Dataset),
                  gdzie ma znaczenie i podpowiedzi zgodności. Domyślna wartość jest seedowana
                  po stronie backendu z georeferencji (default_split_strategy). */}
            </HStack>
          </VStack>

          {sourceType === "nitf_sensor" && (
            <VStack align="stretch" spacing={3}>
              <Text fontWeight="bold" color={textColor}>{t("Airborne NITF scenes")}</Text>
              <HStack>
                <Input
                  placeholder={t("Folder with .ntf files (or package subfolders)")}
                  value={nitfFolder}
                  isReadOnly
                  flex="1"
                />
                <Button
                  leftIcon={<MdFolderOpen />}
                  onClick={handleBrowseNitfFolder}
                  variant="outline"
                >
                  {t("Browse")}
                </Button>
              </HStack>
              <Text fontSize="xs" color="secondaryGray.600">
                {t("Panchromatic UInt16 scenes are labeled in native sensor geometry (no basemap); spatial position is preserved via TPS.")}
              </Text>
              {/* Tryb adnotacji jest w sekcji „Project Profile" (wspólnej dla wszystkich
                  typów projektu) — nie dublujemy go tutaj. */}
            </VStack>
          )}

          {classesFile && (
            <Text fontSize="xs" color="green.500">
              {t("Classes file")}: {classesFile}
            </Text>
          )}
          <HStack>
            <Input value={classesFile} isReadOnly placeholder={t("Classes file (optional)")} />
            <Button
              leftIcon={<MdUploadFile />}
              variant="outline"
              onClick={async () => {
                const selected = isTauriRuntime()
                  ? await openClassesFileDialog()
                  : window.prompt(t("Classes file"), classesFile);
                if (selected) setClassesFile(selected);
              }}
            >
              {t("Browse")}
            </Button>
          </HStack>

          {sourceType === "local_scenes" && (
            <VStack align="stretch" spacing={1}>
              <Text fontSize="sm" fontWeight="600">{t("Scene display preparation")}</Text>
              <FormControl mb={3}>
                <FormLabel fontSize="xs">{t("Import mode")}</FormLabel>
                <Select
                  value={sceneImportMode}
                  onChange={(event) => setSceneImportMode(event.target.value as SceneImportMode)}
                >
                  <option value="on_demand">{t("Quick import (on demand)")}</option>
                  <option value="background">{t("Prepare pyramids in background")}</option>
                  <option value="prepare_all">{t("Prepare everything before opening")}</option>
                </Select>
                <FormHelperText>
                  {sceneImportMode === "on_demand"
                    ? t("The project opens fastest; a display pyramid is built when a scene is opened.")
                    : sceneImportMode === "prepare_all"
                      ? t("Uses the measured high-throughput profile and opens the project after all display pyramids are ready.")
                      : t("The project opens after cataloguing; display pyramids continue with a responsiveness-friendly I/O profile.")}
                </FormHelperText>
              </FormControl>
              <Text fontSize="sm" fontWeight="600">{t("Tile grid")}</Text>
              <HStack>
                <FormControl>
                  <FormLabel fontSize="xs">{t("Tile size")} (px)</FormLabel>
                  <Input
                    type="number"
                    value={tileSize}
                    min={128}
                    max={4096}
                    step={64}
                    onChange={(e) => setTileSize(Math.max(128, Number(e.target.value) || 640))}
                  />
                </FormControl>
                <FormControl>
                  <FormLabel fontSize="xs">{t("Overlap")} (px)</FormLabel>
                  <Input
                    type="number"
                    value={tileBuffer}
                    min={0}
                    step={8}
                    onChange={(e) => setTileBuffer(Math.max(0, Number(e.target.value) || 0))}
                  />
                </FormControl>
              </HStack>
              <Text fontSize="xs" color="secondaryGray.600">
                {t("Defines how scenes are tiled - the grid you review is the grid you train on. You can change it later in the Review grid.")}
              </Text>
            </VStack>
          )}

          <Button
            colorScheme="brand"
            onClick={sourceType === "nitf_sensor" ? handleCreateNitf : handleCreate}
            isLoading={creating}
            isDisabled={
              !newName.trim() ||
              (sourceType === "local_scenes"
                ? !sceneSources.some((source) => source.root_path.trim()) || !sourcePreview
                : !nitfFolder.trim())
            }
          >
            {t("Create Project")}
          </Button>
        </VStack>
      </Card>

      <AlertDialog
        isOpen={isDeleteDialogOpen}
        leastDestructiveRef={deleteCancelRef}
        onClose={() => {
          if (deletingProject) return;
          closeDeleteDialog();
          setProjectToDelete(null);
        }}
        isCentered
      >
        <AlertDialogOverlay>
          <AlertDialogContent>
            <AlertDialogHeader fontSize="lg" fontWeight="bold">
              {t("Delete project?")}
            </AlertDialogHeader>
            <AlertDialogBody>
              <VStack align="stretch" spacing={3}>
                <Text>{t("Delete project confirmation", { name: projectToDelete?.name || "" })}</Text>
                <Text fontSize="sm" color="secondaryGray.600">
                  {t("Project deletion details")}
                </Text>
              </VStack>
            </AlertDialogBody>
            <AlertDialogFooter>
              <Button
                ref={deleteCancelRef}
                onClick={() => {
                  closeDeleteDialog();
                  setProjectToDelete(null);
                }}
                isDisabled={deletingProject}
              >
                {t("Cancel")}
              </Button>
              <Button
                colorScheme="red"
                onClick={handleDelete}
                ml={3}
                isLoading={deletingProject}
              >
                {t("Delete project")}
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialogOverlay>
      </AlertDialog>

      <ImportProgressModal
        projectId={importModal?.projectId ?? null}
        jobId={importModal?.jobId ?? null}
        isOpen={Boolean(importModal)}
        onClose={handleImportClose}
        onDone={handleImportDone}
      />

    </VStack>
  );
}
