
"""NiliCAD experimental OCCT STEP backend. Run with uvicorn server:app --host 0.0.0.0 --port 8765."""
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from typing import Any
import cadquery as cq
from OCP.gp import gp_Trsf, gp_Mat, gp_XYZ
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopoDS import TopoDS_Compound
from OCP.BRep import BRep_Builder
import tempfile, os, math, time, uuid, re, threading

app=FastAPI(title="NiliCAD OCCT STEP bridge")
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["POST","GET"],allow_headers=["*"])

class Model(BaseModel):
    objects:list[dict[str,Any]]

def apply_transform(shape,mat):
    if not isinstance(mat,list) or len(mat)!=16:
        raise ValueError('Invalid transformation matrix')
    a=[float(v) for v in mat]
    tr=gp_Trsf()
    tr.SetValues(a[0],a[4],a[8],a[12],
                 a[1],a[5],a[9],a[13],
                 a[2],a[6],a[10],a[14])
    return cq.Shape(BRepBuilderAPI_Transform(shape.wrapped,tr,True).Shape())

def orient_for_step(shape):
    """Convert Three.js Y-up model coordinates to STEP Z-up coordinates.

    Rotation +90 degrees about X: (x, y, z) -> (x, -z, y).
    Apply only after all modeling and Boolean operations are complete.
    """
    tr = gp_Trsf()
    tr.SetValues(1, 0, 0, 0,
                 0, 0, -1, 0,
                 0, 1, 0, 0)
    return cq.Shape(BRepBuilderAPI_Transform(shape.wrapped, tr, True).Shape())

def make(node,level=0):
    if level>40: raise ValueError("Feature tree too deep")
    if node.get('op')=='transform':
        return apply_transform(make(node['shape'],level+1),node['transform'])
    if 'op' in node:
        a=make(node['left'],level+1);b=make(node['right'],level+1)
        op=node['op']
        if op=='union':return a.fuse(b)
        if op=='subtract':return a.cut(b)
        if op=='intersect':return a.intersect(b)
        raise ValueError('Unknown boolean operation')
    t=node['type'];d=node.get('dims') or {}
    x=float(d.get('x',40));y=float(d.get('y',30));z=float(d.get('z',20))
    if min(x,y,z)<=0:raise ValueError('Dimensions must be positive')
    # Three.js box uses (x,z,y), Y is vertical
    if t in ('box','extrudeRect'):
        shape=cq.Workplane('XY').box(x,z,y,centered=(True,True,True)).val()
        # CadQuery box(x,z,y) already matches Three.js box(x,z,y).
        # Do not rotate: that would swap the vertical and depth axes.
    elif t in ('cylinder','extrudeCircle'):
        shape=cq.Workplane('XY').circle(x/2).extrude(z/2,both=True).val()
        shape=shape.rotate((0,0,0),(1,0,0),-90)
    elif t=='sphere':
        shape=cq.Workplane('XY').sphere(x/2).val()
    elif t=='cone':
        shape=cq.Solid.makeCone(x/2,0,z,cq.Vector(0,-z/2,0),cq.Vector(0,1,0))
    elif t=='hexPrism':
        shape=cq.Workplane('XY').polygon(6,x).extrude(z/2,both=True).val()
        shape=shape.rotate((0,0,0),(1,0,0),-90)
    else:raise ValueError('Unsupported exact primitive: '+t)
    mat=node.get('transform')
    if mat: shape=apply_transform(shape,mat)
    return shape

@app.get('/health')
def health():return {'ok':True,'kernel':'Open CASCADE (OCP)'}

@app.post('/export-step')
def export_step(model:Model):
    try:
        if not model.objects or len(model.objects)>100:raise ValueError('Expected 1–100 objects')
        writer=STEPControl_Writer()
        for node in model.objects:
            shape=orient_for_step(make(node))
            writer.Transfer(shape.wrapped,STEPControl_AsIs)
        with tempfile.NamedTemporaryFile(suffix='.step',delete=False) as f: path=f.name
        try:
            if writer.Write(path)!=IFSelect_RetDone:raise RuntimeError('OCCT STEP writer failed')
            data=open(path,'rb').read()
        finally:os.unlink(path)
        return Response(data,media_type='model/step',headers={'Content-Disposition':'attachment; filename="NiliCAD-precise.step"'})
    except Exception as exc:raise HTTPException(422,str(exc))


# Two-stage STEP delivery. Jobs are local to this Render instance and expire.
_STEP_DIR = os.path.join(tempfile.gettempdir(), 'nilicad-step-jobs')
os.makedirs(_STEP_DIR, exist_ok=True)
_STEP_TTL = 3600
_STEP_LOCK = threading.Lock()
_STEP_JOBS = {}

def _cleanup_step_jobs():
    now = time.time()
    with _STEP_LOCK:
        for key, (path, created) in list(_STEP_JOBS.items()):
            if now - created > _STEP_TTL:
                _STEP_JOBS.pop(key, None)
                try: os.unlink(path)
                except OSError: pass

def _write_step(model, path):
    if not model.objects or len(model.objects) > 100:
        raise ValueError('Expected 1–100 objects')
    writer = STEPControl_Writer()
    for node in model.objects:
        shape = orient_for_step(make(node))
        if writer.Transfer(shape.wrapped, STEPControl_AsIs) != IFSelect_RetDone:
            raise ValueError('OCCT could not transfer shape to STEP')
    if writer.Write(path) != IFSelect_RetDone:
        raise ValueError('OCCT STEP writer failed')
    size = os.path.getsize(path)
    if size == 0:
        raise ValueError('Generated STEP is empty')
    return size

@app.post('/prepare-step')
def prepare_step(model: Model):
    _cleanup_step_jobs()
    job_id = uuid.uuid4().hex
    path = os.path.join(_STEP_DIR, job_id + '.step')
    try:
        size = _write_step(model, path)
        with _STEP_LOCK:
            _STEP_JOBS[job_id] = (path, time.time())
        return {'job_id': job_id, 'size': size, 'download_url': '/download-step/' + job_id, 'expires_in_seconds': _STEP_TTL}
    except Exception as exc:
        try: os.unlink(path)
        except OSError: pass
        raise HTTPException(422, str(exc))

@app.get('/download-step/{job_id}')
def download_step(job_id: str, range_header: str | None = Header(default=None, alias='Range')):
    _cleanup_step_jobs()
    if not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise HTTPException(404, 'STEP job not found')
    with _STEP_LOCK:
        job = _STEP_JOBS.get(job_id)
    if not job or not os.path.isfile(job[0]):
        raise HTTPException(404, 'STEP file expired or unavailable; generate it again')
    path = job[0]
    size = os.path.getsize(path)
    start, end = 0, size - 1
    headers = {'Accept-Ranges': 'bytes', 'Content-Disposition': 'attachment; filename="NiliCAD-precise.step"', 'Cache-Control': 'no-store', 'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges, Content-Disposition'}
    status = 200
    if range_header:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header.strip())
        if not match or (not match.group(1) and not match.group(2)):
            return Response(status_code=416, headers={**headers, 'Content-Range': f'bytes */{size}'})
        if not match.group(1):
            suffix = int(match.group(2))
            start = max(0, size - suffix) if suffix else size
        else:
            start = int(match.group(1))
        if match.group(1) and match.group(2):
            end = min(int(match.group(2)), size - 1)
        if start >= size or end < start:
            return Response(status_code=416, headers={**headers, 'Content-Range': f'bytes */{size}'})
        status = 206
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'
    length = end - start + 1
    headers['Content-Length'] = str(length)
    def chunks():
        with open(path, 'rb') as f:
            f.seek(start)
            remaining = length
            while remaining:
                block = f.read(min(64 * 1024, remaining))
                if not block: break
                remaining -= len(block)
                yield block
    return StreamingResponse(chunks(), status_code=status, media_type='model/step', headers=headers)
