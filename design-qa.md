# Mole WoxView verification

## Current revision — reject wall occupancy and unsafe strokes, 2026-11-07

The preceding acceptance was insufficient: the off-route `center-behind` probe
placed the body inside a solid wall, and the `study-side-front` probe put the
extended broom on the wall. They must be rejected, not accepted merely because
the draw order behaves as specified. Those examples are now negative regression
tests and are no longer emitted as valid preview screenshots.

Added floor-occupancy checks independent of occlusion, with scaled contact
envelopes for the body and carried broom. Every walking segment is checked
before state changes; sweeping additionally reserves the whole visible stroke
away from wall silhouettes. No Pillow dependency or per-frame collision work
was added to runtime. Sprite size and foot anchor are shared with the renderer.

This also found a real route issue: the old horizontal study-entry turn was too
wide for the central-wall clearance. The route now stays forward-facing through
the threshold, moves north/east into clear floor, and only then turns across the
study. A second bend rounds the cap before going south. Short segments retain
the same walking speed instead of the former 0.6-second minimum. The living-room
sweep spot moved inward to clear the complete stroke, including the idle pose.

Verified in `build/clearance-fixtures/`:

- 40 tests passed; Ruff checks/format and mypy passed. Both user-reported invalid
  poses and the former horizontal doorway shortcut are explicitly rejected.
  Unsafe routes leave the previous position and pending route unchanged.
- Independent alpha checks cover idle/stopped and both walking poses at 101
  positions on each directed edge, against solid floor footprints. Complete
  idle/sweep silhouettes at all five sweep spots clear the wall artwork.
- 144 valid static captures: 120 actual route positions, 20 light/dark sweep
  poses, and four mid-walk stop/failure poses. The fixture generator refuses
  unsafe floor positions/strokes. All six `clearance-review-*.png` sheets were
  inspected; no artificial wall-straddling probes are included.
- 24 native edge-playback captures, five active sweep/dust captures and the
  compact study turn were inspected in `clearance-native-review.png`. The
  completion-return fixture was replayed and its final porch state checked.
- `safe-positions.png` shows the actual adjusted doorway approach, study sweep
  and living-room sweep. The local archive was rebuilt and checked against all
  workspace source/art bytes and ZIP CRC.

WoxView and the existing wall assets were unchanged in this correction. Native
validation used Windows and simulated cleanup; no cleanup/install was executed.

## Superseded revision — wall depth only, 2026-11-07

The rendering checks below passed, but their pose acceptance was insufficient
and is superseded by the occupancy/stroke checks above. The study wall cap hides the part of
the body/broom behind it. Front-facing positions remain in front of walls. Wall
tops/faces and entrance panels are extracted from the original house; no WoxView
or SDK changes are required. Five wall silhouettes use foot-ground half-planes,
including both faces of the L-shaped study partition.

Front/rear wall instances share a full-canvas atlas and complementary native
opacity timelines synchronized with the character. The original bitmap is kept
as export input; inverse source-over alpha builds a complementary runtime base.
Reassembling the base and five cells reproduces the source RGBA exactly, without
duplicating translucent shadows. Full-canvas cells share the base destination
box, avoiding independent snapping of cropped texture rectangles. Raster
filtering at subpixel boundaries is not a pixel-identical screenshot guarantee.

The study doorway center moved from x=902 to x=894 (original 1536-wide units).
This clears the broom in both walking frames at the depth switch. No extra
waypoint, pause, heartbeat, per-frame snapshot, or runtime imaging dependency
was introduced. A 1 ms native opacity step switches only in clear door openings.
Stop/failure use the interpolated feet; successful return uses the same depth
rules along reverse routes.

Validation and evidence under `build/native-fixtures/`:

- 38 Python tests passed; Ruff checks/format and mypy passed. New checks cover
  pixel reconstruction, doorway transparency, front/back classification, every
  directed edge at 101 positions, both walking frames at each depth switch,
  complementary clocks, the reported study cap, and stop/failure mid-edge.
- 134 static native captures: 100 route positions, 10 sweep poses, 20 light/dark
  depth probes and four frozen positions. All six `occlusion-review-*.png` sheets
  were inspected. Off-route depth probes are deliberate overlap stress cases.
- 20 native edge-playback captures without intermediate plugin publications,
  plus active dust and a compact view. `occlusion-native-review.png` was inspected.
- Completion return was replayed through 42 native captures; the final porch
  pose and frozen amount/timer were inspected in `exit-finished.png`.
- `occlusion-comparison.png` shows the same study-cap pose before/after depth
  ordering. Empty-house and close-up captures were checked in both themes.
- Native 30-second playback: 1,420 warm measured frames, zero warm image uploads
  or evictions, 11,238 cache hits, zero tree updates after mount, zero hidden
  frames. Frame work P50/P95/P99: 1.710/2.884/3.631 ms. Final texture residency
  23,658,496 bytes (22.56 MiB); the full-canvas wall atlas adds 16.875 MiB over the
  former 5.69 MiB scene. This remains below the host's 32 MiB texture budget.

The local archive was rebuilt with the runtime base/atlas and checked against
the workspace sources/assets and ZIP CRC. No actual cleanup, plugin installation,
store upload, or commit was performed. macOS/Linux were not exercised natively.

Reproduce: `uv run python -m tests.make_occlusion_fixture build/native-fixtures`,
then the native preview harness with `occlusion`, `occlusion-motion`, and
`occlusion-validation`. The existing `exit` fixture checks completion return.

## Previous revision — preserve the completion amount row

The amount row no longer disappears when the CLI supplies no numeric total.
The installed Windows CLI's `Show-CleanupSummary` has distinct numeric,
owner-tool-only and already-clean branches. The latest operation log contains
owner-tool activity without sized removals, consistent with the owner-only
branch. No cleanup was executed to reproduce this.

Known totals stay unchanged; an explicit "no additional space freed" summary
maps to `0MB`; unreported totals display `— MB` without guessing a value.
Locale-specific decimal/group separators from PowerShell's size formatter are
also accepted. The caption below remains just "清理完成" / "Cleanup complete".

29 tests passed, including streamed real-format summary text through the runner
and into the final view. Ruff/format and mypy passed. Native screenshots
`amount-reported.png`, `amount-zero.png`, `amount-unknown.png` in
`D:/dev/Wox.Plugin.Mole/build/preview/` were inspected; all preserve the quantity,
caption and frozen elapsed rows. Native error output was empty. The package was
rebuilt and its CRC and updated source contents verified.

## Previous revision — concise completion caption

The final caption now contains only the localized "Cleanup complete" text
(Chinese: "清理完成"). Removed the extra amount-source wording from the preview;
the reported amount and frozen timer remain. If no amount was reported, the
completion heading appears once with no additional subtitle.
`D:/dev/Wox.Plugin.Mole/build/preview/complete-simple.png` was inspected in the
native renderer. All 27 tests, Ruff/format, mypy and archive CRC/source checks
passed; the local package was rebuilt. No actual cleanup was executed.

## Previous revision — completion return, 2026-11-06

Final result: passed. Successful cleanup now immediately freezes the result and
elapsed time, then Mole follows doorway edges back to the porch and stands
facing the viewer. Completion during a walk finishes its current edge before
returning, so no position jump or diagonal shortcut is introduced. A separate
visual task continues updates after the cleanup process exits; leaving the
query pauses it, reentry resumes it, and restarting/unloading cancels it.
Failure and manual stop retain their existing in-place behavior.

Native captures in `D:/dev/Wox.Plugin.Mole/build/preview/` were inspected:
`exit-000.png` begins leaving the bedroom, `exit-008.png` crosses the hall,
`exit-018.png` approaches the front door, and `exit-finished.png` shows the
front-facing idle pose outside. The simulated result and 00:20 elapsed value
stay unchanged during the entire walk. Native renderer error output was empty.

Validation: 27 Python tests passed, including all-room return paths, mid-edge
completion, early completion, post-process animation, query reentry and
restart/unload cancellation. Ruff checks/format and mypy passed. The local
`.wox` archive was rebuilt. No live cleanup or installation was performed.

## Previous revision — four visual changes, 2026-11-06

Final result: passed for the four requested visual changes. This section
supersedes the original character size, directional-art limitation and test
count in the historical report below.

- Reduced the character cell from 104 to 56 logical units (46% smaller in each
  dimension). The back/front walking captures show clearance in the narrow
  study doorway; the ready capture shows the smaller character on the porch.
- Generated and integrated a separate transparent front/back atlas, selected
  by the dominant direction of travel. Both atlases remain mounted so Wox
  retains decoded artwork while switching directions.
- Added two transparent dust puffs at the broom tip at each stroke's end.
  Native transforms expand and drift them over 0.85 seconds while fading.
  Repeated updates within one stroke do not create duplicate bursts.
- Removed the quiet-process waiting message. The current item remains visible;
  elapsed digits now roll upward individually and the colon pulses. Stopping
  freezes the timer and removes all remaining dust.

Native evidence in `D:/dev/Wox.Plugin.Mole/build/preview/`:

- `walking-back.png`, `walking-front.png`: new views at the study doorway.
- `ready-light.png`, `ready-dark.png`, `sweeping-light.png`, `sweeping-dark.png`:
  final scale, alpha edges, text contrast and sweep effects in both themes.
- `ready-compact.png`, `sweeping-compact.png`: 600 × 400 logical content fit.
- `effects-000.png`, `effects-001.png`, `effects-003.png`: 00:59, the native
  intermediate digit roll, and 01:00 with dust drifting from the broom tip.
- `effects-stopped.png`: elapsed time frozen at 01:01 and no remaining dust.
- `motion-003.png`, `motion-007.png`, `motion-025.png`, `motion-043.png`:
  successive live native updates for entrance, room navigation and sweeping.
- `complete-light.png`, `failed-dark.png`: terminal captions and timer fit.

All listed captures were reopened and inspected. The native timer check caught
a centered label expanding to fill its row and pushing digits offscreen; a
shrink-wrapped label corrected this before the final capture and packaging.
The original house/reference comparison still applies; the requested smaller
character intentionally supersedes its earlier emphasis.

Validation: 22 Python tests passed; Ruff checks/format and mypy passed. Native
parser/render runs produced no errors. All four PNGs decode with transparent
corners. The rebuilt 4,264,260-byte archive passed CRC, isolated Wox Python host
loading, preview construction and all four packaged asset-path checks.
Generation prompts are saved in `image/scene/generation-prompts.json`.

No live cleanup or installation was performed. Verification uses the local Wox
renderer and simulated cleanup output; macOS and an installed-plugin end-to-end
run remain unverified. Walking still uses two alternating poses per direction
with continuous native position interpolation.

## Historical initial implementation report

final result: passed

Scope: the native cleanup preview and its Python action/process adapter. No live
cleanup was executed, and the plugin was not installed into the user's Wox.

## Visual evidence

- Source: `C:/Users/qianl/.codex/generated_images/01a1b10b-4849-7593-9a09-a5c44659fdd2/exec-52e1b4e1-33a5-40d9-b209-adb924fc8d8e.png`
  (1536 × 1024 generated reference, including launcher chrome).
- Implementation: `D:/dev/Wox.Plugin.Mole/build/preview/ready-light.png` and
  `ready-dark.png`, `sweeping-light.png`, `sweeping-dark.png`,
  `complete-light.png`, `failed-dark.png` in the same directory.
- Native viewport: 760 × 470 logical units; captured pixels 1900 × 1175 at
  Windows display scale 2.5. Additional content viewports of 600 × 400 are in
  `ready-compact.png` and `sweeping-compact.png` (inside the same capture window).
- The final ready render and source were opened together in one comparison
  input. Comparison concerns the house, character and caption region; the mock's
  search bar and toolbar belong to Wox and are intentionally outside the preview.
  The source is a generated mock with no known logical pixel density, so this
  is a proportional visual comparison, not a pixel-difference score.
- Focused checks: sprite feet/broom clipping at the entrance and in rooms,
  dark-background edges, caption readability and compact-height fit. These
  details are readable in the full-resolution captures; no separate crop needed.
- Motion evidence: `motion-001.png`, `motion-003.png`, `motion-007.png`,
  `motion-025.png` show waiting, intermediate entrance positions and the study
  doorway using successive native tree updates. Output in these fixtures is
  simulated; animation is not a cleanup progress estimate.

## Comparison and fixes

1. Initial native render: P1, house and character absent while captions worked.
   Wox's absolute-image loader mistook embedded SVG metadata for an SVG file.
   Normalized the PNG container metadata; the compressed pixels and alpha were
   preserved byte-for-byte. Revised native captures display both assets.
2. First visual comparison: P2, character too small relative to the house and
   title weight lighter than the reference. Increased atlas cell display size
   from 80 to 104, and used native semibold titles. Revised ready captures show
   the intended character emphasis and caption hierarchy.
3. Layout check: reduced house width from 520 to 460 logical units to fit the
   scene and status into a 400-unit content height. Bounded status copy to one
   line. The final compact running capture retains the house, current item and
   elapsed time. This smaller composition is an intentional launcher constraint.
4. First-walk check: a zero-duration transition unmounted Wox's interpolation
   nodes. Keeping them mounted with a 1 ms idle duration makes the first step
   interpolate too; native motion captures show intermediate positions.
5. Final capture run uses a nonactivating topmost verification window, because
   the Windows capture API samples desktop pixels and can otherwise be occluded.
   All final state screenshots were reopened and visually checked after capture.

## Required surfaces

- Typography: native UI font/fallback, semibold 22-unit status, 28-unit amount,
  regular 13-unit status detail and 12-unit elapsed time. Chinese and Latin text
  render clearly. Native font metrics intentionally follow Wox, not rasterized
  mock text. Long current items truncate rather than expanding the layout.
- Spacing: centered house over compact caption; full preview contains no list,
  additional command controls or duplicate toolbar. Scene coordinates and sprite
  feet use the same original-house coordinate system.
- Color: warm cream/wood house and brown mole match the chosen direction. No
  preview background is authored. Text/error colors resolve through Wox tokens.
  Both light and dark captures preserve readable contrast.
- Images: real generated RGBA artwork; house surroundings are transparent, with
  only local soft edge shadows. No opaque rectangle, checkerboard, atlas neighbor
  leakage or clipped broom appeared in the checked poses.
- Copy: ready view gives the Enter instruction; running view shows one current
  item; terminal states report success/failure/stop. Reported tracked cleanup is
  distinguished from actual freed space. No invented progress percentage.

## Functional checks and limits

- 19 Python tests passed: query does not execute cleanup, default Enter starts
  once, Stop/cancellation/unload, returning to a running session, visibility and
  stale-context races, hidden PowerShell quoting/UTF-8/exit status, UI transport
  failures, bounded output and random routes constrained to doorway edges.
- Ruff and mypy passed. WoxView's native parser accepted all fixture trees.
- Archive CRC, Wox Python host isolated package loading, preview construction
  and packaged asset paths passed. Native error output was empty in the final run.
- The renderer and host-loader checks are separate from an installed-plugin
  end-to-end run. Real deletion, macOS rendering/execution, unusually small
  custom launcher sizes and screen-reader behavior remain unverified.
- P3 follow-up: a larger directional atlas could soften the two-pose walk cycle.
  Native position interpolation is continuous; the current eight poses satisfy
  the selected waiting/walking/sweeping behavior.

## Implementation checklist

- [x] Clean-only full WoxView preview and Enter action.
- [x] Transparent house and separate character atlas packaged locally.
- [x] Doorway-constrained random walks and alternating sweep poses.
- [x] Terminal state handling and compact current-item copy.
- [x] Light/dark, compact viewport, native motion and package verification.

## Native playback optimization — 2026-11-06

Wox core changes live in `D:/dev/Wox`: a bounded 32 MiB / 128-entry Windows
texture LRU replaces the single-large-image slot and the 1–4 MiB cache gap.
Evicted textures remain addressable until EndDraw for same-frame reuse. Frame
metrics now report actual native uploads, hits and evictions.

WoxView adds `sprite`, common `timeline`, and `timer` contracts, with regenerated
Python and TypeScript wire types. Source rectangles are implemented in Windows,
macOS, Linux and software encoders. Playback uses the shared native Host clock;
ordinary tree echoes do not restart it. Finite playback holds its terminal pose.

Mole now publishes choreography boundaries and changed cleanup status only.
Duplicate snapshots and unchanged action updates are skipped. Walking frames,
two fixed dust particles and elapsed digits run locally; the 200ms heartbeat and
Python per-frame pose/dust calculations are removed. Doorway routing, Enter/Stop,
completion amount, returning outside, and frozen error/stop poses remain covered.

RGBA exports: house 1152x768, combined 4x4 atlas 768x768 (192px cells), dust
128x128. Decoded source pixels: 24.00 -> 5.69 MiB, a 76.3% reduction. Originals
are backed up under ignored build/asset-originals, outside the plugin archive.

Verification:
- 30 plugin tests, Ruff and mypy passed; archive CRC and packaged assets passed.
- Full ui/widget, ui/woxview and ui/runtime/internal/window Go suites passed.
- Native pixel tests include atlas regions at 1, 1.25, 1.5 and 2.5 scale.
- Windows cache tests cover mixed small/medium/large images, 120 warm frames,
  budget eviction, same-frame reuse, clear/recreate and zero warm uploads.
- Wox core compiled to build/wox-sprite.exe; Node SDK type checks and 13 Python
  WoxView SDK tests passed. Generated schemas/types match the Go contract.
- Native visual captures include light/dark, compact, walking directions,
  sweeping, amount and successful return to the porch. This uses simulated
  cleanup; no real cleanup was started.

180-second native Windows playback check (2.5x display scale): one immutable
sweeping scene, no UpdateResult or tree updates after mount, no per-frame capture.
After 5 seconds warm-up: 9,995 measured frames, 0 image uploads, 29,224 cache hits,
0 evictions; texture resident bytes stayed at 5,963,776 throughout. Frame work
(build/layout + draw recording + native encode/present) P50 1.223ms, P95 2.156ms,
P99 2.681ms. Sampled Go heap was 8.36–14.44 MiB with normal GC variation; this is
not a measurement of the whole Wox process/private GPU memory. Hiding the test
window produced zero additional frames during the checked interval.
Raw results: build/native-fixtures/benchmark.json. Reproduce with
`build/native-benchmark.exe build/native-fixtures 180` after rebuilding the harness.

Only Windows was exercised natively. macOS/Linux source-region adapters were
updated but were not built or run on those platforms. The running user Wox
process was not replaced or restarted; load the plugin with a rebuilt Wox core
that includes the new components. No store upload or commit was made.
