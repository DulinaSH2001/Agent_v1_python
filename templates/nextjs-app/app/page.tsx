import { Header } from "@/components/layout/Header";
import { PageContainer } from "@/components/layout/PageContainer";
import { StatCard } from "@/components/data/StatCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { OrdersTable } from "@/app/orders-table";
import {
    Users,
    DollarSign,
    ShoppingCart,
    TrendingUp,
    Plus,
} from "lucide-react";

// Sample data — replace with real data fetching
const sampleOrders = [
    { id: "ORD-001", customer: "Alice Johnson", status: "completed" as const, amount: 124.99, date: "2025-03-01" },
    { id: "ORD-002", customer: "Bob Smith", status: "pending" as const, amount: 59.00, date: "2025-03-02" },
    { id: "ORD-003", customer: "Carol White", status: "completed" as const, amount: 349.50, date: "2025-03-02" },
    { id: "ORD-004", customer: "David Brown", status: "cancelled" as const, amount: 89.99, date: "2025-03-03" },
    { id: "ORD-005", customer: "Eve Davis", status: "pending" as const, amount: 210.00, date: "2025-03-03" },
    { id: "ORD-006", customer: "Frank Miller", status: "completed" as const, amount: 175.25, date: "2025-03-04" },
];

export default async function HomePage() {
    // Replace these with real data fetching:
    // const stats = await fetchStats();
    // const orders = await fetchRecentOrders();

    return (
        <div className="flex h-screen flex-col">
            <Header
                breadcrumbs={[{ label: "Dashboard" }]}
                user={{ name: "Admin User", email: "admin@example.com" }}
            />
            <PageContainer>
                {/* Page title */}
                <div className="flex items-center justify-between">
                    <div>
                        <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
                        <p className="text-sm text-muted-foreground">
                            Welcome back — here&apos;s what&apos;s happening today.
                        </p>
                    </div>
                    <Button>
                        <Plus className="mr-2 h-4 w-4" />
                        New Order
                    </Button>
                </div>

                {/* Stats row */}
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                    <StatCard
                        title="Total Revenue"
                        value="$45,231"
                        change={12.5}
                        changeLabel="vs last month"
                        icon={DollarSign}
                    />
                    <StatCard
                        title="Active Users"
                        value={2350}
                        change={8.2}
                        changeLabel="vs last month"
                        icon={Users}
                    />
                    <StatCard
                        title="Orders"
                        value={1284}
                        change={-3.1}
                        changeLabel="vs last month"
                        icon={ShoppingCart}
                    />
                    <StatCard
                        title="Growth"
                        value="18.7%"
                        change={4.0}
                        changeLabel="vs last quarter"
                        icon={TrendingUp}
                    />
                </div>

                {/* Recent Orders */}
                <div className="space-y-3">
                    <div className="flex items-center justify-between">
                        <h2 className="text-lg font-semibold">Recent Orders</h2>
                        <Badge variant="secondary">{sampleOrders.length} orders</Badge>
                    </div>
                    <OrdersTable data={sampleOrders} />
                </div>
            </PageContainer>
        </div>
    );
}
