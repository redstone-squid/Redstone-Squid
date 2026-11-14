import type {
  DoorRecordEvidence,
  ExpansionBranch,
  ExpansionEvidence,
  ExpansionTiming,
  LiveDoorConfiguration,
} from "../generated/types.gen";

export type RecordConfiguration = {
  configuration: LiveDoorConfiguration;
  branch: ExpansionBranch;
  timings: ExpansionTiming[];
  excerpts: ExpansionEvidence[];
};

export function holderConfigurations(
  evidence: DoorRecordEvidence,
  buildId: number,
): RecordConfiguration[] {
  const source = evidence.sources.find((source) => source.build_id === buildId);
  if (!source || !evidence.evaluation.resolution.holder_ids?.includes(buildId)) return [];
  const selected = new Set([
    ...evidence.evaluation.holder_configuration_ids,
    ...evidence.evaluation.provisional_configuration_ids,
  ]);
  return evidence.evaluation.configurations.flatMap((configuration) => {
    if (
      configuration.build_id !== buildId ||
      configuration.branch_index === null ||
      !selected.has(configuration.id)
    )
      return [];
    // The generated schema includes unknown in these validated source-document tuples.
    const branch = source.expansion.branches?.[configuration.branch_index] as
      ExpansionBranch | undefined;
    if (!branch) return [];
    return [
      {
        configuration,
        branch,
        timings: (branch.timings ?? []) as ExpansionTiming[],
        excerpts: [
          ...(source.expansion.evidence ?? []),
          ...(branch.evidence ?? []),
        ] as ExpansionEvidence[],
      },
    ];
  });
}
