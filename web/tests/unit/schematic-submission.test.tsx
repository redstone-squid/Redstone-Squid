import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SchematicSubmissionSection from "../../src/components/SchematicSubmissionSection";
import type { SubmissionApi } from "../../src/lib/submission-api";

describe("schematic suggestions", () => {
  it("requires explicit acceptance and shows missing detections", async () => {
    const onAccept = vi.fn();
    const suggestion = { field_id: "capture_width", value: 3, evidence: "Occupied bounds" };
    const api = {
      listSchematics: vi
        .fn()
        .mockResolvedValue([
          { id: "upload", filename: "door.schem", state: "ready", primary: true },
        ]),
      schematicSuggestions: vi.fn().mockResolvedValue({
        suggestions: [suggestion],
        detections: [{ field_id: "category", status: "not_detected" }],
      }),
    } as unknown as SubmissionApi;
    render(
      <SchematicSubmissionSection
        api={api}
        draftId="draft"
        locale="en"
        disabled={false}
        labels={{ capture_width: "Build width" }}
        choices={{}}
        onMutation={async (operation) => {
          await operation();
        }}
        onAccept={onAccept}
      />,
    );
    await screen.findByText("Build width");
    expect(onAccept).not.toHaveBeenCalled();
    expect(screen.getByText(/could not be detected/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Use this suggestion" }));
    expect(onAccept).toHaveBeenCalledWith(suggestion);
  });

  it("retains upload errors through refresh and lets the user retry the same file", async () => {
    const api = {
      listSchematics: vi.fn().mockResolvedValue([]),
      uploadSchematic: vi
        .fn()
        .mockRejectedValueOnce(new Error("Upload interrupted"))
        .mockResolvedValue({ state: "pending" }),
    } as unknown as SubmissionApi;
    render(
      <SchematicSubmissionSection
        api={api}
        draftId="draft"
        locale="en"
        disabled={false}
        labels={{}}
        choices={{}}
        onMutation={async (operation) => {
          await operation();
        }}
        onAccept={vi.fn()}
      />,
    );
    const input = screen.getByLabelText("Upload schematic");
    const file = new File(["schematic"], "door.schem");
    await userEvent.upload(input, file);
    await waitFor(() => expect(api.listSchematics).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("alert")).toHaveTextContent("Upload interrupted");
    await userEvent.upload(input, file);
    await waitFor(() =>
      expect(api.uploadSchematic).toHaveBeenCalledWith(
        "draft",
        expect.any(File),
        expect.any(String),
      ),
    );
    await waitFor(() => expect(api.listSchematics).toHaveBeenCalledTimes(3));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
