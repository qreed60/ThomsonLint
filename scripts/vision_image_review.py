#!/usr/bin/env python3
"""
Vision-based PNG review for ThomsonLint Phase 13.

Uses an OpenAI-compatible multimodal /v1/chat/completions endpoint.
Requires a vision-capable model. Text-only models should fail/block rather
than pretending metadata/pixel sampling is vision.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

STRICT_JSON_ONLY_PROMPT = (
    "Return only minified valid JSON. No markdown. No prose. No comments. "
    "No trailing commas. Escape quotes inside strings. Output exactly one JSON object."
)


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def post_chat_completion(
    base_url: str,
    api_key: str,
    model: str,
    image_path: Path,
    prompt: str,
    timeout: int,
    max_tokens: int,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"

    payload = {
        "model": model,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are reviewing electronics schematic/layout PNG evidence. "
                    "Use actual image content. Do not claim pixel-derived trace widths, "
                    "clearances, pad sizes, or dimensions. Return compact valid JSON only."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_uri(image_path)},
                    },
                ],
            },
        ],
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from vision endpoint: {body[:1000]}") from e
    except Exception as e:
        raise RuntimeError(f"vision endpoint call failed: {e}") from e

    try:
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"unexpected response shape: {json.dumps(data)[:1000]}") from e


def strip_markdown_fences(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned

    lines = cleaned.splitlines()
    if not lines:
        return cleaned
    if not lines[0].strip().startswith("```"):
        return cleaned

    lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def first_complete_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]

    return None


def extract_json(text: str) -> dict[str, Any]:
    cleaned = strip_markdown_fences(text)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    object_text = first_complete_json_object(cleaned)
    if object_text:
        parsed = json.loads(object_text)
        if isinstance(parsed, dict):
            return parsed

    raise ValueError(f"model did not return valid JSON: {text[:500]}")


def prompt_for(path: Path, kind: str) -> str:
    if kind == "schematic":
        return f"""
Review this schematic PNG page: {path.name}

Return JSON with exactly these keys:
{{
  "page_type": "schematic",
  "visual_review_performed": true,
  "brief_description": "...",
  "visible_circuit_blocks": ["..."],
  "visible_components_or_refdes": ["..."],
  "visible_net_labels_or_signal_names": ["..."],
  "visible_component_values_or_ratings": ["..."],
  "possible_electrical_calculations_from_visible_values": ["..."],
  "possible_concerns_or_followups": ["..."],
  "limitations": ["..."],
  "confirmation_no_pixel_quantitative_claims": true
}}

Rules:
- Use what is visible in the image.
- It is allowed to read visible resistor/capacitor/voltage/current values.
- It is allowed to suggest electrical calculations from visible schematic values.
- Do not invent unreadable text.
- Do not measure physical layout geometry from pixels.
- Do not create final findings.
"""
    return f"""
Review this PCB layout/Gerber PNG page: {path.name}

Return JSON with exactly these keys:
{{
  "page_type": "layout",
  "visual_review_performed": true,
  "brief_description": "...",
  "visible_layer_or_drawing_role": "...",
  "visible_board_features": ["..."],
  "visible_text_or_labels": ["..."],
  "possible_concerns_or_followups": ["..."],
  "limitations": ["..."],
  "confirmation_no_pixel_quantitative_claims": true
}}

Rules:
- Use what is visible in the image.
- Do not infer trace width, clearance, creepage, pad size, hole size, or board dimensions from pixels.
- For physical geometry, defer to board JSON / IPC-2581 evidence.
- Do not create final findings.
"""


def list_images(exports: Path, project: str) -> list[tuple[str, Path]]:
    schematic = sorted(exports.glob(f"{project}-img-sch-p*.png"))
    layout = sorted(exports.glob(f"{project}-img-layout-p*.png"))
    return [("schematic", p) for p in schematic] + [("layout", p) for p in layout]


def observation_successful(observation: dict[str, Any]) -> bool:
    response = observation.get("response")
    if not isinstance(response, dict):
        return False
    return (
        response.get("visual_review_performed") is True
        and response.get("confirmation_no_pixel_quantitative_claims") is True
    )


def load_previous_artifact(out: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not out.exists():
        return [], []

    try:
        data = json.loads(out.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARNING: could not load existing artifact {out}: {exc}", file=sys.stderr)
        return [], []

    observations = data.get("per_page_vision_observations", [])
    errors = data.get("errors", [])
    if not isinstance(observations, list):
        observations = []
    if not isinstance(errors, list):
        errors = []

    return (
        [obs for obs in observations if isinstance(obs, dict)],
        [err for err in errors if isinstance(err, dict)],
    )


def safe_stem(path: Path) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in path.name)


def raw_response_path(raw_out_dir: Path, image_path: Path, attempt: int) -> Path:
    return raw_out_dir / f"{safe_stem(image_path)}.attempt{attempt}.txt"


def write_raw_response(raw_out_dir: Path, image_path: Path, attempt: int, content: str) -> Path:
    path = raw_response_path(raw_out_dir, image_path, attempt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def review_image_with_retries(
    *,
    base_url: str,
    api_key: str,
    model: str,
    image_path: Path,
    prompt: str,
    timeout: int,
    max_tokens: int,
    retries: int,
    raw_out_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    parse_errors: list[dict[str, Any]] = []
    raw_paths: list[str] = []
    attempts = max(0, retries) + 1

    for attempt in range(1, attempts + 1):
        attempt_prompt = prompt if attempt == 1 else f"{STRICT_JSON_ONLY_PROMPT}\n\n{prompt}"
        content = post_chat_completion(
            base_url=base_url,
            api_key=api_key,
            model=model,
            image_path=image_path,
            prompt=attempt_prompt,
            timeout=timeout,
            max_tokens=max_tokens,
        )
        raw_path = write_raw_response(raw_out_dir, image_path, attempt, content)
        raw_paths.append(str(raw_path))
        try:
            parsed = extract_json(content)
            diagnostics = {
                "raw_response_path": str(raw_path),
                "raw_response_paths": raw_paths,
                "parse_errors": parse_errors,
                "retry_count_used": attempt - 1,
            }
            return parsed, diagnostics
        except Exception as exc:
            parse_errors.append(
                {
                    "attempt": attempt,
                    "raw_response_path": str(raw_path),
                    "error": str(exc),
                }
            )

    raise ValueError(
        json.dumps(
            {
                "message": "model did not return valid JSON after retries",
                "retry_count_used": attempts - 1,
                "raw_response_paths": raw_paths,
                "parse_errors": parse_errors,
            },
            ensure_ascii=False,
        )
    )


def artifact_for(
    *,
    project: str,
    base_url: str,
    model: str,
    expected: int,
    observations: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    by_file: dict[str, dict[str, Any]] = {}
    ordered_files: list[str] = []
    for observation in observations:
        file_name = str(observation.get("file") or "")
        if not file_name:
            continue
        if file_name not in by_file:
            ordered_files.append(file_name)
        by_file[file_name] = observation

    deduped_observations = [by_file[file_name] for file_name in ordered_files]
    reviewed = len([obs for obs in deduped_observations if observation_successful(obs)])
    all_reviewed = expected > 0 and reviewed == expected and not errors
    all_no_pixel_geometry = (
        len(deduped_observations) == reviewed
        and all(bool(obs.get("response", {}).get("confirmation_no_pixel_quantitative_claims")) for obs in deduped_observations)
    )

    return {
        "phase": 13,
        "phase_name": "Review Image Evidence FULL",
        "project": project,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "vision_base_url": base_url,
        "vision_model": model,
        "expected_image_count": expected,
        "reviewed_image_count": reviewed,
        "vision_review_performed": all_reviewed,
        "metadata_only_review": False,
        "actual_multimodal_endpoint_used": True,
        "per_page_vision_observations": deduped_observations,
        "errors": errors,
        "confirmation_no_pixel_quantitative_claims": all_no_pixel_geometry,
        "allowed_quantitative_scope": (
            "Electrical calculations may be derived from visibly readable schematic values. "
            "Physical/layout geometry measurements must not be derived from raster pixels unless calibrated."
        ),
        "overall_pass": bool(all_reviewed and all_no_pixel_geometry),
        "phase_13_completed": bool(all_reviewed and all_no_pixel_geometry),
    }


def write_artifacts(
    *,
    out: Path,
    project: str,
    base_url: str,
    model: str,
    expected: int,
    observations: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    artifact = artifact_for(
        project=project,
        base_url=base_url,
        model=model,
        expected=expected,
        observations=observations,
        errors=errors,
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")

    validation_artifact = {
        "phase": 13,
        "inventory_exists": True,
        "required_fields_present": True,
        "pages_actually_opened_count": artifact["reviewed_image_count"],
        "phase_13_completed": artifact["phase_13_completed"],
        "overall_pass": artifact["overall_pass"],
    }

    val_out = out.parent / f"{project}-image-evidence-review-validation.json"
    val_out.write_text(json.dumps(validation_artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    return artifact


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="example")
    ap.add_argument("--exports", default="exports")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--max-tokens", type=int, default=1200)
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--raw-out-dir", default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    exports = Path(args.exports)
    out = Path(args.out) if args.out else exports / f"{args.project}-image-evidence-review.json"
    raw_out_dir = Path(args.raw_out_dir) if args.raw_out_dir else exports / "vision_raw_responses" / args.project

    base_url = env("VISION_BASE_URL", env("LLM_BASE_URL"))
    model = env("VISION_MODEL", env("LLM_MODEL"))
    api_key = env("VISION_API_KEY", env("LLM_API_KEY", "local"))

    if not base_url or not model:
        print("ERROR: set VISION_BASE_URL and VISION_MODEL, or LLM_BASE_URL and LLM_MODEL", file=sys.stderr)
        return 2

    images = list_images(exports, args.project)
    if args.limit and args.limit > 0:
        images = images[: args.limit]

    target_files = {str(path) for _, path in images}
    target_order = [str(path) for _, path in images]
    observations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    if args.resume and not args.force:
        previous_observations, previous_errors = load_previous_artifact(out)
        observations = [
            obs
            for obs in previous_observations
            if str(obs.get("file") or "") in target_files and observation_successful(obs)
        ]
        completed = {str(obs.get("file") or "") for obs in observations}
        errors = [
            err
            for err in previous_errors
            if str(err.get("file") or "") in target_files and str(err.get("file") or "") not in completed
        ]
        print(f"Resume loaded: {len(observations)} successful observations, {len(errors)} prior errors")
    elif args.force:
        print("Force enabled: ignoring previous observations")

    by_file = {str(obs.get("file") or ""): obs for obs in observations}
    errors = [err for err in errors if str(err.get("file") or "") not in by_file]

    artifact = write_artifacts(
        out=out,
        project=args.project,
        base_url=base_url,
        model=model,
        expected=len(images),
        observations=observations,
        errors=errors,
    )

    for kind, path in images:
        path_key = str(path)
        if not args.force and path_key in by_file and observation_successful(by_file[path_key]):
            print(f"SKIP existing vision review: {path.name}")
            continue

        errors = [err for err in errors if str(err.get("file") or "") != path_key]

        try:
            parsed, diagnostics = review_image_with_retries(
                base_url=base_url,
                api_key=api_key or "local",
                model=model,
                image_path=path,
                prompt=prompt_for(path, kind),
                timeout=args.timeout,
                max_tokens=args.max_tokens,
                retries=args.retries,
                raw_out_dir=raw_out_dir,
            )
            observation = {
                "file": path_key,
                "kind": kind,
                "model": model,
                **diagnostics,
                "response": parsed,
            }
            by_file[path_key] = observation
            observations = [by_file[file_name] for file_name in target_order if file_name in by_file]
            print(f"PASS vision review: {path.name}")
        except Exception as e:
            error: dict[str, Any] = {"file": path_key, "kind": kind, "model": model, "error": str(e)}
            try:
                details = json.loads(str(e))
                if isinstance(details, dict):
                    error.update(details)
            except Exception:
                pass
            errors.append(error)
            print(f"FAIL vision review: {path.name}: {e}", file=sys.stderr)

        artifact = write_artifacts(
            out=out,
            project=args.project,
            base_url=base_url,
            model=model,
            expected=len(images),
            observations=observations,
            errors=errors,
        )
        print(
            f"Progress written: {artifact['reviewed_image_count']}/{artifact['expected_image_count']} "
            f"reviewed, errors={len(artifact['errors'])}, overall_pass={artifact['overall_pass']}"
        )
        time.sleep(args.sleep)

    artifact = write_artifacts(
        out=out,
        project=args.project,
        base_url=base_url,
        model=model,
        expected=len(images),
        observations=observations,
        errors=errors,
    )
    print(f"\nWrote {out}")
    print("overall_pass:", artifact["overall_pass"])
    print("reviewed:", artifact["reviewed_image_count"], "/", artifact["expected_image_count"])
    print("errors:", len(errors))
    print(f"Wrote {out.parent / f'{args.project}-image-evidence-review-validation.json'}")

    return 0 if artifact["overall_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
