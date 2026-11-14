import { assert, describe, expect, it } from "vitest";
import { holderConfigurations } from "../../src/lib/record-configurations";
import { doorEvidence } from "../fixtures/door-record";

describe("published door configurations", () => {
  it("joins selected configurations to their saved branch, not a build sample", () => {
    const [result] = holderConfigurations(doorEvidence, 1);
    expect(result?.configuration.parameters).toEqual([["n", 6]]);
    expect(result?.configuration.volume).toBe(30);
    expect(result?.branch.volume).toBe("10ceil(n/2)");
    expect(result?.timings[0]?.expression).toBe("floor(n/2)");
    expect(result?.excerpts[0]?.source_message_id).toBe(42);
    expect(holderConfigurations(doorEvidence, 2)).toEqual([]);
  });

  it("retains all selected alternatives for one holder without including losing branches", () => {
    const evidence = structuredClone(doorEvidence);
    const configuration = evidence.evaluation.configurations[0];
    assert(configuration);
    evidence.evaluation.configurations.push({ ...configuration, id: 1, volume: 40 });
    evidence.evaluation.configurations.push({ ...configuration, id: 2, volume: 50 });
    evidence.evaluation.holder_configuration_ids = [];
    evidence.evaluation.provisional_configuration_ids = [0, 1];
    expect(holderConfigurations(evidence, 1).map((c) => c.configuration.volume)).toEqual([30, 40]);
    evidence.evaluation.resolution = { status: "unresolved", provisional_holder_ids: [1] };
    expect(holderConfigurations(evidence, 1)).toEqual([]);
  });
});
