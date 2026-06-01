from __future__ import annotations


class ProjectionService:
    def product_projection(self, events: list[dict], workspace_id: str, product_id: str) -> list[dict]:
        out: list[dict] = []
        for item in events:
            entity_type = str(item.get("entity_type") or "")
            entity_id = str(item.get("entity_id") or "")
            ws = str(item.get("workspace_id") or "")
            if ws != workspace_id:
                continue
            if entity_type == "product" and entity_id == product_id:
                out.append(dict(item))
        return out

    def approval_projection(self, events: list[dict], workspace_id: str) -> list[dict]:
        return [dict(x) for x in events if x.get("workspace_id") == workspace_id and str(x.get("event_type") or "").startswith("approval")]

    def campaign_projection(self, events: list[dict], workspace_id: str) -> list[dict]:
        return [dict(x) for x in events if x.get("workspace_id") == workspace_id and "campaign" in str(x.get("event_type") or "")]

    def asset_projection(self, events: list[dict], workspace_id: str) -> list[dict]:
        return [dict(x) for x in events if x.get("workspace_id") == workspace_id and "asset" in str(x.get("event_type") or "")]
