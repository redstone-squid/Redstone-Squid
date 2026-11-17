# One composition planner

All built-in targets now use shared composition. Discord V2, classic Discord,
HTML, Slack messages, Slack Home, and Slack modals provide target rules to the
same search and assembly pipeline. The former complete target compilers and
their dialect entry points have been removed.

Callers use `planning.plan` or `plan_request`. `CompositionOptions` supplies
fixed selections, explicit partitions, and search limits; custom targets can
supply `AutomaticComposition` or `FixedComposition` with their own rules.

## Caller changes

- Content is lossless unless the author supplies truncation, omission, or
  replacement policies. A budget alone does not authorize deleting content.
  Use `truncate`, `optional`, or declared alternatives when needed.
- Classic loose prose uses message content. Use an article, section, block, or
  native card to request an embed, including when contributing beside host
  content. Article and section headings occupy the embed title.
- Slack has direct block composition. Unsupported decorative colours do not
  prevent its content from rendering. Forms are inline on HTML, trigger buttons
  on Slack document surfaces, and inputs on Slack modal surfaces.
- Single-choice pickers and action menus can use shared pagination. Page cuts,
  generated action identifiers, and automatic presentation choices can differ;
  dispatch using the planned bindings and reconcile keyed page positions.
- Character-only `Paged` and `Budgeted` wrappers require a shared character
  axis. For Slack and classic Discord, use regions and resource budgets.
- Planning failures use shared search diagnostics. Inspect violations and
  completion status rather than matching legacy compiler error strings.
- `PlanCache` stores callback-free partition hints, not completed scenes or
  incremental measurement certificates. Inspect `partition_hint` and shared
  work metrics. Current callbacks and content are certified again.
  `PlanningSnapshot` retains an owner's candidate and committed live results;
  committed allocations support page turns and ordinary action callback replacement
  without repeating search. See the [caching design](../squid-ui-caching.md).
- Delivery resources retain the adapter convention `asset:<key>`. Assets with
  public URLs do not consume Discord upload capacity.
