"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

interface Options {
  title: string;
  body: string;
  action?: string;
}

type Confirm = (options: Options) => Promise<boolean>;

const ConfirmContext = createContext<Confirm>(async ({ title }) => window.confirm(title));

export const useConfirm = () => useContext(ConfirmContext);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<(Options & { resolve: (ok: boolean) => void }) | null>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);

  const confirm = useCallback<Confirm>((options) => new Promise((resolve) => setRequest({ ...options, resolve })), []);

  useEffect(() => {
    if (request && !dialogRef.current?.open) dialogRef.current?.showModal();
  }, [request]);

  function close(ok: boolean) {
    request?.resolve(ok);
    setRequest(null);
    dialogRef.current?.close();
  }

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <dialog
        ref={dialogRef}
        aria-labelledby="confirm-title"
        onCancel={(event) => {
          event.preventDefault();
          close(false);
        }}
        onClick={(event) => {
          if (event.target === event.currentTarget) close(false);
        }}
        className="m-auto w-[min(26rem,calc(100%-2rem))] rounded-lg border border-line bg-raised p-0 text-ink shadow-[0_16px_48px_rgb(16_19_24/0.2)] backdrop:bg-black/30"
      >
        {request && (
          <div className="p-5">
            <h2 id="confirm-title" className="text-[15px] font-semibold text-ink">
              {request.title}
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-ink-2">{request.body}</p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                autoFocus
                onClick={() => close(false)}
                className="rounded-md border border-line-strong px-3 py-1.5 text-[13px] text-ink hover:bg-sunken"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => close(true)}
                className="rounded-md bg-bad px-3 py-1.5 text-[13px] font-medium text-white hover:bg-bad/85"
              >
                {request.action ?? "Delete"}
              </button>
            </div>
          </div>
        )}
      </dialog>
    </ConfirmContext.Provider>
  );
}
