import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const DEFAULT_CDP_URL = "http://127.0.0.1:9333";
const APP_URL = "http://127.0.0.1:3000";
const DOTA_URL = `${APP_URL}/projects/4efccb7f42d5/scenes/bcb956887832/label`;

function argument(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

function hasArgument(name) {
  return process.argv.includes(name);
}

function sleep(ms) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, ms));
}

class CdpClient {
  constructor(url) {
    this.url = url;
    this.nextId = 1;
    this.pending = new Map();
    this.pageExceptions = [];
  }

  async connect() {
    this.socket = new WebSocket(this.url);
    await new Promise((resolvePromise, reject) => {
      const timeout = setTimeout(() => reject(new Error(`CDP connection timeout: ${this.url}`)), 10_000);
      this.socket.addEventListener("open", () => {
        clearTimeout(timeout);
        resolvePromise();
      }, { once: true });
      this.socket.addEventListener("error", () => {
        clearTimeout(timeout);
        reject(new Error(`Cannot connect to CDP: ${this.url}`));
      }, { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (!message.id) {
        if (message.method === "Runtime.exceptionThrown") {
          const detail = message.params?.exceptionDetails;
          const description = detail?.exception?.description || detail?.text || "unknown";
          this.pageExceptions.push(description);
          process.stderr.write(`[page exception] ${description}\n`);
        } else if (message.method === "Log.entryAdded" && message.params?.entry?.level === "error") {
          process.stderr.write(`[page error] ${message.params.entry.text}\n`);
        }
        return;
      }
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(`${pending.method}: ${message.error.message}`));
      else pending.resolve(message.result);
    });
    await this.call("Page.enable");
    await this.call("Runtime.enable");
    await this.call("Log.enable");
    await this.call("Performance.enable");
  }

  call(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolvePromise, reject) => {
      this.pending.set(id, { resolve: resolvePromise, reject, method });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const result = await this.call("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
      userGesture: true,
    });
    if (result.exceptionDetails) {
      const description = result.result?.description || result.exceptionDetails.text || "Runtime.evaluate failed";
      throw new Error(description);
    }
    return result.result?.value;
  }

  close() {
    this.socket?.close();
  }
}

async function findPage(cdpUrl) {
  const response = await fetch(`${cdpUrl}/json/list`);
  if (!response.ok) throw new Error(`CDP target discovery failed: HTTP ${response.status}`);
  const targets = await response.json();
  const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
  if (!page) throw new Error("No debuggable WebView2 page found");
  return page;
}

async function waitFor(client, expression, timeoutMs, description) {
  const started = performance.now();
  let lastError = null;
  while (performance.now() - started < timeoutMs) {
    try {
      const value = await client.evaluate(expression);
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await sleep(200);
  }
  throw new Error(`Timeout waiting for ${description}${lastError ? `: ${lastError.message}` : ""}`);
}

async function navigateToBenchmark(client, url, expectedCount, timeoutMs = 180_000) {
  const started = performance.now();
  process.stdout.write(`\n[load] ${url}\n`);
  await client.call("Page.navigate", { url });
  await waitFor(
    client,
    `location.href === ${JSON.stringify(url)} && document.readyState === "complete"`,
    30_000,
    `page ${url}`,
  );
  await waitFor(
    client,
    `window.__GEOTILE_ANNOTATION_BENCHMARK__?.snapshot().total >= ${expectedCount}`,
    timeoutMs,
    `at least ${expectedCount.toLocaleString("en-US")} indexed annotations`,
  );
  await sleep(750);
  const readyMs = Math.round(performance.now() - started);
  const readySnapshot = await snapshot(client);
  process.stdout.write(`[ready] ${readySnapshot.total.toLocaleString("en-US")} annotations in ${readyMs} ms\n`);
  return readyMs;
}

async function snapshot(client) {
  return client.evaluate("window.__GEOTILE_ANNOTATION_BENCHMARK__.snapshot()");
}

async function resetView(client, zoomDelta = 0) {
  await client.evaluate(`(() => {
    const bridge = window.__GEOTILE_ANNOTATION_BENCHMARK__;
    bridge.fit();
    ${zoomDelta ? `bridge.zoomBy(${zoomDelta});` : ""}
    return bridge.snapshot();
  })()`);
  await sleep(750);
  return snapshot(client);
}

async function measureMotion(client, mode, durationMs = 3_000) {
  return client.evaluate(`(async () => {
    const bridge = window.__GEOTILE_ANNOTATION_BENCHMARK__;
    const mode = ${JSON.stringify(mode)};
    const durationMs = ${durationMs};
    const frameTimes = [];
    const longTasks = [];
    const started = performance.now();
    let previous = null;
    let frame = 0;
    let observer = null;
    if (typeof PerformanceObserver !== "undefined" && PerformanceObserver.supportedEntryTypes?.includes("longtask")) {
      observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) longTasks.push(entry.duration);
      });
      observer.observe({ entryTypes: ["longtask"] });
    }
    await new Promise((resolve) => {
      const tick = (timestamp) => {
        if (previous !== null) frameTimes.push(timestamp - previous);
        previous = timestamp;
        frame += 1;
        if (mode === "pan") {
          const direction = Math.floor(frame / 36) % 2 === 0 ? 1 : -1;
          bridge.panBy(direction * 5, frame % 3 === 0 ? direction * 2 : 0);
        } else if (mode === "zoom" && frame % 18 === 0) {
          const direction = Math.floor(frame / 18) % 2 === 0 ? 1 : -1;
          bridge.zoomBy(direction);
        }
        if (performance.now() - started >= durationMs) resolve();
        else requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    if (mode === "pan") bridge.panEnd();
    await new Promise((resolve) => setTimeout(resolve, 350));
    observer?.disconnect();
    const sorted = [...frameTimes].sort((a, b) => a - b);
    const percentile = (value) => {
      if (!sorted.length) return null;
      return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * value))];
    };
    const elapsedMs = performance.now() - started;
    const frameElapsedMs = frameTimes.reduce((sum, value) => sum + value, 0);
    return {
      mode,
      durationMs,
      elapsedMs,
      frameCount: frameTimes.length,
      fps: frameElapsedMs > 0 ? frameTimes.length * 1000 / frameElapsedMs : 0,
      frameMs: {
        p50: percentile(0.5),
        p95: percentile(0.95),
        p99: percentile(0.99),
        max: sorted.length ? sorted[sorted.length - 1] : null,
      },
      framesOver50Ms: frameTimes.filter((value) => value > 50).length,
      longTasks: {
        count: longTasks.length,
        totalMs: longTasks.reduce((sum, value) => sum + value, 0),
        maxMs: longTasks.length ? Math.max(...longTasks) : 0,
      },
      snapshot: bridge.snapshot(),
    };
  })()`);
}

async function processMetrics(client) {
  const [performanceResult, domResult] = await Promise.all([
    client.call("Performance.getMetrics"),
    client.call("Memory.getDOMCounters"),
  ]);
  const metrics = Object.fromEntries(performanceResult.metrics.map(({ name, value }) => [name, value]));
  return {
    jsHeapUsedBytes: metrics.JSHeapUsedSize ?? null,
    jsHeapTotalBytes: metrics.JSHeapTotalSize ?? null,
    taskDurationSeconds: metrics.TaskDuration ?? null,
    scriptDurationSeconds: metrics.ScriptDuration ?? null,
    layoutDurationSeconds: metrics.LayoutDuration ?? null,
    documents: domResult.documents,
    nodes: domResult.nodes,
    listeners: domResult.jsEventListeners,
  };
}

async function dispatchClick(client, point) {
  await client.call("Input.dispatchMouseEvent", {
    type: "mousePressed", x: point.x, y: point.y, button: "left", buttons: 1, clickCount: 1,
  });
  await client.call("Input.dispatchMouseEvent", {
    type: "mouseReleased", x: point.x, y: point.y, button: "left", buttons: 0, clickCount: 1,
  });
}

async function dispatchDrag(client, start, end, steps = 8) {
  await client.call("Input.dispatchMouseEvent", {
    type: "mouseMoved", x: start.x, y: start.y, button: "none", buttons: 0,
  });
  await client.call("Input.dispatchMouseEvent", {
    type: "mousePressed", x: start.x, y: start.y, button: "left", buttons: 1, clickCount: 1,
  });
  for (let step = 1; step <= steps; step += 1) {
    await client.call("Input.dispatchMouseEvent", {
      type: "mouseMoved",
      x: start.x + (end.x - start.x) * step / steps,
      y: start.y + (end.y - start.y) * step / steps,
      button: "left",
      buttons: 1,
    });
  }
  await client.call("Input.dispatchMouseEvent", {
    type: "mouseReleased", x: end.x, y: end.y, button: "left", buttons: 0, clickCount: 1,
  });
  await sleep(150);
}

async function benchmarkState(client) {
  return client.evaluate(`(() => {
    const root = document.querySelector('[data-testid="annotation-benchmark-view"]');
    if (!root) return null;
    return {
      selectedId: root.dataset.selectedId || null,
      selectedCount: Number(root.dataset.selectedCount || 0),
      updateCount: Number(root.dataset.updateCount || 0),
      createCount: Number(root.dataset.createCount || 0),
      classChangeCount: Number(root.dataset.classChangeCount || 0),
      lastEventId: root.dataset.lastEventId || null,
      activeTool: root.dataset.activeTool || null,
    };
  })()`);
}

async function visibleGeometry(client, geometryType) {
  return client.evaluate(`(() => {
    const bridge = window.__GEOTILE_ANNOTATION_BENCHMARK__;
    const point = bridge.renderedCenters(${JSON.stringify(geometryType)}, 1000).find((item) => (
      item.x > 90 && item.y > 90 && item.x < innerWidth - 90 && item.y < innerHeight - 90
    ));
    return point ? bridge.renderedGeometry(point.id) : null;
  })()`);
}

async function setTool(client, tool) {
  await client.evaluate(`document.querySelector('[data-testid="benchmark-tool-${tool}"]').click()`);
  await waitFor(
    client,
    `document.querySelector('[data-testid="annotation-benchmark-view"]')?.dataset.activeTool === ${JSON.stringify(tool)}`,
    3_000,
    `tool ${tool}`,
  );
}

async function runInteractionRegression(client) {
  await resetView(client, 3);
  const checks = [];
  const record = (name, passed, detail) => checks.push({ name, passed: Boolean(passed), detail });

  await setTool(client, "select");
  const bbox = await visibleGeometry(client, "bbox");
  if (!bbox) throw new Error("No visible bbox found for interaction regression");
  await dispatchClick(client, bbox.center);
  await sleep(150);
  let state = await benchmarkState(client);
  record("bbox selection", state.selectedId === bbox.id, state);

  const bboxMoveBefore = state.updateCount;
  await dispatchDrag(client, bbox.center, { x: bbox.center.x + 18, y: bbox.center.y + 10 });
  state = await benchmarkState(client);
  record("bbox move", state.updateCount > bboxMoveBefore && state.lastEventId === bbox.id, state);

  const bboxResizeBefore = state.updateCount;
  await dispatchDrag(client, bbox.corners[2], { x: bbox.corners[2].x + 14, y: bbox.corners[2].y + 12 });
  state = await benchmarkState(client);
  record("bbox resize", state.updateCount > bboxResizeBefore && state.lastEventId === bbox.id, state);

  const copyBefore = state.createCount;
  await client.call("Input.dispatchKeyEvent", {
    type: "keyDown", key: "c", code: "KeyC", text: "c", windowsVirtualKeyCode: 67, nativeVirtualKeyCode: 67,
  });
  await dispatchDrag(client, bbox.center, { x: bbox.center.x - 16, y: bbox.center.y + 12 });
  await client.call("Input.dispatchKeyEvent", {
    type: "keyUp", key: "c", code: "KeyC", windowsVirtualKeyCode: 67, nativeVirtualKeyCode: 67,
  });
  state = await benchmarkState(client);
  record("copy modifier", state.createCount > copyBefore, state);

  const rotated = await visibleGeometry(client, "rotated_bbox");
  if (!rotated) throw new Error("No visible rotated_bbox found for interaction regression");
  await dispatchClick(client, rotated.center);
  await sleep(150);
  state = await benchmarkState(client);
  record("OBB selection", state.selectedId === rotated.id, state);

  const obbMoveBefore = state.updateCount;
  await dispatchDrag(client, rotated.center, { x: rotated.center.x + 16, y: rotated.center.y - 10 });
  state = await benchmarkState(client);
  record("OBB move", state.updateCount > obbMoveBefore && state.lastEventId === rotated.id, state);

  const obbResizeBefore = state.updateCount;
  await dispatchDrag(client, rotated.corners[2], { x: rotated.corners[2].x + 12, y: rotated.corners[2].y + 10 });
  state = await benchmarkState(client);
  record("OBB resize", state.updateCount > obbResizeBefore && state.lastEventId === rotated.id, state);

  const rotateBefore = state.updateCount;
  await dispatchDrag(
    client,
    rotated.rotateHandle,
    { x: rotated.rotateHandle.x + 18, y: rotated.rotateHandle.y - 14 },
  );
  state = await benchmarkState(client);
  record("OBB rotate", state.updateCount > rotateBefore && state.lastEventId === rotated.id, state);

  await setTool(client, "class_paint");
  const classBefore = state.classChangeCount;
  await dispatchClick(client, bbox.center);
  await sleep(150);
  state = await benchmarkState(client);
  record("class paint", state.classChangeCount > classBefore && state.lastEventId === bbox.id, state);

  await setTool(client, "multi_select");
  await dispatchDrag(client, { x: 250, y: 220 }, { x: 650, y: 520 }, 12);
  state = await benchmarkState(client);
  record("rectangle multi-select", state.selectedCount > 1, state);

  return {
    passed: checks.every((check) => check.passed),
    checks,
  };
}

async function runBatchedHitRegression(client) {
  const view = await resetView(client, 3);
  const checks = [];
  const record = (name, passed, detail) => checks.push({ name, passed: Boolean(passed), detail });
  record("batched renderer active", view.renderer === "batched-canvas", view);

  await setTool(client, "select");
  const bbox = await visibleGeometry(client, "bbox");
  if (!bbox) throw new Error("No visible batched bbox found");
  await dispatchClick(client, bbox.center);
  await sleep(150);
  let state = await benchmarkState(client);
  record("batched bbox hit-test", state.selectedId === bbox.id, state);

  const rotated = await visibleGeometry(client, "rotated_bbox");
  if (!rotated) throw new Error("No visible batched rotated_bbox found");
  await dispatchClick(client, rotated.center);
  await sleep(150);
  state = await benchmarkState(client);
  record("batched OBB hit-test", state.selectedId === rotated.id, state);

  await setTool(client, "class_paint");
  const classBefore = state.classChangeCount;
  await dispatchClick(client, bbox.center);
  await sleep(150);
  state = await benchmarkState(client);
  record("batched class paint", state.classChangeCount > classBefore && state.lastEventId === bbox.id, state);

  return {
    passed: checks.every((check) => check.passed),
    checks,
  };
}

async function runScenario(client, { name, url, count, timeoutMs }) {
  process.stdout.write(`[scenario] ${name}\n`);
  const loadReadyMs = await navigateToBenchmark(client, url, count, timeoutMs);
  const fit = await resetView(client, 0);
  const zoom3 = await resetView(client, 3);
  const beforeMetrics = await processMetrics(client);
  const pan = await measureMotion(client, "pan");
  process.stdout.write(`[pan] ${pan.fps.toFixed(1)} FPS, p95 ${pan.frameMs.p95?.toFixed(1)} ms\n`);
  await resetView(client, 2);
  const zoom = await measureMotion(client, "zoom");
  process.stdout.write(`[zoom] ${zoom.fps.toFixed(1)} FPS, p95 ${zoom.frameMs.p95?.toFixed(1)} ms\n`);
  const afterMetrics = await processMetrics(client);
  return {
    name,
    url,
    expectedAnnotations: count,
    loadReadyMs,
    snapshots: { fit, zoom3 },
    motion: { pan, zoom },
    process: { before: beforeMetrics, after: afterMetrics },
  };
}

function roundDeep(value) {
  if (typeof value === "number") return Number(value.toFixed(3));
  if (Array.isArray(value)) return value.map(roundDeep);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, nested]) => [key, roundDeep(nested)]));
  }
  return value;
}

async function main() {
  const cdpUrl = argument("--cdp", DEFAULT_CDP_URL);
  const output = resolve(argument(
    "--output",
    "../benchmark-results/p2_4/p2_4_b_webview2.json",
  ));
  const page = await findPage(cdpUrl);
  const client = new CdpClient(page.webSocketDebuggerUrl);
  await client.connect();
  try {
    if (hasArgument("--probe")) {
      process.stdout.write(`${JSON.stringify(await client.evaluate(`({
        href: location.href,
        readyState: document.readyState,
        benchmark: window.__GEOTILE_ANNOTATION_BENCHMARK__?.snapshot() ?? null,
        text: document.body?.innerText?.slice(0, 800) ?? "",
        bodyHtml: document.body?.innerHTML?.slice(0, 1200) ?? "",
        rootChildren: document.querySelector("#root")?.childElementCount ?? null,
        resources: performance.getEntriesByType("resource").slice(-10).map(({ name, duration }) => ({ name, duration })),
      })`), null, 2)}\n`);
      return;
    }
    const scenarios = [];
    let batchedInteractions = null;
    if (!hasArgument("--skip-real")) {
      scenarios.push(await runScenario(client, {
        name: "DOTA P1868 (real project)",
        url: DOTA_URL,
        count: 10_000,
        timeoutMs: 240_000,
      }));
    }
    if (!hasArgument("--skip-100k")) {
      scenarios.push(await runScenario(client, {
        name: "Synthetic 100k",
        url: `${APP_URL}/__benchmarks/annotation-renderer?count=100000`,
        count: 100_000,
        timeoutMs: 300_000,
      }));
      batchedInteractions = await runBatchedHitRegression(client);
    }
    let interactions = null;
    if (!hasArgument("--skip-interactions")) {
      await navigateToBenchmark(
        client,
        `${APP_URL}/__benchmarks/annotation-renderer?count=10000`,
        10_000,
        180_000,
      );
      interactions = await runInteractionRegression(client);
    }

    const tenK = scenarios.find((scenario) => scenario.name.startsWith("DOTA"));
    const hundredK = scenarios.find((scenario) => scenario.expectedAnnotations === 100_000);
    const gates = {
      real10kPanAtLeast30Fps: tenK ? tenK.motion.pan.fps >= 30 : null,
      real10kZoomAtLeast30Fps: tenK ? tenK.motion.zoom.fps >= 30 : null,
      hundredKNoTwoSecondFrame: hundredK
        ? Math.max(hundredK.motion.pan.frameMs.max ?? 0, hundredK.motion.zoom.frameMs.max ?? 0) < 2_000
        : null,
      hundredKNoTwoSecondLongTask: hundredK
        ? Math.max(hundredK.motion.pan.longTasks.maxMs, hundredK.motion.zoom.longTasks.maxMs) < 2_000
        : null,
      hundredKDomIsSublinear: hundredK
        ? hundredK.snapshots.zoom3.domNodes < hundredK.expectedAnnotations * 0.1
        : null,
      interactionRegression: interactions || batchedInteractions
        ? [interactions, batchedInteractions].filter(Boolean).every((result) => result.passed)
        : null,
      noPageExceptions: client.pageExceptions.length === 0,
    };
    const result = roundDeep({
      schemaVersion: 1,
      generatedAt: new Date().toISOString(),
      environment: {
        cdpUrl,
        targetTitle: page.title,
        userAgent: await client.evaluate("navigator.userAgent"),
        viewport: await client.evaluate("({ width: innerWidth, height: innerHeight, devicePixelRatio })"),
      },
      scenarios,
      interactions,
      batchedInteractions,
      pageExceptions: client.pageExceptions,
      gates: {
        ...gates,
        passed: Object.values(gates).filter((value) => value !== null).every(Boolean),
      },
    });
    await mkdir(dirname(output), { recursive: true });
    await writeFile(output, `${JSON.stringify(result, null, 2)}\n`, "utf8");
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    process.stdout.write(`\nSaved: ${output}\n`);
    if (!result.gates.passed) process.exitCode = 2;
  } finally {
    client.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message || error);
  process.exitCode = 1;
});
