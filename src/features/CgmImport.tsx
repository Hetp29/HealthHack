import { FileUp } from "lucide-react";
import { useRef, useState } from "react";

import { Field, Notice, Spinner, inputClass } from "@/components/glimpse/primitives";
import { Button } from "@/components/ui/button";
import * as api from "@/lib/api";
import { useRefreshAll } from "@/lib/queries";
import { useGuardedMutation } from "@/lib/useGuardedMutation";

const MAX_BYTES = 20 * 1024 * 1024;

/** Uploads a CGM export file to the real import endpoint and reports exactly what was stored. */
export function CgmImport() {
  const refreshAll = useRefreshAll();
  const input = useRef<HTMLInputElement>(null);
  const [format, setFormat] = useState<"dexcom_clarity_csv" | "libreview_csv">(
    "dexcom_clarity_csv",
  );
  const [dateOrder, setDateOrder] = useState<"" | "MDY" | "DMY">("");
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const upload = useGuardedMutation({
    mutationFn: () => api.importCgmFile(file!, format, dateOrder || undefined),
    onSuccess: () => {
      setFile(null);
      if (input.current) input.current.value = "";
      void refreshAll();
    },
  });

  const choose = (chosen: File | null) => {
    upload.reset();
    setProblem(null);
    if (chosen && chosen.size > MAX_BYTES) {
      setFile(null);
      setProblem("That file is larger than 20 MB. Export a shorter date range and try again.");
      return;
    }
    if (chosen && !/\.csv$/i.test(chosen.name)) {
      setFile(null);
      setProblem("Choose the .csv file exported from your glucose monitor's app.");
      return;
    }
    setFile(chosen);
  };
  const counts = upload.data?.counts;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field id="cgm-format" label="Where the file came from">
          <select
            id="cgm-format"
            className={inputClass}
            value={format}
            onChange={(e) => setFormat(e.target.value as typeof format)}
          >
            <option value="dexcom_clarity_csv">Dexcom Clarity export</option>
            <option value="libreview_csv">LibreView export</option>
          </select>
        </Field>
        <Field
          id="cgm-date-order"
          label="Date format in the file"
          hint="Leave on automatic unless the import reports a date problem."
        >
          <select
            id="cgm-date-order"
            className={inputClass}
            value={dateOrder}
            onChange={(e) => setDateOrder(e.target.value as typeof dateOrder)}
          >
            <option value="">Automatic</option>
            <option value="MDY">Month / day / year</option>
            <option value="DMY">Day / month / year</option>
          </select>
        </Field>
      </div>
      <Field id="cgm-file" label="Export file (.csv)" error={problem}>
        <input
          id="cgm-file"
          ref={input}
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => choose(e.target.files?.[0] ?? null)}
          className="block w-full text-sm text-muted-foreground file:mr-3 file:h-10 file:cursor-pointer file:rounded-lg file:border file:border-solid file:border-border-strong file:bg-card file:px-3 file:text-sm file:font-medium file:text-foreground"
        />
      </Field>
      {format === "libreview_csv" && (
        <p className="text-[13px] text-muted-foreground">
          LibreView exports one reading every 15 minutes. That is enough for forecasts, but too
          sparse for a past meal to count towards personal history.
        </p>
      )}
      <Button
        onClick={() => upload.mutate()}
        disabled={!file || upload.isPending}
        variant="outline"
        className="h-10"
      >
        {upload.isPending ? <Spinner /> : <FileUp className="h-4 w-4" />}{" "}
        {upload.isPending ? "Importing…" : "Import readings"}
      </Button>
      {upload.isError && (
        <Notice tone="danger" title="The file wasn't imported">
          {api.describeError(upload.error)}
        </Notice>
      )}
      {counts && (
        <Notice
          tone={counts.inserted > 0 ? "success" : "info"}
          title={
            counts.inserted > 0
              ? `${counts.inserted.toLocaleString()} readings imported`
              : "No new readings"
          }
        >
          {counts.received.toLocaleString()} rows read
          {counts.duplicates > 0 && `, ${counts.duplicates.toLocaleString()} already stored`}
          {counts.rejected > 0 &&
            `, ${counts.rejected.toLocaleString()} rejected (${Object.entries(
              counts.rejected_by_reason,
            )
              .map(([reason, n]) => `${n} ${reason.replace(/_/g, " ")}`)
              .join(", ")})`}
          .
        </Notice>
      )}
    </div>
  );
}
