import { Dialog as Primitive } from "radix-ui";
import { X } from "lucide-react";
import { cx } from "class-variance-authority";
import type { ComponentProps, ReactNode } from "react";
import { Button } from "./Button";

type Props = Omit<ComponentProps<typeof Primitive.Root>, "children"> & {
  trigger: ReactNode;
  title: string;
  description: string;
  closeLabel: string;
  children: ReactNode;
  contentProps?: Omit<ComponentProps<typeof Primitive.Content>, "children" | "asChild">;
};
export function Dialog({
  trigger,
  title,
  description,
  closeLabel,
  children,
  contentProps,
  ...rootProps
}: Props) {
  return (
    <Primitive.Root {...rootProps}>
      <Primitive.Trigger asChild>{trigger}</Primitive.Trigger>
      <Primitive.Portal>
        <Primitive.Overlay className="sq-dialog-overlay" />
        <Primitive.Content {...contentProps} className={cx("sq-dialog", contentProps?.className)}>
          <div className="sq-row sq-between">
            <Primitive.Title>{title}</Primitive.Title>
            <Primitive.Close asChild>
              <Button variant="quiet" aria-label={closeLabel}>
                <X size={18} aria-hidden="true" />
              </Button>
            </Primitive.Close>
          </div>
          <Primitive.Description className="sq-muted">{description}</Primitive.Description>
          {children}
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}
