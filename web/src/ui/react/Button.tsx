import type { ComponentProps } from "react";
import { buttonStyles, type ButtonVariants } from "../variants";

export function Button({
  variant,
  size,
  className,
  type = "button",
  ...props
}: ComponentProps<"button"> & ButtonVariants) {
  return <button type={type} className={buttonStyles({ variant, size, className })} {...props} />;
}
