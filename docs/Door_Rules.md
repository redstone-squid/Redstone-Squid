# **Squid Records \- Rules**

New to Redstone Doors? Check out the [Door Rules for Dummies](https://docs.google.com/document/d/1a0xI0Pj6lKlULCmVr9n4GH2jnpjDXal4HeiJGhzRF0s/edit) here.

(For a better reading experience, switch to “viewing” mode.)

# 1 Structure Of The Catalogue

Records are stored in the **\#record-logs** channel of our [Discord Server](https://discord.gg/Khj8MyA).

The post contains the creator(s), a build description, working versions, additional information and links to images, videos of the build and/or world downloads.

The post should also contain a link to all previous records that were beaten, stored as a collective post in the **\#build-logs** channel.

# 2 Build Categorisation

A build is a collection of blocks and entities that serves a specific function, with one [input device](#bookmark=id.376yito2774y) to control the whole build. Every function of a build must always be achievable with a single use of the [input device](#bookmark=id.376yito2774y). (This purple text means it is a definition in [Terminology](#5-terminology)).

We currently have support and recognise records for these types of builds.

* [Piston Doors](#2.1-piston-doors)

* [Entrances](#2.2-entrances)

* [Piston Extenders](#2.3-piston-extenders)

* [Utilitie](#2.4-utilities)s

Builds can break with every Minecraft update. To acknowledge this, builds working in the latest version have an additional and may compete in additional “up-to-date” record titlesbuilds not working in the latest version have **\[BROKEN\]** appended to the title.

## 2.1 Piston Doors {#2.1-piston-doors}

A Piston Door is a type of [entrance](#2.2-entrances) where:

* the [hallway](#bookmark=id.u3zwg5scbk10) is only horizontal or vertical

* the [hallway](#bookmark=id.u3zwg5scbk10) is [CONTINUABLE](#bookmark=id.9ril221an9j8)

The name of a piston door is as follows.

| Title:  | [\<wiring placement restrictions\>](#bookmark=id.dw1u10bmv4zy) [\<animated restrictions\>](#bookmark=id.71ilnvs5ujkt) \<size\> \<type\> \<orientation\> |
| :---- | :---- |
| Subtitle: | [**\<component restrictions\>**](#bookmark=id.ny3ght6f4bhl) **[\<miscellaneous restrictions\>](#bookmark=id.34l6fv2mkho2)** |
| e.g.  | flush seamless full-sync 5x5 iris funnel trapdoor |
|  | No slimes, honey, observers, directional |

### 2.1.1 Size

**\<size\>** is the **minimal** set of dimensions that can describe the \<type\>’s size inside of the door frame. This can have one of the following forms:

1) m × n × k, where m is the width, n is the height and k is the depth.  
2) m × n.

3) m wide.  
4) n high.

e.g. “16\*16 [funnel](https://imgur.com/a/m41EKUE) door” minimally describes this funnel’s size, even though its door blocks stretch 16\*16\*8 in all 3 dimensions.

For EXPANDABLE and TILEABLE, if the door expands in only one dimension, use one of the following terms

1) m wide  
2) n high

If the door expands in two or more dimensions, use algebraic notations to describe the possible values the door size can take.

### 2.1.2 Type

**\<type\>** can be combined as long as they don’t conflict. The below conditions determine a door’s **\<type\>**:

1) **Arrangement of the door blocks:** defaults to **regular** if not specified**.**

   [REGULAR](https://imgur.com/a/3mj0xbW), [FUNNEL](https://imgur.com/a/m41EKUE), ASDJKE, [CAVE](https://imgur.com/a/Ktst2kL), CORNER, DUAL CAVE CORNER, STAIRCASE, GOLD PLAY BUTTON, VORTEX, PITCH, BAR

   VERTICAL …: The type is rotated 90°. *Only* applies to BAR, GLASS STRIPE, PITCH (YAW)

   REVERSED …: The type is rotated 180°. *Only* applies to PITCH.

   INVERTED …: The type is flipped front-to-back. *Only* applies to [FUNNEL](https://imgur.com/a/dWZ2mhh).

   DUAL …: The type is mirrored to look the same from both sides of the hallway, with both sides overlapping at least 1 door block.  
   *Note: only applies if the frame width \>= 2\.*

   VAULT: alias for DUAL FUNNEL.

   2) **Composition of the door blocks:** defaults to quartz-like blocks if not specified.

   [IRIS](https://imgur.com/a/uqqO833), ONION, [STARGATE](https://imgur.com/a/vBCTl8K), FULL STARGATE, [FULL LAMP](https://imgur.com/a/QnhJfEp), [LAMP](https://imgur.com/a/j3Jq04K), [SISSY BAR](https://imgur.com/a/eW6hmtx), CHECKERBOARD, FULL CHECKERBOARD, WINDOWS, REDSTONE BLOCK CENTER  
      SAND: Door blocks are made of sand, gravel, or concrete powder blocks.  
      GLASS: Door blocks are made of glass blocks (but not panes)  
      GLASS STRIPE: Door blocks in the center row are made of glass  
      [CENTER GLASS](https://imgur.com/a/nd7XvxD): Door blocks in the 1x1/2x2 center are made of glass  
      ALWAYS ON LAMP: Lamp stays on in both open/closed states

   3) **Arrangement of the hallway:**

   [CIRCLE](https://imgur.com/a/JNaXQBC), [TRIANGLE](https://imgur.com/a/GUWPVek), RIGHT TRIANGLE, BANANA, DIAMOND  
      SLAB-SHIFTED…: using slabs, the hallway appears to have shifted vertically by 0.5 blocks.

   4) **Composition of the hallway:** when both closed/open, depends on door block composition and ***FLUSH/HIPSTER***. Door frame defaults to quartz-like blocks if not specified. Blocks within the hallway default to air blocks when opened.

   *Note: this condition is **optional** for any quartz-like/air blocks defined if the door is non-**SUPER/FULL SEAMLESS**, blocks required to obtain **SEAMLESS/DENTLESS/MINOR DENTS ONLY** titles are mandatory regardless.*

   RAIL, DUAL RAIL

   [CARPET](https://imgur.com/a/tY7e6Ps): not compatible with skydoors. The carpet is mandatory.

   SEMI TNT: Door blocks and frame are made of TNT (mandatory)

   FULL TNT: Door blocks and hallway are made of TNT

   HIDDEN …: Hallway is made of quartz-like blocks. *Only* applies to [LAMP](https://imgur.com/a/h83Pkng) and SAND.

### 2.1.3 Orientation

**\<orientation\>** refers to the orientation of the frame:

1) SKYDOOR: if the door faces up or down, or TRAPDOOR under [certain circumstances](#bookmark=id.nfxdw85x60ra).

2) DOOR: If the door faces one of the four cardinal directions.

### 2.1.4 Wiring Placement Restriction

**\<wiring placement restrictions\>** are optional. They describe how the circuitry and frame must be positioned relative to each other and the floor/walls/ceiling.

1) SEAMLESS: Generally, a door is seamless if you can’t tell if it’s there inside the hallway.  Specifically, seamless doors must match the \<type\>’s [hallway composition](#bookmark=id.u2f6cgbeoybs) i.e. no circuitry is visible inside the hallway. The operations/states this condition is followed determines the tier of seamless.

   4 tiers where:  
   \= No circuitry AND no entities must be visible inside the hallway  
   \= No circuitry must be visible inside the hallway

|  | On front side of closed door blocks | When opened | When closed | During opening  | During closing |
| :---- | :---: | :---: | :---: | :---: | :---: |
| **1\) SUPER SEAMLESS [Eg.](https://twitter.com/i/status/1205857511930097664)** |  |  |  |  |  |
| **2\) FULL SEAMLESS** |  |  |  |  |  |
| **3\) SEMI SEAMLESS** |  |  |  |  |  |
| **4\) QUART SEAMLESS [Eg.](https://youtu.be/1S5Lk-fAu9M?t=633)** |  |  |  |  |  |

   NOTE: Certain SEAMLESS tiers are not compatible with certain \<type\>, e.g. QUART SEAMLESS and GLASS / HIDDEN SAND / HIDDEN LAMP.

2) DENTLESS: There must be no protrusions or recessions present on the hallway, except those that match the \<type\>’s [hallway composition](#bookmark=id.4ewp9eay2vdg).

   protrusions inside the hallway include:

* components with no collision effect on player movement  
  e.g. entities with no collision effect, torches, dust, opened fence gates  
* visuals

  e.g. leads, particles

* liquids, powdered snow and other protrusions with collision effects on player movement are NOT eligible.

  e.g. water, lava, repeaters, carpets, cobwebs

recessions include:

* blocks falling out of the hallway  
  e.g. recessed/missing hallway block  
* blocks with non-flat collision box which do not occupy the hallway  
  e.g. hopper, shaft of piston head, flower pot  
* hallway block replacements with a collision box that occupies the hallway are NOT eligible.

  e.g. fences, cobblestone walls

  NOTE: Including any ineligible components as protrusions or recessions means the build cannot be recognized under Sections 2.1-2.3

3) MINOR DENTS ONLY: As DENTLESS, but the following protrusions or recessions are allowed:

   allowed protrusions include:

* components with no collision effect on player movement and placed on supporting hallway blocks

  e.g. wall torches, floor dust

* visuals  
* entities, opened fence gates and components with no effect on player movement but without block support are NOT eligible for MINOR DENTS ONLY

  e.g. torches with no block support, string

  allowed recessions include:

* all recessions as wall/ceiling block replacement  
* recessions as floor block replacement are NOT eligible for MINOR DENTS ONLY

4) TRAPDOOR // FLUSH // DELUXE: 2 tiers:

   1) FULL TRAPDOOR // FLUSH // DELUXE: The door blocks protrude 0 // 1 // 2 blocks in front of the circuitry, which must be at or behind the outer surface. 2 types of trapdoors:

      1) FLOOR // CEILING TRAPDOOR: A type of skydoor.

      NOTE: in the category title this restriction will not be listed among the other restrictions. Instead SKYDOOR will be replaced with FLOOR // CEILING TRAPDOOR

      2) WALL TRAPDOOR

      NOTE: in the category title this restriction will not be listed among the other restrictions. Instead DOOR will be replaced with WALL TRAPDOORFLUSH LAYOUT: Just a WALL TRAPDOOR

   2) SEMI FLUSH // DELUXE: as above, but all circuitry can also be at or below the floor. This restriction is not compatible with skydoors.

5) HIPSTER: Note: Every FLOOR HIPSTER is SEMI FLUSH.

   1) FULL FLOOR // CEILING // WALL HIPSTER: all the circuitry must be **behind** floor // ceiling // wall level **in both stable states**.

   2) SEMI FLOOR // CEILING HIPSTER: as FULL HIPSTER, but all circuitry can also be **on** floor // ceiling level. This restriction is not compatible with skydoors.

   3) SEMI WALL HIPSTER: as FULL HIPSTER, but all circuitry can also be **on** wall level.

6) EXPANDABLE: sets of additional layers can be repeated to expand the frame to create a single larger door simultaneously with each other, with the original wiring needing only  minor changes.  
   All layers are controlled from one input block, located on the original wiring.

   1) INFINITELY EXPANDABLE: The door can be expanded indefinitely, [ignoring world border](#bookmark=id.86enyuqr85h3).

   2) FINITELY EXPANDABLE:  There is a limit (8 at minimum[**)**](https://dictionary.cambridge.org/vi/dictionary/british-grammar/future-be-going-to-i-am-going-to-work) to how much the door can be expanded

7) TILEABLE: As EXPANDABLE, but the sets of repeated layers \[or “tiles”\] are **independent** of each other.  
   All layers have their own input device, which controls only its respective layer.

   1) FULL TILEABLE: Each tile does not affect adjacent tiles.

   2) SEMI TILEABLE: Each tile affects adjacent tiles if their operations occur at the same time.

8) WEATHERPROOF: The door is guaranteed to remain functional under any weather.

This means the door should withhold all block updates, block state changes, addition or removal of blocks, entity damage, or entity spawning resulting from rain, lightning, or snow formation in any state.

9) *M* WIDE: The door is *M* blocks wide.

10) *N* HIGH: The door is *N* blocks high.



### 2.1.5 Animated Restrictions

**\<animated restrictions\>** are optional. They describe the "look" of the opening/closing sequences. Excludes any block movements on or behind the hallway.

1) SYMMETRICAL: 2 tiers:

   1) FULL SYMMETRICAL: symmetry along ALL *possible* axes, depending on \<type\> and \<wiring placement restrictions\> e.g. hipster.

   2) SYMMETRICAL: Block movement symmetry along any 1 axis

2) SYNCHRONOUS: A door is synchronous if it is **symmetrical** and follows one of the types below. Based on \<type\>, door blocks are divided into block sections, e.g. based on their distance from the closest wall when closed. 3 tiers of sync doors:  \[TODO: block section layout list\]

   1) SUPER SYNC:

      1) Is FULL SYNC

      2) If the \<type\> has one, uses a **supreme layout**.

      3) Minimal opening/closing **door block motions.** ignoring block teleportation.

   2) FULL SYNC:

      1) is FULL SYMMETRICAL *where possible* or has a **named layout.**

   e.g. a 3x3 spiral door is not full symmetrical, but is full sync.

      2) Every block in each block section retracts/extends in unison.

   3) SEMI SYNC:

      1) Every block in the last block section retracts/extends in unison.  
         OR

      2) is SYMMETRICAL

3) CLEAN: properties:

   1) **Super-seamlessly animated** : Superseamless animation with no unnecessary blocks used to ‘fake’ this animation.

   2) **Necessary movements**: to avoid "spazzy" sequences, every door block extension/retraction must progress the opening/closing sequence. No “jerking” blocks allowed \[for e.g. due to burnout\].

4) **Block Section Layouts:**

   1) **Named Layout:** A layout with a special name. for specific \<types\> and \<door sizes\>. These restrictions include: SPIRAL, SHUTTER, SCISSOR. [Full list](https://docs.google.com/document/d/19y4btgeuM7tPogXV-087_2cV-JZ32TwQkyygbrfm-hk/edit#heading=h.46mdf9718n18)

   2) **Supreme Layout:** A layout with higher priority to named layout

### 2.1.6 Component Restrictions

**\<component restrictions\>** are optional. They describe the limitations of the usage of certain redstone components.

1) RESTRICTED SLIME // HONEY // GRAVITY BLOCKS:

   1) SLIMELESSNO SLIME // HONEY // GRAVITY BLOCKS.

      1) NO STICKY PISTONS: No sticky surfaces including sticky pistons, slime blocks and honey blocks.

   2) CONTAINED SLIME // HONEY BLOCKS: The build uses slime/honey blocks and still works with any combination of air, quartz-like blocks, slime blocks, honey blocks, and immovable blocks surrounding it.

   3) ONLY WIRING SLIME // HONEY // GRAVITY BLOCKS: These blocks do not appear inside the hallway.

2) NO OBSERVERS.

3) NO NOTE BLOCKS.

4) NO CLOCKS: no use of clocks that are running permanently when the door is in the closed/open state.

5) NO ENTITIES: no use of entities, except block entities, falling block entities and item entities.

6) NO FLYING MACHINES: no use of flying machines. A flying machine is a unit of blocks, relying on an internal loop to move itself multiple blocks in at least 1 direction.

e.g. [2-way](https://static.wikia.nocookie.net/minecraft_gamepedia/images/9/94/SlimeBlockFlyingMachineAnimation300lrgr.gif) FM, and a [1-way](https://youtu.be/8_jVdWTS1Q0?t=60) FM used in a door

7) CONTAINED: The build still works with any combination of air, hand-placed quartz-like blocks, slime blocks, honey blocks, and immovable blocks surrounding it.

8) ZOMBA-: only uses quartz-like blocks, layout (sticky) pistons, hoppers, droppers, and comparators. Any items are allowed inside hoppers and droppers.

9) ZOMBI-: only uses quartz-like blocks, (sticky) pistons, cauldrons, and comparators.

10) TORCH AND DUST ONLY: only uses quartz-like blocks, layout (sticky) pistons, redstone dust, and redstone torches.

11) REDSTONE BLOCK ONLY: only uses quartz-like blocks, (sticky) pistons, and redstone blocks.

### 2.1.7 Miscellaneous Restrictions

**\<miscellaneous restrictions\>** are optional. In all of the following restrictions, assume build height and world border as non-existent and do not limit where the door can be placed.

1) NOT LOCATIONAL // DIRECTIONAL: the door functions in any given location/direction.

   1) LOCATIONAL // DIRECTIONAL WITH FIXES: the door does not function in all locations/ facing all 4 cardinal directions, but there are fixes for any given location/direction.

   2) UP-TO-DATE: the door works in the latest version of minecraft.

## 2.2 Entrances {#2.2-entrances}

An entrance is an opening that connects one room to another through a hallway along with the wiring that opens and closes the opening. With one interaction with the input device, it switches between an open state and a closed state.

It is not necessary that the seals in entrances prevent players from going through.

This record has a title and a subtitle.

Title:		**\<wiring placement restrictions\> \<size\> \<type\>**

Subtitle:	**\<component restrictions\> \<miscellaneous restrictions\>**

1) **\<size\>** is similar to [here](#bookmark=id.qqqpwejd11cl).[1](#bookmark=id.gcaq4hw724k)

2) **\<type\>** refers to the type, placement, orientation and composition of the entrance blocks.

   1) **Staircases:**

      1) HIDDEN FLOOR // WALL STAIRCASE

      2) HIDDEN SPIRAL STAIRCASE

      3) CLIFF STAIRCASE

      4) SLAB

      5) STAIRCASE TO HEAVEN

      6) POP OUT FLOOR // WALL // CEILING STAIRCASE

   2) **Other:**

      1) TREE ENTRANCE

      2) PILLAR TRAPDOOR

3) **\<wiring placement restrictions\>** are optional. They describe how the wiring and entrance blocks must be positioned relative to each other and the (outer) wall/floor/ceiling.

   1) SEAMLESS, FLUSH, HIPSTER, DELUXE, DENTLESS, EXPANDABLE, TILEABLE, ***M*** WIDE, ***N*** HIGH: similar to the piston door restrictions [here](#bookmark=id.dw1u10bmv4zy).[1](#bookmark=id.gcaq4hw724k)

   2) CONTINUABLE: the entrance pattern can be continued beyond the wiring region indefinitely.

   3) SWAPPED: The hallway is made of only quartz-like blocks when opened.  
      This restriction only applies to staircases.

4) **\<component restrictions\>** are similar to the piston door restrictions [here](#bookmark=id.ny3ght6f4bhl).[1](#bookmark=id.gcaq4hw724k)

5) **\<miscellaneous restrictions\>** are similar to the piston door restrictions [here](#bookmark=id.34l6fv2mkho2).[1](#bookmark=id.gcaq4hw724k)

## 2.3 Piston Extenders {#2.3-piston-extenders}

A piston extender is a machine that uses (sticky) pistons to extend and retract a block at a specified distance. With one interaction with the input device, it switches between an extended state and a retracted state. A piston extender must not have any wiring in front of the block extended/retracted by the piston extender.

Piston extenders have a title and a subtitle in the form.

Title:  **\<wiring placement restrictions\> \<orientation\> \<length\>** **\<type\>** piston extender

Subtitle: **\<component restrictions\> \<miscellaneous restrictions\>**

1) **\<orientation\>** 3 types:

   1) UPWARD // DOWNWARD // HORIZONTAL: There is also either a ceiling // floor // wall perpendicular to the extender’s orientation.  
      e.g. a horizontal extender has a wall in front of it

2) **\<length\>** is the distance the block moves when being extended/retracted.

3) **\<type\>** describes the materials the block and/or the wall/floor/ceiling must be made of.

4) **\<wiring placement restrictions\>** are optional. They describe how the wiring and the block (when retracted) must be positioned relative to each other and the wall/floor/ceiling.

   1) SEAMLESS: Generally, a piston extender is considered seamless if no wiring is visible. The wall/floor/ceiling must match the \<type\>’s floor/wall/ceiling composition. The operations/states this condition is followed determines the tier of seamless.

      Note: For piston extenders, SEAMLESS implies FLUSH.

       \= All circuitry/entities must be hidden behind the wall, floor or ceiling

|  | When retracted | When extended | During retraction  | During extension |
| :---- | :---: | :---: | :---: | :---: |
| **1\) SUPER SEAMLESS** |  |  |  |  |
| **2\) FULL SEAMLESS** |  |  |  |  |
| **3) SEMI SEAMLESS** |  |  |  |  |



   2) FLUSH // DELUXE: the block is flat with // 1 block in front of  the wall, floor or ceiling when retracted. All the circuitry must be behind the wall, floor or ceiling.

   3) DENTLESS, EXPANDABLE, TILEABLE, ***M*** WIDE, ***N*** HIGH: similar to the piston door restrictions [here](#bookmark=id.dw1u10bmv4zy). [1](#bookmark=id.gcaq4hw724k)

5) **\<component restrictions\>** are similar to the piston door restrictions [here](#bookmark=id.ny3ght6f4bhl).[1](#bookmark=id.gcaq4hw724k)

6) **\<miscellaneous restrictions\>** are similar to the piston door restrictions [here](#bookmark=id.34l6fv2mkho2).[1](#bookmark=id.gcaq4hw724k)

## 2.4 Utilities {#2.4-utilities}

Utilities are blocks or structures that provide a service for a player. A few examples are crafting benches and enchanting setups.

Utilities have a title and a subtitle in the form.

Title:   **\<wiring placement restrictions\>** **\<utility type\>**

Subtitle: **\<component restrictions\> \<miscellaneous restrictions\>**

1) **\<utility type\>**: the basic utility type.

2) **\<wiring placement restrictions\>** are optional. They describe how the wiring and the utility must be positioned relative to each other and/or the wall and/or floor and/or ceiling. Some restrictions have different types. Not all combinations of types are possible for each restriction. The first order restrictions include but aren’t limited to:

   1) SEAMLESS: as a general rule of thumb, a utility is considered to be seamless if no wiring is visible. There are four types of SEAMLESS:

      1) SUPERSEAMLESS: the wiring must not be visible from the outside, during the operating sequences as well as while the machine is at rest.

      2) FULL SEAMLESS: the wall, floor, or ceiling must be constructed of opaque conductive movable blocks when the machine is at rest, unless otherwise required by the utility type. The (other parts of the) wiring must be behind the wall, under the floor, or above the ceiling, depending on the utility type.

   2) FLUSH:

      1) FLUSH: the utility is flat with the wall, floor, or ceiling, depending on the utility type. All the wiring, apart from any conductive movable blocks, must be behind the wall, floor, or ceiling, depending on the utility type.

   3) M WIDE: the build is *M* blocks wide.

   4) N HIGH: the build is *N* blocks high.

   5) …

3) **\<component restrictions\>** are optional. They describe the limitations of the usage of certain redstone components. The second order restrictions include but aren’t limited to:

   1) SLIME BLOCK-LESS: the use of slime blocks is not permitted.

   2) OBSERVERLESS: the use of observers is not permitted.

   3) REDSTONE BLOCK ONLY: only the use of redstone blocks, (sticky) pistons, transparent/conductive movable blocks is permitted.

   4) ZOMBA-: only the use of conductive movable blocks, layout (sticky) pistons, hoppers, droppers, and comparators is permitted.

   5) ZOMBI-: only the use of conductive movable blocks, (sticky) pistons, cauldrons, and comparators is permitted.

4) **\<miscellaneous restrictions\>** are optional. In all of the following restrictions, exclude malfunctioning due to build height or world border.

   1) NOT LOCATIONAL // DIRECTIONAL: the door functions in any given location/direction.

      1) LOCATIONAL // DIRECTIONAL WITH FIXES: the door does not function in all locations/ facing all 4 cardinal directions, but there are fixes for any given location/direction.

   2) UP-TO-DATE: the door works in the latest version of minecraft.

# 3 Record Eligibility

Despite being in accordance with section 2, a build is eligible for Record Titles only if:

1) It uses a valid input, which is defined as:

   1) The input device is either a stone button, a wooden button, or a lever.

   2) The input device is placed on the surface area of the contraption. [example](https://imgur.com/a/EEiZneZ).

   3) The contraption functions with both direct input and repeater input.  
      Definition of Direct input:

      1) For builds with button input: The build functions with player input on activation, and repeater input on deactivation.

      2) For builds with lever input: The build functions with player input on both activation and deactivation.

      (Refer to the link for the definition of Player vs Repeater Input: [https://bugs.mojang.com/browse/MC-172213](https://bugs.mojang.com/browse/MC-172213))

   4) The input block can be remotely connected, i.e. accessible from almost everywhere, by powering the input block from the position of the input device.

   5) The input device has constant block support by any block inside the volume of the contraption.



2) It does not become unreliable over time. In particular, the following must not be used:

   1) Glitches that do not work reliably.

   2) Wiring that only works at certain times of the day.

   3) Mob AIs that do not work reliably. Note that the record builder must provide sufficient proof of reliability when mob AI is used.

   4) Wiring that breaks upon reloading the chunks containing the door, or otherwise normal gameplay  
      e.g. running clocks that break when reloading chunks. Clocks that have to be running permanently when the build is in a stable state for it to function, and these clocks can break due to normal gameplay.



3) The opening/closing operation does not require “infinite” time \- there has to be an upper limit to the amount of time it takes to finish. “Infinite” time typically happens through the use of RNG or LCG’s (e.g. randomTicks in redstone ores.).

   1) For doors and entrances that have the INFINITELY EXPANDABLE restriction: it is eligible as long as the upper limit can be expressed as a function of size.



4) It has an unlimited number of uses. Examples of ineligible build:

   1) Containers have to be manually re-filled to keep the build working.

      EXCEPTION: dispensers with flint and steel/fire charges for lighting nether portals if the **\<door type\>** requires one.

   2) It is save-state dependent.

   3) Entities/blocks/etc. move slightly after a powering of a contraption and need to be readjusted manually to keep the build working.  
      e.g. carts in cobwebs move slightly when pushed by pistons, despite not being visible.

5) It does not use non-vanilla behavior and does not require cheats to be enabled to be built or to function.

6) The building process does not require switching between different versions.

7) The build works in at least one release version of Minecraft: Java Edition.

8) During any stable states, player movements in the hallway cannot cause the build to break or fail to maintain in the corresponding stable state.

   1) EXCEPTION: the build’s functionality and stability within the hallway is completely independent of player movements in the hallway.

      e.g. a pressure plate within the hallway which does nothing when stepped on when the door is opened

9) It does not use non-isolated components which cause the build to break or fail to maintain in a certain stable state if activated.  
   eg: sculk sensors not isolated by the door's volume or wool  
   eg: pufferfish that can inflate from entities outside the door's volume

   1) EXCEPTION: the build’s functionality and stability within the hallway is completely independent of the state of the aforementioned components.  
      e.g. a sculk sensor or pufferfish is used as a clocking updater

10) For piston door and entrance Record Titles, it attains at least one tier of the SEAMLESS title.

11) It works in the overworld.

12) No loopholes allowed. Not that there are any left, mind you. We checked, there are none. You believe us, right? There’s no need to check for yourself, we were very thorough. Definitely.

# 4 Record Classification

Record Titles have the form ‘**\<base title\>** **\<record category\>**’.

1) **\<base title\>** are the fields in which a build must be optimal in order to be eligible for a Record Title. There are 5 types:

   1) **FIRST**.

   2) **FASTEST**.

   3) **SMALLEST**.

   4) **FASTEST SMALLEST**.

   5) **SMALLEST FASTEST**.

2) **\<record category\>** is the name of the build as covered in [2.1](#2.1-piston-doors), [2.2](#2.2-entrances), [2.3](#2.3-piston-extenders) and [2.4](#2.4-utilities).

## 4.1 First

A build is considered **FIRST** if there is no other build in the same \<record category\> with an earlier proveable date of completion (Date and Time in UTC). This section only applies to piston doors, entrances and utilities.

## 4.2 Smallest (Volume) {#4.2-smallest-(volume)}

A build is considered **SMALLEST** if there is no other build in the same \<record category\> with a smaller volume. If there are multiple builds tied in volume, the SMALLEST record title holder is the one with an earlier proveable date of completion.

**Volume \= width \* height \* depth** of the [circuitrywiring](#bookmark=id.9ltm257ngphf) of the build, measured along the x, y, and z dimensions, rounded up to the nearest integer. If the build is EXPANDABLE, there are additional guidelines, please scroll down three paragraphs. Both blocks and entities are considered in volume calculation, per the definition of [circuitrywiring](#bookmark=id.9ltm257ngphf).

Each dimension is cumulatively measured across every closing/opening operation and opened/closed state. Hence the Volume is the **cumulative volume** that the wiring “covers” throughout all operations and states.

**EXCEPTIONS:**

1) Exclude any door, outer surface, hallway blocks if not required to be the block that fits the \<type\>’s hallway composition in the open state, the door frame and the input device.

2) Exclude blocks or entities inside the hallway except in the open state.

3) Exclude an entity if not required to extend outside the wiring for the build to function, i.e. when surrounding the wiring with a layer of blocks, the entity does not cross the surrounding layer and the contraption is functional.

4) Exclude the section of an entity’s hitbox where the entity is only occupying a block at a float position, i.e. a block can be placed by hand at any position where it coincides with the hitbox of the floated entity.

EXPANDABLE **Volume:**

5) Colloquially, the overall Volume consists of 2 parts:

   1) **Control wiring Volume**: This is the static part of the overall Volume, Expanding the build DOES NOT increase its Volume.

   2) **Expandable layer Volume**: This is the dynamic part of the overall Volume. Expanding the build increases its Volume.

6) Describing an expandable record consists of 4 parts

   1) **Title**

   2) **Volume** as an expression

   3) **Expandable domain** of layers

   4) **Record-breaking domain** of layers

      e.g. Smallest expandable 3 high x-wide staircase door  
      Volume: 40+20x blocks		x \= 1-14  
      Breaks record at:			x \= 5-14

7) A door is considered SMALLEST if there is **at least one layer** which is smaller than other doors in the same \<record category\>.  
   e.g. The door above comes in 2 Volumes:  
   40+20x blocks, SMALLEST at x \<= 2  
   60+10x blocks, SMALLEST at x \>= 2

CONTAINED **Volume:** The diagram on the right explains how to convert an uncontained volume to a contained volume, based on the uncontained block’s position.

###

## 4.3 Fastest (Speed) {#4.3-fastest-(speed)}

**This section only applies to piston doors, entrances and piston extenders.**

A build is considered **FASTEST** if there is no other build in the same **\<record category\>** with a smaller time, using an equally or higher ranked method. If there are still multiple builds tied after all timing methods are considered, the **FASTEST** record title holder is the one with an earlier proveable date of completion.

Methods for measuring the time of different types of builds are listed below. The time should be measured in-game using gameticks or seconds, where 1 second \= 20 gameticks. **Use repeater input when timing.** Note that a record [must work with both player input and repeater input](#bookmark=id.m25j8hjr6s0g), so this is always possible. The ending time of any block or piston movement is measured using the following method:

* 0 to 2gt pulse extension: Starting time of block or piston movement \+ Pulse length received

* Standard extension: Starting time of block or piston movement \+ 2gt

* Instant retraction: Starting time of block or piston movement

* Standard retraction: Starting time of block or piston movement \+ 2gt

If a build has a range of possible speed, take the slowest possible ranked speed, using the same logic as comparing door speeds**.**

If a build has a range of possible speed for a certain timing method, for the sake of ranking purposes, its ranked speed is the slowest possible time out of all possible variations **given that the higher ranked timing methods took their respective ranked speed.**

### 4.3.1 Piston Doors & Entrances

[See image in full screen](https://i.imgur.com/buTojHK.png)

These are listed in **order of priority**:

1. OPENING TIME: time from input interaction to the end of the last block movement such that the arrangement and composition of blocks inside the hallway matches the “opened” pattern specified by the build’s **\<type\>**.

2. OPENING VISIBLE TIME (OPENING SEAMLESS TIME): time from input interaction to the last visible block movement from inside the hallway, or if the build does not have a final visible block movement due to continuously running clocks, to when the build reaches its stable open state.

   NOTE: “visible block movement” includes z-fighting block movements.

3. CLOSING TIME: time from input interaction to the last door block movement during closing.

4. CLOSING VISIBLE TIME (CLOSING SEAMLESS TIME).

5. OPENING RESET TIME: time from the end of OPENING VISIBLE TIME to the earliest time which the door can be properly closed again at any time thereafter.

   NOTE: “properly closed // opened again” requires the build to remain functional ; Reset times are negative when the build can be closed // opened before the end of OPENING // CLOSING VISIBLE TIME.

6. CLOSING RESET TIME.

### 4.3.2 Piston Extenders

Methods of measuring the time of a piston extender, ranked in **order of priority**:

1. RETRACTION TIME: time from input interaction to the last extender block movement during retraction.

2. EXTENSION TIME: time from input interaction to the last extender block movement during extension.

3. RETRACTION RESET TIME: time from the end of RETRACTION TIME to the time which the door can be extended again.

   NOTE: “retracted // extended again” requires the build to be functional and satisfy the above times claimed for record title at any time point of (de)activation thereafter; Reset times are negative when the extender can be extended//retracted before the end of RETRACTION // EXTENSION TIME.

4. EXTENSION RESET TIME.

## 4.4 Fastest Smallest

A build is considered **FASTEST SMALLEST** if it is the [FASTEST](#4.3-fastest-\(speed\)) build among the pool of [SMALLEST](#4.2-smallest-\(volume\)) builds. This section only applies to piston doors, entrances and piston extenders.

## 4.5 Smallest Fastest

A build is considered **SMALLEST FASTEST** if it is the [SMALLEST](#4.2-smallest-\(volume\)) build among the pool of [FASTEST](#4.3-fastest-\(speed\)) builds. This section only applies to piston doors, entrances and piston extenders.

# 5 Terminology {#5-terminology}

1) DOOR/ENTRANCE BLOCKS: all the blocks that are placed inside the hallway to seal it. Door blocks and entrance blocks can be used interchangeably based on context.

2) WALL/FLOOR/CEILING BLOCKS: the blocks that make up a hallway.

3) HALLWAY: the tunnel which is opened and closed by the door

4) FRAME: cross-section of hallway that contains the door blocks when closed.

5) OUTER SURFACE: a surface like a wall/floor/ceiling perpendicular to the door frame that is connected to the hallway.

6) OPEN // CLOSED STATE: stable states where none // all door blocks are inside the frame.

7) RETRACTED // EXTENDED STATE: stable states where the block is in its retracted // extended position. Equivalent to open // closed state.

8) STABLE STATE: a state which the build remains in or returns to at regular intervals indefinitely, until receiving an input signal.

9) INPUT BLOCK: the block that connects the input device to the wiring of the door. The input block is part of the wiring.

10) INPUT DEVICE: the block/circuit the player uses to operate the contraption. With one interaction with the input device, it must send one signal to the input block of the contraption such that the contraption changes from one stable state to another.

11) WIRING: the arrangement of redstone components that:

    1)  are connected by 1 input device.

    2)  if removed, would prevent the build from functioning.

12) CIRCUITRY: as WIRING, but ignores any door, outer surface, hallway blocks that fit the \<type\>’s hallway composition.

13) LAYOUT: a subset of WIRING in the open state. This arrangement:

    1) has multiple remote input locations for its pistons to be powered in sequence.

    2) is infinitely functional.

    3) is irreducible.

14) REDSTONE COMPONENT: refers to any blocks, items or entities used in the build.

15) SWAPS BLOCKS: a door has this property if it has multiple closed and open states where the positions of individual door blocks are different.

16) QUARTZ-LIKE BLOCKS: blocks that keep the build functional before and after replacing them with quartz blocks. These blocks are opaque, push/pullable, conductive and not affected by gravity (non-exhaustive).

17) OPAQUE: an opaque block blocks light.

18) CONDUCTIVE: a conductive block can transmit redstone power.

19) ISOLATED: a redstone component is isolated if its state is neither changed by actions outside of the contraptions volume nor by actions inside a doors hallway when the build is in a stable state, excluding block state changes, powering and unpowering.  
    e.g. Sculk Sensors that cannot detect activity from a door's hallway or outside a contraption's volume are isolated.  
    e.g. Pufferfish that cannot inflate from entities within a door's hallway or outside a contraption's volume are isolated.  
    e.g. Tripwire hooks that cannot receive updates from other outside tripwire hooks are isolated.

20) BLOCK MOTION: a gametick instance in which at least 1 door block moves within the hallway.

21) FUNCTIONAL: a build is functional if it can switch between all its states as designed, while maintaining all its design constraints, such as the requirements imposed by its build type, declared size and speed, and restrictions.

# 6 Community Agreed Principles

This section includes principles commonly agreed by the community or decided to remove ambiguity of regulations. **The moderation team of the document has the ultimate right to decide on cases where ambiguity of regulations arise, and on editing regulations where necessary.**

1) The functionality of a build is determined on normal condition assumptions. A non-exhaustive list of assumptions are written below.

   1) There is no lag and no unloaded chunks.

   2) There are no special weather mechanics.

      e.g. It is okay if rain changing a cauldron’s signal strength would break the build  
      e.g. It is okay if lightning being attracted by a lightning rod would break the build  
      e.g. It is not okay if lightning being attracted by a lightning rod is a required part of the build to function properly

   3) There is no world border.

      In reality, the world border and computational limits prevent any door from being expanded to infinity horizontally, and the world height limit prevents any door from being expanded infinitely vertically, but we still consider doors to be infinitely expandable if the world border is the only thing stopping it. [⤴](#bookmark=id.fyhkk573xy5d)

      This also means you cannot use the world border as a locational immoveable barrier.

   4) There are no mobs outside of the door.

2) All trivial build records (e.g. 1-block builds/single piston extender) are assumed to be claimed by the game developer.

# 7 Notes

1. **Apply common sense.** e.g. an entrance section asking to refer to a door section: remember to apply door terminology \[such as door blocks\] to entrances \[such as entrance blocks\].

2. ‘//’ means “individually applied”.  
   e.g. NO SLIME // HONEY BLOCKS means 2 separate restrictions: NO SLIME BLOCKS and NO HONEY BLOCKS.

3. In all cases where we use “remains functional”, “functions”, or any other similar wording, we impose an additional requirement that the build must not only be operable normally (the common sense definition of “function”), it must also maintain all of the design constraints, including its build type, declared size and speed, declared restrictions, etc.  
   For example, the definition of NOT LOCATIONAL is “the door functions in any given location”. Now consider a hypothetical fastest door where the opening time is 0.3s except in certain locations where it would take 0.45s, this door functions everywhere, but you should not be able to claim that the door is both NOT LOCATIONAL and that its opening speed is 0.3s.

4. The typography of this document follows this [standard](https://docs.google.com/document/u/0/d/1bok0G6tI1PzkL_pQEvgJ7TssecLN79pXXqMJ13OGMqE/edit).
