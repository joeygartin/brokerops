import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// The shadcn/ui class helper: merge conditional class lists, with tailwind-merge
// resolving conflicting Tailwind utilities so a caller's `className` override
// always wins over a component's defaults (BOP-026).
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
