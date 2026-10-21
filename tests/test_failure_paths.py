"""Reject ambiguous CAD and recover cleanly from solver and job failures."""
import importlib
import io
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import ezdxf
import pandas as pd
import pytest
import wntr
from fastapi.testclient import TestClient

from fireplan.app import app
from fireplan.hydraulics import evaluate
from fireplan.ingestion import ingest_dxf
from fireplan.models import Settings
from fireplan.optimizer import optimize

GRID=Path(__file__).parent/'fixtures'/'industrial-grid.dxf'


@pytest.mark.parametrize('case', ['arc','bulge','elevated_pipe','elevated_candidate',
                                 'duplicate_candidate','missing_candidates','missing_risk',
                                 'out_of_range_risk','multiple_pumps','off_pipe_source'])
def test_unsupported_or_ambiguous_cad_returns_validation_error(case):
    doc=ezdxf.readfile(GRID);model=doc.modelspace()
    pipe=next(e for e in model if e.dxf.layer=='WATER_PIPES_MAIN' and e.dxftype()=='LWPOLYLINE')
    candidate=next(e for e in model if e.dxf.layer=='CANDIDATE_NODES' and e.dxftype()=='CIRCLE')
    if case=='arc':
        model.add_arc((20,20),5,0,180,dxfattribs={'layer':'WATER_PIPES_MAIN'})
    elif case=='bulge':
        points=list(pipe.get_points());points[0]=(*points[0][:4],.5);pipe.set_points(points)
    elif case=='elevated_pipe':
        pipe.dxf.elevation=2
    elif case=='elevated_candidate':
        candidate.dxf.center=(10,25,1)
    elif case=='duplicate_candidate':
        model.add_circle(candidate.dxf.center,candidate.dxf.radius,dxfattribs={'layer':'CANDIDATE_NODES'})
    elif case=='missing_candidates':
        for e in list(model):
            if e.dxf.layer=='CANDIDATE_NODES' and e.dxftype()=='CIRCLE':model.delete_entity(e)
    elif case=='missing_risk':
        for e in list(model):
            if e.dxf.layer=='HAZARD_LABELS':model.delete_entity(e)
    elif case=='out_of_range_risk':
        next(e for e in model if e.dxf.layer=='HAZARD_LABELS').dxf.text='ZONE_1: CHEM (RISK_WEIGHT=11)'
    elif case=='multiple_pumps':
        model.add_text('MAIN_PUMP_SOURCE',dxfattribs={'insert':(10,10),'height':1})
    elif case=='off_pipe_source':
        next(e for e in model if e.dxf.layer=='WATER_PIPES_MAIN' and e.dxftype()=='CIRCLE').dxf.center=(11,11,0)
    stream=io.StringIO();doc.write(stream)
    response=TestClient(app).post('/api/import',files={'file':('bad.dxf',stream.getvalue().encode())})
    assert response.status_code==422, response.text


@pytest.mark.parametrize('data',[b'',b'not CAD',b'\xff\xfe\x00\x01',b'AutoCAD Binary DXF\r\n\x1a\x00'])
def test_corrupt_or_binary_dxf_fails_cleanly(data):
    response=TestClient(app).post('/api/import',files={'file':('bad.dxf',data)})
    assert response.status_code==422


def test_hydraulic_solver_exception_cannot_be_returned_as_a_feasible_layout(monkeypatch):
    network=ingest_dxf(GRID.read_bytes(),metres_per_unit=1)
    def failure(*args,**kwargs):raise RuntimeError('Injected convergence failure')
    monkeypatch.setattr(wntr.sim.WNTRSimulator,'run_sim',failure)
    result=optimize(network,Settings(population=8,generations=2))
    assert result['solution'] is None
    assert 'hydraulic_solver_failure' in result['best_attempt']['violations']


def test_nonfinite_solver_results_cannot_pass_constraints(monkeypatch):
    network=ingest_dxf(GRID.read_bytes(),metres_per_unit=1)
    fake=SimpleNamespace(node={'pressure':pd.DataFrame({n.id:[float('nan')] for n in network.nodes})},
                         link={'flowrate':pd.DataFrame({p.id:[0.] for p in network.pipes})})
    monkeypatch.setattr(wntr.sim.WNTRSimulator,'run_sim',lambda *a,**kw:fake)
    result=evaluate(network,Settings(),['N0'])
    assert not result['feasible']
    assert result['violations']==['hydraulic_solver_failure']
    assert not result['constraints']['solver_valid']


def test_concurrent_requests_and_cancellation_recover_for_the_next_run(monkeypatch):
    module=importlib.import_module('fireplan.app')
    entered,release=threading.Event(),threading.Event()
    def blocked(network,settings,progress):
        entered.set()
        assert release.wait(3)
        progress({'evaluated':1})
        return {'solution':None}
    client=TestClient(app)
    request={'network':ingest_dxf(GRID.read_bytes(),metres_per_unit=1).model_dump(),
             'settings':{'population':8,'generations':2}}
    with monkeypatch.context() as patch:
        patch.setattr(module,'optimize',blocked)
        response=client.post('/api/optimize',json=request)
        assert response.status_code==202
        job_id=response.json()['job_id']
        try:
            assert entered.wait(2)
            assert client.post('/api/optimize',json=request).status_code==429
            assert client.post('/api/jobs/'+job_id+'/cancel').json()['status']=='cancellation_requested'
        finally:
            release.set()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            job=client.get('/api/jobs/'+job_id).json()
            if job['status']!='running':break
            time.sleep(.01)
        assert job['status']=='cancelled'
        assert job.get('result') is None
    response=client.post('/api/optimize',json=request)
    assert response.status_code==202
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        job=client.get('/api/jobs/'+response.json()['job_id']).json()
        if job['status']!='running':break
        time.sleep(.01)
    assert job['status']=='complete' and job['result']['solution']['feasible']
    assert client.post('/api/jobs/not-a-job/cancel').status_code==404
