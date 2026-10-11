import { Link, createFileRoute } from "@tanstack/react-router";
import { Camera, ChevronRight, Plus, Utensils } from "lucide-react";

import {
  Async,
  EmptyState,
  PageHeader,
  Panel,
  Pill,
  SkeletonLines,
} from "@/components/glimpse/primitives";
import { Button } from "@/components/ui/button";
import { isRecordedMeal, macroSummary, mealStatus } from "@/features/mealText";
import { useMeals } from "@/lib/queries";
import { useSession } from "@/lib/session";
import type { Meal } from "@/lib/types";

export const Route = createFileRoute("/meals/")({ component: MealsPage });

function MealsPage() {
  const { clock, isDemoAccount } = useSession();
  const meals = useMeals();
  return (
    <>
      <PageHeader
        title="Meals"
        description="Every meal you've logged, with its forecast and how it compared with what happened."
        actions={
          <Button asChild className="h-11 lg:hidden">
            <Link to="/meals/log">
              <Plus className="h-4 w-4" /> Log a meal
            </Link>
          </Button>
        }
      />
      <Async
        query={meals}
        skeleton={
          <Panel>
            <SkeletonLines lines={6} />
          </Panel>
        }
      >
        {({ meals: list }) => {
          if (!list.length) {
            return (
              <Panel>
                <EmptyState
                  icon={Utensils}
                  title="No meals yet"
                  action={
                    <Button asChild>
                      <Link to="/meals/log">
                        <Camera className="h-4 w-4" /> Log your first meal
                      </Link>
                    </Button>
                  }
                >
                  {isDemoAccount
                    ? "Advance the replay from the demo controls to log the recording's first meal, or add one of your own."
                    : "Photograph a meal or enter its nutrition to get a two-hour glucose forecast."}
                </EmptyState>
              </Panel>
            );
          }
          const days = new Map<string, Meal[]>();
          for (const meal of [...list].sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))) {
            const day = clock.day(meal.occurred_at);
            days.set(day, [...(days.get(day) ?? []), meal]);
          }
          return (
            <div className="space-y-5">
              {[...days.entries()].map(([day, dayMeals]) => (
                <Panel key={day} title={day} padded={false}>
                  <ul className="divide-y divide-border border-t border-border">
                    {dayMeals.map((meal) => {
                      const status = mealStatus(meal);
                      const unfinished = meal.status !== "confirmed";
                      const row = (
                        <>
                          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-muted text-muted-foreground">
                            {meal.entry_method === "photo" ? (
                              <Camera className="h-4 w-4" />
                            ) : (
                              <Utensils className="h-4 w-4" />
                            )}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-medium">
                              {clock.time(meal.occurred_at)}
                              <Pill tone={status.tone}>{status.label}</Pill>
                              {isDemoAccount && !isRecordedMeal(meal) && (
                                <Pill tone="neutral">Added by you</Pill>
                              )}
                            </span>
                            <span className="mt-0.5 block truncate text-[13px] text-muted-foreground">
                              {macroSummary(meal.confirmation)}
                            </span>
                          </span>
                          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                        </>
                      );
                      const className =
                        "flex items-center gap-3 px-5 py-3.5 hover:bg-muted/50 sm:px-6";
                      return (
                        <li key={meal.meal_id} data-testid="meal-row">
                          {unfinished ? (
                            <Link
                              to="/meals/log"
                              search={{ meal: meal.meal_id }}
                              className={className}
                            >
                              {row}
                            </Link>
                          ) : (
                            <Link
                              to="/meals/$mealId"
                              params={{ mealId: meal.meal_id }}
                              className={className}
                            >
                              {row}
                            </Link>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </Panel>
              ))}
            </div>
          );
        }}
      </Async>
    </>
  );
}
