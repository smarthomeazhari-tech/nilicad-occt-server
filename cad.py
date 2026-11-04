"""Exact modeling in world coordinates (XZ sketch plane, Y height, mm)."""
import io
import math
import cadquery as cq
from OCP.BOPAlgo import BOPAlgo_Splitter
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.TopAbs import TopAbs_IN, TopAbs_ON
from OCP.gp import gp_Pnt


def finite(value):
    value = float(value)
    if not math.isfinite(value) or abs(value) > 1e6:
        raise ValueError('Expected finite coordinate within 1e6 mm')
    return value


def dump(shape):
    stream = io.BytesIO()
    shape.exportBrep(stream)
    return stream.getvalue().decode('ascii')


def load(data):
    if not isinstance(data, str) or len(data) > 20_000_000:
        raise ValueError('Invalid B-Rep payload')
    shape = cq.Shape.importBrep(io.BytesIO(data.encode('ascii')))
    if shape.isNull() or not shape.isValid():
        raise ValueError('Invalid B-Rep shape')
    return shape


def vector(p):
    return cq.Vector(finite(p['x']), 0, finite(p['y']))


def profile(node):
    if node.get('brep'):
        faces = load(node['brep']).Faces()
        if len(faces) != 1:
            raise ValueError('Expected a single planar profile')
        face = faces[0]
        if face.geomType() != 'PLANE' or any(abs(v.Center().y) > 1e-6 for v in face.Vertices()):
            raise ValueError('Profile must lie in world XZ plane')
        return face
    circle = node.get('circle')
    if circle:
        radius = finite(circle['r'])
        if radius <= 0:
            raise ValueError('Circle radius must be positive')
        wire = cq.Wire.makeCircle(radius, vector(circle['center']), cq.Vector(0,1,0))
    else:
        segments = node.get('segments', [])
        if not 3 <= len(segments) <= 2000:
            raise ValueError('Expected 3–2000 profile edges')
        edges = []
        for seg in segments:
            a, b = vector(seg['a']), vector(seg['b'])
            if seg['kind'] == 'line':
                edges.append(cq.Edge.makeLine(a,b))
            elif seg['kind'] == 'arc':
                edges.append(cq.Edge.makeThreePointArc(a,vector(seg['m']),b))
            else:
                raise ValueError('Unsupported sketch edge')
        wire = cq.Wire.assembleEdges(edges)
    face = cq.Face.makeFromWires(wire)
    if not wire.IsClosed() or not face.isValid() or face.Area() < 1e-8:
        raise ValueError('Profile must be closed and valid')
    return face


def mesh(shape):
    vertices, triangles = shape.tessellate(0.05, 0.1)
    return {'positions':[v for p in vertices for v in p.toTuple()],
            'indices':[i for tri in triangles for i in tri]}


def result(shape):
    if not shape.isValid():
        raise ValueError('OCCT produced invalid geometry')
    return {**mesh(shape), 'brep':dump(shape), 'volume':shape.Volume(),
            'solids':len(shape.Solids()), 'valid':True}


def split_profiles(nodes):
    if not 1 <= len(nodes) <= 100:
        raise ValueError('Expected 1–100 profiles')
    splitter = BOPAlgo_Splitter()
    splitter.SetNonDestructive(True)
    sources = [profile(node) for node in nodes]
    for face in sources:
        splitter.AddArgument(face.wrapped)
    if len(sources)==1:
        faces=sources
    else:
        splitter.Perform()
        if splitter.HasErrors():
            raise ValueError('OCCT profile split failed')
        faces = cq.Shape.cast(splitter.Shape()).Faces()
    regions = []
    # Shared result faces occur only once in OCCT topology.
    for face in faces:
        if face.Area() <= 1e-8:
            continue
        display = mesh(face)
        tri = display['indices'][:3]
        positions = display['positions']
        point = gp_Pnt(*[sum(positions[i*3+axis] for i in tri)/3 for axis in range(3)])
        owners = [i for i,source in enumerate(sources) if BRepClass_FaceClassifier(source.wrapped,point,1e-7).State() in (TopAbs_IN,TopAbs_ON)]
        regions.append({**display, 'brep':dump(face), 'area':face.Area(),
                        'sources':owners, 'owner':min(owners,key=lambda i:sources[i].Area()) if owners else None})
    return regions


def extrude(node):
    face = profile(node)
    depth = finite(node['depth'])
    if not .01 <= abs(depth) <= 10000:
        raise ValueError('Extrusion depth must be .01–10000 mm')
    return cq.Solid.extrudeLinear(face.outerWire(),face.innerWires(),cq.Vector(0,depth,0))
