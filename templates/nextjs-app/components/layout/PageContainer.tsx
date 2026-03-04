import { cn } from "@/lib/utils";

interface PageContainerProps {
    children: React.ReactNode;
    className?: string;
    /** Use full width instead of max-w container */
    fluid?: boolean;
}

/**
 * PageContainer — standard page layout wrapper.
 * Wrap page content in this for consistent padding + max-width.
 *
 * @example
 * <PageContainer>
 *   <h1>Dashboard</h1>
 *   <DataTable ... />
 * </PageContainer>
 */
export function PageContainer({ children, className, fluid = false }: PageContainerProps) {
    return (
        <div
            className={cn(
                "flex flex-1 flex-col gap-6 overflow-auto p-6",
                !fluid && "mx-auto w-full max-w-7xl",
                className
            )}
        >
            {children}
        </div>
    );
}
