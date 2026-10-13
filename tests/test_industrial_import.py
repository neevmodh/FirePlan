import io
from pathlib import Path

import ezdxf
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from fireplan.app import app
from fireplan.ingestion import ingest_dxf
from fireplan.hydraulics import evaluate
from fireplan.models import Settings
from fireplan.optimizer import optimize

SOURCE=Path(__file__).parent/'fixtures'/'industrial-grid.dxf'


def edited(change):
    doc=ezdxf.readfile(SOURCE)
    change(doc)
    output=io.StringIO();doc.write(output)
    return output.getvalue().encode()


def test_actual_windows_dxf_imports_all_pipe_layers_and_candidate_locations():
    data=SOURCE.read_bytes()
    assert b'\r\n' in data
    network=ingest_dxf(data,metres_per_unit=1)
    assert len(network.nodes)==15 and len(network.pipes)==15
    candidates={node.id:node for node in network.nodes if node.candidate}
    assert set(candidates)=={f'N{i}' for i in range(12)}
    assert (candidates['N4'].x,candidates['N4'].y)==(40,10)
    assert any(pipe.start=='N4' or pipe.end=='N4' for pipe in network.pipes)
    assert not next(n for n in network.nodes if (n.x,n.y)==(10,70)).candidate
    assert len(network.drawing_lines)==8
    assert network.import_details.pipe_layers==['WATER_PIPES_BRANCH','WATER_PIPES_MAIN']


def test_closed_wall_rooms_are_matched_to_exact_risk_labels():
    network=ingest_dxf(SOURCE.read_bytes(),metres_per_unit=1)
    zones={zone.id:zone for zone in network.zones}
    assert {key:zone.risk for key,zone in zones.items()}=={'Z1':.9,'Z2':.95,'Z3':.6,'Z4':.2}
    assert {key:Polygon(zone.polygon).area for key,zone in zones.items()}=={'Z1':1600,'Z2':1600,'Z3':5200,'Z4':1200}
    assert sum(Polygon(zone.polygon).area for zone in network.zones)==9600
    assert zones['Z3'].label=='HEAVY MANUFACTURING'
    assert network.import_details.risk_method=='labeled_rooms'


def test_source_marker_and_pressure_metadata_and_manual_override():
    network=ingest_dxf(SOURCE.read_bytes(),metres_per_unit=1)
    source=next(n for n in network.nodes if n.id==network.source_id)
    assert (source.x,source.y)==(10,10) and not source.candidate
    assert network.import_details.source_autodetected
    assert network.import_details.suggested_source_pressure_bar==7
    override=ingest_dxf(SOURCE.read_bytes(),metres_per_unit=1,source_x=110,source_y=10)
    assert not override.import_details.source_autodetected
    assert override.import_details.suggested_source_pressure_bar is None
    assert (next(n for n in override.nodes if n.id==override.source_id).x)==110
    with pytest.raises(ValueError,match='Source must match'):
        ingest_dxf(SOURCE.read_bytes(),metres_per_unit=1,source_x=0,source_y=0)


def test_candidate_and_source_splitting_respects_unit_scale():
    network=ingest_dxf(SOURCE.read_bytes(),metres_per_unit=.3048)
    candidate=next(n for n in network.nodes if n.id=='N4')
    assert candidate.x==pytest.approx(12.192)
    source=next(n for n in network.nodes if n.id==network.source_id)
    assert source.x==pytest.approx(3.048)
    assert network.import_details.source_coordinates==(10,10)


def test_labeled_risk_is_not_guessed_without_closed_rooms():
    def remove_wall(doc):
        model=doc.modelspace()
        for e in list(model):
            if e.dxf.layer=='STRUCTURAL_WALLS' and e.dxftype()=='LINE' and e.dxf.start.x==40:
                model.delete_entity(e)
    with pytest.raises(ValueError,match='unique|enclosed'):
        ingest_dxf(edited(remove_wall),metres_per_unit=1)


def test_off_pipe_candidate_markers_are_rejected():
    def move(doc):
        next(e for e in doc.modelspace() if e.dxf.layer=='CANDIDATE_NODES' and e.dxftype()=='CIRCLE').dxf.center=(1,1,0)
    with pytest.raises(ValueError,match='Candidate node must match'):
        ingest_dxf(edited(move),metres_per_unit=1)


def test_uploaded_file_imports_and_optimizes_with_only_marked_candidates():
    client=TestClient(app)
    response=client.post('/api/import',files={'file':('Industrial_Fire_Optimization_Grid.dxf',SOURCE.read_bytes())})
    assert response.status_code==200
    assert response.json()['import_details']['source_coordinates']==[10,10]
    network=ingest_dxf(SOURCE.read_bytes(),metres_per_unit=1)
    result=optimize(network,Settings(source_pressure_bar=7))
    assert result['solution']['feasible']
    assert set(result['solution']['placements']).issubset({f'N{i}' for i in range(12)})
    assert result['solution']['min_active_pressure_bar']>=3.5
    assert result['solution']['total_demand_lps']<=60


def test_industrial_pressure_threshold_changes_layout_when_binding():
    network=ingest_dxf(SOURCE.read_bytes(),metres_per_unit=1)
    solutions={minimum:optimize(network,Settings(source_pressure_bar=7,min_pressure_bar=minimum))['solution']
               for minimum in [1.5,6.5,6.7,6.9,7.1]}
    for minimum,solution in solutions.items():
        if solution:
            assert solution['feasible']
            assert solution['min_active_pressure_bar']>=minimum
            assert evaluate(network,Settings(source_pressure_bar=7,min_pressure_bar=minimum),
                            solution['placements'])['feasible']
    # Both thresholds are below the delivered pressure, so an unchanged layout is correct.
    assert set(solutions[1.5]['placements'])==set(solutions[6.5]['placements'])
    assert solutions[1.5]['min_active_pressure_bar']==pytest.approx(solutions[6.5]['min_active_pressure_bar'])
    assert solutions[6.5]['constraints']['pressure_margin_bar']==pytest.approx(
        solutions[1.5]['constraints']['pressure_margin_bar']-5)
    assert len(solutions[6.7]['placements'])<len(solutions[6.5]['placements'])
    assert len(solutions[6.9]['placements'])<len(solutions[6.7]['placements'])
    assert solutions[6.9]['coverage_pct']<solutions[6.5]['coverage_pct']
    assert not evaluate(network,Settings(source_pressure_bar=7,min_pressure_bar=6.7),
                        solutions[6.5]['placements'])['feasible']
    assert solutions[7.1] is None
