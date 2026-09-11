import { useRef, useEffect, useCallback, useState } from "react";
import { Box } from "@chakra-ui/react";
import type { Annotation, LabelClass, TilingPreview } from "../../types";

interface Props {
  imageUrl: string;
  annotations: Annotation[];
  classes: LabelClass[];
  activeClassId: number | null;
  tilingPreview?: TilingPreview | null;
  showGrid?: boolean;
  drawingMode: boolean;
  onAnnotationCreate?: (bbox: [number, number, number, number]) => void;
}

interface ViewState {
  offsetX: number;
  offsetY: number;
  scale: number;
}

export default function SceneCanvas({
  imageUrl,
  annotations,
  classes,
  activeClassId,
  tilingPreview,
  showGrid = false,
  drawingMode,
  onAnnotationCreate,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const viewRef = useRef<ViewState>({ offsetX: 0, offsetY: 0, scale: 1 });
  const [imageLoaded, setImageLoaded] = useState(false);

  // Drawing state
  const drawStartRef = useRef<{ x: number; y: number } | null>(null);
  const [drawPreview, setDrawPreview] = useState<{
    x0: number;
    y0: number;
    x1: number;
    y1: number;
  } | null>(null);

  // Panning state
  const isPanningRef = useRef(false);
  const panStartRef = useRef({ x: 0, y: 0 });

  const classMap = new Map(classes.map((c) => [c.id, c]));

  // Load image
  useEffect(() => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      imageRef.current = img;
      setImageLoaded(true);
      // Fit to canvas
      if (containerRef.current) {
        const cw = containerRef.current.clientWidth;
        const ch = containerRef.current.clientHeight;
        const scale = Math.min(cw / img.width, ch / img.height) * 0.95;
        viewRef.current = {
          scale,
          offsetX: (cw - img.width * scale) / 2,
          offsetY: (ch - img.height * scale) / 2,
        };
      }
    };
    img.src = imageUrl;
  }, [imageUrl]);

  // Render loop
  const render = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imageRef.current;
    if (!canvas || !img) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { offsetX, offsetY, scale } = viewRef.current;

    // Resize canvas to container
    if (containerRef.current) {
      canvas.width = containerRef.current.clientWidth;
      canvas.height = containerRef.current.clientHeight;
    }

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.save();
    ctx.translate(offsetX, offsetY);
    ctx.scale(scale, scale);

    // Draw image
    ctx.drawImage(img, 0, 0);

    // Draw tile grid
    if (showGrid && tilingPreview) {
      ctx.strokeStyle = "rgba(0, 255, 0, 0.4)";
      ctx.lineWidth = 1 / scale;
      for (const x of tilingPreview.grid_lines_x) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, img.height);
        ctx.stroke();
      }
      for (const y of tilingPreview.grid_lines_y) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(img.width, y);
        ctx.stroke();
      }
    }

    // Draw annotations
    for (const ann of annotations) {
      const cls = classMap.get(ann.class_id);
      const color = cls?.color || "#FF0000";
      const [x0, y0, x1, y1] = ann.bbox;

      ctx.strokeStyle = color;
      ctx.lineWidth = 2 / scale;
      ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);

      // Label
      const label = cls?.name || `Class ${ann.class_id}`;
      ctx.fillStyle = color;
      const fontSize = Math.max(12 / scale, 8);
      ctx.font = `bold ${fontSize}px DM Sans`;
      ctx.fillRect(x0, y0 - fontSize - 4, ctx.measureText(label).width + 6, fontSize + 4);
      ctx.fillStyle = "white";
      ctx.fillText(label, x0 + 3, y0 - 3);
    }

    // Draw preview rect
    if (drawPreview) {
      ctx.strokeStyle = "rgba(255, 255, 0, 0.8)";
      ctx.lineWidth = 2 / scale;
      ctx.setLineDash([5 / scale, 5 / scale]);
      ctx.strokeRect(
        drawPreview.x0,
        drawPreview.y0,
        drawPreview.x1 - drawPreview.x0,
        drawPreview.y1 - drawPreview.y0
      );
      ctx.setLineDash([]);
    }

    ctx.restore();
  }, [annotations, classes, tilingPreview, showGrid, drawPreview, imageLoaded]);

  useEffect(() => {
    const id = requestAnimationFrame(render);
    return () => cancelAnimationFrame(id);
  }, [render]);

  // Convert screen coords to scene coords
  const screenToScene = (clientX: number, clientY: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    const { offsetX, offsetY, scale } = viewRef.current;
    return {
      x: (clientX - rect.left - offsetX) / scale,
      y: (clientY - rect.top - offsetY) / scale,
    };
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.button === 1 || (e.button === 0 && e.altKey)) {
      // Middle-click or Alt+click → pan
      isPanningRef.current = true;
      panStartRef.current = { x: e.clientX - viewRef.current.offsetX, y: e.clientY - viewRef.current.offsetY };
      return;
    }

    if (drawingMode && activeClassId !== null && e.button === 0) {
      const pt = screenToScene(e.clientX, e.clientY);
      if (!drawStartRef.current) {
        // First click
        drawStartRef.current = pt;
      } else {
        // Second click → create annotation
        const x0 = Math.min(drawStartRef.current.x, pt.x);
        const y0 = Math.min(drawStartRef.current.y, pt.y);
        const x1 = Math.max(drawStartRef.current.x, pt.x);
        const y1 = Math.max(drawStartRef.current.y, pt.y);
        if (x1 - x0 > 2 && y1 - y0 > 2) {
          onAnnotationCreate?.([x0, y0, x1, y1]);
        }
        drawStartRef.current = null;
        setDrawPreview(null);
      }
    }
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isPanningRef.current) {
      viewRef.current.offsetX = e.clientX - panStartRef.current.x;
      viewRef.current.offsetY = e.clientY - panStartRef.current.y;
      render();
      return;
    }

    if (drawStartRef.current) {
      const pt = screenToScene(e.clientX, e.clientY);
      setDrawPreview({
        x0: Math.min(drawStartRef.current.x, pt.x),
        y0: Math.min(drawStartRef.current.y, pt.y),
        x1: Math.max(drawStartRef.current.x, pt.x),
        y1: Math.max(drawStartRef.current.y, pt.y),
      });
    }
  };

  const handleMouseUp = () => {
    isPanningRef.current = false;
  };

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    const v = viewRef.current;
    const newScale = v.scale * delta;

    v.offsetX = mouseX - (mouseX - v.offsetX) * (newScale / v.scale);
    v.offsetY = mouseY - (mouseY - v.offsetY) * (newScale / v.scale);
    v.scale = newScale;
    render();
  };

  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    // Right-click cancels drawing
    if (drawStartRef.current) {
      drawStartRef.current = null;
      setDrawPreview(null);
    }
  };

  return (
    <Box
      ref={containerRef}
      flex="1"
      position="relative"
      overflow="hidden"
      cursor={drawingMode && activeClassId !== null ? "crosshair" : "grab"}
    >
      <canvas
        ref={canvasRef}
        style={{ width: "100%", height: "100%", display: "block" }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onWheel={handleWheel}
        onContextMenu={handleContextMenu}
      />
    </Box>
  );
}
