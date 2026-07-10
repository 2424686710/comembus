"""Data model for leased shared-memory object lifecycle tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


ACTIVE = "active"
RECLAIMED = "reclaimed"
FORCE_CLEANED = "force_cleaned"


@dataclass
class ObjectLifecycleRecord:
    object_id: str
    shm_name: str
    owner_agent: str
    consumer_agents: List[str] = field(default_factory=list)
    ref_count: int = 0
    lease_deadline: float = 0.0
    state: str = ACTIVE
    created_at: float = 0.0
    last_access: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "object_id": self.object_id,
            "shm_name": self.shm_name,
            "owner_agent": self.owner_agent,
            "consumer_agents": list(self.consumer_agents),
            "ref_count": self.ref_count,
            "lease_deadline": self.lease_deadline,
            "state": self.state,
            "created_at": self.created_at,
            "last_access": self.last_access,
        }
