
"""NiliCAD experimental OCCT STEP backend. Run with uvicorn server:app --host 0.0.0.0 --port 8765."""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Any
import cadquery as cq
from OCP.gp import gp_Trsf, gp_Mat, gp_XYZ
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopoDS import TopoDS_Compound
from OCP.BRep import BRep_Builder
import tempfile, os, math

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
