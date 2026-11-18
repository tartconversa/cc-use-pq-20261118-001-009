"""输入边界校验、标准身份与确定性指纹。"""
import hashlib
import json
from datetime import datetime, timezone
from .errors import require
from .storage import encode


STANCES = {"positive", "neutral", "negative", "mixed"}


def digest(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def text(value, name, maximum=160):
    require(isinstance(value, str), "invalid_field", f"{name} must be text", field=name)
    value = value.strip()
    require(0 < len(value) <= maximum, "invalid_field", f"{name} length invalid", field=name)
    require(all(ord(ch) >= 32 for ch in value), "invalid_field", f"{name} contains controls", field=name)
    return value


def timestamp(value, name):
    value = text(value, name, 40)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    require(parsed is not None and parsed.tzinfo is not None, "invalid_time", f"{name} must have a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def string_set(value, name, maximum=32):
    require(isinstance(value, list) and 0 < len(value) <= maximum, "invalid_field", f"{name} must be a nonempty list")
    result = sorted({text(item, name, 80) for item in value})
    require(len(result) == len(value), "duplicate_field", f"{name} contains duplicate values")
    return result


def source(payload):
    require(isinstance(payload, dict), "invalid_source", "source must be an object")
    require(set(payload) == {"source_id", "region", "outlet", "topics", "audiences"},
            "invalid_source", "source fields mismatch")
    return {
        "source_id": text(payload["source_id"], "source_id"),
        "region": text(payload["region"], "region"),
        "outlet": text(payload["outlet"], "outlet"),
        "topics": string_set(payload["topics"], "topics"),
        "audiences": string_set(payload["audiences"], "audiences"),
    }


def observation(payload):
    require(isinstance(payload, dict), "invalid_observation", "observation must be an object")
    fields = {"origin", "external_id", "source_id", "story_key",
              "published_at", "received_at", "headline", "url"}
    require(set(payload) == fields, "invalid_observation", "observation fields mismatch")
    data = {key: text(payload[key], key, 400 if key in {"headline", "url"} else 160)
            for key in fields - {"published_at", "received_at"}}
    data["published_at"] = timestamp(payload["published_at"], "published_at")
    data["received_at"] = timestamp(payload["received_at"], "received_at")
    require(data["url"].startswith(("https://", "http://")), "invalid_url", "url must be HTTP(S)")
    return data


def decision(payload):
    fields = {"decision_id", "origin", "external_id", "revision", "reviewer",
              "topic", "stance", "audience", "reason"}
    require(isinstance(payload, dict) and set(payload) == fields, "invalid_decision", "decision fields mismatch")
    data = {key: text(payload[key], key, 800 if key == "reason" else 160)
            for key in fields - {"revision"}}
    require(type(payload["revision"]) is int and payload["revision"] > 0,
            "invalid_revision", "revision must be positive")
    data["revision"] = payload["revision"]
    require(data["stance"] in STANCES, "invalid_stance", "unsupported stance")
    return data


def adjudication(payload):
    fields = {"origin", "external_id", "revision", "adjudicator",
              "topic", "stance", "audience", "reason"}
    require(isinstance(payload, dict) and set(payload) == fields, "invalid_adjudication", "adjudication fields mismatch")
    data = decision({**{key: value for key, value in payload.items() if key != "adjudicator"},
                     "decision_id": "temporary", "reviewer": payload["adjudicator"]})
    data.pop("decision_id")
    data.pop("reviewer")
    data["adjudicator"] = text(payload["adjudicator"], "adjudicator")
    return data


def report_query(payload):
    fields = {"start", "end", "regions", "topics", "audiences"}
    require(isinstance(payload, dict) and set(payload) == fields, "invalid_query", "query fields mismatch")
    start = timestamp(payload["start"], "start")
    end = timestamp(payload["end"], "end")
    require(start < end, "invalid_window", "start must precede end")
    filters = {}
    for key in ("regions", "topics", "audiences"):
        value = payload[key]
        require(isinstance(value, list) and len(value) <= 32, "invalid_query", f"{key} must be list")
        filters[key] = sorted({text(item, key, 80) for item in value})
        require(len(filters[key]) == len(value), "invalid_query", f"duplicate {key}")
    return {"start": start, "end": end, **filters}


def canonical_json(raw):
    if isinstance(raw, (bytes, str)):
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("invalid JSON") from exc
    return raw

