# Data quality, transformations and exclusions

## CGMacros

1. **Interpolated glucose (critical).** Both CGM columns are linearly interpolated to 1-minute
   rows between true readings (Dexcom every 5 min, Libre every 15 min). An interpolated
   minute is computed from the NEXT real reading, i.e. it leaks the future. The adapter
   keeps only the true readings (126,259 Dexcom, 46,234 Libre) and drops
   503,566 Dexcom and 641,126 Libre interpolated rows. Method: within each contiguous
   run, keep the sampling phase whose values are whole mg/dL.
2. **Two sensors.** Dexcom G6 Pro is the primary `cgm` series (5-min cadence matches the
   contract); Libre Pro goes to `cgm_secondary` with its sensor name. `--primary-sensor libre`
   switches them. Sensor identity is stored on every reading.
3. **Header variants.** 9 different headers across 45 files (`METs` vs `Intensity` vs `Steps`,
   an extra `Sugar` / `RecordIndex`, a trailing-space `Amount Consumed `, a leading index
   column, swapped Libre/Dexcom order). The adapter normalizes by name, not position.
4. **Meals vs photos.** 1553 photos sit on rows without a meal type (end-of-meal photos);
   they are not counted as meals. 0 referenced meal photos are missing on disk.
5. **Implausible fiber.** 8 meals (participants 016 and 019) report 1,020–2,830 g of fiber,
   probably milligrams. They are **quarantined** (`implausible_fiber_g`), not unit-guessed.
   Many meals report fiber = 0 (stored as 0; it may mean "not estimated").
6. **Preserved but not in the API.** `Amount Consumed` (% of the meal eaten;
   391 meals < 100%, 131 > 100%; macros are NOT scaled by it) and `bio.csv` A1c are stored in the
   data-layer-only `source_attributes` table for later evaluation. The A1c data dictionary
   says mmol/mol, but values 4.6–8.5 are consistent with %; the unit is left unset.
   Not stored: `Meal Type`, `Sugar`, `RecordIndex`, other `bio.csv` columns, gut-health and
   microbiome tables (they remain in the source files).
7. **Coverage.** Participant 007 has a fragmented Dexcom signal (~66% coverage); 018 has two
   recording periods ~9 days apart. Both are kept and reported.
8. **Medications.** None recorded. Absent ≠ "not taking any" (T2D participants may be medicated).
9. **Activity units.** METs are stored as MET (source is MET×10 per the data dictionary);
   Fitbit `Intensity` as `fitbit_intensity_level`; `Calories (Activity)` as kcal per minute.

## MetaboNet

1. **Identity collisions.** 222 numeric subject ids occur in more than one source study. Participant
   identity is `(source_file, id)` → `metabonet_<source>_<id>`.
2. **Missing modalities by source.** 6 source studies (DCLP3, DCLP5, Flair, IOBP2,
   ShanghaiT1DM, SIESTA) have **no carbohydrate entries**, so no meal episodes. HR exists only
   in OhioT1DM, BrisT1D, HUPA-UCM and SIESTA. See `modality_coverage.csv`.
3. **Meals are carbs only.** `carbs` is zero-filled; a meal = carbs > 0. Protein, fat, fiber,
   kcal and images do not exist; they stay null.
4. **Insulin.** Bolus events (> 0) become `medications` (`insulin_bolus`, unit `U`, inferred).
   4 negative bolus values are quarantined. Basal delivery is reported but not ingested as
   events. No pharmacodynamic effect is assumed.
5. **Implausible CGM.** A handful of readings below 10 mg/dL are rejected; values 10–39 are
   kept and flagged.
6. **Train/test split.** All 132,811,004 rows have `is_test = False` (0 test rows), i.e. we
   received the training portion. 166 subjects are flagged `subject_split_across_traintest`
   (part of their data is in a held-out test split we do not have).
7. **Ingestion scope.** The full file is audited by streaming. Ingestion loads, per eligible
   subject, an 8-day window (7 days before the first eligible meal's day + that day).
   Subjects without an eligible episode are not ingested. Everything outside the window
   is excluded from the database (still counted here).

## Both datasets

- **Time — unknown timezone, NOT UTC.** Both sources give offset-naive, date-shifted local
  wall-clock times with no timezone. The data layer does not claim they are UTC:
  - every row keeps the original text in `source_timestamp`;
  - the participant is marked `time_basis = "source_local_unknown_tz"`, `timezone = null`;
  - the contract still requires an offset on `timestamp`, so it carries the source wall
    clock with a `+00:00` label as an **ordering key**, not a real UTC instant. Within a
    participant, ordering and durations are exact; across participants, or against real
    clocks, the instants are meaningless;
  - local time of day comes from `SqliteDataRepository.local_minutes_of_day()` /
    `data.timeutil.local_minutes_of_day()`, which reads the source clock for this basis
    (and returns None for real-UTC data without a timezone instead of guessing).
- **Provenance.** Every row carries `data_source="official"`, `ingestion_run_id`, and its
  participant's `source_dataset`; every run records the input SHA-256.
- **Nothing invented.** No macro, activity, medication or image value is imputed.
