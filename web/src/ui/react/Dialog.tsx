import { Dialog as Primitive } from "radix-ui";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "./Button";

type Props = {
  trigger: ReactNode;
  title: string;
  description: string;
  closeLabel: string;
  children: ReactNode;
};
export function Dialog({ trigger, title, description, closeLabel, children }: Props) {
  return (
    <Primitive.Root>
      <Primitive.Trigger asChild>{trigger}</Primitive.Trigger>
      <Primitive.Portal>
        <Primitive.Overlay className="sq-dialog-overlay" />
        <Primitive.Content className="sq-dialog">
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
