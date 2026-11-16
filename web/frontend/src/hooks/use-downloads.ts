"use client";
import { useEffect, useRef, useState, useCallback } from "react";
import { api, Action, errorText, Job } from "../lib/api";
import { connectDownloads, applyEvent, ConnectionStatus } from "../lib/events";
export function useDownloads() {
  const [jobs, setJobs] = useState<Job[]>([]),
    [status, setStatus] = useState<ConnectionStatus>("Connecting…"),
    [error, setError] = useState(""),
    [pending, setPending] = useState<Record<string, boolean>>({});
  const inFlight = useRef(new Set<string>());
  const jobsRef = useRef<Job[]>([]),
    connection = useRef<ReturnType<typeof connectDownloads> | null>(null);
  const replace = useCallback((next: Job[]) => {
    jobsRef.current = next;
    setJobs(next);
  }, []);
  useEffect(() => {
    const current = connectDownloads({
      snapshot: replace,
      status: setStatus,
      event: (event) => {
        const existing = jobsRef.current.find(
          (job) => job.id === event.download_id,
        );
        if (!existing) return false;
        replace(
          jobsRef.current.map((job) =>
            job.id === event.download_id ? applyEvent(job, event) : job,
          ),
        );
        return true;
      },
    });
    connection.current = current;
    return () => current.close();
  }, [replace]);
  const refresh = useCallback(async () => {
    await connection.current?.refresh();
  }, []);
  const addJob = useCallback(
    async (id: string) => {
      const job = await api.get(id);
      replace([...jobsRef.current.filter((item) => item.id !== id), job]);
    },
    [replace],
  );
  const action = useCallback(
    async (id: string, operation: Action) => {
      if (inFlight.current.has(id)) return;
      inFlight.current.add(id);
      setPending((p) => ({ ...p, [id]: true }));
      setError("");
      try {
        await api.action(id, operation);
        await refresh();
      } catch (err) {
        setError(errorText(err));
        await refresh().catch(() => {});
      } finally {
        inFlight.current.delete(id);
        setPending((p) => ({ ...p, [id]: false }));
      }
    },
    [refresh],
  );
  return { jobs, status, error, pending, action, refresh, addJob };
}
