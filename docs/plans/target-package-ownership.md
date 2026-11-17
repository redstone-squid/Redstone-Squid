# Target package ownership

Platform definitions belong to `squid-ui-discord`, `squid-ui-html`, and
`squid-ui-slack`. Core owns portable authoring, planning, and runtime contracts.
A new target must not need a change to core or a core dependency on its package.

Extract target markers together with the native definitions that use them. Keep
`RenderTarget`, `Renderable`, pagination ownership, and overflow policies shared.
Move native scene bodies and their codecs, schema declarations, and query edges
to the owning targets. Replace closed target unions and native planning branches
with explicit extension contracts. Preserve static rejection of incompatible
native nodes and validate package import isolation as well as rendering behavior.

Update callers directly; core must not re-export target definitions. Existing
unrelated working-tree changes are outside this refactor.

Adapter markers and HTML/Slack target markers now live in their target packages.
Discord dialect markers still require extracting native primitive planning. Core
scene bodies, codecs, and selectors also retain platform definitions; the boundary
described above is not yet complete.
