/**
 * Common Type Definitions
 * 
 * Extend these types for your application.
 */

// Base entity with common fields
export interface BaseEntity {
    id: string;
    createdAt: Date;
    updatedAt: Date;
}

// User type
export interface User extends BaseEntity {
    email: string;
    name: string;
    avatar?: string;
    role: "admin" | "user" | "guest";
}

// API Response wrapper
export interface ApiResponse<T> {
    data: T;
    success: boolean;
    message?: string;
    errors?: Record<string, string[]>;
}

// Pagination
export interface PaginatedResponse<T> {
    data: T[];
    total: number;
    page: number;
    pageSize: number;
    totalPages: number;
}

// Common form state
export interface FormState {
    success: boolean;
    errors?: Record<string, string[]>;
    message?: string;
}

// Navigation item
export interface NavItem {
    title: string;
    href: string;
    icon?: string;
    disabled?: boolean;
    external?: boolean;
}

// Table column definition
export interface ColumnDef<T> {
    id: string;
    header: string;
    accessorKey: keyof T;
    cell?: (value: T[keyof T], row: T) => React.ReactNode;
}
