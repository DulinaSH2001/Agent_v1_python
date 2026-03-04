import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind CSS classes without conflicts */
export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}

/** Format a date to a readable string */
export function formatDate(date: Date | string, options?: Intl.DateTimeFormatOptions): string {
    return new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        ...options,
    }).format(new Date(date));
}

/** Format a number as currency */
export function formatCurrency(amount: number, currency = "USD"): string {
    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency,
    }).format(amount);
}

/** Format a number with locale separators */
export function formatNumber(n: number, decimals?: number): string {
    return new Intl.NumberFormat("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    }).format(n);
}

/** Capitalize the first letter of a string */
export function capitalize(str: string): string {
    return str.charAt(0).toUpperCase() + str.slice(1);
}

/** Truncate a string to a specified length */
export function truncate(str: string, length: number): string {
    if (str.length <= length) return str;
    return str.slice(0, length) + "...";
}

/** Wait for a specified number of milliseconds */
export function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Generate a random ID */
export function generateId(): string {
    return Math.random().toString(36).substring(2, 9);
}

/** Debounce a function call */
export function debounce<T extends (...args: unknown[]) => unknown>(fn: T, ms: number): T {
    let timer: ReturnType<typeof setTimeout>;
    return ((...args: unknown[]) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), ms);
    }) as T;
}

/** Group an array of objects by a key */
export function groupBy<T>(arr: T[], key: keyof T): Record<string, T[]> {
    return arr.reduce<Record<string, T[]>>((acc, item) => {
        const k = String(item[key]);
        (acc[k] ??= []).push(item);
        return acc;
    }, {});
}

/** Safely parse a JSON string — returns null on failure */
export function safeJson<T = unknown>(str: string): T | null {
    try {
        return JSON.parse(str) as T;
    } catch {
        return null;
    }
}
