"use server";

/**
 * Server Actions Template
 * 
 * This file contains server-side actions for data mutations.
 * Import and use these in your components directly.
 */

import { z } from "zod";
import { revalidatePath } from "next/cache";

// Example schema
const ExampleSchema = z.object({
    name: z.string().min(1, "Name is required"),
    email: z.string().email("Invalid email address"),
});

/**
 * Example server action
 * Replace this with your actual implementation
 */
export async function createExample(formData: FormData) {
    // Parse and validate input
    const validatedFields = ExampleSchema.safeParse({
        name: formData.get("name"),
        email: formData.get("email"),
    });

    if (!validatedFields.success) {
        return {
            success: false,
            errors: validatedFields.error.flatten().fieldErrors,
        };
    }

    const { name, email } = validatedFields.data;

    try {
        // TODO: Replace with actual API call or database operation
        console.log("Creating example:", { name, email });

        // Revalidate the path to refresh data
        revalidatePath("/");

        return { success: true, data: { name, email } };
    } catch (error) {
        console.error("Error creating example:", error);
        return { success: false, error: "Failed to create example" };
    }
}

/**
 * Example GET action (for server-side data fetching)
 */
export async function getItems<T>(
    endpoint: string,
    options?: RequestInit
): Promise<T> {
    const response = await fetch(endpoint, {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...options?.headers,
        },
    });

    if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
    }

    return response.json();
}

/**
 * Example POST action
 */
export async function postItem<T>(
    endpoint: string,
    data: Record<string, unknown>
): Promise<T> {
    const response = await fetch(endpoint, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify(data),
    });

    if (!response.ok) {
        throw new Error(`Failed to post: ${response.statusText}`);
    }

    return response.json();
}
