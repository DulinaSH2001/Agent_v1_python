import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
    return (
        <div className="flex flex-1 flex-col gap-6 p-6 animate-fade-in">
            {/* Page heading skeleton */}
            <div className="space-y-3">
                <Skeleton className="h-9 w-64" />
                <Skeleton className="h-5 w-96 max-w-full" />
            </div>

            {/* Content cards skeleton */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {Array.from({ length: 3 }).map((_, i) => (
                    <div key={i} className="rounded-xl border bg-card p-6 space-y-4">
                        <Skeleton className="h-5 w-32" />
                        <Skeleton className="h-4 w-full" />
                        <Skeleton className="h-4 w-3/4" />
                        <div className="flex gap-2 pt-2">
                            <Skeleton className="h-6 w-16 rounded-full" />
                            <Skeleton className="h-6 w-20 rounded-full" />
                        </div>
                    </div>
                ))}
            </div>

            {/* Content area skeleton */}
            <div className="rounded-xl border bg-card p-6 space-y-4">
                <Skeleton className="h-5 w-40" />
                <div className="space-y-3">
                    {Array.from({ length: 4 }).map((_, i) => (
                        <Skeleton key={i} className="h-4 w-full" />
                    ))}
                </div>
            </div>
        </div>
    );
}
