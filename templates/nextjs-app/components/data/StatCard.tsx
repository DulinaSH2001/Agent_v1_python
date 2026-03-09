/**
 * @component StatCard
 * @description KPI metric card with trend indicator. Use for dashboards with stats/KPIs. Always import this instead of creating a new card.
 * @example
 * ```tsx
 * import { StatCard } from "@/components/data/StatCard";
 * import { DollarSign, Users, ShoppingCart, TrendingUp } from "lucide-react";
 *
 * // Grid of KPI cards:
 * <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
 *   <StatCard title="Total Revenue" value="$45,231" change={12.5} changeLabel="vs last month" icon={DollarSign} />
 *   <StatCard title="Active Users" value={2350} change={8.2} icon={Users} />
 *   <StatCard title="Orders" value={1284} change={-3.1} icon={ShoppingCart} />
 *   <StatCard title="Growth" value="18.7%" change={4.0} icon={TrendingUp} />
 * </div>
 * ```
 */
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TrendingUp, TrendingDown, Minus, type LucideIcon } from "lucide-react";
import { cn, formatNumber } from "@/lib/utils";

interface StatCardProps {
    title: string;
    value: string | number;
    /** Change percentage (positive = up, negative = down) */
    change?: number;
    /** Label for the change period e.g. "vs last month" */
    changeLabel?: string;
    /** Lucide icon component */
    icon?: LucideIcon;
    className?: string;
}

export function StatCard({
    title,
    value,
    change,
    changeLabel = "vs last period",
    icon: Icon,
    className,
}: StatCardProps) {
    const displayValue =
        typeof value === "number" ? formatNumber(value) : value;

    const isPositive = change !== undefined && change > 0;
    const isNegative = change !== undefined && change < 0;
    const isNeutral = change === undefined || change === 0;

    return (
        <Card className={cn("relative overflow-hidden", className)}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                    {title}
                </CardTitle>
                {Icon && (
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-muted">
                        <Icon className="h-4 w-4 text-muted-foreground" />
                    </div>
                )}
            </CardHeader>
            <CardContent>
                <div className="text-2xl font-bold tracking-tight">{displayValue}</div>
                {change !== undefined && (
                    <div
                        className={cn(
                            "mt-1 flex items-center gap-1 text-xs",
                            isPositive && "text-green-600 dark:text-green-400",
                            isNegative && "text-red-600 dark:text-red-400",
                            isNeutral && "text-muted-foreground"
                        )}
                    >
                        {isPositive && <TrendingUp className="h-3.5 w-3.5" />}
                        {isNegative && <TrendingDown className="h-3.5 w-3.5" />}
                        {isNeutral && <Minus className="h-3.5 w-3.5" />}
                        <span>
                            {isPositive ? "+" : ""}
                            {change.toFixed(1)}% {changeLabel}
                        </span>
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
