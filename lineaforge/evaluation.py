"""Evaluate declared annotation scope; unknown regions are never negatives."""
from __future__ import annotations
from .pipeline import iou


def evaluate(gold: dict, prediction: dict, threshold=.5):
    """Common-format full/region annotation metric, not a VLM inference runner.

    Inputs: {scope: full|regions, elements:[{kind,tag,bbox}], connections:[[id,id]]}.
    Partial gold must supply evaluation_regions; predictions outside are ignored.
    """
    scope = gold.get("scope", "full")
    regions = gold.get("evaluation_regions", [])
    if scope != "full" and not regions:
        return {"status": "NOT_MEASURED", "reason": "Partial annotations lack complete evaluation regions; whole-page precision/recall is undefined"}
    truth = gold.get("elements", [])
    predicted = prediction.get("elements", [])
    if regions:
        predicted = [p for p in predicted if any(r[0] <= (p["bbox"][0]+p["bbox"][2])/2 <= r[2] and
                                                 r[1] <= (p["bbox"][1]+p["bbox"][3])/2 <= r[3] for r in regions)]
    in_scope_ids = {p["id"] for p in predicted}
    matched, pairs = set(), []
    for p in predicted:
        candidates = [(iou(p["bbox"], g["bbox"]), j) for j, g in enumerate(truth)
                      if j not in matched and p["kind"] == g["kind"]]
        if candidates and max(candidates)[0] >= threshold:
            _, j = max(candidates)
            matched.add(j)
            pairs.append((p, truth[j]))
    tp = len(pairs)
    precision = tp/len(predicted) if predicted else 0.0
    recall = tp/len(truth) if truth else 0.0
    tagged = [(p, g) for p, g in pairs if g.get("tag")]
    tag_correct = sum(p.get("tag") == g["tag"] for p, g in tagged)
    line_pairs = [(p, g) for p, g in pairs if g.get("line_number")]
    mapping = {p["id"]: g["id"] for p, g in pairs}
    gold_edges = {tuple(sorted(e)) for e in gold.get("connections", [])}
    pred_edges = {tuple(sorted([mapping.get(e[0], "unmatched:"+e[0]), mapping.get(e[1], "unmatched:"+e[1])]))
                  for e in prediction.get("connections", [])
                  if not regions or (e[0] in in_scope_ids and e[1] in in_scope_ids)}
    edge_tp = len(gold_edges & pred_edges)
    edge_precision = edge_tp/len(pred_edges) if pred_edges else 0
    edge_recall = edge_tp/len(gold_edges) if gold_edges else 0
    return {"status": "MEASURED", "scope": scope, "gold_symbols": len(truth), "predicted_symbols": len(predicted),
            "symbol_precision": precision, "symbol_recall": recall,
            "symbol_f1": 2*precision*recall/(precision+recall) if precision+recall else 0,
            "tag_exact_match": tag_correct/len(tagged) if tagged else None,
            "tag_detection_exact_recall": tag_correct/sum(bool(g.get("tag")) for g in truth) if any(g.get("tag") for g in truth) else None,
            "line_number_exact_match": sum(p.get("line_number") == g["line_number"] for p,g in line_pairs)/len(line_pairs) if line_pairs else None,
            "connectivity_f1": 2*edge_precision*edge_recall/(edge_precision+edge_recall) if edge_precision+edge_recall else (0 if gold_edges or pred_edges else None)}
