from __future__ import annotations

from typing import Annotated, Literal

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from shapely.geometry import Polygon

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")]
Coordinate = Annotated[float, Field(ge=-1e7, le=1e7, allow_inf_nan=False)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Node(StrictModel):
    id: Identifier
    x: Coordinate
    y: Coordinate
    elevation_m: Annotated[float, Field(ge=-500, le=9000, allow_inf_nan=False)] = 0
    candidate: bool = True
    label: str = Field(default="", max_length=120)
    background_demand_lps: Annotated[float, Field(ge=0, le=1000, allow_inf_nan=False)] = 0


class Pipe(StrictModel):
    id: Identifier
    start: Identifier
    end: Identifier
    length_m: Annotated[float, Field(gt=0, le=100000, allow_inf_nan=False)]
    diameter_mm: Annotated[float, Field(ge=10, le=2000, allow_inf_nan=False)] = 100
    roughness: Annotated[float, Field(ge=20, le=200, allow_inf_nan=False)] = 120
    minor_loss: Annotated[float, Field(ge=0, le=1000, allow_inf_nan=False)] = 0


class Zone(StrictModel):
    id: Identifier
    label: str = Field(max_length=120)
    risk: Annotated[float, Field(ge=0, le=10, allow_inf_nan=False)]
    polygon: list[tuple[Coordinate, Coordinate]] = Field(min_length=3, max_length=100)

    @model_validator(mode="after")
    def validate_polygon(self):
        shape = Polygon(self.polygon)
        if not shape.is_valid or shape.area < 0.01:
            raise ValueError("Zone polygons must be valid and have nonzero area")
        return self


class ImportDetails(StrictModel):
    pipe_layers: list[str] = Field(max_length=20)
    source_coordinates: tuple[Coordinate, Coordinate]
    source_autodetected: bool
    suggested_source_pressure_bar: Annotated[float | None, Field(gt=0, le=30, allow_inf_nan=False)] = None
    risk_method: Literal["explicit_polygons", "labeled_rooms"]


class Network(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    synthetic: bool = False
    source_id: Identifier
    nodes: list[Node] = Field(min_length=2, max_length=120)
    pipes: list[Pipe] = Field(min_length=1, max_length=250)
    zones: list[Zone] = Field(min_length=1, max_length=40)
    drawing_lines: list[tuple[tuple[Coordinate, Coordinate], tuple[Coordinate, Coordinate]]] = Field(default_factory=list, max_length=2000)
    import_details: ImportDetails | None = None
    existing_placements: list[Identifier] = Field(default_factory=list, max_length=80)
    required_placements: list[Identifier] = Field(default_factory=list, max_length=80)

    @model_validator(mode="after")
    def validate_graph(self):
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("Node IDs must be unique")
        if self.source_id not in ids:
            raise ValueError("Source must reference a node in the network")
        source = next(n for n in self.nodes if n.id == self.source_id)
        if source.candidate or source.background_demand_lps:
            raise ValueError("Source must have candidate=false and no background demand")
        candidates = {n.id for n in self.nodes if n.candidate}
        if not candidates or len(candidates) > 80:
            raise ValueError("Network requires 1 to 80 candidate nodes")
        if len({p.id for p in self.pipes}) != len(self.pipes):
            raise ValueError("Pipe IDs must be unique")
        if len({z.id for z in self.zones}) != len(self.zones):
            raise ValueError("Zone IDs must be unique")
        graph = nx.Graph()
        graph.add_nodes_from(ids)
        for pipe in self.pipes:
            if pipe.start not in ids or pipe.end not in ids or pipe.start == pipe.end:
                raise ValueError(f"Invalid endpoints for pipe {pipe.id}")
            graph.add_edge(pipe.start, pipe.end)
        if not nx.is_connected(graph):
            raise ValueError("All pipe junctions must connect to the source")
        if len(set(self.existing_placements)) != len(self.existing_placements):
            raise ValueError("Existing placements must be unique")
        if not set(self.existing_placements).issubset(candidates):
            raise ValueError("Existing placements must reference candidate nodes")
        if len(set(self.required_placements)) != len(self.required_placements) or not set(self.required_placements).issubset(candidates):
            raise ValueError("Required placements must be unique candidate nodes")
        return self


class Settings(StrictModel):
    algorithm: Literal["ga"] = "ga"
    max_outlets: int = Field(default=8, ge=1, le=80)
    source_pressure_bar: Annotated[float, Field(gt=0, le=30, allow_inf_nan=False)] = 6
    min_pressure_bar: Annotated[float, Field(gt=0, le=20, allow_inf_nan=False)] = 3.5
    pump_capacity_lps: Annotated[float, Field(gt=0, le=2000, allow_inf_nan=False)] = 60
    outlet_demand_lps: Annotated[float, Field(gt=0, le=100, allow_inf_nan=False)] = 5
    coverage_radius_m: Annotated[float, Field(gt=0, le=200, allow_inf_nan=False)] = 35
    diameter_override_mm: Annotated[float | None, Field(ge=10, le=2000, allow_inf_nan=False)] = None
    population: int = Field(default=28, ge=8, le=64)
    generations: int = Field(default=16, ge=1, le=40)
    seed: int = Field(default=42, ge=0, le=2147483647)
    evaluation_budget: int = Field(default=250, ge=8, le=1000)
    penalty_weight: Annotated[float, Field(ge=1, le=10000, allow_inf_nan=False)] = 500


class OptimizationRequest(StrictModel):
    network: Network
    settings: Settings = Field(default_factory=Settings)


class EvaluationRequest(OptimizationRequest):
    placements: list[Identifier] = Field(max_length=80)

    @model_validator(mode="after")
    def validate_placements(self):
        if len(set(self.placements)) != len(self.placements):
            raise ValueError("Placements must be unique")
        candidates = {n.id for n in self.network.nodes if n.candidate}
        if not set(self.placements).issubset(candidates):
            raise ValueError("Placements must reference candidate nodes")
        return self
