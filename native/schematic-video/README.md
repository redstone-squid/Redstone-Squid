# Native schematic video renderer

This server-side executable adapts Nucleation's simulation-video example at
v0.12.0. The engine and mesher revisions are pinned in Cargo.lock. It reconstructs
moving blocks from the simulation trace and renders their intermediate positions,
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
Use MP4 output for H.264 delivery to Discord.

This executable is an internal rendering primitive, not a public upload endpoint.
The application must enforce source authorization and schematic size limits before
launching it. Run it under process supervision with memory, wall-time, output-size,
and temporary-storage limits. Source input, pack, and output paths are local files.
