# Paper world capture

Use Paper's server-side world data to capture a cuboid for the player's
[synchronized draft](minecraft-schematic-upload.md). The same selection flow
works on a standalone Paper installation.

1. Implement `/squid select`, `/squid pos1`, `/squid pos2`, and `/squid selection`
   on Paper. Store the corners and world per player. Reject corners in different
   worlds, and show the selection dimensions and volume before capture.
2. Check the capture budget and loaded chunks before reading blocks. If chunks
   are missing, report how many and let the player load them before retrying.
3. Start with Paper 26.1.2's `Structure.fill` and
   `StructureManager.saveStructure`. Capture on the thread that owns the world
   and upload the resulting Java structure `.nbt` off-thread. The backend
   already accepts that format. Measure capture time before setting the size cap.
4. Compare a real Paper export against a known test region with redstone block
   states, sign text, a container with nested items, and an entity on the
   boundary. Verify an oversized selection is refused before reading blocks.

WorldEdit's clipboard API is an option if we need `.schem` output or want to
reuse a player's WorldEdit selection. Entity inclusion and Folia support still
need decisions.
