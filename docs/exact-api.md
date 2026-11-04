# Exact modeling API

World coordinates: XZ sketch plane, Y extrusion, millimetres. Existing STEP
routes remain compatible. POST JSON unless noted; errors return HTTP 422.

- `/regions`: `{profiles:[{segments:[{kind:'line',a:{x,y},b:{x,y}}]}]}`.
  Arc edges use kind `arc` and midpoint `m`; circles use `{circle:{center:{x,y},r}}`.
  Existing planar B-Reps use `{brep:string}`. Returns independent OCCT faces as
  `{regions:[{brep,positions,indices,area,sources,owner}]}`. Curves stay analytic.
  Meshes are for display and selection only. `owner` is the smallest containing
  input profile, enabling consistent selection across nested profiles.
- `/evaluate`: recursive primitive/Boolean/transform recipe, or
  `{type:'profile',brep,depth}` / `{type:'brep',brep}`. Returns tessellation,
  self-contained B-Rep, volume in cubic mm, solid count and validity.
  Empty intersection returns a valid empty mesh/zero solids.
- `/import-cad/{step|stp|iges|igs}`: raw file bytes (20 MB maximum).
  Conventional Z-up files convert to browser Y-up. Returns the same exact result.
- `/export-iges`: `{objects:[recipe,...]}`; returns Z-up millimetre IGES.

B-Rep payloads are self-contained and can be stored in project JSON. No temporary
server identifier is required for modeling/import round trips. STEP download
jobs remain process-local and expiring. Binary mesh imports do not acquire
analytic CAD history; exact operations reject these until a conversion workflow
is explicitly implemented.

Run: `python -m unittest discover -s tests -v` after installing requirements-dev.
API tests check OCCT validity, areas/volumes, analytic circular intersections,
holes, negative/multiple extrusion, exact Boolean volumes, asymmetric STEP/IGES
round trips, invalid requests and partial STEP download.
