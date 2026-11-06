# Scene assets

Generated with ImageGen for the selected miniature-house design, then resized
without regenerating artwork. All runtime images retain transparent RGBA.

- `house.png`: 1152 x 768. Route coordinates remain in the original 1536 x 1024
  coordinate system; display dimensions have not changed.
- `mole-atlas.png`: 768 x 768, four columns and four rows. Columns: idle,
  walking A, walking B, sweep. Rows: right, left, back, front. Each cell is
  192 x 192; feet are at approximately 86.6% of cell height.
- `dust-puff.png`: 128 x 128, shared by two fixed native timeline particles.
- `house-base.png`: 1152 x 768, the runtime house with foreground wall alpha
  removed. Keep `house.png` as the unmodified export source.
- `walls-atlas.png`: 1152 x 3840, five full-canvas cells: bedroom partition,
  central partition, study partition, left facade/door, right facade/door.
  Full-canvas source rectangles align with the house at fractional DPI; cropped
  destination rectangles would be snapped independently by the native renderer.

Runtime decoded RGBA totals 22.56 MiB (5.69 MiB before wall occlusion). All ten
front/rear wall nodes share one immutable 16.875 MiB atlas; the source house is
not loaded at runtime. Character frames likewise share one atlas texture.

Run `uv run --with pillow python scripts/optimize_assets.py` to re-export from
originals retained in ignored `build/asset-originals/`. Original generation
prompts remain in `generation-prompts.json`. Export removes embedded metadata
and preserves transparency. Keep cell layout and foot alignment consistent with
`src/scene.py` and `src/views.py`.

Run `uv run --with pillow python -m scripts.export_walls` from the repository
root to rebuild wall layers after changing `src/occlusion.py` or the house.
This is an exact pixel extraction, not a regenerated illustration. Coverage at
wall junctions is assigned once; inverse source-over alpha removes duplicated
translucent wall shadows. The base and five cells reassemble to the original
RGBA pixels exactly. Runtime image filtering can still affect subpixel edges.

Ground half-planes select which walls cover the character and both dust puffs.
Front/rear copies use complementary opacity on the same native movement clock.
The 1 ms depth switches happen in clear door openings; tests check both walking
frames in both directions at every switch. Stopping uses the actual foot
position, and completion follows the same rules on the return route.

Occlusion is not permission to stand inside a wall. `src/navigation.py` defines
separate floor footprints and checks the body/carried-broom contact envelope
before a route segment starts. Sweeping reserves the entire visible stroke plus
clearance; only the authored safe spots are used. The study route passes the
threshold, moves away from the central partition, then turns into the room.
The former wall-straddling and brush-on-wall probes are negative tests, not
acceptable visual examples. The preview fixture generator rejects unsafe poses.
