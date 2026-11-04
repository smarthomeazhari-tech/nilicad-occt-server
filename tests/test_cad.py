import math
import unittest
from fastapi.testclient import TestClient
import cad
from server import app,make,import_cad_bytes


def rect(x,y,w,h):
    points=[{'x':x,'y':y},{'x':x+w,'y':y},{'x':x+w,'y':y+h},{'x':x,'y':y+h}]
    return {'segments':[{'kind':'line','a':a,'b':points[(i+1)%4]} for i,a in enumerate(points)]}


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)

    def test_overlap_regions(self):
        response=self.client.post('/regions',json={'profiles':[rect(0,0,10,10),rect(5,0,10,10)]})
        self.assertEqual(response.status_code,200,response.text)
        regions=response.json()['regions']
        self.assertEqual(len(regions),3)
        for r in regions:
            self.assertAlmostEqual(r['area'],50)
            self.assertTrue(cad.load(r['brep']).isValid())
        solids=[cad.extrude({'brep':r['brep'],'depth':4}) for r in regions]
        self.assertAlmostEqual(sum(s.Volume() for s in solids),600)
        for i,s in enumerate(solids):
            for other in solids[i+1:]:
                self.assertAlmostEqual(s.intersect(other).Volume(),0)

    def test_holes_nested_circles_negative_and_multiple(self):
        regions=cad.split_profiles([rect(0,0,20,20),{'circle':{'center':{'x':10,'y':10},'r':5}}, {'circle':{'center':{'x':10,'y':10},'r':2}}])
        self.assertEqual(len(regions),3)
        self.assertAlmostEqual(sum(r['area'] for r in regions),400)
        for region in regions:
            shape=make({'type':'profile','brep':region['brep'],'depth':-3})
            self.assertTrue(shape.isValid())
            self.assertAlmostEqual(shape.Volume(),region['area']*3)
            self.assertAlmostEqual(shape.BoundingBox().ymin,-3)
        ring=max(regions,key=lambda r:r['area'])
        self.assertEqual(len(cad.load(ring['brep']).Faces()[0].innerWires()),1)

    def test_circle_intersections_are_analytic(self):
        regions=cad.split_profiles([{'circle':{'center':{'x':x,'y':0},'r':5}} for x in (0,5)])
        self.assertEqual(len(regions),3)
        overlap=50*math.acos(.5)-2.5*math.sqrt(75)
        self.assertAlmostEqual(min(r['area'] for r in regions),overlap,places=6)
        for r in regions:
            self.assertTrue(all(e.geomType()=='CIRCLE' for e in cad.load(r['brep']).Edges()))

    def test_exact_booleans(self):
        a={'type':'box','dims':{'x':10,'y':10,'z':10}}
        b={**a,'transform':[1,0,0,0,0,1,0,0,0,0,1,0,5,0,0,1]}
        for op,volume in [('union',1500),('subtract',500),('intersect',500)]:
            with self.subTest(op=op):
                response=self.client.post('/evaluate',json={'op':op,'left':a,'right':b})
                self.assertEqual(response.status_code,200,response.text)
                r=response.json();self.assertTrue(r['valid']);self.assertAlmostEqual(r['volume'],volume)
                self.assertGreater(len(r['indices']),0)
                self.assertAlmostEqual(cad.load(r['brep']).Volume(),volume)

    def test_step_iges_roundtrip(self):
        # Asymmetric dimensions and transform catch axis swapping.
        node={'type':'box','dims':{'x':10,'y':20,'z':30},'transform':[1,0,0,0,0,1,0,0,0,0,1,0,7,9,11,1]}
        original=make(node)
        for endpoint,ext in [('/export-step','step'),('/export-iges','iges')]:
            response=self.client.post(endpoint,json={'objects':[node]})
            self.assertEqual(response.status_code,200,response.text)
            shape=import_cad_bytes(response.content,ext)
            self.assertTrue(shape.isValid());self.assertAlmostEqual(shape.Volume(),6000,places=3)
            for axis in ('x','y','z'):
                self.assertAlmostEqual(getattr(shape.Center(),axis),getattr(original.Center(),axis),places=4)
            imported=self.client.post('/import-cad/'+ext,content=response.content)
            self.assertEqual(imported.status_code,200,imported.text)
            self.assertAlmostEqual(imported.json()['volume'],6000,places=3)

    def test_invalid_input_and_download(self):
        for node in [{'type':'nope'},{'type':'profile',**rect(0,0,10,10),'depth':0}, {'type':'brep','brep':'bad'}]:
            self.assertEqual(self.client.post('/evaluate',json=node).status_code,422)
        self.assertEqual(self.client.post('/regions',json={'profiles':[]}).status_code,422)
        self.assertEqual(self.client.post('/import-cad/step',content=b'bad').status_code,422)
        info=self.client.post('/prepare-step',json={'objects':[{'type':'box'}]}).json()
        full=self.client.get(info['download_url']);part=self.client.get(info['download_url'],headers={'Range':'bytes=0-99'})
        self.assertEqual(part.status_code,206);self.assertEqual(part.content,full.content[:100])
        self.assertEqual(len(full.content),info['size'])

if __name__=='__main__':
    unittest.main()
