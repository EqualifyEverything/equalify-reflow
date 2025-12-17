import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap font-semibold transition-all duration-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        // Primary - UIC Red (main CTA)
        default:
          "bg-uic-red text-white hover:bg-red-700 shadow-lg hover:shadow-xl",
        // Secondary - White with border
        secondary:
          "bg-white text-uic-blue border border-gray-200 hover:bg-gray-50 shadow-lg hover:shadow-xl",
        // Destructive
        destructive:
          "bg-red-600 text-white hover:bg-red-700",
        // Outline
        outline:
          "border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 hover:border-gray-400",
        // Ghost
        ghost:
          "text-gray-600 hover:bg-gray-100 hover:text-gray-900",
        // Link
        link:
          "text-uic-red underline-offset-4 hover:underline",
        // UIC Blue variant
        primary:
          "bg-uic-blue text-white hover:bg-blue-900 shadow-lg hover:shadow-xl",
      },
      size: {
        default: "h-10 px-6 py-2 text-sm rounded-lg",
        sm: "h-9 px-4 py-2 text-sm rounded-lg",
        lg: "h-12 px-8 py-3 text-base rounded-lg",
        // Pill-shaped buttons (UIC OSF style)
        pill: "px-8 py-4 text-lg rounded-full",
        "pill-sm": "px-6 py-3 text-sm rounded-full",
        icon: "h-10 w-10 rounded-lg",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button, buttonVariants }
