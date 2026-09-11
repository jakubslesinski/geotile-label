import { useMemo, useState } from "react";
import { Box, Button, HStack, Text } from "@chakra-ui/react";
import SceneMapCanvas from "../components/canvas/SceneMapCanvas";
import type { Annotation, LabelClass, MapTool } from "../types";

const SCENE_WIDTH = 10_000;
const SCENE_HEIGHT = 10_000;
const MAX_ANNOTATIONS = 150_000;
const TRANSPARENT_TILE =
  "data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=";

function requestedCount(): number {
  const value = Number(new URLSearchParams(window.location.search).get("count") || "100000");
  if (!Number.isFinite(value)) return 100_000;
  return Math.max(1, Math.min(MAX_ANNOTATIONS, Math.floor(value)));
}

function syntheticAnnotations(count: number): Annotation[] {
  const columns = Math.ceil(Math.sqrt(count));
  const spacingX = SCENE_WIDTH / columns;
  const rows = Math.ceil(count / columns);
  const spacingY = SCENE_HEIGHT / rows;
  const createdAt = "2026-08-30T00:00:00Z";
  return Array.from({ length: count }, (_, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    const cx = (column + 0.5) * spacingX;
    const cy = (row + 0.5) * spacingY;
    const width = Math.max(6, spacingX * 0.55);
    const height = Math.max(5, spacingY * 0.42);
    const angleDeg = (index * 17) % 180;
    const rotated = index % 2 === 1;
    const bbox: [number, number, number, number] = [
      cx - width / 2,
      cy - height / 2,
      cx + width / 2,
      cy + height / 2,
    ];
    return {
      id: `p2-4-${index}`,
      class_id: index % 4,
      geometry_type: rotated ? "rotated_bbox" : "bbox",
      bbox,
      rotated_bbox: rotated ? { cx, cy, width, height, angle_deg: angleDeg } : null,
      is_negative: false,
      created_at: createdAt,
    };
  });
}

const CLASSES: LabelClass[] = [
  { id: 0, name: "alpha", color: "#8b5cf6", hotkey: null },
  { id: 1, name: "beta", color: "#38bdf8", hotkey: null },
  { id: 2, name: "gamma", color: "#f59e0b", hotkey: null },
  { id: 3, name: "delta", color: "#22c55e", hotkey: null },
];

/** Development-only deterministic surface used by P2.4-B WebView2/CDP benchmarks. */
export default function AnnotationRendererBenchmarkView() {
  const count = useMemo(requestedCount, []);
  const annotations = useMemo(() => syntheticAnnotations(count), [count]);
  const [activeTool, setActiveTool] = useState<MapTool>("select");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [updateCount, setUpdateCount] = useState(0);
  const [createCount, setCreateCount] = useState(0);
  const [classChangeCount, setClassChangeCount] = useState(0);
  const [lastEventId, setLastEventId] = useState("");

  return (
    <Box
      position="fixed"
      inset={0}
      bg="#131316"
      data-testid="annotation-benchmark-view"
      data-selected-id={selectedId ?? ""}
      data-selected-count={selectedIds.length}
      data-update-count={updateCount}
      data-create-count={createCount}
      data-class-change-count={classChangeCount}
      data-last-event-id={lastEventId}
      data-active-tool={activeTool}
    >
      <HStack
        position="absolute"
        top={2}
        left={2}
        zIndex={2000}
        spacing={2}
        bg="blackAlpha.800"
        p={2}
        borderRadius="md"
      >
        <Text fontSize="sm" data-testid="benchmark-count">P2.4-B: {count}</Text>
        {(["select", "multi_select", "class_paint"] as MapTool[]).map((tool) => (
          <Button
            key={tool}
            size="xs"
            data-testid={`benchmark-tool-${tool}`}
            colorScheme={activeTool === tool ? "purple" : "gray"}
            onClick={() => setActiveTool(tool)}
          >
            {tool}
          </Button>
        ))}
      </HStack>
      <SceneMapCanvas
        projectId="p2-4-benchmark"
        tileUrlTemplate={TRANSPARENT_TILE}
        sceneWidth={SCENE_WIDTH}
        sceneHeight={SCENE_HEIGHT}
        maxZoom={6}
        annotations={annotations}
        classes={CLASSES}
        activeClassId={1}
        drawingMode={false}
        activeMapTool={activeTool}
        selectedAnnotationId={selectedId}
        selectedAnnotationIds={selectedIds}
        onAnnotationSelect={setSelectedId}
        onAnnotationMultiSelect={(ids) => {
          setSelectedIds(ids);
          setSelectedId(ids.length > 0 ? ids[ids.length - 1] : null);
        }}
        onAnnotationUpdate={(id) => {
          setUpdateCount((value) => value + 1);
          setLastEventId(id);
        }}
        onAnnotationCreate={() => {
          setCreateCount((value) => value + 1);
          setLastEventId("created");
        }}
        onAnnotationClassChange={(id) => {
          setClassChangeCount((value) => value + 1);
          setLastEventId(id);
        }}
        enableMapDragging={false}
        showAnnotationBoxes
        showAnnotationLabels
      />
    </Box>
  );
}
