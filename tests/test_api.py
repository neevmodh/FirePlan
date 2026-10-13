import io
import time

import ezdxf
from fastapi.testclient import TestClient

from fireplan.app import app
from fireplan.hydraulics import evaluate
from fireplan.models import Network, Settings
from fireplan.samples import sample_network

client = TestClient(app)


def drawing():
    doc=ezdxf.new()
    for name in ['PIPES','RISK_8','WALLS']:
        doc.layers.add(name)
    model=doc.modelspace()
    for a,b in [((0,0),(10,0)),((10,0),(20,0)),((10,0),(10,10)),((20,0),(20,10)),((10,10),(20,10))]:
        model.add_line(a,b,dxfattribs={'layer':'PIPES'})
    model.add_lwpolyline([(-2,-2),(22,-2),(22,12),(-2,12)],close=True,dxfattribs={'layer':'RISK_8'})
    model.add_lwpolyline([(-3,-3),(23,-3),(23,13),(-3,13)],close=True,dxfattribs={'layer':'WALLS'})
    output=io.StringIO();doc.write(output)
    return output.getvalue().encode()


def wait_for_job(job_id):
    for _ in range(150):
        job=client.get('/api/jobs/'+job_id).json()
        if job['status']!='running':
            return job
        time.sleep(.03)
    raise AssertionError('Optimization did not finish')


def test_one_page_and_only_core_routes():
    assert client.get('/api/health').json()['hydraulics']=='WNTRSimulator'
    page=client.get('/').text
    assert 'Optimal plan workspace' in page and 'Optimal setup' in page
    for label in ['Solver lab','Real plan library','Search history','Constraints & stress','Export layout']:
        assert label not in page
    paths=set(app.openapi()['paths'])
    assert paths=={'/api/health','/api/import','/api/optimize','/api/jobs/{job_id}','/api/jobs/{job_id}/cancel'}


def test_cad_import_keeps_architecture_and_pipe_network():
    response=client.post('/api/import',files={'file':('office.dxf',drawing(),'application/dxf')})
    assert response.status_code==200
    network=Network.model_validate(response.json())
    assert network.name=='office'
    assert len(network.drawing_lines)==4
    assert len(network.pipes)==5
    assert len(network.nodes)==5
    assert network.zones[0].risk==8
    source=next(n for n in network.nodes if n.id==network.source_id)
    assert not source.candidate and source.x==0 and source.y==0


def test_cad_import_units_source_validation_and_limits():
    response=client.post('/api/import',files={'file':('office.dxf',drawing())},data={'metres_per_unit':'.3048'})
    assert response.status_code==200
    assert response.json()['pipes'][0]['length_m']==3.048
    assert client.post('/api/import',files={'file':('office.dxf',drawing())},data={'source_x':'5','source_y':'8'}).status_code==422
    assert client.post('/api/import',files={'file':('bad.dxf',b'not a CAD file')}).status_code==422
    assert client.post('/api/import',files={'file':('large.dxf',b'0'*(4*1024*1024+1))}).status_code==413


def test_unsupported_formats_are_explained():
    for name in ['plan.dwg','plan.pdf','network.json']:
        result=client.post('/api/import',files={'file':(name,b'unsupported')})
        assert result.status_code==415
        assert 'DXF' in result.json()['detail']


def test_import_then_optimize_verifies_hard_constraints():
    network=client.post('/api/import',files={'file':('office.dxf',drawing())}).json()
    settings={'max_outlets':2,'coverage_radius_m':8,'population':8,'generations':3}
    response=client.post('/api/optimize',json={'network':network,'settings':settings})
    assert response.status_code==202
    job=wait_for_job(response.json()['job_id'])
    assert job['status']=='complete'
    solution=job['result']['solution']
    assert solution['feasible']
    assert 0<len(solution['placements'])<=2
    assert solution['min_active_pressure_bar']>=3.5
    assert solution['total_demand_lps']<=60
    fresh=evaluate(Network.model_validate(network),Settings(**settings),solution['placements'])
    assert fresh['feasible'] and fresh['coverage_pct']==solution['coverage_pct']
    assert 'cancel' not in job
    assert client.get('/api/jobs/missing').status_code==404


def test_only_ga_and_valid_physical_settings_are_accepted():
    network=sample_network().model_dump()
    for settings in [{'algorithm':'sa'},{'annealing_steps':100},{'min_pressure_bar':0},{'generations':10000}]:
        assert client.post('/api/optimize',json={'network':network,'settings':settings}).status_code==422


def test_no_feasible_solution_is_not_presented_as_an_optimal_plan():
    response=client.post('/api/optimize',json={'network':sample_network().model_dump(),
        'settings':{'source_pressure_bar':1,'min_pressure_bar':3.5,'population':8,'generations':1}})
    job=wait_for_job(response.json()['job_id'])
    assert job['status']=='complete'
    assert job['result']['solution'] is None
    assert job['result']['status']=='no_feasible_layout_found'
