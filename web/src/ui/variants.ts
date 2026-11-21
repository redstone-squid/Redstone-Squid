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

export const layoutStyles = cva("sq-layout", {
  variants: {
    pattern: {
      stack: "sq-stack",
      row: "sq-row",
      actions: "sq-row sq-actions",
      grid: "sq-grid",
      container: "sq-container",
    },
    gap: {
      compact: "sq-gap--compact",
      normal: "sq-gap--normal",
      section: "sq-gap--section",
    },
  },
  defaultVariants: { pattern: "stack" },
});
export type LayoutVariants = VariantProps<typeof layoutStyles>;

export const noticeStyles = cva("sq-notice", {
  variants: {
    tone: {
      info: "sq-notice--info",
      success: "sq-notice--success",
      warning: "sq-notice--warning",
      danger: "sq-notice--danger",
    },
  },
  defaultVariants: { tone: "info" },
});
export type NoticeProps = VariantProps<typeof noticeStyles> & {
  title?: string;
  role?: "status" | "alert";
};
