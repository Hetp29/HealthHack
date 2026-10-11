"""The FICTIONAL parts of the demonstration account.

Everything in this file is invented and is labelled as invented wherever it is
stored. It is kept apart from `source.py` on purpose: that module holds genuine
research observations, this one holds made-up profile data, and the two are never
mixed without a record of which is which.

None of it is a model input.
"""

from __future__ import annotations

DISPLAY_NAME = "Demo User (fictional)"
DISPLAY_NAMES = {"new_user": DISPLAY_NAME, "returning_user": "Returning Demo User (fictional)"}

FICTIONAL_NOTICE = "FICTIONAL DEMO RECORD - not a real person, prescription or clinical value."

#: Uses the established question ids. Measured clinical answers are left "unknown"
#: rather than invented: a made-up HbA1c beside real glucose would be exactly the
#: mixing of authentic and fictional clinical values the demo must avoid.
ONBOARDING_ANSWERS = {
    "primary_goal": "meal_responses",
    "diabetes_status": "prefer_not_to_say",
    "hba1c": "unknown",
    "fasting_glucose": "unknown",
    "monitoring_method": "cgm",
    "cgm_connect_preference": "later",
    "takes_glucose_medication": "yes",
    "wants_medication_reminders": True,
    "meals_per_day": 3,
    "meal_logging_method": "manual",
    "uses_wearable": "no",
    "wants_spike_alerts": True,
    "alert_channels": ["in_app"],
}

#: Names that cannot be mistaken for a real product. Class "other" so the education
#: content, which is about real medication classes, is never attached to them.
CURRENT_REGIMEN = {
    "name": "Demozide (fictional)", "medication_class": "other", "form": "tablet",
    "dose_amount": 500, "dose_unit": "mg", "frequency": "twice daily",
    "schedule_times": ["08:00", "20:00"], "reminders_enabled": True,
    "prescriber_note": FICTIONAL_NOTICE,
}
DISCONTINUED_REGIMEN = {
    "name": "Samplitol (fictional)", "medication_class": "other", "form": "capsule",
    "dose_amount": 10, "dose_unit": "mg", "frequency": "once daily",
    "schedule_times": [], "reminders_enabled": False, "prescriber_note": FICTIONAL_NOTICE,
}

#: Scripted, deliberate user actions on reminder slots: the second slot of every three
#: is skipped, the others are taken. Slots in the last 12 hours before the final meal
#: are left unanswered, so a genuine pending reminder exists to act on live.
SKIP_REMAINDER = 2
SKIP_MODULUS = 3
LEAVE_UNANSWERED_HOURS = 12
DOSE_ACTION_DELAY_MINUTES = 10

ALERT_COOLDOWN_MINUTES = 60
