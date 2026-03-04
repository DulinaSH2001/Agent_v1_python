"use client";

import { useState, useMemo } from "react";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ChevronUp, ChevronDown, ChevronsUpDown, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import type { TableColumn, SortConfig, SortDirection } from "@/types";

interface DataTableProps<T> {
    data: T[];
    columns: TableColumn<T>[];
    isLoading?: boolean;
    searchable?: boolean;
    searchPlaceholder?: string;
    pageSize?: number;
    className?: string;
    emptyMessage?: string;
}

function SortIcon({ direction }: { direction?: SortDirection }) {
    if (direction === "asc") return <ChevronUp className="ml-1 h-3.5 w-3.5" />;
    if (direction === "desc") return <ChevronDown className="ml-1 h-3.5 w-3.5" />;
    return <ChevronsUpDown className="ml-1 h-3.5 w-3.5 opacity-40" />;
}

export function DataTable<T extends Record<string, unknown>>({
    data,
    columns,
    isLoading = false,
    searchable = true,
    searchPlaceholder = "Search...",
    pageSize = 10,
    className,
    emptyMessage = "No results found.",
}: DataTableProps<T>) {
    const [search, setSearch] = useState("");
    const [sort, setSort] = useState<SortConfig<T> | null>(null);
    const [page, setPage] = useState(0);

    const filtered = useMemo(() => {
        if (!search.trim()) return data;
        const q = search.toLowerCase();
        return data.filter((row) =>
            columns.some((col) => {
                const val = row[col.key as keyof T];
                return String(val ?? "").toLowerCase().includes(q);
            })
        );
    }, [data, search, columns]);

    const sorted = useMemo(() => {
        if (!sort) return filtered;
        return [...filtered].sort((a, b) => {
            const av = a[sort.key as keyof T];
            const bv = b[sort.key as keyof T];
            const cmp = String(av ?? "") < String(bv ?? "") ? -1 : 1;
            return sort.direction === "asc" ? cmp : -cmp;
        });
    }, [filtered, sort]);

    const paginated = useMemo(
        () => sorted.slice(page * pageSize, page * pageSize + pageSize),
        [sorted, page, pageSize]
    );

    const totalPages = Math.ceil(sorted.length / pageSize);

    function toggleSort(key: keyof T) {
        setSort((prev) => {
            if (prev?.key === key) {
                if (prev.direction === "asc") return { key, direction: "desc" };
                return null;
            }
            return { key, direction: "asc" };
        });
        setPage(0);
    }

    if (isLoading) {
        return (
            <div className={cn("space-y-3", className)}>
                {searchable && <Skeleton className="h-9 w-64" />}
                <div className="rounded-md border">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                {columns.map((col) => (
                                    <TableHead key={String(col.key)}>
                                        <Skeleton className="h-4 w-20" />
                                    </TableHead>
                                ))}
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {Array.from({ length: 5 }).map((_, i) => (
                                <TableRow key={i}>
                                    {columns.map((col) => (
                                        <TableCell key={String(col.key)}>
                                            <Skeleton className="h-4 w-full" />
                                        </TableCell>
                                    ))}
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            </div>
        );
    }

    return (
        <div className={cn("space-y-3", className)}>
            {searchable && (
                <div className="relative w-64">
                    <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder={searchPlaceholder}
                        value={search}
                        onChange={(e) => {
                            setSearch(e.target.value);
                            setPage(0);
                        }}
                        className="pl-8"
                    />
                </div>
            )}

            <div className="rounded-md border">
                <Table>
                    <TableHeader>
                        <TableRow>
                            {columns.map((col) => (
                                <TableHead
                                    key={String(col.key)}
                                    className={cn(col.sortable !== false && "cursor-pointer select-none")}
                                    onClick={() => col.sortable !== false && toggleSort(col.key as keyof T)}
                                >
                                    <span className="flex items-center">
                                        {col.header}
                                        {col.sortable !== false && (
                                            <SortIcon
                                                direction={
                                                    sort?.key === col.key
                                                        ? sort.direction
                                                        : undefined
                                                }
                                            />
                                        )}
                                    </span>
                                </TableHead>
                            ))}
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {paginated.length === 0 ? (
                            <TableRow>
                                <TableCell
                                    colSpan={columns.length}
                                    className="h-24 text-center text-muted-foreground"
                                >
                                    {emptyMessage}
                                </TableCell>
                            </TableRow>
                        ) : (
                            paginated.map((row, i) => (
                                <TableRow key={i}>
                                    {columns.map((col) => (
                                        <TableCell key={String(col.key)}>
                                            {col.render
                                                ? col.render(row[col.key as keyof T], row)
                                                : String(row[col.key as keyof T] ?? "")}
                                        </TableCell>
                                    ))}
                                </TableRow>
                            ))
                        )}
                    </TableBody>
                </Table>
            </div>

            {totalPages > 1 && (
                <div className="flex items-center justify-between text-sm text-muted-foreground">
                    <span>
                        {sorted.length} result{sorted.length !== 1 ? "s" : ""}
                    </span>
                    <div className="flex items-center gap-2">
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setPage((p) => Math.max(0, p - 1))}
                            disabled={page === 0}
                        >
                            Previous
                        </Button>
                        <span>
                            {page + 1} / {totalPages}
                        </span>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                            disabled={page >= totalPages - 1}
                        >
                            Next
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
}
