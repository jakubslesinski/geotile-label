import { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "../api/client";

const ACTIVE = new Set<api.JobStatus>(["queued", "starting", "running", "cancelling"]);

export function useJob(projectId?: string, jobId?: string) {
  const [detail, setDetail] = useState<api.DurableJobDetail | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!projectId || !jobId) return;
    setLoading(true);
    try {
      setDetail(await api.getJob(projectId, jobId));
    } finally {
      setLoading(false);
    }
  }, [projectId, jobId]);

  useEffect(() => {
    void refresh();
    if (!projectId || !jobId) return;
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, [projectId, jobId, refresh]);

  const cancel = useCallback(async () => {
    if (!projectId || !jobId) return;
    await api.cancelJob(projectId, jobId);
    await refresh();
  }, [projectId, jobId, refresh]);

  const retry = useCallback(async () => {
    if (!projectId || !jobId) return null;
    const result = await api.retryJob(projectId, jobId);
    return result;
  }, [projectId, jobId]);

  return { detail, loading, refresh, cancel, retry };
}

export function useProjectJobs(projectId?: string) {
  const [index, setIndex] = useState<api.DurableJobsIndex | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!projectId) {
      setIndex(null);
      return;
    }
    try {
      setIndex(await api.getJobs(projectId));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [projectId]);

  const activeCount = useMemo(
    () => index?.jobs.filter((item) => ACTIVE.has(item.state.status)).length ?? 0,
    [index],
  );

  useEffect(() => {
    void refresh();
    if (!projectId) return;
    const timer = window.setInterval(() => void refresh(), activeCount > 0 ? 1500 : 5000);
    return () => window.clearInterval(timer);
  }, [projectId, activeCount, refresh]);

  return { index, jobs: index?.jobs ?? [], activeCount, error, refresh };
}

