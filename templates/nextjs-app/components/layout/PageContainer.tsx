/**
 * @component PageContainer
 * @description Standard page layout wrapper providing consistent padding, max-width, and scroll. Always wrap page content in this instead of adding padding manually.
 * @example
 * ```tsx
 * import { PageContainer } from "@/components/layout/PageContainer";
 *
 * // Standard page (max-w-7xl centered):
 * export default function OrdersPage() {
 *   return (
 *     <PageContainer>
 *       <h1 className="text-2xl font-bold">Orders</h1>
 *       <DataTable data={orders} columns={columns} />
 *     </PageContainer>
 *   );
 * }
 *
 * // Full-width page (no max-width constraint):
 * <PageContainer fluid>
 *   <FullWidthChart />
 * </PageContainer>
 * ```
 */
import { cn } from "@/lib/utils";

interface PageContainerProps {
    children: React.ReactNode;
    className?: string;
    /** Use full width instead of max-w container */
    fluid?: boolean;
}
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
