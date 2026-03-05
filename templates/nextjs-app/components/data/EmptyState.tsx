/**
 * @component EmptyState
 * @description Empty placeholder shown when a list or table has no data. Import this instead of building a custom empty view.
 * @example
 * ```tsx
 * import { EmptyState } from "@/components/data/EmptyState";
 * import { FileX } from "lucide-react";
 *
 * // Basic:
 * <EmptyState title="No results found" description="Try adjusting your search." />
 *
 * // With icon and action:
 * <EmptyState
 *   title="No orders yet"
 *   description="Create your first order to get started."
 *   icon={FileX}
 *   action={{ label: "Create Order", onClick: () => router.push("/orders/new") }}
 * />
 * ```
 */
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { type LucideIcon, Inbox } from "lucide-react";
import { cn } from "@/lib/utils";

interface EmptyStateProps {
    title: string;
    description?: string;
    icon?: LucideIcon;
    action?: {
        label: string;
        onClick: () => void;
    };
    className?: string;
}

export function EmptyState({
    title,
    description,
    icon: Icon = Inbox,
    action,
    className,
}: EmptyStateProps) {
    return (
        <Card className={cn("border-dashed", className)}>
            <CardContent className="flex flex-col items-center justify-center gap-3 py-12 text-center">
                <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted">
                    <Icon className="h-6 w-6 text-muted-foreground" />
                </div>
                <div className="space-y-1">
                    <p className="font-medium text-sm">{title}</p>
                    {description && (
                        <p className="text-xs text-muted-foreground max-w-xs">
                            {description}
                        </p>
                    )}
                </div>
                {action && (
                    <Button size="sm" onClick={action.onClick}>
                        {action.label}
                    </Button>
                )}
            </CardContent>
        </Card>
    );
}
