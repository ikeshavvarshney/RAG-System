"use client";

import { useCallback, useEffect, useState } from "react";
import { listSessionDocuments, type SessionDocument } from "@/lib/api";
import { listCorpusDocuments, type CorpusDocument } from "@/lib/viewer";

const RETRY_MS = 5000;

/** The corpus and this session's uploads, refetched after every change and retried while the backend is down. */
export function useDocuments() {
  const [corpus, setCorpus] = useState<CorpusDocument[] | null>(null);
  const [session, setSession] = useState<SessionDocument[]>([]);
  const [offline, setOffline] = useState(false);
  const [version, setVersion] = useState(0);

  const refresh = useCallback(() => setVersion((value) => value + 1), []);

  useEffect(() => {
    let active = true;
    let retry: ReturnType<typeof setTimeout> | undefined;

    Promise.all([listCorpusDocuments(), listSessionDocuments()])
      .then(([fetchedCorpus, fetchedSession]) => {
        if (!active) return;
        setCorpus(fetchedCorpus);
        setSession(fetchedSession);
        setOffline(false);
      })
      .catch(() => {
        if (!active) return;
        setOffline(true);
        retry = setTimeout(refresh, RETRY_MS);
      });

    return () => {
      active = false;
      clearTimeout(retry);
    };
  }, [version, refresh]);

  return { corpus, session, offline, refresh };
}
