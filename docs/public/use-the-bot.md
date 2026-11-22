# Use Redstone Squid

Open Discord in a server that has Redstone Squid installed. Type `/help` and
select the bot's command to see what you can do there.

## Find a build

A **build** is a catalogue entry for a redstone design. A **record** can be
held by one or more qualifying builds, such as the smallest 3×3 piston door.

- Run `/build browse` to explore the catalogue. If you know a build's ID, pass it
  in the `id` option to open that entry directly.
- Run `/search` to look for something specific. Try `width:5` in the `query`
  field and select `builds` as the scope. Discord suggests filters as you type.
- Run `/records` to browse records and open the builds that hold them.

## Submit a build

1. Run `/build submit`. You can leave the command options blank and enter the
   details in the private form.
2. Choose the build category and fill in the sections. Credit the creators and
   include the Minecraft versions the design works in. Use **Files** to manage
   attachments. For a door, the opening size and the size of the full mechanism
   are two separate measurements.
3. Open **Preview** to check the build card, then press **Submit**. If the form
   flags missing or invalid details, fix them and submit again.

The form saves your answers as you go. Use `/build drafts` to return to an
unfinished submission.

After the bot processes the submission, it assigns a build ID and marks the
build as pending review. You can still edit it while it waits. An approved
build can hold a record if it qualifies.
