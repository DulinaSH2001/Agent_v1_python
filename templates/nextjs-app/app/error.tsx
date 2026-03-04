"use client";

import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AlertCircle } from "lucide-react";

interface ErrorProps {
    error: Error & { digest?: string };
    reset: () => void;
}

export default function Error({ error, reset }: ErrorProps) {
    useEffect(() => {
        console.error(error);
    }, [error]);

    return (
        <div className="flex flex-1 items-center justify-center p-6">
            <Card className="w-full max-w-md">
                <CardHeader className="text-center">
                    <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
                        <AlertCircle className="h-6 w-6 text-destructive" />
                    </div>
                    <CardTitle>Something went wrong</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col items-center gap-4 text-center">
                    <p className="text-sm text-muted-foreground">
                        {error.message || "An unexpected error occurred. Please try again."}
                    </p>
                    {error.digest && (
                        <p className="text-xs text-muted-foreground font-mono">
                            Error ID: {error.digest}
                        </p>
                    )}
                    <Button onClick={reset} className="w-full">
                        Try again
                    </Button>
                </CardContent>
            </Card>
        </div>
    );
}
