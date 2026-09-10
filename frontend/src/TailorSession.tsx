import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { AnalysisResult, SuggestionRow } from "./api";

/**
 * The Tailor tab's work in progress, held above the router.
 *
 * Every piece of this used to live in the Tailor component, so navigating to
 * Changes unmounted it and threw away the pasted job description, the
 * analysis, and any accepted suggestions. Holding it here means switching tabs
 * is free, and mirroring it into sessionStorage means a reload is too.
 *
 * Transient flags -- busy, generating, errors -- stay local to the component.
 * Restoring "generating…" from storage would be a lie.
 */
export interface TailorSession {
  jdText: string;
  jdUrl: string;
  masterId: number | undefined;
  result: AnalysisResult | null;
  suggestions: SuggestionRow[];
  view: "terms" | "suggestions";
  tailorNote: string;
  providerNote: string;
}

const EMPTY: TailorSession = {
  jdText: "",
  jdUrl: "",
  masterId: undefined,
  result: null,
  suggestions: [],
  view: "terms",
  tailorNote: "",
  providerNote: "",
};

const STORAGE_KEY = "tailorSession";

interface Store {
  session: TailorSession;
  update: (patch: Partial<TailorSession>) => void;
  reset: () => void;
}

const Context = createContext<Store | null>(null);

function load(): TailorSession {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? { ...EMPTY, ...(JSON.parse(raw) as TailorSession) } : EMPTY;
  } catch {
    return EMPTY; // private window, cleared storage, blocked site data
  }
}

export function TailorSessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<TailorSession>(load);

  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    } catch {
      /* non-fatal: the in-memory copy still survives tab switches */
    }
  }, [session]);

  const store = useMemo<Store>(
    () => ({
      session,
      update: (patch) => setSession((current) => ({ ...current, ...patch })),
      reset: () => setSession(EMPTY),
    }),
    [session],
  );

  return <Context.Provider value={store}>{children}</Context.Provider>;
}

export function useTailorSession(): Store {
  const store = useContext(Context);
  if (!store) throw new Error("useTailorSession outside TailorSessionProvider");
  return store;
}
