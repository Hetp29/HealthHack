import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { GlucoseUnit } from "./types";

const KEY = "glimpse.glucose-unit";
const MGDL_PER_MMOL = 18.016;

export const toUnit = (mgDl: number, unit: GlucoseUnit) =>
  unit === "mg/dL" ? mgDl : mgDl / MGDL_PER_MMOL;
export const toMgDl = (value: number, unit: GlucoseUnit) =>
  unit === "mg/dL" ? value : value * MGDL_PER_MMOL;

/** A glucose value for display. Differences (`delta`) convert the same way but keep their sign. */
export function formatGlucose(
  mgDl: number | null | undefined,
  unit: GlucoseUnit,
  delta = false,
): string {
  if (mgDl === null || mgDl === undefined || !Number.isFinite(mgDl)) return "–";
  const value = toUnit(mgDl, unit);
  const text = unit === "mg/dL" ? Math.round(value).toString() : value.toFixed(1);
  return delta && value > 0 ? `+${text}` : text;
}

interface Units {
  unit: GlucoseUnit;
  setUnit: (unit: GlucoseUnit) => void;
  glucose: (mgDl: number | null | undefined, delta?: boolean) => string;
}

const UnitsContext = createContext<Units | null>(null);

/**
 * The display unit is a preference kept on this device. The server always stores
 * mg/dL; nothing about a reading changes when this does.
 */
export function UnitsProvider({ children }: { children: ReactNode }) {
  const [unit, setUnitState] = useState<GlucoseUnit>("mg/dL");
  useEffect(() => {
    const saved = window.localStorage.getItem(KEY);
    if (saved === "mmol/L" || saved === "mg/dL") setUnitState(saved);
  }, []);
  const setUnit = useCallback((next: GlucoseUnit) => {
    setUnitState(next);
    window.localStorage.setItem(KEY, next);
  }, []);
  const value = useMemo<Units>(
    () => ({ unit, setUnit, glucose: (mgDl, delta) => formatGlucose(mgDl, unit, delta) }),
    [unit, setUnit],
  );
  return <UnitsContext.Provider value={value}>{children}</UnitsContext.Provider>;
}

export function useUnits(): Units {
  const context = useContext(UnitsContext);
  if (!context) throw new Error("useUnits needs a UnitsProvider");
  return context;
}
