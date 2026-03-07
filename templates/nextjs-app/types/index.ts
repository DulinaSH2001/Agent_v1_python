/**
 * Common Type Definitions
 * Extend these types for your application.
 */
import type { ReactNode } from "react";

// ─── Base ─────────────────────────────────────────────────────────────────────

export interface BaseEntity {
    id: string;
    createdAt: Date;
    updatedAt: Date;
}

// ─── Users ────────────────────────────────────────────────────────────────────

export interface User extends BaseEntity {
    email: string;
    name: string;
    avatar?: string;
    role: "admin" | "user" | "guest";
}

// ─── API ──────────────────────────────────────────────────────────────────────

export interface ApiResponse<T> {
    data: T;
    success: boolean;
    message?: string;
    errors?: Record<string, string[]>;
}

export interface PaginatedResponse<T> {
    data: T[];
    total: number;
    page: number;
    pageSize: number;
    totalPages: number;
}

// ─── Forms ────────────────────────────────────────────────────────────────────

export interface FormState {
    success: boolean;
    errors?: Record<string, string[]>;
    message?: string;
}

// ─── Navigation ───────────────────────────────────────────────────────────────

/** Navigation link — used by Sidebar and Header components */
export interface NavLink {
    href: string;
    label: string;
    /** lucide icon name — see iconMap in Sidebar.tsx */
    icon?: string;
    /** Badge count or label shown next to nav item */
    badge?: string | number;
    disabled?: boolean;
}

// ─── Tables ───────────────────────────────────────────────────────────────────

export type SortDirection = "asc" | "desc";

export interface SortConfig {
    key: string;
    direction: SortDirection;
}

export interface PaginationState {
    page: number;
    pageSize: number;
}

export interface PaginatedResult<T> {
    items: T[];
    total: number;
    page: number;
    pageSize: number;
    totalPages: number;
}

/** Column definition for DataTable<T> */
export interface TableColumn<T> {
    /** Property key of the data row */
    key: string;
    /** Column header label */
    header: string;
    /** Custom cell renderer — receives (value, row) */
    render?: (value: unknown, row: T) => ReactNode;
    /** Set to false to disable sorting for this column (default: true) */
    sortable?: boolean;
}

// ─── Select / Dropdowns ───────────────────────────────────────────────────────

export interface SelectOption<T = string> {
    label: string;
    value: T;
    disabled?: boolean;
}
