import { cva, type VariantProps } from "class-variance-authority";

export const buttonStyles = cva("sq-button", {
  variants: {
    variant: {
      primary: "sq-button--primary",
      secondary: "sq-button--secondary",
      quiet: "sq-button--quiet",
    },
    size: { normal: "", small: "sq-button--small" },
  },
  defaultVariants: { variant: "secondary", size: "normal" },
});
export type ButtonVariants = VariantProps<typeof buttonStyles>;
