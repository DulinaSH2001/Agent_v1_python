"use server";

/**
 * Server Actions
 *
 * Define all data mutations here with Zod validation.
 * Import and call these directly from Server or Client components.
 *
 * Pattern:
 *   export async function doSomething(input): Promise<{ data, error }>
 */

import { z, type ZodSchema } from "zod";
import { revalidatePath } from "next/cache";

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** Validate data against a Zod schema — returns { data, error } */
export function validateData<T>(
    schema: ZodSchema<T>,
    input: unknown
): { data: T; error: null } | { data: null; error: Record<string, string[]> } {
    const result = schema.safeParse(input);
    if (!result.success) {
        return { data: null, error: result.error.flatten().fieldErrors as Record<string, string[]> };
    }
    return { data: result.data, error: null };
}

/** Stringify an unknown error for user display */
export function handleApiError(error: unknown): string {
    if (error instanceof Error) return error.message;
    if (typeof error === "string") return error;
    return "An unexpected error occurred.";
}

// ─── Generic fetch helpers ────────────────────────────────────────────────────

/** Fetch data from an external API (use in Server Components or Server Actions) */
export async function fetchItems<T>(
    url: string,
    options?: RequestInit
): Promise<{ data: T | null; error: string | null }> {
    try {
        const response = await fetch(url, {
            ...options,
            headers: {
                "Content-Type": "application/json",
                ...options?.headers,
            },
        });

        if (!response.ok) {
            return { data: null, error: `Request failed: ${response.status} ${response.statusText}` };
        }

        const data = (await response.json()) as T;
        return { data, error: null };
    } catch (err) {
        return { data: null, error: handleApiError(err) };
    }
}

/** POST data to an external API */
export async function postItem<T>(
    url: string,
    body: Record<string, unknown>,
    options?: RequestInit
): Promise<{ data: T | null; error: string | null }> {
    try {
        const response = await fetch(url, {
            method: "POST",
            ...options,
            headers: {
                "Content-Type": "application/json",
                ...options?.headers,
            },
            body: JSON.stringify(body),
        });

        if (!response.ok) {
            return { data: null, error: `Request failed: ${response.status} ${response.statusText}` };
        }

        const data = (await response.json()) as T;
        return { data, error: null };
    } catch (err) {
        return { data: null, error: handleApiError(err) };
    }
}

// ─── Example CRUD Actions ─────────────────────────────────────────────────────
// Replace these with your real implementation.

const ExampleSchema = z.object({
    name: z.string().min(1, "Name is required"),
    email: z.string().email("Invalid email address"),
});

export type ExampleInput = z.infer<typeof ExampleSchema>;

export async function createExample(formData: FormData) {
    const { data, error } = validateData(ExampleSchema, {
        name: formData.get("name"),
        email: formData.get("email"),
    });

    if (error) return { success: false, errors: error };

    try {
        // TODO: Replace with actual database operation
        console.log("Creating:", data);
        revalidatePath("/");
        return { success: true, data };
    } catch (err) {
        return { success: false, error: handleApiError(err) };
    }
}

export async function updateExample(id: string, formData: FormData) {
    const { data, error } = validateData(ExampleSchema, {
        name: formData.get("name"),
        email: formData.get("email"),
    });

    if (error) return { success: false, errors: error };

    try {
        // TODO: Replace with actual database operation
        console.log("Updating:", id, data);
        revalidatePath("/");
        return { success: true, data };
    } catch (err) {
        return { success: false, error: handleApiError(err) };
    }
}

export async function deleteExample(id: string) {
    try {
        // TODO: Replace with actual database operation
        console.log("Deleting:", id);
        revalidatePath("/");
        return { success: true };
    } catch (err) {
        return { success: false, error: handleApiError(err) };
    }
}
