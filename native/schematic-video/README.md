# Native schematic video renderer

This server-side executable adapts Nucleation's simulation-video example at
v0.12.0 and uses the v0.12.2 engine. The engine and mesher revisions are pinned in
Cargo.lock. It reconstructs moving blocks from the simulation trace and renders their intermediate positions,
including retracting piston heads. FFmpeg must be on PATH.

Build and test:

```sh
cargo build --locked --release --manifest-path native/schematic-video/Cargo.toml
cargo test --locked --manifest-path native/schematic-video/Cargo.toml
```

Render a structure exported by `TickSimulation.gametest_snbt`:

```sh
native/schematic-video/target/release/squid-schematic-video \
  pack.zip structure.snbt result.mp4 --in-world \
  --ticks 120 --click 3,1,2@20 --click 3,1,2@80 \
  --width 640 --height 360 --fps 30 --orbit 180
```

Ticks play at 20 per second, independently of output frame rate. The orbit spans
the full clip; use `--orbit 0` for a fixed camera. Framing includes the whole run.
Use MP4 output for H.264 delivery to Discord. Use `--static` to disable simulation
while retaining camera motion. Videos visualize the simulator; they do not prove
in-game timing or behavior for unsupported Minecraft mechanics.

This executable is an internal rendering primitive, not a public upload endpoint.
The application must enforce source authorization and schematic size limits before
launching it. Run it under process supervision with memory, wall-time, output-size,
and temporary-storage limits. Source input, pack, and output paths are local files.

## Deployment and requests

Apply Alembic migrations, then build the worker image with Docker target
`runtime-video` and build argument `WITH_SCHEMATICS=1`. This target includes the
native helper, FFmpeg, and Mesa software rendering. The API and bot can keep their
existing images. For local development, set `SQUID_SCHEMATIC_VIDEO_HELPER_PATH`
to the absolute path of the built executable.

Enable `SQUID_SCHEMATIC_VIDEO_ENABLED=true` on the API, bot, and worker. Configure
`SQUID_SCHEMATIC_RENDER_PACK_PATH` (or the verified remote pack URL and SHA-256)
and `SQUID_SCHEMATIC_RENDER_PUBLIC_BASE_URL` as for image previews. PNG preview
publication does not need to be enabled. The helper must be installed on workers;
clients stage their verified resource pack with each request.

Authenticated clients submit to
`POST /v1/builds/{build_id}/schematics/{schematic_id}/videos`, poll the returned
`status_url`, then fetch `download_url`. The OpenAPI contract describes the camera
and simulation options. An `Idempotency-Key` replays an equivalent submission.
A fixed camera sets `camera.mode` to `fixed`; a static orbit sets
`simulation.enabled` to `false` and omits interactions.

Discord's schematic tools offer Render video and Check video. Each activation
line is `tick x y z`; a bare tick requests automatic input selection. The bot
attaches the completed MP4 privately, or links to the authenticated API download
if Discord's attachment limit is smaller than the result. If an interaction
expires or delivery fails, Check video can retrieve the stored result without
rendering again. Background delivery runs while the bot process is alive.

Video requests are capped at two pending jobs per account. Output retention uses
`SQUID_SCHEMATIC_JOB_RETENTION_HOURS` (24 hours by default). Expiry removes our
stored copy, not an attachment already delivered to Discord. Videos are not added
to build media automatically. The usual schematic source-access rules still apply
when reading a result.
