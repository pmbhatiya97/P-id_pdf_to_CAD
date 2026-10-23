from __future__ import annotations
import argparse
import json
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="lineaforge", description="Local-first PDF / CAD toolkit")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("desktop")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("source", type=Path)
    convert = commands.add_parser("convert")
    convert.add_argument("source", type=Path)
    convert.add_argument("destination", type=Path)
    convert.add_argument("--converter")
    convert.add_argument("--converter-kind", choices=["oda", "libredwg"], default="oda")
    convert.add_argument("--version", default="R2018")
    convert.add_argument("--pages", default="all")
    convert.add_argument("--paper", default="A3")
    convert.add_argument("--scale", default="fit")
    convert.add_argument("--raster", action="store_true")
    pid = commands.add_parser("digitize")
    pid.add_argument("source", type=Path)
    pid.add_argument("--output", type=Path, required=True)
    pid.add_argument("--endpoint", default="http://127.0.0.1:8000/v1")
    pid.add_argument("--model", required=True)
    pid.add_argument("--page", type=int, default=1)
    pid.add_argument("--cloud", action="store_true")
    pid.add_argument("--consent-external-transfer", action="store_true", help="Explicitly authorize this job's source/crop transmission")
    pid.add_argument("--max-calls", type=int, default=500)
    pid.add_argument("--max-seconds", type=int, default=3600)
    pid.add_argument("--cost-budget", type=float, default=10)
    build = commands.add_parser("build-cad")
    build.add_argument("graph", type=Path)
    build.add_argument("destination", type=Path)
    schemas = commands.add_parser("schemas")
    schemas.add_argument("--output", type=Path, default=Path("schemas"))
    evaluate_cmd = commands.add_parser("evaluate")
    evaluate_cmd.add_argument("gold", type=Path)
    evaluate_cmd.add_argument("prediction", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "desktop":
            from .desktop import main as desktop_main
            return desktop_main()
        if args.command == "inspect":
            from .core import inspect_pdf
            result = inspect_pdf(args.source)
        elif args.command == "convert":
            from .core import convert_file, ConverterConfig
            options = {"pages": args.pages, "raster": args.raster, "scale": 1 if args.scale == "fit" else float(args.scale)} if args.source.suffix.lower() == ".pdf" else {"paper": args.paper, "scale": args.scale}
            result = convert_file(args.source, args.destination,
                                  config=ConverterConfig(args.converter, args.converter_kind, args.version), **options)
        elif args.command == "digitize":
            from .models import PipelineSettings
            from .providers import EndpointProvider
            from .pipeline import Pipeline
            mode = "cloud" if args.cloud else "local"
            settings = PipelineSettings(provider_mode=mode, cloud_consent=args.consent_external_transfer,
                                        max_calls=args.max_calls, max_seconds=args.max_seconds, max_estimated_cost=args.cost_budget)
            provider = EndpointProvider(args.endpoint, args.model, mode=mode,
                                        allow_external=args.consent_external_transfer, artifact_dir=args.output,
                                        api_key=os.getenv("LINEAFORGE_VLM_KEY"))
            result = Pipeline(provider, settings, args.output).run(args.source, page=args.page-1).model_dump()
        elif args.command == "build-cad":
            from .models import PidGraph
            from .cad import EzdxfBackend, build_graph_cad
            graph = PidGraph.model_validate_json(args.graph.read_text(encoding="utf-8"))
            result = build_graph_cad(graph, EzdxfBackend(), args.destination)
        elif args.command == "schemas":
            from .models import Element, PidGraph, Extraction, AgentMessage, PipelineSettings, DenoisePlan
            args.output.mkdir(parents=True, exist_ok=True)
            for model in (Element, PidGraph, Extraction, AgentMessage, PipelineSettings, DenoisePlan):
                (args.output / (model.__name__+".schema.json")).write_text(json.dumps(model.model_json_schema(), indent=2), encoding="utf-8")
            result = {"schemas": str(args.output)}
        elif args.command == "evaluate":
            from .evaluation import evaluate
            result = evaluate(json.loads(args.gold.read_text(encoding="utf-8")), json.loads(args.prediction.read_text(encoding="utf-8")))
        print(json.dumps(result, indent=2, default=str))
        return result
    except Exception as exc:
        parser.exit(1, f"LineaForge: {exc}\n")
