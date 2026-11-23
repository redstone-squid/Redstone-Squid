"""Identify the passage and closed door blocks in a Minecraft schematic.

Start with a three-block-wide tunnel. Inside it a piston moves one block into
the middle lane. Counting the moved blocks gives a width of one. Counting the
space between the tunnel walls gives a width of three. Both measurements are
real, but they answer different questions.

The worker supplies three snapshots: before using the control, after using it,
and after using it again. Each is a map from (x, y, z) to a Minecraft block
state. Missing entries are air. Coordinates stay in the uploaded schematic's
coordinate system so a builder can find any block in the report.

Read the detector in this order:

1. `detection.detect_cycle` tries each endpoint as the open state. A file may
   have been saved with its door closed.
2. `hallway.hallways` looks along X, Y and Z in turn. Looking down the tunnel
   lets us measure its outline in slices. Joining those slices gives a set of
   interior cells, including the two clear lanes beside the middle block.
3. `patterns.matches` tries arrangements of blocks inside that measured space.
   A central bar can match even when a path beside it stays open. For a thick
   solid plug, either outer sheet or both sheets may fit the observations.
4. `DoorCandidate` records what each interpretation used. Submissions can
   suggest dimensions on which the candidates agree and show the different
   block selections to the person reviewing the build.

All geometry here uses whole block cells. A partial block may help identify a
pattern, but a seal that depends on its exact collision shape remains uncertain.
"""
