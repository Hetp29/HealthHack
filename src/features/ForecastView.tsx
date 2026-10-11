import { Clock3, History, Sparkles, TrendingUp, Users } from "lucide-react";

import { ForecastChart } from "@/components/charts/ForecastChart";
import {
  Disclosure,
  KeyValue,
  Notice,
  Pill,
  SourceBadge,
  Stat,
} from "@/components/glimpse/primitives";
import { formatSpan } from "@/lib/time";
import type { ForecastPoint, Outcome, Prediction } from "@/lib/types";
import { useUnits } from "@/lib/units";

const HORIZONS = ["30", "60", "90", "120"] as const;

export function PersonalizationPill({ prediction }: { prediction: Prediction }) {
  const p = prediction.personalization;
  if (!p.consent)
    return (
      <Pill tone="neutral" icon={Users}>
        General forecast · personal history is off
      </Pill>
    );
  if (p.retrieval_active) {
    return (
      <Pill tone="teal" icon={Sparkles}>
        Uses your history · {p.admissible_prior_meals} earlier meal
        {p.admissible_prior_meals === 1 ? "" : "s"}
      </Pill>
    );
  }
  return (
    <Pill tone="neutral" icon={Users}>
      General forecast · {p.admissible_prior_meals} of {p.retrieval_min_history} eligible meals
    </Pill>
  );
}

/**
 * One forecast, exactly as the server returned it. `observed` is passed only when the
 * server has released and scored the two hours after the meal.
 */
export function ForecastView({
  prediction,
  observed,
  thresholdMgDl,
}: {
  prediction: Prediction;
  observed?: ForecastPoint[] | null;
  thresholdMgDl?: number | null;
}) {
  const { unit, glucose } = useUnits();
  const { forecast, personalization: p, context_quality: context, alert } = prediction;
  const threshold = alert?.payload.threshold.mg_dl ?? thresholdMgDl ?? null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-1.5">
        <PersonalizationPill prediction={prediction} />
        {context.cgm_device && <SourceBadge source={context.cgm_device.data_class} />}
        {prediction.prediction_kind === "retrospective" && (
          <Pill tone="amber" icon={History}>
            Made after the meal time
          </Pill>
        )}
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
        <Stat
          label="Forecast peak"
          value={glucose(forecast.metrics.peak_mg_dl)}
          unit={unit}
          tone="predicted"
          hint={`${glucose(forecast.metrics.peak_delta_mg_dl, true)} ${unit} from the meal`}
        />
        <Stat
          label="Time to peak"
          value={forecast.metrics.time_to_peak_minutes}
          unit="min"
          hint="after the meal"
        />
        <div className="col-span-2 sm:col-span-1">
          <div className="text-[13px] font-medium text-muted-foreground">Forecast at</div>
          <dl className="mt-1 grid grid-cols-4 gap-2 text-center">
            {HORIZONS.map((minute) => (
              <div key={minute} className="rounded-lg bg-predicted-soft px-1 py-1.5">
                <dt className="text-[11px] text-muted-foreground">{minute} min</dt>
                <dd className="tabular text-sm font-semibold text-accent-foreground">
                  {glucose(forecast.glucose_at_minutes[minute])}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </div>

      <ForecastChart
        predicted={forecast.points}
        observed={observed ?? null}
        unit={unit}
        thresholdMgDl={threshold}
      />

      {alert && (
        <Notice tone="warning" title={alert.payload.label}>
          The forecast first reaches your alert level of {glucose(alert.payload.threshold.mg_dl)}{" "}
          {unit} about {alert.payload.predicted_crossing_minute} minutes after the meal.
          {alert.status !== "active" &&
            " This alert was recorded but not raised, because of your quiet hours, your alert cooldown, or because the forecast was made after the meal."}
          <span className="mt-1 block text-[13px] opacity-90">{alert.payload.disclaimer}</span>
        </Notice>
      )}
      {!alert && threshold !== null && (
        <p className="text-[13px] text-muted-foreground">
          The forecast stays below your alert level of {glucose(threshold)} {unit}.
        </p>
      )}

      <div className="grid gap-3 text-[13px] text-muted-foreground sm:grid-cols-2">
        <div className="flex gap-2">
          <Clock3 className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {context.latest_cgm_age_minutes === null
              ? "No glucose reading age was recorded."
              : `Latest glucose reading was ${formatSpan(context.latest_cgm_age_minutes)} old when this was made`}
            {context.cgm_readings_last_60_min !== null &&
              `, with ${context.cgm_readings_last_60_min} readings in the hour before.`}
          </span>
        </div>
        <div className="flex gap-2">
          <TrendingUp className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            No range is drawn around the curve because this model's uncertainty has not been
            calibrated.
          </span>
        </div>
      </div>

      {forecast.limitations.length > 0 && (
        <Disclosure title="Limitations of this forecast">
          <ul className="list-disc space-y-1.5 pl-5">
            {forecast.limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </Disclosure>
      )}
      <Disclosure title="How this forecast works">
        <h3 className="text-[15px] font-semibold text-foreground">
          How does GLIMPSE forecast your glucose?
        </h3>
        <p className="mt-2 text-foreground">
          GLIMPSE combines continuous glucose monitoring (CGM) data, meal nutrition information,
          meal timing, and your personal glucose-response history to predict how your glucose may
          change over the next two hours.
        </p>
        <p className="mt-2 text-foreground">
          As you accumulate eligible meal records, GLIMPSE adapts its predictions using patterns in
          your previous responses, creating a more personalized forecasting experience.
        </p>
        <p className="mt-2 text-foreground">
          GLIMPSE also brings together heart rate, activity, sleep, and other wearable measurements
          to provide a broader picture of your metabolic health.
        </p>
        <div className="mt-4 rounded-lg bg-muted/70 px-3 py-2.5 text-[13px] leading-relaxed text-muted-foreground">
          <h4 className="font-semibold text-foreground">About this prototype</h4>
          <p className="mt-1">
            Glucose forecasts are generated by a fixed research model that incorporates eligible
            personal meal-response history. Wearable measurements provide additional health context
            but are not currently inputs to the glucose prediction model. Automatic model retraining
            is a future capability.
          </p>
        </div>
        <KeyValue
          className="mt-3"
          items={[
            ["Eligible earlier meals", String(p.admissible_prior_meals)],
            ["Personal history used", p.retrieval_active ? "Yes" : "No"],
            ["Inputs you confirmed", prediction.inputs_confirmed ? "Yes" : "No"],
            ["Model", `${prediction.model.model_id} ${prediction.model.model_version}`],
            ["Feature set", prediction.model.feature_set_version],
            [
              "Uncertainty",
              forecast.uncertainty_status === "not_calibrated"
                ? "Not calibrated"
                : forecast.uncertainty_status,
            ],
          ]}
        />
      </Disclosure>
    </div>
  );
}

const REASONS: Record<string, string> = {
  missing_horizon_targets: "glucose readings were missing at one or more of the checkpoints",
  insufficient_curve_points: "there were too few glucose readings in the two hours after the meal",
  intervening_meal: "another meal fell inside the two-hour window",
  sensor_correction_in_window: "a sensor reading in the window was later corrected",
  quality_flagged_readings: "some readings in the window were flagged as unreliable",
  meal_not_eaten: "the meal was marked as not eaten",
  meal_partially_eaten: "the meal was marked as partly eaten",
  inputs_unconfirmed: "the meal's nutrition values were not confirmed",
  meal_inputs_revised: "the meal's nutrition values were changed after the forecast",
  personalization_consent_absent: "learning from past meals is switched off",
  meal_voided: "the meal was deleted",
};
export const describeReason = (reason: string) => REASONS[reason] ?? reason.replace(/_/g, " ");

/** How a forecast compared with the glucose later recorded. */
export function OutcomeView({
  outcome,
  formatTime,
}: {
  outcome: Outcome;
  formatTime: (iso: string) => string;
}) {
  const { unit, glucose } = useUnits();
  if (outcome.status === "not_applicable") {
    return (
      <Notice title="No comparison for this meal">
        You added this meal to a historical replay. The recorded participant did not eat it, so the
        glucose recorded afterwards is not this meal's response. GLIMPSE does not score it and never
        uses it as personal history.
      </Notice>
    );
  }
  if (outcome.status === "pending") {
    return (
      <Notice title="Waiting for the two hours after this meal">
        The comparison appears once the glucose readings for the full two hours are in
        {outcome.scoring_not_before ? `, from ${formatTime(outcome.scoring_not_before)}` : ""}.
      </Notice>
    );
  }
  if (outcome.status === "insufficient_data") {
    return (
      <Notice tone="warning" title="Not enough glucose readings to compare">
        {outcome.coverage
          ? `Only ${outcome.coverage.readings_in_window} of the ${outcome.coverage.required_readings} readings needed were recorded in the two hours after this meal.`
          : "Too few readings were recorded in the two hours after this meal."}
      </Notice>
    );
  }
  if (outcome.status === "voided" || !outcome.observed || !outcome.errors) {
    return <Notice title="No comparison">This meal's outcome is not being scored.</Notice>;
  }
  const { observed, errors, predicted } = outcome;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
        <Stat
          label="Forecast peak"
          value={glucose(predicted.metrics.peak_mg_dl)}
          unit={unit}
          tone="predicted"
        />
        <Stat
          label="Observed peak"
          value={glucose(observed.peak_mg_dl)}
          unit={unit}
          tone="observed"
          hint={
            observed.time_to_peak_minutes !== undefined
              ? `at ${observed.time_to_peak_minutes} min`
              : undefined
          }
        />
        <Stat
          label="Peak difference"
          value={glucose(errors.peak_error_mg_dl, true)}
          unit={unit}
          hint="forecast minus observed"
        />
        <Stat
          label="Average miss"
          value={glucose(errors.mae_mg_dl)}
          unit={unit}
          hint="across the four checkpoints"
        />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-80 text-left text-sm">
          <caption className="sr-only">
            Forecast and observed glucose at each checkpoint, in {unit}
          </caption>
          <thead className="text-xs text-muted-foreground">
            <tr>
              <th scope="col" className="py-2 pr-4 font-medium">
                After the meal
              </th>
              <th scope="col" className="py-2 pr-4 font-medium">
                Forecast
              </th>
              <th scope="col" className="py-2 pr-4 font-medium">
                Observed
              </th>
              <th scope="col" className="py-2 font-medium">
                Difference
              </th>
            </tr>
          </thead>
          <tbody className="tabular">
            {HORIZONS.filter((h) => errors.by_horizon[h]).map((h) => {
              const row = errors.by_horizon[h]!;
              return (
                <tr key={h} className="border-t border-border">
                  <th scope="row" className="py-2 pr-4 font-normal">
                    {h} min
                  </th>
                  <td className="py-2 pr-4 font-medium text-predicted">
                    {glucose(row.predicted_mg_dl)}
                  </td>
                  <td className="py-2 pr-4 font-medium text-observed">
                    {glucose(row.observed_mg_dl)}
                  </td>
                  <td className="py-2">{glucose(row.error_mg_dl, true)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {outcome.eligible_for_personalization ? (
        <Notice tone="success" title="Counts towards your personal history">
          This scored meal can inform forecasts for your later meals.
        </Notice>
      ) : (
        <Notice title="Doesn't count towards your personal history">
          {outcome.ineligibility_reasons.length
            ? `Because ${outcome.ineligibility_reasons.map(describeReason).join("; ")}.`
            : "This meal did not meet the conditions for being used in later forecasts."}
        </Notice>
      )}
    </div>
  );
}
