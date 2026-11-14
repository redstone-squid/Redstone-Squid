import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { CreditEditor } from "../../src/components/CreditEditor";

it("preserves provider subjects when changing contribution roles", () => {
  const onCommit = vi.fn();
  render(
    <CreditEditor
      disabled={false}
      value={[
        {
          name: "Alex",
          kind: "discord",
          username: "alex_d",
          subject: "123",
          role: "creator",
          attribution: "assumed_sender",
          source_message_id: 456,
        },
      ]}
      onCommit={onCommit}
    />,
  );
  fireEvent.change(screen.getByLabelText("Contribution"), { target: { value: "layout" } });
  fireEvent.blur(screen.getByLabelText("Contribution"));
  expect(onCommit).toHaveBeenLastCalledWith([
    expect.objectContaining({
      name: "Alex",
      kind: "discord",
      subject: "123",
      role: "layout",
      attribution: "explicit",
    }),
  ]);
});

it("clears an old provider ID when changing its namespace", () => {
  const onCommit = vi.fn();
  render(
    <CreditEditor
      disabled={false}
      value={[
        {
          name: "Alex",
          kind: "discord",
          username: "alex_d",
          subject: "123",
          role: "creator",
        },
      ]}
      onCommit={onCommit}
    />,
  );
  fireEvent.change(screen.getByLabelText("Identity"), { target: { value: "minecraft_java" } });
  fireEvent.blur(screen.getByLabelText("Identity"));
  expect(onCommit).toHaveBeenLastCalledWith([
    expect.objectContaining({
      kind: "minecraft_java",
      subject: null,
      username: null,
    }),
  ]);
});
