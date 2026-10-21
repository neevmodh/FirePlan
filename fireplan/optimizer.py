from __future__ import annotations

import copy
import random
import time
from typing import Callable

from deap import base, tools

from .hydraulics import Coverage, evaluate
from .models import Network, Settings

ALGORITHMS = {"ga": "Genetic algorithm"}


class Fitness(base.Fitness):
    weights = (1.0,)


class Individual(list):
    def __init__(self, values=()):
        super().__init__(values)
        self.fitness = Fitness()


class BudgetReached(Exception):
    pass


class Search:
    def __init__(self, network, settings, progress):
        self.network, self.settings, self.progress = network, settings, progress
        self.rng = random.Random(settings.seed)
        self.candidates = [n.id for n in network.nodes if n.candidate]
        self.limit = min(settings.max_outlets, len(self.candidates))
        self.required = {i for i, n in enumerate(self.candidates) if n in network.required_placements}
        self.coverage = Coverage(network, settings.coverage_radius_m)
        self.cache = {}
        self.best = None
        self.best_attempt = None
        self.history = []
        self.step = 0
        self.measure_limit = settings.evaluation_budget

    @staticmethod
    def quality(check):
        return (check["weighted_score"], -len(check["placements"]),
                check["min_active_pressure_bar"] if check["min_active_pressure_bar"] is not None else -float("inf"))

    def measure(self, bits):
        key = tuple(n for n, bit in zip(self.candidates, bits) if bit)
        if key not in self.cache:
            if len(self.cache) >= self.measure_limit:
                raise BudgetReached()
            check = evaluate(self.network, self.settings, list(key), self.coverage)
            self.cache[key] = check
            if check["feasible"] and key and (self.best is None or self.quality(check) > self.quality(self.best)):
                self.best = check
            if key and (self.best_attempt is None or check["weighted_score"] > self.best_attempt["weighted_score"]):
                self.best_attempt = check
            self.record()
        check = self.cache[key]
        score = check["constraints"]["penalized_score"]
        return (score if score is not None else -1e12) - len(key) * 1e-7

    def record(self):
        record = {"generation": self.step, "step": self.step,
            "coverage_pct": self.best["coverage_pct"] if self.best else 0,
            "evaluated": len(self.cache),
            "feasible_layouts": sum(c["feasible"] and bool(c["placements"]) for c in self.cache.values())}
        if not self.history or self.history[-1]["evaluated"] != len(self.cache):
            self.history.append(record)
        if self.progress:
            self.progress({**record, "algorithm": self.settings.algorithm,
                "algorithm_label": ALGORITHMS[self.settings.algorithm],
                "evaluation_budget": self.settings.evaluation_budget,
                "total_generations": self.settings.generations})

    def repair(self, bits):
        for i in self.required:
            bits[i] = 1
        chosen = [i for i, bit in enumerate(bits) if bit and i not in self.required]
        for i in self.rng.sample(chosen, max(0, sum(bits) - self.limit)):
            bits[i] = 0
        return bits

    def random_bits(self):
        bits = [0] * len(self.candidates)
        for i in self.rng.sample(range(len(bits)), self.rng.randint(1, self.limit)):
            bits[i] = 1
        return self.repair(bits)

    def seed_layouts(self):
        # Coverage-greedy, existing and single-outlet layouts seed the GA.
        chosen = []
        greedy = []
        for _ in range(self.limit):
            node = max((n for n in self.candidates if n not in chosen),
                       key=lambda n: self.coverage.calculate(chosen + [n])["weighted_score"])
            chosen.append(node)
            greedy.append([int(n in chosen) for n in self.candidates])
        seeds = greedy + [[int(n in self.network.existing_placements) for n in self.candidates]]
        seeds += [[int(i == j) for i in range(len(self.candidates))] for j in range(len(self.candidates))]
        return [self.repair(bits) for bits in seeds]


def genetic_search(search: Search, seeds):
    settings, rng = search.settings, search.rng
    toolbox = base.Toolbox()
    toolbox.register("clone", copy.deepcopy)
    toolbox.register("best", tools.selBest)
    population = [Individual(bits) for bits in seeds]
    population += [Individual(search.random_bits()) for _ in range(settings.population)]
    for ind in population:
        ind.fitness.values = (search.measure(ind),)
    population = toolbox.best(population, settings.population)
    for generation in range(1, settings.generations + 1):
        search.step = generation
        offspring = [toolbox.clone(ind) for ind in toolbox.best(population, 2)]
        while len(offspring) < settings.population:
            child = toolbox.clone(max(rng.sample(population, min(3, len(population))), key=lambda ind: ind.fitness))
            mate = max(rng.sample(population, min(3, len(population))), key=lambda ind: ind.fitness)
            if rng.random() < .8:
                for i in range(len(child)):
                    if rng.random() < .5:
                        child[i] = mate[i]
            if rng.random() < .6:
                for i in range(len(child)):
                    if rng.random() < max(1 / len(child), .04):
                        child[i] = 1 - child[i]
            search.repair(child)
            del child.fitness.values
            offspring.append(child)
        offspring[-1] = Individual(search.random_bits())
        for child in offspring:
            if not child.fitness.valid:
                child.fitness.values = (search.measure(child),)
        population = offspring
        search.record()


def refine_layout(search: Search, solve_limit: int):
    """Try feasible one-outlet removals, additions and swaps within the same budget."""
    if search.best is None or solve_limit <= 0:
        return 0
    started = len(search.cache)
    incumbent = search.best
    active = set(incumbent["placements"])
    optional = sorted(active - set(search.network.required_placements),
                      key=lambda node: (incumbent["pressures_bar"][node], node))
    unselected = sorted(set(search.candidates) - active,
                        key=lambda node: (-search.coverage.calculate([node])["weighted_score"], node))
    neighbours = ([active - {node} for node in optional] +
                  ([active | {node} for node in unselected] if len(active) < search.limit else []) +
                  [active - {old} | {new} for old in optional for new in unselected])
    for neighbour in neighbours[:max(128, solve_limit * 4)]:
        if len(search.cache) - started >= solve_limit:
            break
        ordered = [node for node in search.candidates if node in neighbour]
        if not ordered or tuple(ordered) in search.cache:
            continue
        # Only neighbors that can improve the incumbent objective need a water solve.
        score = search.coverage.calculate(ordered)["weighted_score"]
        if score < search.best["weighted_score"] or (score == search.best["weighted_score"] and
                                                      len(ordered) > len(search.best["placements"])):
            continue
        search.measure([int(node in neighbour) for node in search.candidates])
    return len(search.cache) - started


def optimize(network: Network, settings: Settings, progress: Callable | None = None) -> dict:
    """Search a penalized objective, retain and independently verify a feasible incumbent."""
    started = time.monotonic()
    search = Search(network, settings, progress)
    stop_reason = "iteration_limit"
    refinement_evaluations = 0
    reserve = min(32, settings.evaluation_budget // 5) if settings.evaluation_budget >= 64 else 0
    try:
        if len(search.required) > search.limit:
            # A search cannot discard mandatory reference outlets to fit the budget.
            bits = [int(i in search.required) for i in range(len(search.candidates))]
            stop_reason = "mandatory_outlet_budget"
            search.measure(bits)
            raise BudgetReached()
        seeds = search.seed_layouts()
        search.measure_limit = settings.evaluation_budget - reserve
        try:
            genetic_search(search, seeds)
        except BudgetReached:
            stop_reason = "ga_evaluation_limit"
        search.measure_limit = settings.evaluation_budget
        refinement_evaluations = refine_layout(search, reserve)
        if len(search.cache) >= settings.evaluation_budget:
            stop_reason = "evaluation_budget"
    except BudgetReached:
        if stop_reason != "mandatory_outlet_budget":
            stop_reason = "evaluation_budget"
    search.record()
    verified = evaluate(network, settings, search.best["placements"], search.coverage) if search.best else None
    if verified is not None and not verified["feasible"]:
        verified = None
    samples = [{"coverage_pct": c["coverage_pct"], "demand_lps": c["total_demand_lps"],
               "outlets": len(c["placements"]), "feasible": c["feasible"],
               "penalty_value": c["constraints"]["penalty_value"]} for c in search.cache.values()]
    return {"status": "feasible" if verified else "no_feasible_layout_found",
        "solution": verified, "best_attempt": search.best_attempt,
        "history": search.history, "evaluated_layouts": len(search.cache),
        "feasible_layouts": sum(c["feasible"] and bool(c["placements"]) for c in search.cache.values()),
        "elapsed_seconds": round(time.monotonic() - started, 3), "stop_reason": stop_reason,
        "refinement_evaluations": refinement_evaluations,
        "algorithm_id": settings.algorithm, "algorithm": ALGORITHMS[settings.algorithm] + " + WNTR hydraulic verification",
        "search_samples": samples,
        "optimality": "Best feasible layout found with bounded outlet refinement; global optimality is not certified.",
        "settings": settings.model_dump(), "network": network.model_dump(),
        "assumptions": ["Constant source pressure; supply flow capacity enforced separately.",
            "All selected outlets operate simultaneously at fixed demand.",
            "Coverage uses unions of 512-sided circles clipped to CAD risk polygons; walls and hose routing are not modeled.",
            "Pressure threshold is a user-supplied design input, not code certification.",
            "Risk layers are area-weighted; overlapping zone polygons represent additive risk layers.",
            "Steady state at t=0; transient behavior and real pump curves are not modeled.",
            "The evaluation budget counts unique search hydraulic solves; final verification is additional."]}
