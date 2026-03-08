"use client";

import { DataTable } from "@/components/data/DataTable";
import { Badge } from "@/components/ui/badge";
import type { TableColumn } from "@/types";

interface Order {
    id: string;
    customer: string;
    status: "pending" | "completed" | "cancelled";
    amount: number;
    date: string;
}

const statusStyles: Record<Order["status"], string> = {
    completed: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
    pending: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
    cancelled: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

const orderColumns: TableColumn<Order>[] = [
    { key: "id", header: "Order ID" },
    { key: "customer", header: "Customer" },
    {
        key: "status",
        header: "Status",
        render: (value) => (
            <span
                className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ${statusStyles[value as Order["status"]]}`}
            >
                {String(value)}
            </span>
        ),
    },
    {
        key: "amount",
        header: "Amount",
        render: (value) => `$${Number(value).toFixed(2)}`,
    },
    { key: "date", header: "Date" },
];

interface OrdersTableProps {
    data: Order[];
}

export function OrdersTable({ data }: OrdersTableProps) {
    return (
        <DataTable
            data={data}
            columns={orderColumns}
            searchPlaceholder="Search orders..."
            pageSize={5}
        />
    );
}
