# NiliCAD architecture audit (2026-11-03)

Audited frontend main f64ede0 and backend main aa3ebdf. No build system, tests,
CI or AGENTS.md exists in either baseline repository.

## Actual frontend
`nilicad/index.html` is the entire application: styles, Persian markup and an
inline ES module. Three.js 0.164.1 and addons load through an import map from
jsDelivr. Three-bvh-csg loads dynamically from esm.sh/jsDelivr. Hosting must
serve the module over HTTP(S); offline support is absent.

`items` owns meshes, dimensions, transforms, optional exactRecipe and
exactBaseMatrix. `state`/`restore` implement object-only undo. Several generations
of sketch UI and pointer listeners coexist; the final `nc*` functions route the
visible v4.2 interface. Sketches use the world XZ plane, Y is height, units mm.
Line/arc/circle profiles become THREE.Shape, then ExtrudeGeometry and
booleanMesh. `ncRegions` and a selection Set are separate from object undo.
Containment heuristics add holes; intersecting profiles are not split.

The active Boolean command evaluates triangle meshes in the browser. It may
retain a recursive exact recipe, but the displayed result does not come from
OCCT. Feature edits also rebuild through browser CSG. Imported meshes and
custom sketch extrusions have no exact recipe in the baseline.

## Actual backend
`nilicad-occt-server/server.py` is a stateless FastAPI service using CadQuery/OCP.
`make` recursively evaluates primitive, transform and union/subtract/intersect
nodes. Unsupported profiles are rejected. Three.js column-major transforms
are converted to gp_Trsf. STEP export rotates (x,y,z) to (x,-z,y), converting
Y-up to Z-up after evaluating the whole feature tree.

Routes: GET /health, POST /export-step, POST /prepare-step,
GET /download-step/{job_id}. STEP jobs are temporary, in-memory indexed,
one-hour TTL, process-local and support HTTP Range. Docker/Render runs one
uvicorn worker. Open CORS permits browser clients. No authentication, durable
database or shared job store is implemented.

## Persistence and interoperability
JSON contains meshes, transforms and optional exact recipes. STL/OBJ/3MF use
Three.js loaders/exporters; these are mesh formats and cannot recover analytic
surfaces. Exact STEP uses server settings in localStorage, a two-stage download
and an explicit user-gesture save panel for Safari. STEP/IGES import and IGES
export are only placeholder alerts in the baseline. Sketch restoration calls
ncSaveRegion without rebuilding nc.surface, so saved regions may be lost.

## Verified defects and required boundaries
Selection does not enable Extrude; touch selection is absent; overlapping
profiles remain overlapping; nested holes are inferred rather than represented
as independent faces. Pull placement ignores the offset parent's position.
Pointer cancel does not reset pull dragging. Multiple-region preview tests only
the last preview mesh when deciding whether a click confirms.

CAD work should use server-evaluated B-Rep with tessellation only for display.
Store self-contained B-Rep recipes for imported/split faces so JSON does not
depend on ephemeral jobs. Preserve analytic arcs during splitting and extrusion.
Do not present successful code generation as validated CAD behavior. Automated
Chromium/WebKit tests are useful, but physical Safari/iPad Pencil and Windows
browser checks remain a separate acceptance step.
