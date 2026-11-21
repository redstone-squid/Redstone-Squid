import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button } from "../../src/ui/react/Button";
import { Dialog } from "../../src/ui/react/Dialog";
import { Field } from "../../src/ui/react/Field";

describe("Field composition", () => {
  it("preserves external descriptions and classes alongside its own error", async () => {
    const user = userEvent.setup();
    const changed = vi.fn();
    render(
      <>
        <p id="form-help">All names are public.</p>
        <Field
          id="build-name"
          label="Build name"
          hint="Use a short name."
          error="Name is required."
          aria-describedby="form-help"
          className="wide-field"
          onChange={changed}
        />
      </>,
    );
    const input = screen.getByRole("textbox", { name: "Build name" });
    expect(input).toHaveClass("sq-input", "wide-field");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(
      "All names are public. Use a short name. Name is required.",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Name is required.");
    await user.type(input, "Door");
    expect(changed).toHaveBeenCalledTimes(4);
  });

  it("keeps caller invalid semantics and unique automatic label associations", () => {
    render(
      <>
        <Field label="First" aria-invalid="grammar" hint="Check spelling." />
        <Field label="Second" error="Server warning" aria-invalid={false} />
        <Field label="Third" />
      </>,
    );
    const first = screen.getByRole("textbox", { name: "First" });
    const second = screen.getByRole("textbox", { name: "Second" });
    expect(first.id).not.toBe(second.id);
    expect(first).toHaveAttribute("aria-invalid", "grammar");
    expect(second).toHaveAttribute("aria-invalid", "false");
    expect(screen.getByRole("textbox", { name: "Third" })).not.toHaveAttribute("aria-describedby");
  });
});

describe("Dialog composition", () => {
  it("supports workflow-controlled closure and content focus policies", async () => {
    const user = userEvent.setup();
    const changed = vi.fn();
    function Workflow() {
      const [open, setOpen] = useState(false);
      return (
        <Dialog
          open={open}
          onOpenChange={(next) => {
            changed(next);
            setOpen(next);
          }}
          title="Draft editor"
          description="Edit before saving."
          closeLabel="Close editor"
          trigger={<Button>Edit draft</Button>}
          contentProps={{
            className: "editor-dialog",
            onOpenAutoFocus: (event) => {
              event.preventDefault();
              document.getElementById("draft-title")?.focus();
            },
            onEscapeKeyDown: (event) => {
              event.preventDefault();
            },
          }}
        >
          <Field id="draft-title" label="Title" />
          <Button
            onClick={() => {
              setOpen(false);
            }}
          >
            Finish editing
          </Button>
        </Dialog>
      );
    }
    render(<Workflow />);
    const trigger = screen.getByRole("button", { name: "Edit draft" });
    await user.click(trigger);
    expect(changed).toHaveBeenLastCalledWith(true);
    expect(screen.getByRole("dialog", { name: "Draft editor" })).toHaveClass(
      "sq-dialog",
      "editor-dialog",
    );
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Finish editing" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("supports an initially open uncontrolled dialog and its close button", async () => {
    const user = userEvent.setup();
    render(
      <Dialog
        defaultOpen
        title="Notice"
        description="Read this first."
        closeLabel="Close notice"
        trigger={<Button>Open notice</Button>}
      >
        <p>Details</p>
      </Dialog>,
    );
    expect(screen.getByRole("dialog", { name: "Notice" })).toHaveAccessibleDescription(
      "Read this first.",
    );
    await user.click(screen.getByRole("button", { name: "Close notice" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

it("makes action buttons safe inside forms and respects disabled state", async () => {
  const user = userEvent.setup();
  const submitted = vi.fn((event: React.SyntheticEvent) => {
    event.preventDefault();
  });
  const clicked = vi.fn();
  render(
    <form onSubmit={submitted}>
      <Button onClick={clicked}>Action</Button>
      <Button disabled onClick={clicked}>
        Unavailable
      </Button>
      <Button type="submit">Submit</Button>
    </form>,
  );
  await user.click(screen.getByRole("button", { name: "Action" }));
  expect(submitted).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Unavailable" }));
  expect(clicked).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(submitted).toHaveBeenCalledOnce();
});
